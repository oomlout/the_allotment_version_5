from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import uuid

import yaml
from werkzeug.utils import secure_filename

MEDIA = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm", ".mov"}
IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
BUILTINS = {"allotment": "Local publication (legacy)", "git": "Git sync", "static": "Static website", "summary":"Allotment summary"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_post_id():
    return f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"


def slug(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", value or ""):
        raise ValueError("Use 1–80 lowercase letters, numbers, underscores or hyphens.")
    return value


def inside(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Path is outside its garden.")
    return path


def read_yaml(path, default=None):
    path = Path(path)
    for attempt in range(6):
        try:
            return yaml.load(path.read_text(encoding="utf-8"), Loader=getattr(yaml, 'CSafeLoader', yaml.SafeLoader)) or ({} if default is None else default)
        except FileNotFoundError:
            return {} if default is None else default
        except PermissionError:
            # Windows may briefly deny a read while an atomic progress update
            # replaces the file. Preserve real errors after a bounded retry.
            if attempt == 5:
                raise
            time.sleep(min(0.01 * 2 ** attempt, 0.08))


def write_yaml(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    try:
        for attempt in range(8):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                # Windows readers/antivirus can briefly hold a target without delete sharing.
                if attempt == 7:
                    raise
                time.sleep(min(0.01 * 2 ** attempt, 0.1))
    finally:
        temp.unlink(missing_ok=True)


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=90)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout).strip()[-2000:])
    return result.stdout.strip()


def commit(repo, message):
    # User repositories contain only public material. Never stage the tools checkout.
    git(repo, "add", "--", ".")
    if git(repo, "diff", "--cached", "--name-only"):
        git(repo, "-c", "user.name=The Allotment", "-c", "user.email=allotment@localhost",
            "commit", "-m", message)


def valid_remote(value):
    if not re.fullmatch(r"(?:https://[^\s]+|git@[a-zA-Z0-9.-]+:[^\s]+)", value or ""):
        raise ValueError("Use an HTTPS Git URL or a git@host:path SSH URL.")
    if re.search(r"https://[^/]*@", value):
        raise ValueError("Keep credentials in Git's credential manager, outside repository URLs.")
    return value


@contextmanager
def lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.mkdir()
    except FileExistsError:
        raise ValueError(f"This garden is busy. If its worker stopped unexpectedly, inspect and remove {path}.")
    try:
        (path / "owner.txt").write_text(f"pid={os.getpid()}\nstarted={now()}\n", encoding="utf-8")
        yield
    finally:
        shutil.rmtree(path)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.local = self.root / ".local"
        self.garden = self.root / "garden"
        self.output = self.root / "allotment_static"
        self.plugins_dir = Path(__file__).parent / "plugins"
        self.local.mkdir(parents=True, exist_ok=True)

    def repo(self, namespace, user):
        return inside(self.garden, f"{slug(namespace)}/{slug(user)}")

    def profile(self, namespace, user):
        path = self.repo(namespace, user) / "profile.yaml"
        if not path.exists():
            raise ValueError("Garden not found. Create or clone it in Setup.")
        return read_yaml(path)

    def profiles(self):
        return [read_yaml(p) for p in sorted(self.garden.glob("*/*/profile.yaml"))]

    def settings_path(self, namespace, user):
        return self.local / "accounts" / slug(namespace) / f"{slug(user)}.yaml"

    def settings(self, namespace, user):
        return read_yaml(self.settings_path(namespace, user), {"destinations": {}})

    def garden_lock(self, namespace, user):
        repo = self.repo(namespace, user)
        return lock(repo / ".git" / "allotment.lock")

    def create(self, namespace, user, display_name=None, remote=None, clone=False):
        repo = self.repo(namespace, user)
        if repo.exists() and not clone:
            raise ValueError("That garden already exists.")
        repo.parent.mkdir(parents=True, exist_ok=True)
        if clone:
            valid_remote(remote)
            if repo.exists():
                # A fresh setup creates default placeholders. Only these pristine, empty
                # repositories may be replaced by a matching remote clone.
                if (self.posts(namespace, user) or self.drafts(namespace, user)
                        or git(repo, "status", "--porcelain") or git(repo, "remote")
                        or git(repo, "rev-list", "--count", "HEAD") != "1"):
                    raise ValueError("This garden already contains work. Use a new namespace/user or sync its existing repository.")
            with tempfile.TemporaryDirectory(dir=self.local) as temporary:
                checkout = Path(temporary) / "checkout"
                result = subprocess.run(["git", "clone", "--", remote, str(checkout)],
                                        capture_output=True, text=True, timeout=120)
                if result.returncode:
                    raise ValueError(result.stderr.strip())
                data = read_yaml(checkout / "profile.yaml")
                if data.get("namespace") != namespace or data.get("user") != user or data.get("schema_version") != 1:
                    raise ValueError("Cloned profile identity/schema does not match this namespace/user. Existing local data was kept.")
                if git(checkout, "branch", "--show-current") != "main":
                    raise ValueError("The data repository must use a main branch.")
                backup = None
                if repo.exists():
                    backup = inside(self.local, f"replaced-gardens/{namespace}/{user}-{uuid.uuid4().hex}")
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    # Both absolute paths were checked by inside() against their workspace roots.
                    repo.rename(backup)
                try:
                    checkout.rename(repo)
                except Exception:
                    if backup:
                        backup.rename(repo)
                    raise
            return repo
        repo.mkdir()
        git(repo, "init", "-b", "main")
        profile = {"schema_version": 1, "namespace": namespace, "user": user,
                   "display_name": display_name or user.replace("_", " ").title(),
                   "bio": "", "website": "", "avatar": "", "banner": "", "social": {},
                   "theme": {"accent": "#43634b", "background": "#f5f3ec", "layout": "timeline",
                             "density": "comfortable", "width": 760, "custom_css": ""}}
        write_yaml(repo / "profile.yaml", profile)
        (repo / ".gitignore").write_text(".DS_Store\nThumbs.db\n", encoding="utf-8")
        (repo / "README.md").write_text(
            f"# {namespace}/{user}\n\nPublic profile and published posts for The Allotment.\n"
            "Drafts and browser sessions live in the tools checkout's ignored `.local/` folder.\n"
            "Queue manifests and receipts in `.allotment/` are public and contain no credentials.\n",
            encoding="utf-8")
        commit(repo, "Create allotment garden")
        if remote:
            git(repo, "remote", "add", "origin", valid_remote(remote))
        return repo

    def bootstrap(self):
        for user, name in [("oomlout_main", "oomlout"), ("allotment_main", "The Allotment")]:
            if not self.repo("the_allotment", user).exists():
                self.create("the_allotment", user, name)

    def draft_dir(self, namespace, user, post_id):
        return self.local / "drafts" / slug(namespace) / slug(user) / slug(post_id)

    def posts(self, namespace, user):
        repo = self.repo(namespace, user)
        data = []
        for path in repo.glob("posts/*/*/*/*/post.yaml"):
            post = read_yaml(path)
            post["path"] = path.parent.relative_to(repo).as_posix()
            data.append(post)
        return sorted(data, key=lambda p: (p.get("published_at", ""), p["id"]), reverse=True)

    def drafts(self, namespace, user):
        folder = self.local / "drafts" / slug(namespace) / slug(user)
        return sorted([read_yaml(p) for p in folder.glob("*/post.yaml")],
                      key=lambda p: p.get("updated_at", ""), reverse=True)

    def published(self, namespace, user, post_id):
        return next((p for p in self.posts(namespace, user) if p["id"] == slug(post_id)), None)

    def draft(self, namespace, user, post_id):
        path = self.draft_dir(namespace, user, post_id) / "post.yaml"
        if path.exists():
            return read_yaml(path)
        post = self.published(namespace, user, post_id)
        if not post:
            raise ValueError("Post not found.")
        return post

    def save_draft(self, namespace, user, text, uploads=(), post_id=None, keep=None, tags=None, github_repo=None, links=None):
        self.profile(namespace, user)
        post_id = slug(post_id) if post_id else new_post_id()
        folder = self.draft_dir(namespace, user, post_id)
        old = self.draft(namespace, user, post_id) if (folder / "post.yaml").exists() or self.published(namespace, user, post_id) else {}
        if tags is not None:
            from .tags import validate_tags
            tags=validate_tags(self,namespace,user,tags)
        attachments = old.get("attachments", [])
        if keep is not None:
            attachments = [a for a in attachments if a["file"] in keep]
        # Validate the entire upload set before touching the previous draft.
        uploads = [u for u in uploads if u and u.filename]
        for upload in uploads:
            if Path(upload.filename).suffix.lower() not in MEDIA:
                raise ValueError("Choose JPG, PNG, GIF, WebP, MP4, WebM or MOV media.")
        folder.mkdir(parents=True, exist_ok=True)
        source = self.repo(namespace, user) / old.get("path", "")
        for attachment in attachments:
            from .images import media_files
            for filename in media_files(attachment):
                target = inside(folder, filename)
                if not target.exists() and source != self.repo(namespace, user):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(inside(source, filename), target)
        for upload in uploads:
            filename = f"{uuid.uuid4().hex[:10]}-{secure_filename(upload.filename) or 'media'}"
            target = folder / "media" / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            upload.save(target)
            attachments.append({"file": f"media/{filename}", "type": "image" if target.suffix.lower() in IMAGES else "video", "alt": ""})
        post = {**old, "schema_version": 1, "id": post_id, "text": text.strip(),
                "created_at": old.get("created_at", now()), "updated_at": now(),
                "status": "draft", "attachments": attachments}
        if tags is not None:post['tags']=tags
        if github_repo is not None:post['github_repo']=github_repo
        if links is not None:post['links']=links
        post.pop("path", None)
        write_yaml(folder / "post.yaml", post)
        return post

    def jobs(self, namespace, user):
        from .receipts import with_private_receipts
        from .sow_review import attach_reviews
        return sorted([attach_reviews(self, namespace, user, with_private_receipts(self, namespace, user, read_yaml(p))) for p in (self.repo(namespace, user) / ".allotment" / "queue").glob("*.yaml")],
                      key=lambda j: (j.get("created_at", ""), j["id"]), reverse=True)

    def job_path(self, namespace, user, job_id):
        return self.repo(namespace, user) / ".allotment" / "queue" / f"{slug(job_id)}.yaml"

    def destination_states(self, namespace, user, post_id):
        result = {}
        for job in self.jobs(namespace, user):
            if job["post"]["id"] == post_id:
                for action, receipt in job["actions"].items():
                    # Built-ins show the newest revision; external successes must remain visible.
                    rank = {"succeeded": 6, "prepared": 5, "uncertain": 4, "submitting": 3, "running": 2}
                    def priority(value):
                        verdict = value.get('review', {}).get('verdict')
                        return 7 if verdict == 'successful' else 0 if verdict == 'unsuccessful' else rank.get(value['status'], 0)
                    if action not in result or (job.get('kind')!='sow' and result[action].get('job_kind')!='sow' and action not in BUILTINS and priority(receipt) > priority(result[action])):
                        result[action] = {**receipt, "revision": job["revision"], "job_id": job["id"], 'job_kind':job.get('kind')}
        return result

    def plugins(self):
        result = {}
        for path in [*sorted(self.plugins_dir.glob("*.yaml")), *sorted((self.local / "plugins").glob("*.yaml"))]:
            data = read_yaml(path)
            if data.get('kind')=='harvest':continue
            result[slug(data["id"])] = data
        return result

    def publish(self, namespace, user, post_id, actions, variants=None, defer_public=False):
        actions = list(dict.fromkeys(actions))
        allowed = {*BUILTINS, *self.plugins()}
        if not actions or any(a not in allowed for a in actions):
            raise ValueError("Select at least one available publishing action.")
        with self.garden_lock(namespace, user):
            post = self.draft(namespace, user, post_id)
            if variants is not None:
                if any(key not in {str(i) for i in range(len(post['attachments']))} for key in variants):
                    raise ValueError('Unknown image selection.')
                for i,attachment in enumerate(post['attachments']):
                    choice=variants.get(str(i),'original')
                    if choice!='original' and choice not in attachment.get('variants',{}):
                        raise ValueError('Choose a saved image version.')
                    attachment['display_variant']=choice
            if not post.get("text") and not post.get("attachments") and not post.get('links'):
                raise ValueError("Add some text or media before publishing.")
            revision = hashlib.sha256(json.dumps({"text": post["text"], "attachments": post["attachments"], 'tags':post.get('tags',[]), 'links':post.get('links',[]),'github_repo':post.get('github_repo','')}, sort_keys=True).encode()).hexdigest()[:16]
            states = self.destination_states(namespace, user, post_id)
            accounts = self.settings(namespace, user).get("destinations", {})
            for action in actions:
                state = states.get(action, {})
                if action not in BUILTINS:
                    from .preparation_approval import require_approval
                    require_approval(self, namespace, user, self.plugins()[action], accounts.get(action, {}), post)
                    if not accounts.get(action, {}).get("enabled"):
                        raise ValueError(f"Enable and configure {action} in Destinations first.")
                    if state.get("status") in {"succeeded", "prepared", "uncertain", "running", "pending", "submitting", "exported"}:
                        raise ValueError(f"{action} has already been submitted or queued. Use its existing link or review the queue.")
                if state.get("status") in {"pending", "running", "submitting", "exported"}:
                    raise ValueError(f"{action} already has an active job for this post.")
            old = self.published(namespace, user, post_id)
            if defer_public and any(a in actions for a in ('static','git')) and 'allotment' not in actions and (not old or old.get('revision')!=revision):
                raise ValueError('Select Publish to the Allotment before exporting this version to a website or Git.')
            published_at = (old or {}).get("published_at", now())
            date = datetime.fromisoformat(published_at).strftime("%Y/%m/%d")
            job_id = f"job-{uuid.uuid4().hex}"
            relative = f".allotment/snapshots/{job_id}" if defer_public else f"posts/{date}/{slug(post_id)}"
            repo = self.repo(namespace, user)
            folder = inside(repo, relative)
            folder.mkdir(parents=True, exist_ok=True)
            draft_folder = self.draft_dir(namespace, user, post_id)
            for attachment in post["attachments"]:
                from .images import media_files
                for filename in media_files(attachment):
                    src = inside(draft_folder if (draft_folder / "post.yaml").exists() else inside(repo,old['path']), filename)
                    dst = inside(folder, filename)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if src != dst:
                        shutil.copy2(src, dst)
            public = {**post, "status": "queued" if defer_public else "published", "published_at": published_at, "revision": revision}
            public.pop("path", None)
            write_yaml(folder / "post.yaml", public)
            job = {"schema_version": 1, "id": job_id, "namespace": namespace, "user": user,
                   "created_at": now(), "revision": revision, "post": {**public, "path": relative},
                   "actions": {a: {"status": "pending", "attempts": 0, "url": "", "error": ""} for a in actions}}
            if defer_public:
                job['deferred_publication']=True
                if (draft_folder/'post.yaml').exists():job['source_draft_sha256']=hashlib.sha256((draft_folder/'post.yaml').read_bytes()).hexdigest()
            from .images import display_file
            job['post']['attachments']=[{**a,'file':display_file(a),'original_file':a['file']} for a in public['attachments']]
            write_yaml(self.job_path(namespace, user, job_id), job)
            if draft_folder.exists() and not defer_public:
                shutil.rmtree(draft_folder)
            return job

    def sync(self, namespace, user):
        repo = self.repo(namespace, user)
        with self.garden_lock(namespace, user):
            if "origin" not in git(repo, "remote").splitlines():
                raise ValueError("Add an origin remote in Setup before sending the queue to another computer.")
            commit(repo, "Sync public posts and publishing queue")
            if git(repo, "ls-remote", "--heads", "origin", "main"):
                git(repo, "pull", "--ff-only", "origin", "main")
            git(repo, "push", "-u", "origin", "main")

    def import_legacy(self, source, namespace, user, limit=10):
        source = Path(source).resolve()
        repo = self.repo(namespace, user)
        self.profile(namespace, user)
        candidates = []
        for path in source.glob("garden/*/*/*/*"):
            if not path.is_dir():
                continue
            if (path / "beet.yaml").exists():
                data = read_yaml(path / "beet.yaml")
            elif (path / "beet").exists():
                data = {f.name: f.read_text(encoding="utf-8") for f in path.iterdir() if f.is_file() and not f.suffix}
            else:
                continue
            stamp = datetime.strptime(path.relative_to(source / "garden").as_posix(), "%Y/%m/%d/%H_%M_%S")
            candidates.append((stamp, path, data))
        imported = []
        with self.garden_lock(namespace, user):
            for stamp, path, data in sorted(candidates, key=lambda c: c[0], reverse=True)[:limit]:
                post_id = f"legacy-{stamp:%Y%m%d-%H%M%S}"
                folder = repo / "posts" / stamp.strftime("%Y/%m/%d") / post_id
                if (folder / "post.yaml").exists():
                    continue
                folder.mkdir(parents=True, exist_ok=True)
                attachments = []
                for media in sorted(path.iterdir()):
                    if media.suffix.lower() in MEDIA:
                        target = folder / "media" / media.name
                        target.parent.mkdir(exist_ok=True)
                        shutil.copy2(media, target)
                        attachments.append({"file": f"media/{media.name}", "type": "image" if media.suffix.lower() in IMAGES else "video", "alt": ""})
                # Legacy timestamps were local, with no timezone. Interpret them as London time.
                from zoneinfo import ZoneInfo
                try:
                    tz = ZoneInfo("Europe/London")
                except KeyError:
                    raise ValueError("Install tzdata to import legacy Europe/London timestamps on Windows.")
                time = stamp.replace(tzinfo=tz).astimezone(timezone.utc).isoformat()
                post = {"schema_version": 1, "id": post_id, "text": data.get("beet", ""),
                        "status": "published", "created_at": time, "updated_at": time, "published_at": time,
                        "revision": "legacy", "attachments": attachments,
                        "legacy": {"id": data.get("id", ""), "source": path.relative_to(source).as_posix(),
                                   "cross_post": data.get("cross_post", []), "external_status": "not_verified"}}
                write_yaml(folder / "post.yaml", post)
                imported.append(post_id)
            profile = self.profile(namespace, user)
            if (source / "bio").exists():
                profile["bio"] = (source / "bio").read_text(encoding="utf-8").strip()
            for field, pattern in [("avatar", "image.*"), ("banner", "image_banner.*")]:
                media = next((p for p in source.glob(pattern) if p.suffix.lower() in IMAGES), None)
                if media:
                    dest = repo / "profile" / media.name
                    dest.parent.mkdir(exist_ok=True)
                    shutil.copy2(media, dest)
                    profile[field] = dest.relative_to(repo).as_posix()
            write_yaml(repo / "profile.yaml", profile)
            commit(repo, "Import recent historical posts without publishing jobs")
        return imported
