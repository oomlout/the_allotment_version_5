from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import pytest
from werkzeug.datastructures import FileStorage
from allotment.storage import Store,read_yaml,write_yaml,now
from allotment.preparation import folder_for,fingerprint
from allotment.preparation_approval import approve_run,approval_status
from allotment.preparation_worker import run_worker
from allotment.test_sample import SAMPLE_IMAGE,SAMPLE_TEXT

NS,USER='the_allotment','oomlout_main'


def completed(store,platform='twitter',status='prepared'):
    recipe=store.plugins()[platform]
    account={'expected_account':'right','profile_url':recipe['base_url']+'/right'}
    settings=store.settings(NS,USER);settings.setdefault('destinations',{})[platform]=account
    write_yaml(store.settings_path(NS,USER),settings)
    folder=folder_for(store,NS,USER,'prep-fixture-'+platform)
    write_yaml(folder/'run.yaml',{'id':folder.name,'namespace':NS,'user':USER,'platform':platform,'mode':'prepare',
               'signature':fingerprint(store,recipe,account),'created_at':now(),'recipe':recipe,'account':account,
               'variables':{'base_url':recipe['base_url'],'target_name':'','handle':'right'},
               'post':{'text':SAMPLE_TEXT,'attachments':[{'type':'image'}]}})
    result=dict(prepared=True,draft_checked=True,manual_submission_required=True,sent=False,verified=False,
                account_checked=True,target_checked=True,text_checked=True,image_checked=True)
    write_yaml(folder/'progress.yaml',{'status':status,'sent':False,'verified':False,'result':result})
    return folder,account


@pytest.mark.parametrize('status',['inspected','failed','stopped','running'])
def test_only_checked_completed_drafts_can_be_approved(tmp_path,status):
    store=Store(tmp_path);store.bootstrap();folder,account=completed(store,status=status)
    with pytest.raises(ValueError):approve_run(store,NS,USER,folder.name)
    assert not store.settings(NS,USER)['destinations']['twitter'].get('enabled')


def test_approval_requires_explicit_call_and_is_bound_to_settings(tmp_path):
    store=Store(tmp_path);store.bootstrap();folder,account=completed(store)
    recipe=store.plugins()['twitter']
    assert not approval_status(store,NS,USER,recipe,account)['valid']
    approved=approve_run(store,NS,USER,folder.name)
    assert approval_status(store,NS,USER,recipe,approved)['valid']
    assert not approval_status(store,NS,USER,recipe,{**approved,'expected_account':'different'})['valid']
    assert not approval_status(store,NS,USER,recipe,{**approved,'browser_profile_url':'https://x.com/changed'})['valid']
    (folder/'stop.flag').write_text('stop')
    assert not approval_status(store,NS,USER,recipe,approved)['valid']


def test_queue_prepares_once_leaves_manual_receipt_and_builds_static(tmp_path,monkeypatch):
    store=Store(tmp_path);store.bootstrap();folder,_=completed(store);approve_run(store,NS,USER,folder.name)
    draft=store.save_draft(NS,USER,'Queued unsent text',[FileStorage(stream=BytesIO(SAMPLE_IMAGE.read_bytes()),filename='test.png')])
    job=store.publish(NS,USER,draft['id'],['twitter','static']);opened=[]
    @contextmanager
    def context(*args):opened.append(args[1]);yield object()
    monkeypatch.setattr('allotment.preparation_worker.preparation_context',context)
    monkeypatch.setattr('allotment.preparation_worker.run_recipe',lambda *a,**kw:{'draft_checked':True,'sent':False})
    result=run_worker(store)[0]
    assert result['actions']['twitter']['status']=='prepared' and not result['actions']['twitter']['sent']
    assert 'url' not in result['actions']['twitter'] or not result['actions']['twitter']['url']
    assert result['actions']['static']['status']=='succeeded'
    assert run_worker(store)==[] and len(opened)==1


def test_queue_rejects_an_unapproved_preparation_even_if_enabled(tmp_path):
    store=Store(tmp_path);store.bootstrap();folder,account=completed(store)
    account['enabled']=True;write_yaml(store.settings_path(NS,USER),{'destinations':{'twitter':account}})
    draft=store.save_draft(NS,USER,'Not approved')
    with pytest.raises(ValueError):store.publish(NS,USER,draft['id'],['twitter'])
