"""Private test runs, independent processes, cancellation, and explicit approval."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import psutil

from .approval import approval_status, run_folder, signature
from .browser import StopRequested, browser_context, check_payload, run_recipe
from .storage import IMAGES, MEDIA, Store, now, read_yaml, slug, write_yaml
from .chrome import browser_mode
from .test_sample import SAMPLE_IMAGE, SAMPLE_TEXT


ACTIVE = {"queued", "running", "submitting", "stopping"}
TERMINAL = {"prepared", "succeeded", "failed", "stopped", "uncertain"}
SWITCH_SECONDS = 8
START_SECONDS = 5


def owned_process(folder):
    """Only accept the exact child created for this run, including its creation time."""
    meta = read_yaml(folder / "process.yaml")
    run = read_yaml(folder / "run.yaml")
    try:
        process = psutil.Process(meta["pid"])
        command = process.cmdline()
        expected = ["-m", "allotment", "--root", meta["root"], "platform-test",
                    run["namespace"], run["user"], run["id"]]
        if abs(process.create_time() - meta["created"]) > 0.1 or command[1:] != expected:
            return None
        return process if process.is_running() and process.status() != psutil.STATUS_ZOMBIE else None
    except (KeyError, psutil.Error):
        return None


def progress_state(folder):
    progress = read_yaml(folder / "progress.yaml", {"status": "queued", "events": []})
    termination = read_yaml(folder / "termination.yaml")
    if termination:
        return {**progress, **termination}
    if (folder / "stop.flag").exists():
        if progress["status"] in {"stopped", "uncertain"}:
            return progress
        # The stop supervisor writes termination.yaml only after process-tree cleanup.
        return {**progress, "status": "stopping", "verified": False}
    if progress["status"] in ACTIVE and not owned_process(folder):
        return {**progress, "status": "uncertain" if progress.get("submission_started") else "failed",
                "verified": False, "error": "The test process stopped unexpectedly. Check the destination before starting another test."}
    if progress['status'] in ACTIVE:
        from .roboclick.desktop import handoff_progress
        progress={**progress,**handoff_progress(folder)}
    return progress


def test_runs(store, namespace, user, test_id=None):
    parent = store.local / "platform-tests" / slug(namespace) / slug(user)
    results = []
    accounts = store.settings(namespace, user).get("destinations", {})
    plugins = store.plugins()
    checks = {}
    paths = [parent / slug(test_id) / 'run.yaml'] if test_id else parent.glob('*/run.yaml')
    for path in paths:
        if not path.is_file():continue
        run = read_yaml(path)
        progress = progress_state(path.parent)
        recipe = plugins.get(run["platform"])
        account = accounts.get(run["platform"], {})
        if run['platform'] not in checks:
            checks[run['platform']] = (signature(store, recipe, account), approval_status(store, namespace, user, recipe, account)) if recipe else (None, {'valid':False})
        stamp, approved = checks[run['platform']]
        current = bool(recipe and run['signature'] == stamp)
        results.append({"id": run["id"], "platform": run["platform"], "name": run["recipe"]["name"],
                        "guided": bool(run.get("guided")), "prepare":bool(run.get("prepare")), "browser_mode": run.get("browser_mode", "persistent"),
                        "expected_account": run["account"]["expected_account"],
                        "site_url": run["account"].get("base_url") or run["recipe"]["base_url"],
                        "site_host": urlparse(run["account"].get("base_url") or run["recipe"]["base_url"]).netloc,
                        "created_at": run["created_at"], "text": run["post"]["text"],
                        "target": run["account"].get("target_name") or run["account"].get("profile_url") or run["account"]["expected_account"],
                        "media_types": sorted({a["type"] for a in run["post"]["attachments"]}),
                        **progress, "active": progress["status"] in ACTIVE,
                        "can_approve": progress["status"] == "prepared" and progress.get("draft_checked") is True and progress.get("sent") is False and current and not (path.parent / "stop.flag").exists(),
                        "approved": approved["valid"] and run["id"] in approved.get("test_ids", [approved.get("test_id")]),
                        "current": current, "screenshots": sorted(p.name for p in path.parent.glob("*.png"))})
    return sorted(results, key=lambda r: r["created_at"], reverse=True)


class TestManager:
    def __init__(self, store):
        self.store = store
        self.guard = threading.Lock()
        self.children = {}
        for flag in (store.local / "platform-tests").glob("*/*/*/stop.flag"):
            if not (flag.parent / "termination.yaml").exists():
                threading.Thread(target=self._stop_process, args=(flag.parent,), daemon=True).start()

    def start(self, namespace, user, platform, *, guided=False, prepare=True):
        with self.guard:
            self.store.profile(namespace, user)
            recipe = self.store.plugins().get(platform)
            if not recipe:
                raise ValueError("Unknown platform.")
            for path in (self.store.local / "platform-tests").glob("*/*/*/run.yaml"):
                if progress_state(path.parent)["status"] in ACTIVE:
                    raise ValueError("Another browser test is active. Stop it or wait for it to finish first.")
            if (self.store.local / "browser.lock").exists():
                raise ValueError("Browser automation is busy. Finish the active worker/dedicated sign-in session, or stop the active test. Your usual Chrome can stay open.")
            account = self.store.settings(namespace, user).get("destinations", {}).get(platform, {})
            test_id = f"test-{uuid.uuid4().hex}"
            marker = f"[Allotment test {test_id[-8:]}]"
            folder = run_folder(self.store, namespace, user, test_id)
            folder.mkdir(parents=True)
            try:
                target = folder / "media" / "test-image.png"
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(SAMPLE_IMAGE, target)
                attachments = [{"file": "media/test-image.png", "type": "image", "alt": "The Allotment test image"}]
                post = {"id": test_id, "path": ".", "text": f"{SAMPLE_TEXT}\n\n{marker}", "attachments": attachments}
                check_payload(recipe, account, post, folder, testing=True)
                run = {"id": test_id, "namespace": namespace, "user": user, "platform": platform,
                       "guided": guided, "prepare": True,
                       "created_at": now(), "recipe": recipe, "account": account, "post": post,
                       "browser_mode": browser_mode(read_yaml(self.store.local / "worker.yaml")),
                       "signature": signature(self.store, recipe, account)}
                write_yaml(folder / "run.yaml", run)
                write_yaml(folder / "progress.yaml", {"status": "queued", "phase": "switching" if guided else "connecting", "events": [], "verified": False, "submission_started": False})
                log = (folder / "process.log").open("w", encoding="utf-8")
                try:
                    process = subprocess.Popen([sys.executable, "-m", "allotment", "--root", str(self.store.root),
                                                "platform-test", namespace, user, test_id],
                                               cwd=Path(__file__).resolve().parent.parent, stdout=log, stderr=log,
                                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                finally:
                    log.close()
                write_yaml(folder / "process.yaml", {"pid": process.pid, "created": psutil.Process(process.pid).create_time(), "root": str(self.store.root)})
                self.children[test_id] = process
                threading.Thread(target=self._reap, args=(process, folder), daemon=True).start()
            except Exception:
                if not (folder / "process.yaml").exists():
                    shutil.rmtree(folder)
                raise
            return test_id

    def _reap(self, process, folder):
        process.wait()
        if not (folder / "stop.flag").exists():
            self._release_browser_lock(folder)

    def countdown_control(self, namespace, user, test_id, action):
        folder = run_folder(self.store, namespace, user, test_id)
        with self.guard:
            run = read_yaml(folder / "run.yaml")
            progress = progress_state(folder)
            if not run.get("guided") or progress["status"] not in {"queued", "running"} or progress.get("submission_started"):
                raise ValueError("This test has no active countdown. Check its current status.")
            phase = progress.get("phase")
            if action == "pause" and phase in {"switching", "countdown"}:
                (folder / "pause.flag").write_text(now(), encoding="utf-8")
            elif action == "resume" and phase == "paused":
                (folder / "continue.flag").write_text(now(), encoding="utf-8")
            else:
                raise ValueError("The countdown has already moved on. Check the current instructions.")

    def stop(self, namespace, user, test_id):
        folder = run_folder(self.store, namespace, user, test_id)
        if not (folder / "run.yaml").exists():
            raise ValueError("Test not found.")
        progress = progress_state(folder)
        if progress["status"] in TERMINAL:
            return
        (folder / "stop.flag").write_text(now(), encoding="utf-8")
        threading.Thread(target=self._stop_process, args=(folder,), daemon=True).start()

    def _stop_process(self, folder):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if not owned_process(folder):
                self._release_browser_lock(folder)
                self._finish_stop(folder)
                return
            time.sleep(0.05)
        process = owned_process(folder)
        if process:
            # Stop only this test process tree; always protect personal Chrome.
            try:
                descendants = process.children(recursive=True)
                # Chrome's default-profile process can outlive its launcher. Even if it
                # began as this test's child, it belongs to the user, not to the test.
                protected = set()
                for child in descendants:
                    try:
                        if child.name().lower() in {"chrome.exe", "chrome", "google-chrome", "chrome_crashpad_handler.exe", "crashpad_handler"}:
                            protected.add(child.pid)
                            protected.update(p.pid for p in child.children(recursive=True))
                    except psutil.Error:
                        pass
                descendants = [child for child in descendants if child.pid not in protected]
                process.terminate()
                for child in reversed(descendants):
                    try:
                        child.terminate()
                    except psutil.Error:
                        pass
                _, alive = psutil.wait_procs([process, *descendants], timeout=1)
                for child in alive:
                    try:
                        child.kill()
                    except psutil.Error:
                        pass
            except psutil.Error:
                pass
        self._release_browser_lock(folder)
        self._finish_stop(folder)

    def _finish_stop(self, folder):
        progress = read_yaml(folder / "progress.yaml")
        uncertain = progress.get("submission_started", False)
        write_yaml(folder / "termination.yaml", {"status": "uncertain" if uncertain else "stopped", "verified": False,
                   "error": "Stopped after submission may have begun. Check the conversation; this test will not retry." if uncertain else "Stopped before submission.",
                   "finished_at": now()})

    def _release_browser_lock(self, folder):
        browser_lock = self.store.local / "browser.lock"
        owner = browser_lock / "owner.txt"
        meta = read_yaml(folder / "process.yaml")
        if owner.exists() and meta.get("pid") and f"pid={meta['pid']}\n" in owner.read_text(encoding="utf-8"):
            if not owned_process(folder):
                shutil.rmtree(browser_lock, ignore_errors=True)


def run_platform_test(store, namespace, user, test_id):
    folder = run_folder(store, namespace, user, test_id)
    run = read_yaml(folder / "run.yaml")
    if not run or run.get("namespace") != namespace or run.get("user") != user:
        raise ValueError("Test run not found.")
    progress = read_yaml(folder / "progress.yaml")
    if progress.get("status") != "queued":
        raise ValueError("Tests are single-use. Start a fresh test instead of rerunning a submitted one.")

    def persist():
        progress["updated_at"] = now()
        write_yaml(folder / "progress.yaml", progress)

    def check_stop():
        if (folder / "stop.flag").exists():
            raise StopRequested("Stop requested")

    def on_step(index, step, phase):
        check_stop()
        progress.pop("connection_message", None)
        progress["phase"] = "running"
        progress["operation"] = step.get("label") or {"navigate": "Opening the site", "click": "Finding the next control",
            "fill": "Entering the sample text", "upload": "Attaching the sample image", "check_account": "Checking the signed-in account",
            "submit": "Publishing the sample", "capture_url": "Checking the published post", "capture_message": "Checking the sent message"}.get(step["command"], "Waiting for the site")
        progress["step"] = index + 1
        progress["total_steps"] = len(run["recipe"]["steps"])
        progress["events"].append({"at": now(), "step": index + 1, "action": step["command"], "phase": phase})
        persist()

    def before_submit():
        raise ValueError("Automatic sending is disabled. Click Post or Send manually in Chrome.")

    try:
        check_stop()
        current = store.plugins().get(run["platform"])
        account = store.settings(namespace, user).get("destinations", {}).get(run["platform"], {})
        if not current or signature(store, current, account) != run["signature"]:
            raise ValueError("Settings changed. Start a new preparation test.")
        check_payload(run["recipe"], run["account"], run["post"], folder, testing=True)
        progress.update(status="running", phase="connecting", started_at=now())
        check_stop()
        progress.update(phase="connecting", connection_started_at=now(), connection_message="Opening a normal Chrome tab")
        persist()
        with browser_context(store,folder,check_stop) as desktop:
            if run.get('guided'):
                from .launch import countdown_in_tab,starting_url
                def update_countdown(**values):
                    progress.update(values);persist()
                countdown_in_tab(desktop,folder,starting_url(run['recipe'],run['account']),update_countdown,SWITCH_SECONDS)
            result = run_recipe(desktop,run["recipe"],run["account"],run["post"],folder,before_submit,
                                prepare=True,testing=True,check_stop=check_stop,on_step=on_step)
        if result.get("prepared") and result.get("draft_checked") is True and result.get("sent") is False:
            progress.update(status="prepared",phase="review",verified=False,sent=False,draft_checked=True,
                            operation="Ready for your Post click",result=result,finished_at=now())
            persist()
            return progress
        raise ValueError("A preparation test must return a checked, unsent draft.")
    except Exception as error:
        uncertain = progress.get("submission_started", False)
        stopped = isinstance(error, StopRequested) or (folder / "stop.flag").exists()
        progress.update(status="uncertain" if uncertain else "stopped" if stopped else "failed", verified=False,
                        failure_stage=progress.get("phase", "running"), phase="finished",
                        error="Stopped after Send may have begun; inspect the destination before testing again." if uncertain and stopped else str(error)[:3500],
                        finished_at=now())
        persist()
    return progress
