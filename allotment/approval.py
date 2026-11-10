"""Local, revision-bound evidence for explicitly approved publishing destinations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform

from .storage import now, read_yaml, slug, write_yaml
from .test_sample import sample_signature


ACCOUNT_FIELDS = ("expected_account", "account_id", "profile_url", "base_url", "identity_locator", "target_url", "target_name", "target_locator")


def checked_draft(progress):
    return (progress.get("status") == "prepared" and progress.get("draft_checked") is True
            and progress.get("sent") is False and progress.get("verified") is False
            and not progress.get("submission_started"))


def signature(store, recipe, account):
    worker = read_yaml(store.local / "worker.yaml")
    package = Path(__file__).parent
    engine_files = [package / name for name in ("browser.py", "chrome.py", "testlab.py", "worker.py", "approval.py", "launch.py")]
    engine_files += sorted(p for p in (package / "roboclick").rglob("*") if p.is_file() and p.suffix in {".py", ".png", ".yaml"})
    engine_bytes = b"".join(str(p.relative_to(package)).encode()+p.read_bytes() for p in engine_files)
    data = {"recipe": recipe, "account": {k: account.get(k) or "" for k in ACCOUNT_FIELDS},
            "worker": worker, "computer": platform.node(), "checkout": str(store.root),
            "engine": hashlib.sha256(engine_bytes).hexdigest(),
            "test_sample": sample_signature()}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def run_folder(store, namespace, user, test_id):
    return store.local / "platform-tests" / slug(namespace) / slug(user) / slug(test_id)


def approval_status(store, namespace, user, recipe, account):
    from .browser import available
    if not available(recipe):
        return {"valid": False, "reason": "Not migrated to roboclick yet.", "capabilities": []}
    approved = account.get("approval", {})
    if not approved.get("test_id"):
        return {"valid": False, "reason": "Prepare a test draft, then approve preparation in Tests. Sending stays manual.", "capabilities": []}
    folder = run_folder(store, namespace, user, approved["test_id"])
    run = read_yaml(folder / "run.yaml")
    progress = read_yaml(folder / "progress.yaml")
    current = signature(store, recipe, account)
    valid = (run.get("platform") == recipe["id"] and run.get("signature") == current
             and approved.get("signature") == current and checked_draft(progress)
             and not (folder / "stop.flag").exists())
    test_ids = list(dict.fromkeys([*approved.get("test_ids", []), approved["test_id"]]))
    capabilities = {"text"}
    verified_ids = []
    if valid:
        for test_id in test_ids:
            evidence = run_folder(store, namespace, user, test_id)
            test = read_yaml(evidence / "run.yaml")
            result = read_yaml(evidence / "progress.yaml")
            if (test.get("platform") == recipe["id"] and test.get("signature") == current
                    and checked_draft(result)
                    and not (evidence / "stop.flag").exists()):
                verified_ids.append(test_id)
                capabilities.update(a["type"] for a in test["post"]["attachments"])
    return {"valid": bool(valid), "reason": "Draft preparation approved; submission is manual" if valid else "Recipe, destination or browser changed. Prepare and approve again.",
            "capabilities": sorted(capabilities) if valid else [], "test_id": approved.get("test_id"), "test_ids": verified_ids}


def require_approval(store, namespace, user, recipe, account, post=None):
    status = approval_status(store, namespace, user, recipe, account)
    if not account.get("enabled") or not status["valid"]:
        raise ValueError(f"{recipe['name']}: {status['reason']}")
    types = {a["type"] for a in (post or {}).get("attachments", [])}
    if not types.issubset(set(status["capabilities"])):
        raise ValueError(f"{recipe['name']}: test and approve a post with this media type first.")
    return status


def approve_run(store, namespace, user, test_id):
    folder = run_folder(store, namespace, user, test_id)
    run = read_yaml(folder / "run.yaml")
    progress = read_yaml(folder / "progress.yaml")
    recipe = store.plugins().get(run.get("platform"))
    from .browser import validate_recipe
    if not recipe or not checked_draft(progress) or (folder / "stop.flag").exists():
        raise ValueError("Only a completed, checked and unsent draft can approve preparation.")
    validate_recipe(recipe)
    settings = store.settings(namespace, user)
    account = settings.get("destinations", {}).get(recipe["id"], {})
    current = signature(store, recipe, account)
    if run.get("signature") != current:
        raise ValueError("The recipe, account, target or browser settings changed after this test. Run a new test.")
    previous = approval_status(store, namespace, user, recipe, account)
    test_ids = list(dict.fromkeys([*(previous.get("test_ids", []) if previous["valid"] else []), test_id]))
    account.update(enabled=True, reviewed=True,
                   approval={"test_id": test_id, "test_ids": test_ids, "signature": current, "approved_at": now()})
    settings.setdefault("destinations", {})[recipe["id"]] = account
    write_yaml(store.settings_path(namespace, user), settings)
    return account
