from copy import deepcopy
from io import BytesIO
import json

from PIL import Image
import pytest
from werkzeug.datastructures import FileStorage

from allotment.app import create_app
from allotment import images,tags
from allotment.creation import attachment_revision
from allotment.storage import Store,read_yaml
from allotment.website import build_site

NS,USER='the_allotment','oomlout_main'

@pytest.fixture
def store(tmp_path):
    store=Store(tmp_path);store.bootstrap();return store

def image_bytes(size=(800,600),colour=(120,160,200)):
    data=BytesIO();Image.new('RGB',size,colour).save(data,'PNG');data.seek(0);return data

def draft(store):
    return store.save_draft(NS,USER,'A post',[FileStorage(stream=image_bytes(),filename='original.png')])

def recipe():
    return {'engine':'cropperjs-1','crop':{'x':100,'y':0,'width':600,'height':600,'rotate':0,'scaleX':1,'scaleY':1},
            'adjustments':{'brightness':1.1,'contrast':1.1,'saturation':1.1}}

def test_generated_variants_preserve_original_and_are_idempotent(store):
    post=draft(store);folder=store.draft_dir(NS,USER,post['id']);original=folder/post['attachments'][0]['file'];before=original.read_bytes()
    post=images.generate_variants(folder,post,0)
    assert set(post['attachments'][0]['variants'])==set(images.RATIOS)
    for key,v in post['attachments'][0]['variants'].items():
        with Image.open(folder/v['file']) as image:
            a,b=images.RATIOS[key];assert abs(image.width*b-image.height*a)<=max(a,b)
        assert v['needs_review'] and v['source_sha256']==images.digest(original)
    assert images.generate_variants(folder,post,0)==post
    assert original.read_bytes()==before and not store.posts(NS,USER) and not store.jobs(NS,USER)

def test_image_edits_are_versioned_and_all_validated_before_writing(store):
    post=draft(store);folder=store.draft_dir(NS,USER,post['id']);source=folder/post['attachments'][0]['file'];hash=images.digest(source)
    first=images.save_variants(folder,post,0,hash,{'square':image_bytes((300,300))},{'square':recipe()})
    old=first['attachments'][0]['variants']['square']['file'];old_bytes=(folder/old).read_bytes()
    with pytest.raises(ValueError,match='aspect ratio'):
        images.save_variants(folder,first,0,hash,{'square':image_bytes((200,200)),'portrait':image_bytes((300,300))},{'square':recipe(),'portrait':recipe()})
    assert read_yaml(folder/'post.yaml')==first
    second=images.save_variants(folder,first,0,hash,{'square':image_bytes((200,200))},{'square':recipe()})
    assert second['attachments'][0]['variants']['square']['file']!=old
    assert (folder/old).read_bytes()==old_bytes and images.digest(source)==hash
    with pytest.raises(ValueError,match='original changed'):
        images.save_variants(folder,second,0,'bad',{'square':image_bytes((200,200))},{'square':recipe()})

def test_publishing_selects_crop_and_keeps_original_and_other_versions(store):
    post=draft(store);folder=store.draft_dir(NS,USER,post['id']);post=images.generate_variants(folder,post,0)
    original=post['attachments'][0]['file'];square=post['attachments'][0]['variants']['square']['file']
    job=store.publish(NS,USER,post['id'],['static'],variants={'0':'square'})
    public=store.published(NS,USER,post['id']);public_folder=store.repo(NS,USER)/public['path']
    assert public['attachments'][0]['file']==original
    assert job['post']['attachments'][0]['file']==square
    assert all((public_folder/file).is_file() for file in images.media_files(public['attachments'][0]))
    edited=store.save_draft(NS,USER,'Edited later',post_id=post['id'])
    assert all((folder/file).is_file() for file in images.media_files(edited['attachments'][0]))
    output=build_site(store,NS,USER)
    assert square in (output/'index.html').read_text(encoding='utf-8')
    assert (output/public['path']/square).is_file()
    assert store.jobs(NS,USER)[0]['post']['text']=='A post'

def test_tags_rename_move_merge_and_filter_without_republishing(store):
    first=tags.save_tag(store,NS,USER,'Storage','Subjects');second=tags.save_tag(store,NS,USER,'Trays','Objects')
    post=draft(store);post=store.save_draft(NS,USER,post['text'],post_id=post['id'],tags=[first['id'],second['id']])
    job=store.publish(NS,USER,post['id'],['static'])
    tags.save_tag(store,NS,USER,'Organising','Topics',first['id'])
    assert store.published(NS,USER,post['id'])['tags']==[first['id'],second['id']]
    tags.merge_tags(store,NS,USER,second['id'],first['id'])
    assert store.published(NS,USER,post['id'])['tags']==[first['id']]
    assert len(store.jobs(NS,USER))==1 and store.jobs(NS,USER)[0]['revision']==job['revision']
    assert list((store.local/'library-backups').rglob('post.yaml'))
    client=create_app(store.root,testing=True,garden=('the_allotment','oomlout_main')).test_client()
    html=client.get(f'/g/{NS}/{USER}/?tag={first["id"]}').text
    assert 'organising' in html and 'A post' in html
    output=build_site(store,NS,USER)
    assert (output/'tags'/first['id']/'index.html').is_file()

def test_creation_is_independent_of_platform_configuration(store,monkeypatch):
    monkeypatch.setattr(Store,'plugins',lambda self:(_ for _ in ()).throw(AssertionError('Platforms should not be loaded')))
    client=create_app(store.root,testing=True,garden=('the_allotment','oomlout_main')).test_client()
    response=client.get(f'/g/{NS}/{USER}/compose/')
    assert response.status_code==200 and b'name="actions"' not in response.data


def test_tag_usage_counts_a_post_once_when_it_also_has_a_draft(store):
    tag=tags.save_tag(store,NS,USER,'Storage','Subjects')
    post=draft(store)
    store.save_draft(NS,USER,post['text'],post_id=post['id'],tags=[tag['id']])
    store.publish(NS,USER,post['id'],['static'])
    store.save_draft(NS,USER,'Edited copy',post_id=post['id'])
    assert tags.usage(store,NS,USER)[tag['id']]==1


def test_tag_names_normalise_and_migration_preserves_references(store):
    from tools.normalize_tags import normalize
    from allotment.storage import write_yaml
    tag=tags.save_tag(store,NS,USER,'  Workshop   STORAGE ','Subjects')
    assert tag['name']=='workshop_storage'
    assert tags.save_tag(store,NS,USER,'Workshop_storage')['id']==tag['id']
    data=tags.catalogue(store,NS,USER);data['tags'][0]['name']='Workshop Storage'
    write_yaml(store.repo(NS,USER)/'tags.yaml',data)
    post=draft(store);store.save_draft(NS,USER,post['text'],post_id=post['id'],tags=[tag['id']])
    assert normalize(store,NS,USER)==1 and normalize(store,NS,USER)==0
    assert tags.catalogue(store,NS,USER)['tags'][0]['name']=='workshop_storage'
    assert store.draft(NS,USER,post['id'])['tags']==[tag['id']]


def test_edit_image_follows_attachment_after_removing_an_earlier_image(store):
    post=store.save_draft(NS,USER,'Two images',[FileStorage(stream=image_bytes(),filename=name) for name in ('first.png','second.png')])
    client=create_app(store.root,testing=True,garden=('the_allotment','oomlout_main')).test_client()
    client.get(f'/g/{NS}/{USER}/compose/{post["id"]}/')
    with client.session_transaction() as session:csrf=session['csrf']
    response=client.post(f'/g/{NS}/{USER}/save',data={'csrf':csrf,'post_id':post['id'],'text':post['text'],
        'keep_media':post['attachments'][1]['file'],'intent':'image:1'})
    assert response.status_code==302 and response.location.endswith('/0/')
    assert client.get(response.location).status_code==200
    response=client.post(f'/g/{NS}/{USER}/save',data={'csrf':csrf,'post_id':post['id'],'intent':'image:invalid'})
    assert response.status_code==400

def test_crop_api_csrf_stale_editor_and_private_storage(store):
    post=draft(store);folder=store.draft_dir(NS,USER,post['id']);a=post['attachments'][0]
    client=create_app(store.root,testing=True,garden=('the_allotment','oomlout_main')).test_client();base=f'/g/{NS}/{USER}/images/{post["id"]}/0/'
    assert client.get(base).status_code==200
    with client.session_transaction() as session:csrf=session['csrf']
    def payload(token=csrf,revision=attachment_revision(a)):
        return {'csrf':token,'revision':revision,'source_hash':images.digest(folder/a['file']),
                'recipes':json.dumps({'square':recipe()}),'square':(image_bytes((300,300)),'crop.jpg')}
    assert client.post(base+'save',data=payload(token='bad')).status_code==400
    assert client.post(base+'save',data=payload()).status_code==200
    assert client.post(base+'save',data=payload()).status_code==400
    assert not store.posts(NS,USER) and not store.jobs(NS,USER)
    assert client.get(f'/media/{NS}/{USER}/media/variants/crop.jpg').status_code==404

@pytest.mark.parametrize('bad',[float('nan'),float('inf'),-1,999])
def test_invalid_image_adjustments_are_rejected(bad):
    value=recipe();value['adjustments']['brightness']=bad
    with pytest.raises(ValueError):images.validate_recipe(value)


def test_legacy_enrichment_preserves_text_timestamps_originals_and_queue(store):
    from tools.enrich_legacy import enrich
    from allotment.storage import write_yaml
    post=draft(store);job=store.publish(NS,USER,post['id'],['static'])
    public=store.published(NS,USER,post['id']);folder=store.repo(NS,USER)/public.pop('path')
    public['legacy']={'source':'original/archive','cross_post':['twitter']};write_yaml(folder/'post.yaml',public)
    plan={'tags':{'Storage':'Subjects'},'posts':{public['id']:['Storage']}}
    before=(folder/public['attachments'][0]['file']).read_bytes();queue=store.job_path(NS,USER,job['id']).read_bytes()
    report=enrich(store,NS,USER,plan,apply=True)
    changed=read_yaml(folder/'post.yaml')
    for key in ('text','status','created_at','updated_at','published_at','revision','legacy'):assert changed[key]==public[key]
    assert len(changed['attachments'][0]['variants'])==4 and changed['tags']
    assert (folder/public['attachments'][0]['file']).read_bytes()==before
    assert store.job_path(NS,USER,job['id']).read_bytes()==queue and report['originals_unchanged']
    first=(folder/'post.yaml').read_bytes();enrich(store,NS,USER,plan,apply=True)
    assert (folder/'post.yaml').read_bytes()==first
