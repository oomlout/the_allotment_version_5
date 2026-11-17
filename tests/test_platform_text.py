import io,json
from copy import deepcopy
import pytest
from werkzeug.datastructures import MultiDict
from allotment.storage import Store,read_yaml,write_yaml,now
from allotment.lifecycle import revision,publish_local
from allotment.job_runs import folder_for
from allotment.translations import versions_for,execute,review
from allotment.platform_text import import_pack,save,view,path_for,pending_path,export_pack
from test_translations import prepared
from test_sow_review import store,NS,USER,client_for


def edit_form(post,entries):
    form=MultiDict([('source_revision',revision(post))])
    for key,value in entries.items():
        form.add('platforms',key);form['message_'+key]=value['message']
        form['title_'+key]=value.get('title') or '';form['tags_'+key]=', '.join(value.get('tags',[]))
        form['status_'+key]=value.get('editorial_status','draft')
    return form


def test_import_edit_save_restart_and_sow_uses_independent_text(store,monkeypatch):
    from allotment.sowing import enqueue
    post,run,pack=prepared(store,monkeypatch)
    pack['image']['alt_text']='Test card 🌱'
    assert import_pack(store,NS,USER,post['id'],json.dumps(pack).encode())==2
    assert view(store,NS,USER,post['id'])['pending']
    assert versions_for(store,NS,USER,post)=={} # Loading is not review or approval.
    entries=deepcopy(pack['posts']);entries['twitter']['message']='A manually edited Twitter version 🌱'
    entries['medium']['title']='My project note';entries['medium']['message']='A longer independent account.\n\nSecond paragraph.'
    save(store,NS,USER,post['id'],edit_form(post,entries))
    restarted=Store(store.root)
    assert not pending_path(store,NS,USER,post['id']).exists()
    assert versions_for(restarted,NS,USER,post)['twitter']['message']==entries['twitter']['message']
    assert store.draft(NS,USER,post['id'])['text']==post['text']
    assert export_pack(store,NS,USER,post['id'])['image']['alt_text']=='Test card 🌱'
    publish_local(store,NS,USER,post['id'])
    with pytest.raises(ValueError):enqueue(store,NS,USER,post['id'],['twitter'])
    seen=[];monkeypatch.setattr('allotment.preparation_approval.require_approval',lambda *a:seen.append(a[-1]['text']))
    job=enqueue(restarted,NS,USER,post['id'],['twitter','medium','bluesky'])
    assert seen==[entries['twitter']['message'],'My project note\n\n'+entries['medium']['message'],post['text']]
    assert job['destination_posts']['twitter']['text']==entries['twitter']['message']
    assert job['destination_posts']['static']['text']==post['text']
    assert job['destination_posts']['summary']['text']==post['text']
    assert store.published(NS,USER,post['id'])['text']==post['text']


@pytest.mark.parametrize('mutation',['source','revision','unknown','published','mismatched','title','tags','duplicate'])
def test_invalid_import_is_atomic_and_preserves_saved_copy(store,monkeypatch,mutation):
    post,run,pack=prepared(store,monkeypatch)
    save(store,NS,USER,post['id'],edit_form(post,pack['posts']))
    before=path_for(store,NS,USER,post['id']).read_bytes()
    if mutation=='source':pack['source']['text']='Other source'
    if mutation=='revision':pack['source_revision']='old'
    if mutation=='unknown':pack['posts']['other']=pack['posts'].pop('medium');pack['destinations']=['twitter','other']
    if mutation=='published':pack['posts']['twitter']['publication_status']='published'
    if mutation=='mismatched':pack['destinations']=['twitter']
    if mutation=='title':pack['posts']['medium']['title']=None
    if mutation=='tags':pack['posts']['twitter']['tags']=['hello']
    raw=json.dumps(pack)
    if mutation=='duplicate':raw=raw.replace('"schema_version": "2.0"','"schema_version":"2.0","schema_version":"2.0"')
    with pytest.raises(ValueError):import_pack(store,NS,USER,post['id'],raw)
    assert path_for(store,NS,USER,post['id']).read_bytes()==before
    assert not pending_path(store,NS,USER,post['id']).exists()


def test_successful_capture_automatically_loads_editors_without_selecting(store,monkeypatch):
    post,run,pack=prepared(store,monkeypatch)
    folder=folder_for(store,NS,USER,run['id']);write_yaml(folder/'progress.yaml',{'status':'queued','updated_at':now()})
    monkeypatch.setattr('allotment.translation_browser.run_pipeline',lambda *args:deepcopy(pack))
    assert execute(store,NS,USER,run['id'])['status']=='complete'
    loaded=view(store,NS,USER,post['id'])
    assert loaded['pending'] and loaded['checked'] and loaded['entries']['medium']['message']==pack['posts']['medium']['message']
    assert versions_for(store,NS,USER,post)=={}
    assert store.destination_states(NS,USER,post['id'])=={}


def test_portable_pack_and_tagged_json_load_with_held_platform_preserved(store,monkeypatch):
    post,run,pack=prepared(store,monkeypatch,('twitter','patreon'))
    pack.pop('source_revision');pack.pop('run_id')
    pack['posts']['patreon']['editorial_status']='draft'
    raw='<<<ALLOTMENT_TRANSLATION_external>>>\n'+json.dumps(pack)+'\n<<<END_ALLOTMENT_TRANSLATION_external>>>'
    import_pack(store,NS,USER,post['id'],raw)
    entries=view(store,NS,USER,post['id'])['entries']
    assert entries['patreon']['editorial_status']=='hold_quip_unavailable'
    save(store,NS,USER,post['id'],edit_form(post,entries))
    changed={**post,'text':'New source'}
    with pytest.raises(ValueError,match='out of date'):versions_for(store,NS,USER,changed)
    stale=edit_form(changed,entries)
    with pytest.raises(ValueError,match='source post changed'):save(store,NS,USER,post['id'],stale)
    review(store,NS,USER,post['id'])
    assert versions_for(store,NS,USER,changed)=={}


def test_json_upload_and_export_routes(store,monkeypatch):
    post,run,pack=prepared(store,monkeypatch);app,client,csrf=client_for(store)
    base=f'/g/{NS}/{USER}/p/{post["id"]}'
    response=client.post(base+'/platform-text/import',data={'csrf':csrf,'pack':(io.BytesIO(json.dumps(pack).encode()),'pack.json')})
    assert response.status_code==302 and response.location.endswith('#platform-text-panel')
    form=edit_form(post,pack['posts']);form['csrf']=csrf
    assert client.post(base+'/platform-text',data=form).status_code==302
    exported=client.get(base+'/platform-text.json')
    assert exported.status_code==200 and 'attachment' in exported.headers['Content-Disposition']
    assert exported.json['posts']['medium']['message']==pack['posts']['medium']['message']
    # This download can be loaded again, without local worker/run IDs.
    assert import_pack(store,NS,USER,post['id'],exported.data)==2


def test_long_twitter_import_and_manual_save_are_truncated(store,monkeypatch):
    post,_,pack=prepared(store,monkeypatch)
    message='Edited 🌱 text\n'*30
    pack['posts']['twitter']['message']=message
    import_pack(store,NS,USER,post['id'],json.dumps(pack))
    assert view(store,NS,USER,post['id'])['entries']['twitter']['message']==message[:280]
    save(store,NS,USER,post['id'],edit_form(post,pack['posts']))
    assert versions_for(store,NS,USER,post)['twitter']['message']==message[:280]
    assert store.draft(NS,USER,post['id'])['text']==post['text']


def test_sow_truncates_original_twitter_only_before_approval(store,monkeypatch):
    from allotment.sowing import enqueue
    source=('Long original 🌱\n'*30).rstrip()
    post=store.save_draft(NS,USER,source)
    publish_local(store,NS,USER,post['id'])
    checked=[]
    monkeypatch.setattr('allotment.preparation_approval.require_approval',lambda *args:checked.append(args[-1]['text']))
    job=enqueue(store,NS,USER,post['id'],['twitter'])
    assert checked==[source[:280]]
    assert job['destination_posts']['twitter']['text']==source[:280]
    assert job['destination_posts']['static']['text']==job['post']['text']==source
    assert store.published(NS,USER,post['id'])['text']==source
