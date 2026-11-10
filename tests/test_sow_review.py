import pytest
from allotment.storage import Store,read_yaml,write_yaml,now
from allotment.lifecycle import publish_local
from allotment.sowing import enqueue
from allotment.sow_review import save_review,blocks_retry
from test_launch import store,client_for,NS,USER


def attempted(store):
    post=store.save_draft(NS,USER,'Sowing review fixture');publish_local(store,NS,USER,post['id'])
    job=enqueue(store,NS,USER,post['id'],['static'])
    job['actions']['static'].update(status='failed',attempts=1,error='Build failed')
    job['actions']['summary'].update(status='succeeded',attempts=1)
    job['actions']['git']={'status':'failed','attempts':1,'error':'Git failed','url':''}
    job['actions']['twitter']={'status':'prepared','attempts':1,'sent':False,'verified':False,'manual_submission_required':True,'url':''}
    write_yaml(store.job_path(NS,USER,job['id']),job)
    return post,job


def test_review_survives_new_store_and_app_without_changing_worker_evidence(store):
    post,job=attempted(store);app,client,csrf=client_for(store)
    url=f'/g/{NS}/{USER}/p/{post["id"]}/sow/{job["id"]}/twitter/review'
    assert client.post(url,data={'csrf':csrf,'verdict':'successful'},headers={'Accept':'application/json'}).json['review']['verdict']=='successful'
    restarted=Store(store.root);app,client,csrf=client_for(restarted)
    state=restarted.destination_states(NS,USER,post['id'])['twitter']
    assert state['review']['verdict']=='successful' and state['sent'] is False and state['verified'] is False
    assert state['status']=='prepared' and not blocks_retry('twitter',state,{})
    raw=read_yaml(store.job_path(NS,USER,job['id']))['actions']['twitter']
    assert 'review' not in raw and raw['status']=='prepared'
    html=client.get(f'/g/{NS}/{USER}/p/{post["id"]}/').text
    assert 'Posted — confirmed by you' in html and 'twitter.svg' in html
    assert client.post(url,data={'csrf':csrf,'verdict':'unsuccessful'}).status_code==302
    state=restarted.destination_states(NS,USER,post['id'])['twitter']
    assert state['review']['verdict']=='unsuccessful' and not blocks_retry('twitter',state,{})
    with pytest.raises(ValueError,match='approve|approved|test'):
        enqueue(restarted,NS,USER,post['id'],['twitter'])


def test_reviews_are_bound_to_attempt_and_new_attempt_starts_unreviewed(store):
    post,job=attempted(store)
    save_review(store,NS,USER,post['id'],job['id'],'static','unsuccessful')
    new=enqueue(store,NS,USER,post['id'],['static'])
    state=store.destination_states(NS,USER,post['id'])['static']
    assert state['job_id']==new['id'] and 'review' not in state
    assert store.jobs(NS,USER)[1]['actions']['static']['review']['verdict']=='unsuccessful'
    for post_id,job_id,action,verdict in [(post['id'],new['id'],'static','successful'),('wrong-post',job['id'],'twitter','successful'),(post['id'],job['id'],'unknown','successful'),(post['id'],job['id'],'twitter','approve')]:
        with pytest.raises(ValueError):save_review(store,NS,USER,post_id,job_id,action,verdict)


def test_active_launch_blocks_review_and_status_contains_attempt_details(store):
    from allotment.job_runs import folder_for
    post,job=attempted(store)
    folder=folder_for(store,NS,USER,'run-current')
    write_yaml(folder/'run.yaml',{'id':'run-current','post_id':post['id'],'created_at':now(),'kind':'launch','destinations':['twitter'],'job_id':job['id']})
    write_yaml(folder/'progress.yaml',{'status':'running','updated_at':now(),'platform':'twitter','operation':'Checking image','step':7,'total_steps':8})
    with pytest.raises(ValueError,match='launch finishes'):save_review(store,NS,USER,post['id'],job['id'],'twitter','successful')
    app,client,csrf=client_for(store)
    data=client.get(f'/g/{NS}/{USER}/p/{post["id"]}/sow/status').json
    assert data['run']['operation']=='Checking image' and data['run']['step']==7
    twitter=next(d for d in data['destinations'] if d['id']=='twitter')
    assert twitter['reviewable'] and twitter['job_id']==job['id'] and twitter['review_url'].endswith('/twitter/review')


def test_explicit_retry_of_posted_destination_uses_new_attempt_and_keeps_history(store,monkeypatch):
    post,job=attempted(store)
    save_review(store,NS,USER,post['id'],job['id'],'twitter','successful')
    from unittest.mock import MagicMock
    approved=MagicMock()
    monkeypatch.setattr('allotment.preparation_approval.require_approval',approved)
    retry=enqueue(store,NS,USER,post['id'],['twitter'])
    approved.assert_called_once()
    state=store.destination_states(NS,USER,post['id'])['twitter']
    assert state['job_id']==retry['id'] and state['status']=='pending' and 'review' not in state
    assert store.jobs(NS,USER)[1]['actions']['twitter']['review']['verdict']=='successful'
    with pytest.raises(ValueError,match='sowing record'):enqueue(store,NS,USER,post['id'],['twitter'])


@pytest.mark.parametrize('verdict',['successful','unsuccessful'])
def test_legacy_export_without_attempt_count_is_reviewable_and_does_not_block_retry(store,monkeypatch,verdict):
    from allotment.sow_review import destination_view
    from unittest.mock import MagicMock
    post,job=attempted(store)
    job.pop('kind',None)
    job['actions']['bluesky']={'status':'exported','attempts':0,'url':'','error':''}
    write_yaml(store.job_path(NS,USER,job['id']),job)
    state=store.destination_states(NS,USER,post['id'])['bluesky']
    assert destination_view('bluesky',state)['reviewable']
    assert not blocks_retry('bluesky',state,{})
    app,client,csrf=client_for(store)
    base=f'/g/{NS}/{USER}/p/{post["id"]}'
    data=client.get(base+'/sow/status').json
    view=next(d for d in data['destinations'] if d['id']=='bluesky')
    assert view['reviewable'] and not view['blocked']
    response=client.post(view['review_url'],data={'csrf':csrf,'verdict':verdict},headers={'Accept':'application/json'})
    assert response.status_code==200
    # No export receipt or delivery claim is rewritten when a user reviews it.
    assert read_yaml(store.job_path(NS,USER,job['id']))['actions']['bluesky']==job['actions']['bluesky']
    restarted=Store(store.root)
    assert restarted.destination_states(NS,USER,post['id'])['bluesky']['review']['verdict']==verdict
    approval=MagicMock()
    monkeypatch.setattr('allotment.preparation_approval.require_approval',approval)
    retry=enqueue(restarted,NS,USER,post['id'],['bluesky'])
    approval.assert_called_once()
    assert restarted.destination_states(NS,USER,post['id'])['bluesky']['job_id']==retry['id']


@pytest.mark.parametrize('previous',[True,False])
def test_launch_explains_platform_approval_and_links_to_its_test_without_changing_it(store,previous):
    post,job=attempted(store)
    account={'enabled':True,'expected_account':'fixture'}
    if previous:account['approval']={'test_id':'prep-previous','signature':'old'}
    write_yaml(store.settings_path(NS,USER),{'destinations':{'medium':account}})
    before=store.settings_path(NS,USER).read_bytes()
    app,client,csrf=client_for(store)
    response=client.post(f'/g/{NS}/{USER}/p/{post["id"]}/launch',data={'csrf':csrf,'actions':'medium'},headers={'Accept':'application/json'})
    assert response.status_code==400
    data=response.json
    assert ('out of date' if previous else 'approval is required') in data['error']
    assert 'post’s ✓' in data['error'] and 'Test → Prepare' in data['error']
    assert data['platform']=='medium' and data['setup_url'].endswith('/destinations/#destination-medium')
    assert store.settings_path(NS,USER).read_bytes()==before
    assert len(store.jobs(NS,USER))==1
