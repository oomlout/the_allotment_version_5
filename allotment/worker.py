from __future__ import annotations

import json
from pathlib import Path
import traceback

from .browser import StopRequested, browser_context, check_payload, run_recipe
from .storage import BUILTINS, commit, git, now, read_yaml, slug, write_yaml
from .website import build_site
from .approval import require_approval
from .receipts import save_job


def execute_job(store, namespace, user, job_id, dry_run=False):
    repo = store.repo(namespace, user)
    path = store.job_path(namespace, user, job_id)
    job = read_yaml(path)
    if not job:
        raise ValueError("Job not found.")
    plugins = store.plugins()
    accounts = store.settings(namespace, user).get("destinations", {})
    if dry_run:
        for action, receipt in job["actions"].items():
            if receipt["status"] == "pending" and action not in BUILTINS:
                require_approval(store, namespace, user, plugins[action], accounts.get(action, {}), job["post"])
                check_payload(plugins[action], accounts.get(action, {}), job["post"], repo)
        return job
    # External posts first so the static website can include their verified links.
    order = [a for a in job["actions"] if a not in BUILTINS] + [a for a in ("static", "git") if a in job["actions"]]
    for action in order:
        receipt = job["actions"][action]
        if receipt["status"] in {"running", "submitting"}:
            receipt.update(status="failed" if action in BUILTINS else "uncertain",
                           error="Worker stopped during this action. Retry the build/Git action or check the social destination before resolving it.")
            save_job(store, namespace, user, job)
        if receipt["status"] != "pending":
            continue
        receipt.update(status="running", started_at=now(), attempts=receipt.get("attempts", 0) + 1, error="")
        save_job(store, namespace, user, job)
        stop_error = None
        try:
            if action == "static":
                current = store.published(namespace, user, job["post"]["id"])
                if current["revision"] != job["revision"]:
                    raise ValueError("This queued revision has been superseded. Queue a build for the current post.")
                build_site(store, namespace, user)
                receipt["url"] = f"/allotment_static/{namespace}/{user}/p/{job['post']['id']}/"
            elif action == "git":
                if "origin" not in git(repo, "remote").splitlines():
                    raise ValueError("No origin remote is configured. Add a remote in Setup, then retry this action.")
                commit(repo, f"Publish {job['post']['id']}")
                git(repo, "push", "-u", "origin", "main")
            else:
                recipe = plugins[action]
                account = accounts.get(action, {})
                require_approval(store, namespace, user, recipe, account, job["post"])
                check_payload(recipe, account, job["post"], repo)
                def before_submit():
                    raise ValueError("Automatic sending is disabled. Submit the prepared tab manually.")
                evidence = store.local / "evidence" / job_id / action
                with browser_context(store,evidence) as desktop:
                    result = run_recipe(desktop,recipe,account,job["post"],repo,before_submit)
                    if not result.get("prepared") or result.get("sent") is not False:
                        raise ValueError("The worker did not return an unsent draft.")
                    receipt.update(status="prepared", sent=False, verified=False, manual_submission_required=True)
            receipt.update(status="succeeded" if action in BUILTINS else "prepared", finished_at=now())
        except Exception as exc:
            ambiguous = receipt["status"] == "submitting"
            receipt.update(status="uncertain" if ambiguous else "failed", error=str(exc)[:2500], finished_at=now())
            if isinstance(exc,StopRequested):
                stop_error=exc
        save_job(store, namespace, user, job)
        if stop_error is not None:
            raise stop_error
        # Include the Git receipt in the remote history too. A push error never repeats social posts.
        if action == "git" and receipt["status"] == "succeeded":
            try:
                commit(repo, f"Record publishing receipt for {job_id}")
                git(repo, "push", "origin", "main")
            except Exception as exc:
                receipt["receipt_sync_error"] = str(exc)
                save_job(store, namespace, user, job)
    return job


def run_worker(store, namespace=None, user=None, job_id=None, dry_run=False, pull=False):
    results = []
    for profile in store.profiles():
        ns, username = profile["namespace"], profile["user"]
        if namespace and ns != namespace or user and username != user:
            continue
        with store.garden_lock(ns, username):
            repo = store.repo(ns, username)
            if pull and "origin" in git(repo, "remote").splitlines():
                if git(repo, "status", "--porcelain"):
                    raise ValueError("Commit/sync local public changes before pulling worker jobs.")
                git(repo, "pull", "--ff-only", "origin", "main")
            for job in reversed(store.jobs(ns, username)):
                if job_id and job["id"] != job_id:
                    continue
                if any(a["status"] in {"pending", "running", "submitting"} for a in job["actions"].values()):
                    result = execute_job(store, ns, username, job["id"], dry_run)
                    results.append(result)
                    print(f"{ns}/{username} {job['id']}: " + ", ".join(f"{k}={v['status']}" for k, v in result["actions"].items()))
            if pull and not dry_run and "origin" in git(repo, "remote").splitlines():
                commit(repo, "Sync worker publishing receipts")
                git(repo, "push", "origin", "main")
    return results


def retry_action(store, namespace, user, job_id, action):
    with store.garden_lock(namespace, user):
        path = store.job_path(namespace, user, job_id)
        job = read_yaml(path)
        receipt = job.get("actions", {}).get(action, {})
        if receipt.get("status") != "failed":
            raise ValueError("Only an action that failed before submission can be retried. Resolve uncertain submissions first.")
        receipt.update(status="pending", error="")
        save_job(store, namespace, user, job)


def resolve_action(store, namespace, user, job_id, action, url):
    from urllib.parse import urlparse
    if urlparse(url).scheme not in {"http", "https"}:
        raise ValueError("Enter the published post's HTTP(S) URL.")
    with store.garden_lock(namespace, user):
        path = store.job_path(namespace, user, job_id)
        job = read_yaml(path)
        receipt = job.get("actions", {}).get(action, {})
        if receipt.get("status") not in {"uncertain", "prepared"}:
            raise ValueError("Only prepared or uncertain actions accept a manually published link.")
        receipt.update(status="succeeded", url=url, error="", resolved_at=now(), resolved_manually=True)
        save_job(store, namespace, user, job)
