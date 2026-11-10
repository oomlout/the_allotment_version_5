from pathlib import Path
from contextlib import contextmanager
import pytest
from allotment.app import create_app
from allotment.storage import Store,write_yaml,read_yaml,now
from allotment.lifecycle import publish_local
from allotment.framing import defaults
from allotment.job_runs import JobRunManager,folder_for,execute_run
from allotment.launch import countdown_in_tab

NS,USER='the_allotment','oomlout_main'

@pytest.fixture
def store(tmp_path):
    store=Store(tmp_path);store.bootstrap();return store

def client_for(store):
    app=create_app(store.root,testing=True,garden=(NS,USER));client=app.test_client();client.get('/')
    with client.session_transaction() as session:csrf=session['csrf']
    return app,client,csrf

def test_queue_and_bundle_endpoints_removed_and_cards_bound(store):
    app,client,csrf=client_for(store);base=f'/g/{NS}/{USER}'
    for path,method in [('/queue/','get'),('/queue/job-test/run','post'),('/queue/job-test/export','post'),('/receipts','post'),('/sync','post')]:
        assert getattr(client,method)(base+path,**({'data':{'csrf':csrf}} if method=='post' else {})).status_code==404
    html=client.get(base+'/destinations/').text
    assert 'platform-logo' in html and 'Default crop' in html and 'card-test' in html
    assert '>Queue<' not in html and '>Tests<' not in html
    assert client.get(base+'/tests/?platform=twitter').location.endswith('/destinations/?platform=twitter&focus=')
    iframe=client.get(base+'/tests/?platform=twitter&embedded=1')
    assert iframe.status_code==302 and '/destinations/' in iframe.location
    assert 'Fixed sample' not in html and '<iframe' not in html and 'test-dashboard' in html and '<dialog' not in html

def test_card_save_changes_one_platform_and_default_only(store):
    write_yaml(store.settings_path(NS,USER),{'destinations':{'telegram':{'expected_account':'keep-me','target_name':'Channel'},'bluesky':{'expected_account':'old'}}})
    app,client,csrf=client_for(store)
    response=client.post(f'/g/{NS}/{USER}/destinations/',data={'csrf':csrf,'platform':'bluesky','bluesky_account':'new','bluesky_profile_url':'https://bsky.app/profile/new','frame_bluesky':'square'})
    assert response.status_code==302 and defaults(store,NS,USER)['bluesky']=='square'
    assert store.settings(NS,USER)['destinations']['telegram']=={'expected_account':'keep-me','target_name':'Channel'}
    assert not store.settings(NS,USER)['destinations']['bluesky']['enabled']

def test_launch_is_immediate_and_failed_process_cancels_unstarted_actions(store,monkeypatch):
    post=store.save_draft(NS,USER,'Launch test');publish_local(store,NS,USER,post['id'])
    manager=JobRunManager(store)
    def fail(*args):raise ValueError('No process')
    monkeypatch.setattr(manager,'_launch',fail)
    with pytest.raises(ValueError):manager.launch_post(NS,USER,post['id'],['static'])
    assert store.jobs(NS,USER)[0]['launch_only'] and store.jobs(NS,USER)[0]['actions']['static']['status']=='cancelled'

def test_open_tab_precedes_countdown_and_guard_rearms_at_handoff(tmp_path):
    events=[]
    class Desktop:
        input_tick=1
        def navigate(self,url,seconds):events.append('navigate')
        def check_stop(self):pass
        def last_input(self):return 42
        def guard(self):assert self.input_tick==42;events.append('guard')
    def update(**values):events.append(values['phase'])
    countdown_in_tab(Desktop(),tmp_path,'https://example.com',update,seconds=.01)
    assert events[0]=='navigate' and events.index('switching')<events.index('guard')<events.index('running')


def test_initial_countdown_pause_resume_and_stop(tmp_path):
    from allotment.browser import StopRequested
    events=[]
    class Desktop:
        def navigate(self,url,seconds):events.append('navigate')
        def check_stop(self):
            if (tmp_path/'stop.flag').exists():raise StopRequested('Stopped')
        def last_input(self):return 42
        def guard(self):events.append('guard')
    (tmp_path/'pause.flag').touch()
    def resume(**values):
        events.append(values['phase'])
        if values['phase']=='paused':(tmp_path/'continue.flag').touch()
    countdown_in_tab(Desktop(),tmp_path,'https://example.com',resume,seconds=.01)
    assert events==['navigate','switching','paused','guard','running']
    assert not (tmp_path/'pause.flag').exists() and not (tmp_path/'continue.flag').exists()
    events.clear();(tmp_path/'pause.flag').touch()
    def stop(**values):
        events.append(values['phase'])
        if values['phase']=='paused':(tmp_path/'stop.flag').touch()
    with pytest.raises(StopRequested):
        countdown_in_tab(Desktop(),tmp_path,'https://example.com',stop,seconds=.01)
    assert events==['navigate','switching','paused']

def test_stop_cancels_new_launch_without_leaving_work_for_later(store):
    from allotment.sowing import enqueue
    post=store.save_draft(NS,USER,'Stopped launch');publish_local(store,NS,USER,post['id'])
    job=enqueue(store,NS,USER,post['id'],['static']);job['launch_only']=True;write_yaml(store.job_path(NS,USER,job['id']),job)
    folder=folder_for(store,NS,USER,'run-stop-launch')
    write_yaml(folder/'run.yaml',{'id':'run-stop-launch','kind':'launch','job_id':job['id'],'destinations':['static']})
    write_yaml(folder/'progress.yaml',{'status':'queued'});(folder/'stop.flag').write_text('stop')
    assert execute_run(store,NS,USER,'run-stop-launch')['status']=='stopped'
    assert store.jobs(NS,USER)[0]['actions']['static']['status']=='cancelled'

def test_garden_selection_is_cards(store):
    client=create_app(store.root,testing=True).test_client();html=client.get('/choose-garden/').text
    assert 'garden-card' in html and '<select' not in html and 'Open oomlout_main' in html and 'Open allotment_main' in html


def test_every_platform_has_local_logo_with_provenance(store):
    import json
    folder=Path(__file__).resolve().parents[1]/'allotment/static/platform-logos'
    sources=json.loads((folder/'sources.json').read_text(encoding='utf-8'))
    for name in store.plugins():
        assert (folder/(name+'.svg')).is_file() and sources[name]['source'].startswith('https://raw.githubusercontent.com/')


def test_one_countdown_then_separate_tabs_for_all_destinations(store,monkeypatch):
    from allotment.sowing import enqueue
    post=store.save_draft(NS,USER,'Multiple destinations');publish_local(store,NS,USER,post['id'])
    job=enqueue(store,NS,USER,post['id'],['static']);job['launch_only']=True;write_yaml(store.job_path(NS,USER,job['id']),job)
    folder=folder_for(store,NS,USER,'run-sequence');write_yaml(folder/'run.yaml',{'id':'run-sequence','kind':'launch','job_id':job['id'],'destinations':['bluesky','twitter']});write_yaml(folder/'progress.yaml',{'status':'queued'})
    events=[]
    def countdown(session,folder,url,update,seconds,resume_flag):events.append('countdown');update(phase='running')
    def execute(store,ns,user,job,stop,on_session,on_action,on_step):
        for name in ['bluesky','twitter']:
            on_action(name);events.append('open '+name)
            on_session(object(),{'name':name,'base_url':'https://example.com','steps':[{'url':'https://example.com'}]}, {})
            on_step(name,2,{'command':'prepare_text','label':'Paste sample text'},'before',8)
            live=read_yaml(folder/'progress.yaml')
            assert live['platform']==name and live['step']==3 and live['total_steps']==8 and live['operation']=='Adding post text'
            events.append('prepare '+name)
        job['actions']['static']['status']='succeeded';write_yaml(store.job_path(ns,user,job['id']),job)
    monkeypatch.setattr('allotment.launch.countdown_in_tab',countdown);monkeypatch.setattr('allotment.sowing.execute',execute)
    assert execute_run(store,NS,USER,'run-sequence')['status']=='complete'
    assert events==['open bluesky','countdown','prepare bluesky','open twitter','prepare twitter']
