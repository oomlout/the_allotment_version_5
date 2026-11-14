from pathlib import Path
from copy import deepcopy
import json
import pytest
from allotment.storage import write_yaml,read_yaml,now
from allotment.translations import start,validate,decode,tags,review,versions_for,load_result,catalog,execute,make_prompt
from allotment.job_runs import folder_for
from allotment.lifecycle import publish_local
from test_sow_review import NS,USER,client_for,store


def pack(run):
    return {'schema_version':'2.0','run_id':run['id'],'source_revision':run['source_revision'],
        'source':{'text':run['source']['text']},'destinations':run['destinations'],
        'posts':{k:{'title':'Workshop update' if k in {'medium','flickr'} else None,
            'message':run['source']['text'] if k=='twitter' else 'A distinct '+k+' update.'+('\n\nThis post was drafted with AI assistance.' if k=='medium' else ''),
            'tags':[],'strategy':'Keep the specific observation.','editorial_status':'draft' if run['catalog'][k]['ready'] else 'hold_preparation_unavailable','publication_status':'not_published'} for k in run['destinations']},
        'image':{'alt_text':None},'questions':[],'delivery_notes':[]}


def prepared(store,monkeypatch,platforms=('twitter','medium')):
    post=store.save_draft(NS,USER,'Original tweet 🌱\nExact punctuation!')
    monkeypatch.setattr('allotment.job_runs.JobRunManager._launch',lambda self,ns,user,run:write_run(store,ns,user,run))
    run_id=start(store,NS,USER,post['id'],list(platforms),'Confirmed project notes.')
    folder=folder_for(store,NS,USER,run_id);run=read_yaml(folder/'run.yaml')
    result=pack(run);(folder/'result.json').write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    write_yaml(folder/'progress.yaml',{'status':'complete','updated_at':now()})
    return post,run,result


def write_run(store,ns,user,run):
    write_yaml(folder_for(store,ns,user,run['id'])/'run.yaml',run)
    return run['id']


def test_tagged_output_preserves_unicode_and_rejects_echo_or_wrong_run(store,monkeypatch):
    post,run,result=prepared(store,monkeypatch);a,b=tags(run)
    assert decode(a+'\n'+json.dumps(result,ensure_ascii=False)+'\n'+b,run)['source']['text']==post['text']
    for text in [a+'{}',a+'{}'+b+a+'{}'+b,make_prompt(run,'Guide')]:
        with pytest.raises(ValueError):decode(text,run)
    for change in [{'run_id':'other'},{'source':{'text':'Changed'}},{'destinations':['twitter']},{'questions':['a']*4}]:
        with pytest.raises(ValueError):validate({**result,**change},run)


@pytest.mark.parametrize('mutation',['duplicate','overlimit','published','twitter','medium','tags','title'])
def test_bad_editorial_payloads_are_rejected(store,monkeypatch,mutation):
    post,run,result=prepared(store,monkeypatch)
    if mutation=='duplicate':result['posts']['medium']['message']=post['text']
    if mutation=='overlimit':result['posts']['medium']['message']+='x'*10001
    if mutation=='published':result['posts']['twitter']['publication_status']='published'
    if mutation=='twitter':result['posts']['twitter']['message']='Rewritten'
    if mutation=='medium':result['posts']['medium']['message']='No disclosure'
    if mutation=='tags':result['posts']['medium']['tags']=['bad tag']
    if mutation=='title':result['posts']['twitter']['title']='Wrong title'
    with pytest.raises(ValueError):validate(result,run)


def test_review_is_explicit_revision_bound_and_sow_keeps_original(store,monkeypatch):
    from allotment.sowing import enqueue
    post,run,result=prepared(store,monkeypatch)
    assert versions_for(store,NS,USER,post)=={}
    review(store,NS,USER,post['id'],run['id'])
    publish_local(store,NS,USER,post['id'])
    # The normal platform gate still runs for the selected translated payload.
    seen=[]
    monkeypatch.setattr('allotment.preparation_approval.require_approval',lambda *args:seen.append(args[-1]['text']))
    job=enqueue(store,NS,USER,post['id'],['medium'])
    expected='Workshop update\n\n'+result['posts']['medium']['message']
    assert seen==[expected] and job['destination_posts']['medium']['text']==expected
    assert job['post']['text']==post['text']==job['destination_posts']['static']['text']
    changed={**post,'text':'Changed source'}
    with pytest.raises(ValueError,match='out of date'):versions_for(store,NS,USER,changed)
    review(store,NS,USER,post['id'])
    assert versions_for(store,NS,USER,changed)=={}


def test_catalog_holds_patreon_and_excludes_ai_and_infrastructure(store,monkeypatch):
    assert not set(catalog(store)) & {'chatgpt_translation','bluesky_harvest','static','summary','git','facebook','telegram','whatsapp'}
    post,run,result=prepared(store,monkeypatch,('patreon',))
    result['posts']['patreon']['editorial_status']='draft'
    with pytest.raises(ValueError,match='held'):validate(result,run)


def test_post_routes_download_and_review_same_canonical_data(store,monkeypatch):
    post,run,result=prepared(store,monkeypatch)
    app,client,csrf=client_for(store);base=f'/g/{NS}/{USER}/p/{post["id"]}'
    html=client.get(base+'/').text
    assert 'Generate versions' in html and 'Download JSON' in html
    assert 'class="translation-result"' not in html
    response=client.get(base+'/translations/'+run['id']+'.json')
    assert response.status_code==200 and json.loads(response.data)['posts']==result['posts']
    assert client.post(base+'/translations/select',data={'csrf':csrf,'run_id':run['id']}).status_code==302
    assert versions_for(store,NS,USER,post)['twitter']['message']==post['text']
    other=store.save_draft(NS,USER,'Other')
    assert client.get(f'/g/{NS}/{USER}/p/{other["id"]}/translations/{run["id"]}.json').status_code==400


def test_stop_prevents_ai_input_and_completed_runs_are_single_use(store,monkeypatch):
    post,run,result=prepared(store,monkeypatch)
    folder=folder_for(store,NS,USER,run['id'])
    with pytest.raises(ValueError,match='single-use'):execute(store,NS,USER,run['id'])
    write_yaml(folder/'progress.yaml',{'status':'queued'});(folder/'stop.flag').write_text('stop')
    monkeypatch.setattr('allotment.translation_browser.run_pipeline',lambda *args:pytest.fail('No input after Stop'))
    assert execute(store,NS,USER,run['id'])['status']=='stopped'


def test_post_page_launch_uses_saved_source_and_private_snapshot(store,monkeypatch):
    post=store.save_draft(NS,USER,'Saved source 🌱')
    monkeypatch.setattr('allotment.job_runs.JobRunManager._launch',lambda self,ns,user,run:write_run(store,ns,user,run))
    app,client,csrf=client_for(store)
    base=f'/g/{NS}/{USER}/p/{post["id"]}'
    response=client.post(base+'/translate',data={'csrf':csrf,'destinations':['twitter','medium'],'context':'Confirmed fact.'},headers={'Accept':'application/json'})
    assert response.status_code==200
    folder=folder_for(store,NS,USER,response.json['id']);run=read_yaml(folder/'run.yaml')
    assert run['source']['text']=='Saved source 🌱' and run['context']=='Confirmed fact.'
    assert (folder/'guide.md').is_file() and (folder/'prompt.txt').is_file()
    assert not store.published(NS,USER,post['id']) and versions_for(store,NS,USER,post)=={}
    bad=client.post(base+'/translate',data={'csrf':csrf,'destinations':['chatgpt_translation']},headers={'Accept':'application/json'})
    assert bad.status_code==400


def test_runtime_prompt_includes_all_guidance_without_external_chat_or_bundle(store,monkeypatch):
    from allotment.translations import GUIDE
    post,run,_=prepared(store,monkeypatch)
    guide=GUIDE.read_text(encoding='utf-8')
    prompt=make_prompt(run,guide)
    assert guide in prompt
    assert guide.count('WHY THIS APPROACH:')==16
    for name in ('Twitter / X','Bluesky','Mastodon','Threads','Instagram','Facebook Page','Tumblr',
                 'Pixelfed','Flickr','MakerWorld','Printables','Discord','Telegram Channel','WhatsApp Channel','Medium','Patreon'):
        assert name in guide
    for missing in ('chatgpt.com/share','chatgpt.com/library','the_allotment_agent_spec.json',
                    'the_allotment_post_pack.schema.json','examples/1000127057.jpg',
                    'examples/the_allotment_2026-11-10_posts.json','bundled schema'):
        assert missing not in prompt
    assert 'Required root fields and types:' in guide
    assert 'Shared image alt text:' in guide and 'BEGIN MESSAGE' in guide
    assert json.dumps(run['source']['text'],ensure_ascii=False) in prompt
    assert 'For discord, whatsapp_channel, telegram_channel, medium and tumblr, include the relevant supplied project/resource URL' in prompt
    assert 'Use github_repo when relevant' in prompt


def test_translation_handoff_reads_its_own_evidence(tmp_path,monkeypatch):
    import time
    from allotment.job_runs import progress_for
    folder=tmp_path/'run';(folder/'evidence').mkdir(parents=True)
    write_yaml(folder/'run.yaml',{'kind':'translation'})
    write_yaml(folder/'progress.yaml',{'status':'running','updated_at':now()})
    (folder/'evidence/window-handoff.json').write_text(json.dumps({'waiting':True,'until':time.time()+5}))
    monkeypatch.setattr('allotment.job_runs.owned_process',lambda folder:True)
    progress=progress_for(folder)
    assert progress['phase']=='handoff' and 0<progress['remaining']<=5
