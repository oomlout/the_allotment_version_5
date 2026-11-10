from pathlib import Path

import pytest

from allotment.storage import read_yaml


def test_progress_read_survives_transient_windows_replace_lock(tmp_path, monkeypatch):
    path = tmp_path / "progress.yaml"
    path.write_text("status: running\n", encoding="utf-8")
    original = Path.read_text
    calls = 0
    def briefly_locked(self, *args, **kwargs):
        nonlocal calls
        if self == path:
            calls += 1
            if calls < 3:
                raise PermissionError("Windows replace lock")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", briefly_locked)
    assert read_yaml(path) == {"status": "running"}


def test_persistent_file_access_error_is_reported(tmp_path, monkeypatch):
    def locked(*args, **kwargs):
        raise PermissionError("Persistent access failure")
    monkeypatch.setattr(Path, "read_text", locked)
    with pytest.raises(PermissionError, match="Persistent access"):
        read_yaml(tmp_path / "progress.yaml")


@pytest.mark.parametrize('fallback',[False,True])
def test_fast_yaml_loader_preserves_safe_tag_rejection(tmp_path,monkeypatch,fallback):
    import yaml
    if fallback:monkeypatch.delattr(yaml,'CSafeLoader',raising=False)
    path=tmp_path/'untrusted.yaml'
    path.write_text('!!python/tuple [1, 2]',encoding='utf-8')
    with pytest.raises(yaml.constructor.ConstructorError):read_yaml(path)
