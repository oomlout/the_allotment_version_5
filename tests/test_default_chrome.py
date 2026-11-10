from types import SimpleNamespace
from unittest.mock import MagicMock
from allotment.app import create_app
from allotment.chrome import browser_mode, launch_chrome
from allotment.storage import Store, write_yaml, read_yaml
from allotment.testlab import TestManager as Manager


def test_normal_chrome_launch_has_no_profile_window_or_debug_flags(monkeypatch):
    call=MagicMock()
    monkeypatch.setattr("allotment.chrome.chrome_executable",lambda:"chrome.exe")
    monkeypatch.setattr("allotment.chrome.subprocess.Popen",call)
    launch_chrome("https://bsky.app")
    assert call.call_args.args[0] == ["chrome.exe","https://bsky.app"]
    assert browser_mode({"cdp_url":"old","mode":"persistent"}) == "roboclick"


def test_stop_never_terminates_default_chrome_even_if_it_was_a_child(tmp_path, monkeypatch):
    store = Store(tmp_path)
    folder = store.local / "platform-tests" / "test"
    write_yaml(folder / "run.yaml", {"browser_mode": "chrome_default"})
    write_yaml(folder / "progress.yaml", {"submission_started": False})
    renderer = MagicMock(pid=3)
    renderer.name.return_value = "chrome.exe"
    renderer.children.return_value = []
    chrome = MagicMock(pid=2)
    chrome.name.return_value = "chrome.exe"
    chrome.children.return_value = [renderer]
    driver = MagicMock(pid=4)
    driver.name.return_value = "node.exe"
    driver.children.return_value = []
    process = MagicMock(pid=1)
    process.children.return_value = [chrome, renderer, driver]
    monkeypatch.setattr("allotment.testlab.owned_process", lambda _: process)
    clock = MagicMock(side_effect=[0, 3])
    monkeypatch.setattr("allotment.testlab.time", SimpleNamespace(monotonic=clock, sleep=lambda _: None))
    monkeypatch.setattr("allotment.testlab.psutil.wait_procs", lambda processes, timeout: (processes, []))
    Manager(store)._stop_process(folder)
    process.terminate.assert_called_once()
    driver.terminate.assert_called_once()
    chrome.terminate.assert_not_called()
    renderer.terminate.assert_not_called()
    chrome.kill.assert_not_called()
    renderer.kill.assert_not_called()
    assert read_yaml(folder / "termination.yaml")["status"] == "stopped"



def test_worker_settings_persist_without_starting_chrome(tmp_path):
    store=Store(tmp_path);store.bootstrap()
    client=create_app(tmp_path,testing=True,garden=('the_allotment','oomlout_main')).test_client()
    path="/g/the_allotment/oomlout_main/worker/"
    page=client.get(path).text
    assert "Roboclick" in page and "CDP URL" not in page
    with client.session_transaction() as session: csrf=session["csrf"]
    assert client.post(path,data={"csrf":csrf,"window_title":"My Chrome"}).status_code==302
    assert read_yaml(store.local/"worker.yaml")["window_title"] == "My Chrome"
    assert read_yaml(store.local/"worker.yaml")["mode"] == "roboclick"
