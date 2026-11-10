import copy,threading
import pytest
from allotment.storage import Store,read_yaml,write_yaml,now
from allotment.test_review import catalog,review_run
from allotment.test_batches import TestBatches as Batches
from allotment.preparation import folder_for,fingerprint,variables_for
from test_platform_tests import seed,NS,USER

@pytest.fixture
def store(tmp_path):
    store=Store(tmp_path);store.bootstrap()
    write_yaml(store.settings_path(NS,USER),{'destinations':{'bluesky':{'expected_account':'oomlout.bsky.social','profile_url':'https://bsky.app/profile/oomlout.bsky.social'},'twitter':{'expected_account':'oomlout','profile_url':'https://x.com/oomlout'}}})
    return store


def inspect_seed(store):
    recipe=store.plugins()['twitter'];account=store.settings(NS,USER)['destinations']['twitter']
    folder=folder_for(store,NS,USER,'prep-inspect')
    write_yaml(folder/'run.yaml',{'id':'prep-inspect','namespace':NS,'user':USER,'platform':'twitter','recipe':recipe,'account':account,'mode':'inspect','signature':fingerprint(store,recipe,account),'variables':variables_for(recipe,account,mode='inspect'),'created_at':now(),'post':{'text':'fixed fixture'}})
    write_yaml(folder/'progress.yaml',{'status':'inspected','events':[],'verified':False,'sent':False})
    return folder


def test_prepare_review_requires_checks_then_invalidation_and_rejection(store):
    folder,run=seed(store)
    write_yaml(folder/'progress.yaml',{'status':'prepared','events':[],'draft_checked':False,'verified':False,'sent':False})
    with pytest.raises(ValueError):review_run(store,NS,USER,run['id'],'passed')
    write_yaml(folder/'progress.yaml',{'status':'prepared','events':[],'draft_checked':True,'verified':False,'sent':False})
    assert catalog(store,NS,USER)['bluesky']['prepare']['state']=='untested'
    review_run(store,NS,USER,run['id'],'passed')
    assert catalog(store,NS,USER)['bluesky']['prepare']['state']=='passed'
    review_run(store,NS,USER,run['id'],'not_passed')
    assert catalog(store,NS,USER)['bluesky']['prepare']['state']=='not_passed'
    assert not store.settings(NS,USER)['destinations']['bluesky']['enabled']
    recipe=copy.deepcopy(store.plugins()['bluesky']);recipe['steps'][0]['seconds']=8
    write_yaml(store.local/'plugins/bluesky.yaml',recipe)
    assert catalog(store,NS,USER)['bluesky']['prepare']['state']=='untested'
    with pytest.raises(ValueError,match='current instructions'):review_run(store,NS,USER,run['id'],'passed')


def test_inspection_review_is_separate_and_recipe_change_resets_both(store):
    inspect_seed(store);review_run(store,NS,USER,'prep-inspect','passed')
    state=catalog(store,NS,USER)['twitter']
    assert state['inspect']['state']=='passed' and state['prepare']['state']=='untested'
    assert not store.settings(NS,USER)['destinations']['twitter'].get('enabled')
    assert not store.settings(NS,USER)['destinations']['twitter'].get('approval')
    recipe=copy.deepcopy(store.plugins()['twitter']);recipe['setup_note']='Changed instructions'
    write_yaml(store.local/'plugins/twitter.yaml',recipe)
    assert catalog(store,NS,USER)['twitter']['inspect']['state']=='untested'


@pytest.mark.parametrize('stop',[False,True])
def test_sequential_batch_skips_passed_and_unavailable_and_never_approves(store,monkeypatch,stop):
    choices={name:{'name':name,'prepare':{'state':state,'eligible':eligible,'signature':'v1'}} for name,state,eligible in [('a','untested',True),('b','not_passed',True),('done','passed',True),('blocked','untested',False)]}
    monkeypatch.setattr('allotment.test_batches.catalog',lambda *args:choices)
    events=[];results=[]
    def start(ns,user,platform,mode):
        events.append(('start',platform,mode));results.append({'id':'test-'+platform,'active':False,'status':'stopped' if stop else 'prepared','error':'Chrome lost focus.' if stop else ''})
        return results[-1]['id']
    manager=Batches(store,start,lambda *args:results,lambda *args:events.append(('stop',args[-1])),lambda tid:True,threading.Lock())
    monkeypatch.setattr(manager,'_spawn',lambda *args:None)
    batch=manager.start(NS,USER,'prepare');manager.execute(NS,USER,batch)
    assert [item['platform'] for item in batch['items']]==['a','b','blocked']
    assert batch['items'][-1]['status']=='blocked' and batch['items'][-1]['error']
    assert events==[('start','a','prepare')]+([] if stop else [('start','b','prepare')])
    assert manager.current(NS,USER)['status']==('stopped' if stop else 'complete')
    if stop:assert manager.current(NS,USER)['items'][0]['error']=='Chrome lost focus.'
    assert not store.settings(NS,USER).get('test_reviews')
    assert not store.settings(NS,USER)['destinations']['bluesky'].get('approval')


def test_changed_recipe_between_batch_steps_is_not_executed(store,monkeypatch):
    choices={name:{'name':name,'inspect':{'state':'untested','eligible':True,'signature':'v1'}} for name in ['a','b']}
    monkeypatch.setattr('allotment.test_batches.catalog',lambda *args:choices)
    results=[]
    def start(ns,user,platform,mode):
        choices['b']['inspect']['signature']='v2';results.append({'id':'test-a','active':False,'status':'inspected'});return 'test-a'
    manager=Batches(store,start,lambda *args:results,lambda *args:None,lambda tid:True,threading.Lock())
    monkeypatch.setattr(manager,'_spawn',lambda *args:None)
    batch=manager.start(NS,USER,'inspect');manager.execute(NS,USER,batch)
    assert [item['status'] for item in manager.current(NS,USER)['items']]==['inspected','skipped']


def test_stop_between_platforms_cancels_remaining_and_restart_never_replays(store,monkeypatch):
    choices={name:{'name':name,'inspect':{'state':'untested','eligible':True,'signature':'v1'}} for name in ['a','b']}
    monkeypatch.setattr('allotment.test_batches.catalog',lambda *args:choices)
    manager=Batches(store,lambda *args:pytest.fail('Must not start'),lambda *args:[],lambda *args:None,lambda tid:True,threading.Lock())
    monkeypatch.setattr(manager,'_spawn',lambda *args:None)
    batch=manager.start(NS,USER,'inspect');manager.stop(NS,USER);manager.execute(NS,USER,batch)
    assert [item['status'] for item in manager.current(NS,USER)['items']]==['cancelled','cancelled']
    batch.update(status='running',pid=99999999)
    write_yaml(manager.folder(NS,USER)/'current.yaml',batch)
    assert manager.current(NS,USER)['status']=='interrupted'


@pytest.mark.parametrize('mode',['prepare','inspect'])
def test_all_current_untested_destinations_are_queued_and_blockers_explained(store,monkeypatch,mode):
    manager=Batches(store,lambda *a:None,lambda *a:[],lambda *a:None,lambda _:True,threading.Lock())
    monkeypatch.setattr(manager,'_spawn',lambda *a:None)
    choices=catalog(store,NS,USER)
    batch=manager.start(NS,USER,mode)
    queued={item['platform'] for item in batch['items'] if item['status']=='pending'}
    expected={name for name,item in choices.items() if item[mode]['state'] in {'untested','not_passed'}}
    assert queued==expected
    for item in batch['items']:
        if item['status']=='blocked':
            assert choices[item['platform']][mode]['state']=='blocked'
            assert item['error']==choices[item['platform']][mode]['reason']


def test_unconfigured_platforms_are_blocked_not_untested(store):
    choices=catalog(store,NS,USER)
    assert choices['telegram']['prepare']['state']=='blocked'
    assert choices['telegram']['inspect']['state']=='blocked'
    assert choices['bluesky']['inspect']['state']=='blocked'
    assert choices['patreon']['prepare']['state']=='blocked'
    assert choices['twitter']['prepare']['state']=='untested'


def test_single_review_lookup_does_not_read_unrelated_history(store):
    from allotment.preparation import preparation_runs
    inspect_seed(store)
    unrelated=folder_for(store,NS,USER,'prep-unrelated')
    unrelated.mkdir(parents=True)
    (unrelated/'run.yaml').write_text('broken: [',encoding='utf-8')
    runs=preparation_runs(store,NS,USER,test_id='prep-inspect')
    assert len(runs)==1 and runs[0]['id']=='prep-inspect' and runs[0]['current']
    assert review_run(store,NS,USER,'prep-inspect','passed')['verdict']=='passed'


def test_status_reuses_platform_checks_only_within_one_request(store,monkeypatch):
    from allotment.preparation import preparation_runs
    folder=inspect_seed(store);run=read_yaml(folder/'run.yaml')
    other=folder_for(store,NS,USER,'prep-inspect-two')
    write_yaml(other/'run.yaml',{**run,'id':'prep-inspect-two'})
    write_yaml(other/'progress.yaml',read_yaml(folder/'progress.yaml'))
    calls=[]
    def measured(*args):calls.append(args[1]['id']);return fingerprint(*args)
    monkeypatch.setattr('allotment.preparation.fingerprint',measured)
    assert all(r['current'] for r in preparation_runs(store,NS,USER))
    assert calls==['twitter']
    recipe=copy.deepcopy(store.plugins()['twitter']);recipe['setup_note']='Changed recipe'
    write_yaml(store.local/'plugins/twitter.yaml',recipe);calls.clear()
    assert all(not r['current'] for r in preparation_runs(store,NS,USER))
    assert calls==['twitter']
