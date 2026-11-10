"""Keep private channel URLs and message IDs out of public Git receipts."""
from copy import deepcopy

from .storage import read_yaml, slug, write_yaml


PRIVATE_FIELDS = ("url", "url_type", "message_id")


def private_path(store, namespace, user, job_id, action):
    return store.local / "receipts" / slug(namespace) / slug(user) / slug(job_id) / f"{slug(action)}.yaml"


def save_job(store, namespace, user, job):
    public = deepcopy(job)
    plugins = store.plugins()
    for action, receipt in job["actions"].items():
        public['actions'][action].pop('review',None)
        if receipt.get("private_link") or plugins.get(action, {}).get("target_required"):
            public["actions"][action]["private_link"] = True
            receipt["private_link"] = True
            details = {key: receipt[key] for key in PRIVATE_FIELDS if receipt.get(key)}
            if details:
                write_yaml(private_path(store, namespace, user, job["id"], action), details)
            for key in PRIVATE_FIELDS:
                public["actions"][action].pop(key, None)
    write_yaml(store.job_path(namespace, user, job["id"]), public)


def with_private_receipts(store, namespace, user, job):
    for action, receipt in job["actions"].items():
        if receipt.get("private_link"):
            receipt.update(read_yaml(private_path(store, namespace, user, job["id"], action)))
    return job
