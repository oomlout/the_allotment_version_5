from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import time
import threading
import zipfile

import pytest
from werkzeug.datastructures import FileStorage

from allotment.app import create_app
from allotment.storage import Store,read_yaml,write_yaml,now
from allotment.publication import run_worker,stoppable_contexts
from allotment.job_runs import execute_run,folder_for,JobRunManager,progress_for
from allotment.browser import StopRequested
from allotment.bundles import export_bundle,import_receipts
from test_publishing import enable
from test_studio_browser import studio

NS,USER='the_allotment','oomlout_main'


@pytest.fixture
def store(tmp_path):
    store=Store(tmp_path);store.bootstrap();return store


def queue(store,actions=('allotment',),text='A post'):
    post=store.save_draft(NS,USER,text,[FileStorage(stream=BytesIO(b'original bytes'),filename='image.png')])
    return store.publish(NS,USER,post['id'],actions,defer_public=True)


def test_queue_is_deferred_and_publishes_immutable_snapshot_preserving_later_edits(store):
    job=queue(store,('allotment','static'))
    assert not store.posts(NS,USER) and store.drafts(NS,USER)
    store.save_draft(NS,USER,'Later edits',post_id=job['post']['id'])
    result=run_worker(store,NS,USER,job['id'])[0]
    assert all(r['status']=='succeeded' for r in result['actions'].values())
    public=store.published(NS,USER,job['post']['id'])
    assert public['text']=='A post' and public['status']=='published'
    assert store.draft(NS,USER,job['post']['id'])['text']=='Later edits'
    assert (store.repo(NS,USER)/public['path']/public['attachments'][0]['file']).read_bytes()==b'original bytes'
    assert (store.output/NS/USER/'p'/public['id']/'index.html').exists()
    assert run_worker(store,NS,USER,job['id'])==[]


def test_allotment_is_optional_and_social_approvals_still_required(store,monkeypatch):
    post=store.save_draft(NS,USER,'Social draft only')
    with pytest.raises(ValueError):store.publish(NS,USER,post['id'],['bluesky'],defer_public=True)
    assert not store.jobs(NS,USER)
    enable(store)
    job=store.publish(NS,USER,post['id'],['bluesky'],defer_public=True)
    from allotment import worker
    @contextmanager
    def context(*args,**kwargs):yield object()
    monkeypatch.setattr(worker,'browser_context',context)
    monkeypatch.setattr(worker,'run_recipe',lambda *args,**kwargs:{'prepared':True,'sent':False})
    result=run_worker(store,NS,USER,job['id'])[0]
    assert result['actions']['bluesky']['status']=='prepared'
    assert not store.posts(NS,USER) and store.drafts(NS,USER)
    with pytest.raises(ValueError,match='already been'):store.publish(NS,USER,post['id'],['bluesky'],defer_public=True)


def test_deferred_bundle_contains_only_selected_snapshot_and_runs_elsewhere(store,tmp_path):
    job=queue(store);other=queue(store,text='Unrelated post')
    path=export_bundle(store,NS,USER,job['id']);target=tmp_path/'portable'
    with zipfile.ZipFile(path) as archive:
        assert not any(other['id'] in name for name in archive.namelist())
        archive.extractall(target)
    portable=Store(target);result=run_worker(portable,NS,USER,job['id'])[0]
    assert result['actions']['allotment']['status']=='succeeded'
    assert portable.posts(NS,USER)[0]['text']=='A post'
    assert not store.posts(NS,USER)
    assert import_receipts(store,NS,USER,result)==1
    assert store.posts(NS,USER)[0]['text']=='A post'
    assert store.posts(NS,USER)[0]['published_at']==portable.posts(NS,USER)[0]['published_at']


def test_post_page_tracks_receipts_and_manual_links(store):
    enable(store);job=queue(store,('bluesky',))
    job['actions']['bluesky'].update(status='prepared',sent=False)
    write_yaml(store.job_path(NS,USER,job['id']),job)
    client=create_app(store.root,testing=True,garden=('the_allotment','oomlout_main')).test_client();base=f'/g/{NS}/{USER}'
    response=client.get(base+f'/p/{job["post"]["id"]}/')
    assert response.status_code==200 and 'Draft ready.' in response.text and 'value="bluesky" disabled' not in response.text
    with client.session_transaction() as session:csrf=session['csrf']
    response=client.post(base+f'/records/{job["id"]}/resolve/bluesky',data={'csrf':csrf,'url':'https://bsky.app/profile/example/post/123','return_to':'post'},follow_redirects=True)
    assert 'https://bsky.app/profile/example/post/123' in response.text and 'resolved_manually' in store.job_path(NS,USER,job['id']).read_text()
    assert client.get(base+f'/publish/{job["post"]["id"]}/').status_code==404


def test_run_now_launches_real_separate_worker_for_one_job(store):
    job=queue(store);other=queue(store,text='Not selected')
    manager=JobRunManager(store);run_id=manager.start(NS,USER,job['id'])
    folder=folder_for(store,NS,USER,run_id)
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        progress=progress_for(folder)
        if progress.get('status') not in {'queued','countdown','paused','running'}:break
        time.sleep(.1)
    assert progress['status']=='complete',progress
    assert [p['id'] for p in store.posts(NS,USER)]==[job['post']['id']]
    assert read_yaml(store.job_path(NS,USER,other['id']))['actions']['allotment']['status']=='pending'


def test_stop_before_countdown_does_not_start_worker(store,monkeypatch):
    run_id='run-fixture';folder=folder_for(store,NS,USER,run_id)
    write_yaml(folder/'run.yaml',{'id':run_id,'job_id':'job-fixture','destinations':['bluesky']})
    write_yaml(folder/'progress.yaml',{'status':'queued'})
    (folder/'stop.flag').write_text('stop')
    monkeypatch.setattr('allotment.publication.run_worker',lambda *a,**k:pytest.fail('Stopped run reached worker'))
    assert execute_run(store,NS,USER,run_id)['status']=='stopped'


def test_countdown_pause_resume_and_stop_do_not_reach_browser(store,monkeypatch):
    run_id='run-controls';folder=folder_for(store,NS,USER,run_id)
    write_yaml(folder/'run.yaml',{'id':run_id,'job_id':'job-fixture','destinations':['bluesky']})
    write_yaml(folder/'progress.yaml',{'status':'queued','updated_at':now()})
    monkeypatch.setattr('allotment.publication.run_worker',lambda *a,**k:pytest.fail('Stopped run reached worker'))
    thread=threading.Thread(target=execute_run,args=(store,NS,USER,run_id,4));thread.start()
    manager=JobRunManager(store)
    def wait_for(status):
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            if read_yaml(folder/'progress.yaml').get('status')==status:return
            time.sleep(.03)
        pytest.fail(f'Worker did not reach {status}')
    try:
        wait_for('countdown');manager.control(NS,USER,run_id,'pause');wait_for('paused')
        manager.control(NS,USER,run_id,'resume');wait_for('countdown')
        manager.control(NS,USER,run_id,'stop');wait_for('stopped')
    finally:
        (folder/'stop.flag').write_text('stop');thread.join(timeout=3)


def test_extra_cancellation_uses_existing_context_and_restores_it(monkeypatch):
    from allotment import worker
    calls=[]
    @contextmanager
    def context(*args,check_stop=None,**kwargs):
        calls.append(check_stop);yield 'session'
    monkeypatch.setattr(worker,'browser_context',context)
    def check():return None
    with stoppable_contexts(check):
        with worker.browser_context(None) as session:assert session=='session'
    assert calls==[check] and worker.browser_context is context


def test_export_requires_publishing_current_version_to_allotment(store):
    post=store.save_draft(NS,USER,'Unpublished')
    with pytest.raises(ValueError,match='Select Publish'):store.publish(NS,USER,post['id'],['static'],defer_public=True)
    assert not store.jobs(NS,USER)


def test_single_post_browser_page_launches_automatic_exports(studio):
    from playwright.sync_api import expect
    store,origin,browser=studio;page=browser.new_page(viewport={'width':1280,'height':900})
    base=origin+f'/g/{NS}/{USER}'
    page.goto(base+'/compose/')
    page.get_by_label('Post text',exact=True).fill('Post page browser walkthrough')
    page.get_by_role('button',name='Save draft',exact=True).click()
    expect(page.get_by_role('heading',name='Post',exact=True)).to_be_visible()
    assert '/p/' in page.url
    permalink=page.url
    page.get_by_role('button',name='Publish locally',exact=True).click()
    assert len(store.posts(NS,USER))==1 and not store.jobs(NS,USER)
    assert page.locator('input[name=actions][value=static]').count()==0
    page.get_by_role('button',name='Launch',exact=True).click()
    expect(page.locator('#job-run-state')).to_have_text('Complete',timeout=15000)
    assert page.url==permalink and len(store.posts(NS,USER))==1
    assert (store.output/NS/USER/'index.html').exists()
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    evidence=Path(__file__).resolve().parents[1]/'.local/post-page-work';evidence.mkdir(parents=True,exist_ok=True)
    page.screenshot(path=str(evidence/'post-mobile.png'),full_page=True)
    page.set_viewport_size({'width':1440,'height':1000})
    page.screenshot(path=str(evidence/'post-desktop.png'),full_page=True)
    page.goto(base+'/compose/')
    page.get_by_label('Post text',exact=True).fill('Run immediately')
    page.get_by_role('button',name='Save & view post',exact=True).click()
    page.get_by_role('button',name='Publish locally',exact=True).click()
    assert page.locator('input[name=actions][value=static]').count()==0
    page.get_by_role('button',name='Launch',exact=True).click()
    expect(page.locator('#job-run-state')).to_have_text('Complete',timeout=15000)
    assert len(store.posts(NS,USER))==2
    page.close()
