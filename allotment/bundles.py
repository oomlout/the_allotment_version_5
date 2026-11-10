from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import uuid
import zipfile

from .storage import BUILTINS, git, inside, read_yaml, write_yaml
from .receipts import save_job


BOOTSTRAP = '''"""Restore public Git history, if available, before running this portable job."""
from pathlib import Path
import shutil
import subprocess
from allotment.storage import Store, read_yaml, git

root = Path(__file__).resolve().parent
manifest = read_yaml(root / "bundle.yaml")
store = Store(root)
repo = store.repo(manifest["namespace"], manifest["user"])
if not (repo / ".git").exists():
    if manifest.get("origin"):
        clone = root / ".local" / "remote-history"
        if not clone.exists():
            subprocess.run(["git", "clone", "--", manifest["origin"], str(clone)], check=True)
        # Keep the exported snapshot and job; add any newer remote files we did not ship.
        for source in clone.rglob("*"):
            relative = source.relative_to(clone)
            if ".git" in relative.parts or ".allotment" in relative.parts or not source.is_file():
                continue
            target = repo / relative
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        shutil.copytree(clone / ".git", repo / ".git")
    else:
        git(repo, "init", "-b", "main")
print("Worker bundle ready. Open normal Chrome and sign in before running desktop destinations.")
'''


def export_bundle(store, namespace, user, job_id):
    with store.garden_lock(namespace, user):
        path = store.job_path(namespace, user, job_id)
        job = read_yaml(path)
        if not job:
            raise ValueError("Job not found.")
        destination = store.local / "bundles" / f"{job_id}.zip"
        if any(a["status"] == "exported" for a in job["actions"].values()):
            if destination.exists():
                return destination
            raise ValueError("This job has already been handed off. Import its receipts before exporting again.")
        pending = {name: receipt for name, receipt in job["actions"].items() if receipt["status"] == "pending"}
        if not pending:
            raise ValueError("Only pending actions can be exported.")
        token = uuid.uuid4().hex
        portable = {**job, "export_token": token, "actions": pending}
        repo = store.repo(namespace, user)
        remote = git(repo, "remote", "get-url", "origin") if "origin" in git(repo, "remote").splitlines() else ""
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=store.local) as temporary:
            folder = Path(temporary)
            package = Path(__file__).parent
            shutil.copytree(package, folder / "allotment", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            target_repo = folder / "garden" / namespace / user
            shutil.copytree(repo, target_repo, ignore=shutil.ignore_patterns(".git", ".allotment"))
            if job.get('deferred_publication'):
                shutil.copytree(inside(repo,job['post']['path']),inside(target_repo,job['post']['path']))
            write_yaml(target_repo / ".allotment" / "queue" / f"{job_id}.yaml", portable)
            write_yaml(folder / "bundle.yaml", {"namespace": namespace, "user": user, "job_id": job_id, "origin": remote})
            # Only this job's account identities and selectors; never cookies, credentials or browser state.
            accounts = store.settings(namespace, user).get("destinations", {})
            from .storage import slug
            for platform in pending:
                template_set=accounts.get(platform,{}).get('local_template_set')
                if not template_set:continue
                source=store.local/'platform-assets'/slug(template_set)/slug(platform)
                if source.is_dir():
                    shutil.copytree(source,folder/'.local/platform-assets'/slug(template_set)/slug(platform))
            write_yaml(folder / ".local" / "accounts" / namespace / f"{user}.yaml",
                       {"destinations": {k: v for k, v in accounts.items() if k in pending}})
            (folder / "bootstrap_worker.py").write_text(BOOTSTRAP, encoding="utf-8")
            (folder / "requirements.txt").write_text('Flask>=3.1,<4\nPyYAML>=6,<7\ntzdata>=2024.1\npsutil>=6,<8\nPyAutoGUI>=0.9.54,<1\npyperclip>=1.9,<2\nPillow>=11,<13\npytesseract>=0.3.13,<1\nopencv-python-headless>=4.10,<5\nbeautifulsoup4>=4.12,<5\npywin32>=308; sys_platform == "win32"\n', encoding="utf-8")
            (folder / "start_test_studio.bat").write_text(
                '@echo off\nsetlocal\ncd /d "%~dp0"\n".venv\\Scripts\\python.exe" -m allotment serve\n', encoding="utf-8")
            overrides = store.local / "plugins"
            if overrides.is_dir():
                shutil.copytree(overrides, folder / ".local" / "plugins")
            (folder / "setup_worker.bat").write_text(
                '@echo off\nsetlocal\ncd /d "%~dp0"\npython -m venv .venv\nif errorlevel 1 exit /b %errorlevel%\n'
                '".venv\\Scripts\\python.exe" -m pip install -r requirements.txt\nif errorlevel 1 exit /b %errorlevel%\n'
                '".venv\\Scripts\\python.exe" bootstrap_worker.py\n', encoding="utf-8")
            (folder / "start_browser.bat").write_text(
                '@echo off\nsetlocal\ncd /d "%~dp0"\n".venv\\Scripts\\python.exe" -m allotment browser\n', encoding="utf-8")
            receipt_path = f"garden\\{namespace}\\{user}\\.allotment\\queue\\{job_id}.yaml"
            (folder / "run_publish.bat").write_text(
                '@echo off\nsetlocal\ncd /d "%~dp0"\n'
                f'".venv\\Scripts\\python.exe" -m allotment worker --namespace {namespace} --user {user} --job {job_id}\n'
                'set "worker_result=%errorlevel%"\n'
                f'".venv\\Scripts\\python.exe" -m allotment receipts-export {namespace} {user} {job_id} receipts.yaml\n'
                'echo Return receipts.yaml to the original Studio Queue.\nexit /b %worker_result%\n', encoding="utf-8")
            (folder / "README.txt").write_text(
                "PORTABLE ALLOTMENT JOB\n\nExtract this entire ZIP to a writable folder on the worker computer.\n"
                "1. Run setup_worker.bat (requires Windows, Python 3.11+, Git, Chrome, Tesseract OCR and internet).\n"
                "2. Run start_browser.bat; sign into the selected platforms manually; leave Chrome open on the primary screen.\n"
                "3. Run start_test_studio.bat. In Platform tests, run and approve each selected destination locally.\n"
                "4. Social actions leave drafts in separate tabs; review and click Post or Send yourself. Leave the keyboard and mouse idle. F8 stops roboclick. Run run_publish.bat. Return receipts.yaml through Studio > Queue > Import receipts.\n\n"
                "The originating queue marks these actions as handed off and will not run them.\n"
                "Run only ONE copy of this bundle. Do not run copies on multiple computers.\n"
                "Repeated runs of this same extracted folder skip completed/uncertain actions.\n"
                "Cookies and passwords are never included. Git authentication uses this computer's credential manager.\n"
                "Test evidence and approvals are not portable. Each worker computer needs its own checked draft test and approval for preparation. Post and Send are always manual.\n"
                "Static output appears in allotment_static/<namespace>/<user>/. Upload that folder's contents.\n",
                encoding="utf-8")
            temporary_zip = destination.with_suffix(".tmp")
            with zipfile.ZipFile(temporary_zip, "w", zipfile.ZIP_DEFLATED) as archive:
                for file in folder.rglob("*"):
                    if file.is_file():
                        archive.write(file, file.relative_to(folder))
            temporary_zip.replace(destination)
        # Hand off only after the complete ZIP is on disk.
        job["export_token"] = token
        for action in pending:
            job["actions"][action]["status"] = "exported"
        write_yaml(path, job)
        return destination


def import_receipts(store, namespace, user, data):
    with store.garden_lock(namespace, user):
        path = store.job_path(namespace, user, data.get("id", ""))
        original = read_yaml(path)
        if not original or not original.get("export_token") or original["export_token"] != data.get("export_token"):
            raise ValueError("These receipts do not match an exported job.")
        if data.get("revision") != original["revision"] or data.get("namespace") != namespace or data.get("user") != user:
            raise ValueError("Receipt identity/revision does not match this garden.")
        updated = 0
        from urllib.parse import urlparse
        for action, receipt in data.get("actions", {}).items():
            if original["actions"].get(action, {}).get("status") != "exported":
                continue
            status = receipt.get("status")
            if status not in {"succeeded", "prepared", "failed", "uncertain"}:
                continue
            if status == "prepared" and (action in BUILTINS or receipt.get("sent") is not False):
                raise ValueError("A prepared receipt must describe an unsent social draft.")
            private = store.plugins().get(action, {}).get("target_required")
            if action not in BUILTINS and not private and status == "succeeded" and urlparse(receipt.get("url", "")).scheme not in {"http", "https"}:
                raise ValueError("A successful social receipt needs an HTTP(S) permalink.")
            if action=='allotment' and receipt.get('url') not in ('',None,f'/g/{namespace}/{user}/p/{original["post"]["id"]}/'):
                raise ValueError('The Allotment receipt does not match this post page.')
            if receipt.get("url") and urlparse(receipt["url"]).scheme not in {"http", "https"} and action not in {"static","allotment","summary"}:
                raise ValueError("Receipt links must be HTTP(S) URLs.")
            if action=='allotment' and status=='succeeded' and original.get('deferred_publication'):
                from .publication import publish_snapshot
                current=store.published(namespace,user,original['post']['id'])
                if not current or max(current.get('publication_queued_at',''),current.get('updated_at',''))<=original['created_at']:
                    publish_snapshot(store,namespace,user,original,receipt.get('published_at') or receipt.get('finished_at'))
            original["actions"][action] = receipt
            updated += 1
        if not updated:
            raise ValueError("No completed receipts were found. Finish running the exported job first.")
        save_job(store, namespace, user, original)
        return updated
