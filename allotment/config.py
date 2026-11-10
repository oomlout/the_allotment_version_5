"""Persistent, computer-local Studio settings."""
from .storage import read_yaml, write_yaml


DEFAULT_STUDIO_PORT = 8879


def studio_settings(store):
    settings = {"port": DEFAULT_STUDIO_PORT, **read_yaml(store.local / "studio.yaml")}
    settings["port"] = valid_port(settings["port"])
    return settings


def valid_port(value):
    try:
        port = int(value)
    except (ValueError, TypeError):
        raise ValueError("Studio port must be a number from 1 to 65535.")
    if not 1 <= port <= 65535:
        raise ValueError("Studio port must be a number from 1 to 65535.")
    return port


def save_studio_settings(store, port):
    settings = {**read_yaml(store.local / "studio.yaml"), "port": valid_port(port)}
    write_yaml(store.local / "studio.yaml", settings)
    return settings
