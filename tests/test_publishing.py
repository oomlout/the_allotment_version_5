from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import shutil
import zipfile

import pytest
from werkzeug.datastructures import FileStorage

from allotment.app import create_app
from allotment.bundles import export_bundle, import_receipts
from allotment.storage import Store, git, inside, read_yaml, write_yaml
from allotment.website import build_site
from allotment.worker import retry_action, run_worker


NS, USER = "the_allotment", "oomlout_main"


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path)
    value.bootstrap()
    return value


def media(name="picture.png", content=b"example image data"):
    return FileStorage(stream=BytesIO(content), filename=name)


def publish(store, actions=("static",), text="A small post 🌱"):
    post = store.save_draft(NS, USER, text, [media()])
    return store.publish(NS, USER, post["id"], actions)


def enable(store, platform="bluesky"):
    write_yaml(store.settings_path(NS, USER), {"destinations": {platform: {"enabled": True, "reviewed": True,
               "expected_account": "@right-account", "profile_url": "https://example.org/right-account"}}})
    # A completed test fixture supplies approval evidence for queue/worker unit tests.
    from allotment.approval import approve_run, run_folder, signature
    account = store.settings(NS, USER)["destinations"][platform]
    folder = run_folder(store, NS, USER, "test-fixture")
    write_yaml(folder / "run.yaml", {"id": "test-fixture", "platform": platform,
               "signature": signature(store, store.plugins()[platform], account),
               "post": {"attachments": [{"type": "image"}]}})
    write_yaml(folder / "progress.yaml", {"status": "prepared", "verified": False, "draft_checked": True, "sent": False})
    approve_run(store, NS, USER, "test-fixture")


def test_draft_media_and_text_never_enter_public_repository(store):
    post = store.save_draft(NS, USER, "private unfinished thought", [media("secret.png")])
    assert not store.posts(NS, USER)
    assert not store.jobs(NS, USER)
    repo = store.repo(NS, USER)
    assert "private unfinished thought" not in "".join(p.read_text(errors="ignore") for p in repo.rglob("*") if p.is_file())
    assert not list(repo.rglob("secret.png"))
    assert git(repo, "status", "--porcelain") == ""
    assert store.draft_dir(NS, USER, post["id"]).is_dir()


def test_publish_snapshots_media_and_worker_builds_real_static_site(store):
    job = publish(store)
    assert store.jobs(NS, USER)[0]["actions"]["static"]["status"] == "pending"
    assert not store.drafts(NS, USER)
    results = run_worker(store)
    assert results[0]["actions"]["static"]["status"] == "succeeded"
    folder = store.output / NS / USER
    html = (folder / "index.html").read_text(encoding="utf-8")
    assert "A small post 🌱" in html
    assert (folder / "p" / job["post"]["id"] / "index.html").exists()
    assert list(folder.rglob("*.png"))
    assert run_worker(store) == []
    assert not (store.output / NS / "allotment_main").exists()


def test_editing_published_post_keeps_previous_feed_until_publish(store):
    job = publish(store)
    post_id = job["post"]["id"]
    draft = store.save_draft(NS, USER, "revised local text", post_id=post_id)
    assert draft["status"] == "draft"
    assert store.published(NS, USER, post_id)["text"] == "A small post 🌱"
    assert inside(store.draft_dir(NS, USER, post_id), draft["attachments"][0]["file"]).exists()
    build_site(store, NS, USER)
    assert "revised local text" not in (store.output / NS / USER / "index.html").read_text(encoding="utf-8")


def test_rejects_unknown_actions_and_duplicate_queued_external_posts(store):
    enable(store)
    job = publish(store, ("bluesky",))
    with pytest.raises(ValueError, match="already been"):
        store.publish(NS, USER, job["post"]["id"], ["bluesky"])
    with pytest.raises(ValueError, match="available"):
        store.publish(NS, USER, job["post"]["id"], ["made-up"])
    assert len(store.jobs(NS, USER)) == 1


@pytest.mark.parametrize("after_click,expected", [(False, "failed"), (True, "failed")])
def test_worker_distinguishes_before_and_after_submission_failures(store, monkeypatch, after_click, expected):
    enable(store)
    job = publish(store, ("bluesky",))
    class Page:
        def screenshot(self, **kwargs): pass
        def content(self): return "<html>evidence</html>"
    class Context:
        def new_page(self): return Page()
    @contextmanager
    def context(*_): yield Context()
    def recipe(page, recipe, account, post, repo, before_submit):
        if after_click:
            before_submit()
        raise RuntimeError("fixture platform failure")
    monkeypatch.setattr("allotment.worker.browser_context", context)
    monkeypatch.setattr("allotment.worker.run_recipe", recipe)
    result = run_worker(store)[0]
    assert result["actions"]["bluesky"]["status"] == expected
    assert run_worker(store) == []
    retry_action(store, NS, USER, job['id'], 'bluesky')
    assert store.jobs(NS, USER)[0]['actions']['bluesky']['status']=='pending'



@pytest.mark.parametrize('after_click,status',[(False,'failed')])
def test_emergency_stop_aborts_remaining_jobs_after_saving_receipt(store,monkeypatch,after_click,status):
    from allotment.browser import StopRequested
    enable(store)
    publish(store,('bluesky',),text='First queued post')
    publish(store,('bluesky',),text='Second queued post')
    @contextmanager
    def context(*args): yield object()
    def stop(desktop,recipe,account,post,repo,before_submit):
        if after_click: before_submit()
        raise StopRequested('F8')
    monkeypatch.setattr('allotment.worker.browser_context',context)
    monkeypatch.setattr('allotment.worker.run_recipe',stop)
    with pytest.raises(StopRequested): run_worker(store)
    statuses=[job['actions']['bluesky']['status'] for job in store.jobs(NS,USER)]
    assert sorted(statuses)==sorted([status,'pending'])


def test_successful_external_post_is_selectable_in_studio_and_retains_link(store, monkeypatch):
    enable(store)
    job = publish(store, ("bluesky",))
    job["actions"]["bluesky"].update(status="succeeded", url="https://x.com/right-account/status/1234")
    write_yaml(store.job_path(NS, USER, job["id"]), job)
    store.save_draft(NS, USER, "New wording", post_id=job["post"]["id"])
    with pytest.raises(ValueError, match="already been"):
        store.publish(NS, USER, job["post"]["id"], ["bluesky"])
    html = create_app(store.root, testing=True,garden=('the_allotment','oomlout_main')).test_client().get(f"/g/{NS}/{USER}/p/{job['post']['id']}/").text
    from bs4 import BeautifulSoup
    assert not BeautifulSoup(html,'html.parser').select_one('[name="actions"][value="bluesky"]').has_attr('disabled')
    assert "https://x.com/right-account/status/1234" in html


def test_export_hands_off_only_selected_job_and_receipts_return_without_duplicates(store, tmp_path):
    enable(store)
    (store.local / "browser-profile").mkdir()
    (store.local / "browser-profile" / "Cookies").write_text("NEVER EXPORT")
    job = publish(store)
    another = publish(store, text="A different pending job")
    bundle = export_bundle(store, NS, USER, job["id"])
    assert read_yaml(store.job_path(NS, USER, job["id"]))["actions"]["static"]["status"] == "exported"
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        assert "run_publish.bat" in names
        assert 'allotment/roboclick/framework.py' in names
        assert 'allotment/roboclick/desktop.py' in names
        assert 'playwright' not in archive.read('requirements.txt').decode().lower()
        assert 'playwright' not in archive.read('setup_worker.bat').decode().lower()
        assert not any("Cookies" in name or "session-secret" in name for name in names)
        assert not any(another["id"] in name for name in names)
        target = tmp_path / "standalone"
        archive.extractall(target)
    portable = Store(target)
    git(portable.repo(NS, USER), "init", "-b", "main")
    results = run_worker(portable)
    assert results[0]["actions"]["static"]["status"] == "succeeded"
    assert (portable.output / NS / USER / "index.html").exists()
    assert import_receipts(store, NS, USER, results[0]) == 1
    assert read_yaml(store.job_path(NS, USER, job["id"]))["actions"]["static"]["status"] == "succeeded"
    # The originating worker executes only the other job, not the handed-off action.
    assert [j["id"] for j in run_worker(store)] == [another["id"]]
    with pytest.raises(ValueError, match="No completed receipts"):
        import_receipts(store, NS, USER, results[0])


def test_import_ten_latest_is_idempotent_and_creates_no_jobs(store, tmp_path):
    source = tmp_path / "legacy-source"
    for day in range(1, 13):
        folder = source / "garden" / "2025" / "03" / f"{day:02}" / "10_00_00"
        folder.mkdir(parents=True)
        write_yaml(folder / "beet.yaml", {"beet": f"Historical day {day}", "id": f"original_{day}", "cross_post": ["bluesky"]})
        (folder / "image.jpg").write_bytes(b"fixture image")
    imported = store.import_legacy(source, NS, USER, 10)
    assert len(imported) == 10
    assert store.posts(NS, USER)[0]["text"] == "Historical day 12"
    assert store.posts(NS, USER)[-1]["text"] == "Historical day 3"
    assert all(p["status"] == "published" and p["legacy"]["id"] for p in store.posts(NS, USER))
    assert not store.jobs(NS, USER)
    assert store.import_legacy(source, NS, USER, 10) == []
    assert len(store.posts(NS, USER)) == 10


def test_static_html_escapes_text_and_resolves_relative_assets(store):
    publish(store, text='<script>alert("x")</script> & a garden')
    output = build_site(store, NS, USER)
    from html.parser import HTMLParser
    from urllib.parse import unquote, urlparse
    class Links(HTMLParser):
        def __init__(self): super().__init__(); self.links = []
        def handle_starttag(self, tag, attrs):
            for key, value in attrs:
                if key in {"src", "href"} and not urlparse(value).scheme and not value.startswith("#"):
                    self.links.append(unquote(value))
    for file in output.rglob("*.html"):
        html = file.read_text(encoding="utf-8")
        assert '<script>alert' not in html
        assert '&lt;script&gt;' in html
        parser = Links(); parser.feed(html)
        assert all((file.parent / link).is_file() for link in parser.links)


def test_web_forms_reject_csrf_and_save_media_then_queue_without_running(store):
    app = create_app(store.root, testing=True,garden=('the_allotment','oomlout_main'))
    client = app.test_client()
    base = f"/g/{NS}/{USER}"
    assert client.post(base + "/save", data={"text": "no csrf"}).status_code == 400
    html = client.get(base + "/compose/").text
    import re
    post_id = re.search('name="post_id" value="([^"]+)"', html)[1]
    with client.session_transaction() as session:
        csrf = session["csrf"]
    response = client.post(base + "/save", data={"csrf": csrf, "post_id": post_id, "text": "Web form post",
                         "intent": "publish", "actions": "static", "media": (BytesIO(b"image bytes"), "upload.png")})
    assert response.status_code == 302
    assert not store.posts(NS,USER) and not store.jobs(NS,USER)
    assert response.location.endswith(f'/p/{post_id}/')
    assert client.post(base+f'/p/{post_id}/local/publish',data={'csrf':csrf}).status_code==302
    app.extensions['job_manager']._launch=lambda *args:'run-fixture'
    response=client.post(base+f'/p/{post_id}/launch',data={'csrf':csrf,'actions':['static']})
    assert response.status_code==302
    assert store.posts(NS, USER)
    assert not store.drafts(NS,USER)
    assert store.jobs(NS, USER)[0]["actions"]["static"]["status"] == "pending"
    assert not store.output.exists()
    assert client.post(base + "/save", data={"csrf": csrf, "text": "test"}, headers={"Origin": "https://evil.example"}).status_code == 400


def test_file_and_identity_paths_cannot_escape_gardens(store):
    with pytest.raises(ValueError):
        store.create("../escape", "user")
    with pytest.raises(ValueError):
        inside(store.repo(NS, USER), "../../../../outside")
    with pytest.raises(ValueError, match="Choose JPG"):
        store.save_draft(NS, USER, "unsafe attachment", [media("script.html")])


def test_dry_run_does_not_mutate_queue_or_create_output(store):
    job = publish(store)
    before = store.job_path(NS, USER, job["id"]).read_bytes()
    run_worker(store, dry_run=True)
    assert store.job_path(NS, USER, job["id"]).read_bytes() == before
    assert not store.output.exists()


def test_full_git_transport_between_studio_and_worker(tmp_path):
    import subprocess
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    studio = Store(tmp_path / "studio"); studio.bootstrap()
    git(studio.repo(NS, USER), "remote", "add", "origin", str(remote))
    job = publish(studio)
    studio.sync(NS, USER)
    worker = Store(tmp_path / "worker")
    worker_repo = worker.repo(NS, USER)
    worker_repo.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", str(remote), str(worker_repo)], check=True, capture_output=True)
    results = run_worker(worker, pull=True)
    assert results[0]["actions"]["static"]["status"] == "succeeded"
    studio.sync(NS, USER)
    receipt = read_yaml(studio.job_path(NS, USER, job["id"]))
    assert receipt["actions"]["static"]["status"] == "succeeded"
    assert run_worker(studio) == []


def test_clone_replaces_only_pristine_default_and_backs_it_up(tmp_path, monkeypatch):
    source = Store(tmp_path / "source"); source.bootstrap()
    publish(source)
    from allotment.storage import commit
    commit(source.repo(NS, USER), "Example public content")
    target = Store(tmp_path / "target"); target.bootstrap()
    monkeypatch.setattr("allotment.storage.valid_remote", lambda url: url)
    target.create(NS, USER, remote=str(source.repo(NS, USER)), clone=True)
    assert len(target.posts(NS, USER)) == 1
    assert list((target.local / "replaced-gardens" / NS).glob(USER + "-*"))
    with pytest.raises(ValueError, match="already contains work"):
        target.create(NS, USER, remote=str(source.repo(NS, USER)), clone=True)


def test_bad_clone_identity_does_not_replace_empty_default(tmp_path, monkeypatch):
    source = Store(tmp_path / "source"); source.bootstrap()
    target = Store(tmp_path / "target"); target.bootstrap()
    monkeypatch.setattr("allotment.storage.valid_remote", lambda url: url)
    with pytest.raises(ValueError, match="identity/schema"):
        target.create(NS, USER, remote=str(source.repo(NS, "allotment_main")), clone=True)
    assert target.profile(NS, USER)["user"] == USER
    assert not git(target.repo(NS, USER), "remote")


def test_handed_off_external_action_cannot_be_requeued(store):
    enable(store)
    job = publish(store, ("bluesky",))
    export_bundle(store, NS, USER, job["id"])
    with pytest.raises(ValueError, match="already been"):
        store.publish(NS, USER, job["post"]["id"], ["bluesky"])


def test_interrupted_browser_submission_requires_review_and_build_can_be_retried(store):
    enable(store)
    job = publish(store, ("bluesky", "static"))
    job["actions"]["bluesky"]["status"] = "submitting"
    job["actions"]["static"]["status"] = "running"
    write_yaml(store.job_path(NS, USER, job["id"]), job)
    result = run_worker(store)[0]
    assert result["actions"]["bluesky"]["status"] == "uncertain"
    assert result["actions"]["static"]["status"] == "failed"
    retry_action(store, NS, USER, job["id"], "static")
    assert run_worker(store)[0]["actions"]["static"]["status"] == "succeeded"



def test_worker_prepares_each_job_in_new_context_and_never_repeats_drafts(store,monkeypatch):
    enable(store)
    publish(store,('bluesky',),text='First draft')
    publish(store,('bluesky',),text='Second draft')
    opened=[]
    @contextmanager
    def context(*args):
        page=object();opened.append(page);yield page
    def prepare(page,recipe,account,post,repo,before_submit):
        return {'prepared':True,'draft_checked':True,'sent':False,'verified':False}
    monkeypatch.setattr('allotment.worker.browser_context',context)
    monkeypatch.setattr('allotment.worker.run_recipe',prepare)
    results=run_worker(store)
    assert len(opened)==2 and opened[0] is not opened[1]
    assert all(j['actions']['bluesky']['status']=='prepared' for j in results)
    assert all(j['actions']['bluesky']['sent'] is False for j in results)
    assert run_worker(store)==[]
    job=results[0]
    with pytest.raises(ValueError):store.publish(NS,USER,job['post']['id'],['bluesky'])
    from allotment.worker import resolve_action
    resolve_action(store,NS,USER,job['id'],'bluesky','https://bsky.app/profile/owner/post/manual')
    assert read_yaml(store.job_path(NS,USER,job['id']))['actions']['bluesky']['resolved_manually']
