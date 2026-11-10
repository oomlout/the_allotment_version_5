"""Explicit, repeatable account presets; applying one never approves a destination."""
from pathlib import Path
import shutil
import uuid

from .preparation_approval import approval_status
from .storage import read_yaml, slug, write_yaml


def apply_preset(store, preset_id="oomlout", namespace=None, user=None):
    preset = read_yaml(Path(__file__).parent / "presets" / f"{slug(preset_id)}.yaml")
    if not preset:
        raise ValueError("Account preset not found.")
    namespace = namespace or preset["namespace"]
    user = user or preset["user"]
    recipes = store.plugins()
    missing = set(preset["destinations"]) - set(recipes)
    if missing:
        raise ValueError("Preset recipes are missing: " + ", ".join(sorted(missing)))
    with store.garden_lock(namespace, user):
        settings = store.settings(namespace, user)
        path = store.settings_path(namespace, user)
        if path.exists():
            backup = store.local / "backups" / f"{namespace}-{user}-{uuid.uuid4().hex}.yaml"
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
        accounts = settings.setdefault("destinations", {})
        profile = store.profile(namespace, user)
        for platform, supplied in preset["destinations"].items():
            previous = accounts.get(platform, {})
            account = {"enabled": False, "reviewed": False, "expected_account": "", **previous, **supplied,
                       "publish_profile_link": supplied.get("publish_profile_link",previous.get("publish_profile_link",True))}
            # Keep a manually configured signed-in identity (which may differ from a public handle).
            if previous.get("expected_account"):
                account["expected_account"] = previous["expected_account"]
            # Exact visible channel titles are confirmed on the worker, not inferred from public IDs.
            if previous.get("target_name") and not supplied.get("target_name"):
                account["target_name"] = previous["target_name"]
            valid = approval_status(store, namespace, user, recipes[platform], account)["valid"]
            account.update(enabled=valid and previous.get("enabled", False), reviewed=valid)
            accounts[platform] = account
            if account["publish_profile_link"]:
                profile.setdefault("social", {})[platform] = supplied["profile_url"]
                profile.setdefault("social_names", {})[platform] = recipes[platform]["name"]
        write_yaml(path, settings)
        write_yaml(store.repo(namespace, user) / "profile.yaml", profile)
    return list(preset["destinations"])
