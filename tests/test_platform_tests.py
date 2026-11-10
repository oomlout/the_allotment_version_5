"""Test lifecycle, immutable payload, explicit approval, cancellation and migration."""
from contextlib import contextmanager
from pathlib import Path
import shutil
import time
from unittest.mock import MagicMock

import pytest
from allotment.app import create_app
from allotment.approval import approve_run,approval_status,run_folder,signature
from allotment.storage import Store,write_yaml,read_yaml,now
from allotment.testlab import TestManager as Manager,run_platform_test,test_runs as runs_for
from allotment.test_sample import SAMPLE_TEXT,SAMPLE_IMAGE
from allotment.browser import DesktopStopped

NS,USER="the_allotment","oomlout_main"

@pytest.fixture
def lab(tmp_path):
    store=Store(tmp_path);store.bootstrap()
    settings={"destinations":{"bluesky":{"expected_account":"oomlout.bsky.social","profile_url":"https://bsky.app/profile/oomlout.bsky.social"}}}
    write_yaml(store.settings_path(NS,USER),settings)
    return store


def seed(store,run_id="test-unit",prepare=False):
    recipe=store.plugins()["bluesky"];account=store.settings(NS,USER)["destinations"]["bluesky"]
    folder=run_folder(store,NS,USER,run_id)
    folder.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(SAMPLE_IMAGE,folder/"test-image.png")
    run={"id":run_id,"namespace":NS,"user":USER,"platform":"bluesky","created_at":now(),"recipe":recipe,"account":account,
         "signature":signature(store,recipe,account),"guided":False,"prepare":prepare,
         "post":{"id":run_id,"path":".","text":SAMPLE_TEXT+"\n\n[Allotment test unit1234]","attachments":[{"file":"test-image.png","type":"image"}]}}
    write_yaml(folder/"run.yaml",run)
    write_yaml(folder/"progress.yaml",{"status":"queued","events":[],"submission_started":False})
    return folder,run


def simulated_run(store,monkeypatch,prepare=False,error=None,after=False):
    folder,run=seed(store,prepare=prepare)
    @contextmanager
    def context(*args): yield object()
    def execute(desktop,recipe,account,post,repo,before_submit,**kwargs):
        if error and not after: raise error
        if after: before_submit()
        if error: raise error
        return {"prepared":True,"draft_checked":True,"sent":False,"verified":False}
    monkeypatch.setattr("allotment.testlab.browser_context",context)
    monkeypatch.setattr("allotment.testlab.run_recipe",execute)
    result=run_platform_test(store,NS,USER,run["id"])
    return folder,run,result


def test_success_requires_separate_approval_and_is_bound_to_engine_and_checkout(lab,monkeypatch,tmp_path):
    folder,run,result=simulated_run(lab,monkeypatch)
    assert result["status"]=="prepared" and result["draft_checked"] and not result["verified"]
    account=lab.settings(NS,USER)["destinations"]["bluesky"]
    assert not approval_status(lab,NS,USER,run["recipe"],account)["valid"]
    approve_run(lab,NS,USER,run["id"])
    account=lab.settings(NS,USER)["destinations"]["bluesky"]
    assert approval_status(lab,NS,USER,run["recipe"],account)["valid"]
    moved=Store(tmp_path/"moved")
    assert signature(moved,run["recipe"],account)!=run["signature"]
    write_yaml(lab.local/"worker.yaml",{"window_title":"Different profile"})
    assert not approval_status(lab,NS,USER,run["recipe"],account)["valid"]


def test_prepare_approval_enables_drafts_only(lab,monkeypatch):
    folder,run,result=simulated_run(lab,monkeypatch,prepare=True)
    assert result["status"]=="prepared" and not result["verified"]
    approve_run(lab,NS,USER,run["id"])
    assert not result["sent"] and not result["submission_started"]


@pytest.mark.parametrize("after,status",[(False,"failed"),(True,"failed")])
def test_failure_classification_prevents_blind_retry(lab,monkeypatch,after,status):
    folder,run,result=simulated_run(lab,monkeypatch,error=ValueError("missing control"),after=after)
    assert result["status"]==status and not result["verified"]
    with pytest.raises(ValueError): approve_run(lab,NS,USER,run["id"])
    with pytest.raises(ValueError,match="single-use"): run_platform_test(lab,NS,USER,run["id"])


def test_f8_stop_is_reported_as_stopped(lab,monkeypatch):
    _,_,result=simulated_run(lab,monkeypatch,error=DesktopStopped("F8"))
    assert result["status"]=="stopped"


def test_changed_account_before_send_is_rejected(lab,monkeypatch):
    folder,run=seed(lab)
    account=lab.settings(NS,USER);account["destinations"]["bluesky"]["expected_account"]="wrong.bsky.social"
    write_yaml(lab.settings_path(NS,USER),account)
    @contextmanager
    def context(*args): yield object()
    monkeypatch.setattr("allotment.testlab.browser_context",context)
    monkeypatch.setattr("allotment.testlab.run_recipe",lambda *args,**kwargs:args[6]())
    result=run_platform_test(lab,NS,USER,run["id"])
    assert result["status"]=="failed" and not result["submission_started"]


def test_stop_cancels_real_child_countdown_without_opening_browser(lab):
    manager=Manager(lab)
    run_id=manager.start(NS,USER,"bluesky",guided=True)
    try:
        manager.stop(NS,USER,run_id)
        manager.children[run_id].wait(timeout=8)
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            run=runs_for(lab,NS,USER)[0]
            if run["status"] in {"stopped","uncertain"}: break
            time.sleep(.1)
        assert run["status"]=="stopped" and not run["can_approve"]
    finally:
        process=manager.children[run_id]
        if process.poll() is None: process.kill()


def test_fixed_sample_and_csrf_routes_and_legacy_platform_gate(lab,monkeypatch):
    app=create_app(lab.root,testing=True,garden=('the_allotment','oomlout_main'));client=app.test_client()
    page=client.get(f"/g/{NS}/{USER}/destinations/").text
    assert '>Prepare</button>' in page and 'Publish test' not in page and 'remote-debugging' not in page
    assert 'name="text"' not in page and 'type="file"' not in page
    with client.session_transaction() as session: csrf=session["csrf"]
    route=f"/g/{NS}/{USER}/tests/start"
    assert client.post(route,data={"platform":"bluesky","intent":"send_test"}).status_code==400
    assert client.post(route,data={"csrf":csrf,"platform":"bluesky","intent":"send_test","text":"override"}).status_code==400
    assert client.post(route,data={"csrf":csrf,"platform":"twitter","intent":"send_test"}).status_code==400
    assert client.post(route,data={"csrf":csrf,"platform":"bluesky","intent":"send_test"}).status_code==400
    start=MagicMock(return_value="test-fixed")
    monkeypatch.setattr(app.extensions["test_manager"],"start",start)
    assert client.post(route,data={"csrf":csrf,"platform":"bluesky","intent":"prepare_test"}).status_code==302
    assert start.call_args.kwargs=={"guided":True,"prepare":True}
    response=client.post(route,data={"csrf":csrf,"platform":"bluesky","intent":"prepare_test"},headers={"Accept":"application/json"})
    assert response.status_code==200 and response.json=={"id":"test-fixed"}
    response=client.post(route,data={"csrf":csrf,"platform":"bluesky","intent":"prepare_test","text":"override"},headers={"Accept":"application/json"})
    assert response.status_code==400 and 'fixed' in response.json['error']


def test_old_runs_are_hidden_and_prepare_cannot_be_forged_as_approval(lab):
    for i in range(2):
        folder,run=seed(lab,"test-history-"+str(i))
        write_yaml(folder/"progress.yaml",{"status":"prepared","verified":False,"events":[]})
    client=create_app(lab.root,testing=True,garden=('the_allotment','oomlout_main')).test_client()
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(client.get(f"/g/{NS}/{USER}/destinations/").text,"html.parser")
    history=soup.select_one('details#previous-test-runs')
    assert history is None
    assert len(soup.select('#current-test-run article'))==0
    assert soup.select_one('#test-dashboard') is not None
    assert soup.select_one('dialog') is None
    with client.session_transaction() as session: csrf=session["csrf"]
    assert client.post(f"/g/{NS}/{USER}/tests/test-history-0/approve",data={"csrf":csrf,"confirm_approval":"yes"}).status_code==400
