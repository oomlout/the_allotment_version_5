import json
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from allotment.roboclick.desktop import Desktop,DesktopStopped,handoff_progress


def test_five_second_handoff_allows_selection_but_never_sends_input(tmp_path,monkeypatch):
    clock=[0.0];observed=[]
    monkeypatch.setattr('allotment.roboclick.desktop.time',SimpleNamespace(monotonic=lambda:clock[0],time=lambda:1000+clock[0],sleep=lambda s:clock.__setitem__(0,clock[0]+s)))
    d=object.__new__(Desktop);d.evidence=tmp_path;d.log=MagicMock();d.guard=MagicMock();d.input=MagicMock()
    d.check_stop=lambda:observed.append(handoff_progress(tmp_path))
    d.window_handoff()
    assert clock[0]==pytest.approx(5) and observed[0]['remaining']==5
    assert observed[0]['phase']=='handoff' and handoff_progress(tmp_path)=={}
    d.guard.assert_not_called();d.input.assert_not_called()


def test_stop_during_handoff_clears_countdown_without_input(tmp_path):
    d=object.__new__(Desktop);d.evidence=tmp_path;d.log=MagicMock();d.input=MagicMock()
    d.check_stop=MagicMock(side_effect=DesktopStopped('F8'))
    with pytest.raises(DesktopStopped,match='F8'):d.window_handoff()
    assert not json.loads((tmp_path/'window-handoff.json').read_text())['waiting']
    d.input.assert_not_called()


def test_attach_selects_window_clicked_during_handoff(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules,'win32event',SimpleNamespace(CreateMutex=lambda *a:1,WaitForSingleObject=lambda *a:0,WAIT_OBJECT_0=0,WAIT_ABANDONED=128))
    monkeypatch.setitem(sys.modules,'win32api',SimpleNamespace(CloseHandle=lambda *a:None))
    d=object.__new__(Desktop);foreground=[1];order=[]
    d.settings={};d.wc=SimpleNamespace(GW_OWNER=4,SW_RESTORE=9)
    d.wg=SimpleNamespace(IsWindowVisible=lambda h:True,IsIconic=lambda h:False,GetClassName=lambda h:'Chrome_WidgetWin_1',
        GetWindow=lambda *a:0,GetWindowText=lambda h:f'Window {h} - Google Chrome',
        EnumWindows=lambda fn,data:[fn(h,data) for h in [1,2]],GetForegroundWindow=lambda:foreground[0],
        ShowWindow=lambda *a:None,SetForegroundWindow=lambda h:foreground.__setitem__(0,h),GetWindowRect=lambda h:(0,0,900,500))
    d.wp=SimpleNamespace(GetWindowThreadProcessId=lambda h:(0,h))
    d.psutil=SimpleNamespace(Process=lambda pid:SimpleNamespace(pid=pid,name=lambda:'chrome.exe',create_time=lambda:1),Error=RuntimeError)
    d.gui=SimpleNamespace(size=lambda:(1920,1080));d.last_input=lambda:10;d.guard=MagicMock();d.check_stop=MagicMock()
    def choose():order.append('handoff');foreground[0]=2
    d.window_handoff=choose
    def key(**kwargs):order.append('key');assert d.hwnd==2
    d.hotkey=key;d.wait=MagicMock();d.snap_top_left=MagicMock();d.navigate=MagicMock();d.screenshot=MagicMock();d.log=MagicMock()
    d.attach()
    assert d.hwnd==2 and order[0]=='handoff'


def test_stopped_or_expired_handoff_never_overlays_active_status(tmp_path):
    (tmp_path/'window-handoff.json').write_text(json.dumps({'waiting':True,'until':1}))
    assert handoff_progress(tmp_path)=={}


def test_late_preview_rechecks_screenshots_without_replaying_input():
    d=object.__new__(Desktop);d.guard=MagicMock();d.wait=MagicMock();d.input=MagicMock()
    d._find_image_once=MagicMock(side_effect=[ValueError('Reference image not found in the screenshot.'),{'fresh':True}])
    assert d.find_image('sample.png')=={'fresh':True}
    assert d._find_image_once.call_count==2
    d.input.assert_not_called()
    d._find_image_once=MagicMock(side_effect=ValueError('More than one reference image is visible. No coordinate was selected.'))
    with pytest.raises(ValueError,match='More than one'):d.find_image('sample.png')
    assert d._find_image_once.call_count==1


def test_file_picker_pause_uses_owned_dialog_guard(monkeypatch):
    clock=[0.0]
    monkeypatch.setattr('allotment.roboclick.desktop.time',SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda s:clock.__setitem__(0,clock[0]+s)))
    d=object.__new__(Desktop);d.guard=MagicMock()
    d.wait(seconds=1.25,dialog=True)
    assert clock[0]==pytest.approx(1.25)
    assert all(call.kwargs=={'dialog':True} for call in d.guard.call_args_list)


def test_worker_status_exposes_handoff_for_tests_launch_and_harvest(tmp_path,monkeypatch):
    import time
    from allotment.storage import Store,write_yaml,now
    from allotment import preparation,testlab,job_runs
    store=Store(tmp_path)
    def evidence(folder):
        folder.mkdir(parents=True,exist_ok=True)
        (folder/'window-handoff.json').write_text(json.dumps({'waiting':True,'until':time.time()+5}))
    for module,reader in [(preparation,preparation.preparation_progress),(testlab,testlab.progress_state)]:
        folder=tmp_path/module.__name__;evidence(folder)
        write_yaml(folder/'progress.yaml',{'status':'running','phase':'connecting'})
        monkeypatch.setattr(module,'owned_process',lambda p:True)
        assert reader(folder)['phase']=='handoff'
        write_yaml(folder/'progress.yaml',{'status':'prepared','phase':'review'})
        assert reader(folder)['phase']=='review'
    monkeypatch.setattr(job_runs,'owned_process',lambda p:True)
    folder=job_runs.folder_for(store,'namespace','user','run-test')
    write_yaml(folder/'run.yaml',{'kind':'launch','job_id':'job-test'})
    write_yaml(folder/'progress.yaml',{'status':'running','platform':'threads','updated_at':now()})
    evidence(store.local/'evidence/job-test/threads')
    assert job_runs.progress_for(folder)['phase']=='handoff'
    write_yaml(folder/'run.yaml',{'kind':'harvest'})
    evidence(folder/'evidence')
    assert job_runs.progress_for(folder)['phase']=='handoff'
