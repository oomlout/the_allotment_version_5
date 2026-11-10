from io import BytesIO
import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage
from allotment.storage import Store,read_yaml,write_yaml
from allotment.app import create_app
from allotment.lifecycle import publish_local,unpublish_local,post_folder
from allotment.sowing import enqueue
from allotment.publication import run_worker
from allotment.framing import save_defaults

NS,USER='the_allotment','oomlout_main'

@pytest.fixture
def store(tmp_path):
    store=Store(tmp_path);store.bootstrap();return store

def new_post(store):
    image=BytesIO();Image.new('RGB',(640,480),'orange').save(image,'PNG');image.seek(0)
    return store.save_draft(NS,USER,'A local post',[FileStorage(stream=image,filename='sample.png')])

def test_local_lifecycle_is_separate_and_preserves_harvest(store):
    post=new_post(store)
    with pytest.raises(ValueError,match='Publish'):enqueue(store,NS,USER,post['id'],['static'])
    publish_local(store,NS,USER,post['id'])
    assert not store.jobs(NS,USER) and not store.drafts(NS,USER)
    folder=post_folder(store,NS,USER,post['id']);write_yaml(folder/'harvest/bluesky/id.yaml',{'post_id':'abc'})
    unpublish_local(store,NS,USER,post['id'])
    assert not store.posts(NS,USER) and store.drafts(NS,USER)
    assert read_yaml(post_folder(store,NS,USER,post['id'])/'harvest/bluesky/id.yaml')['post_id']=='abc'
    publish_local(store,NS,USER,post['id']);assert not store.jobs(NS,USER)

def test_sowing_frames_snapshot_without_changing_original_and_exports(store):
    post=new_post(store);publish_local(store,NS,USER,post['id']);folder=post_folder(store,NS,USER,post['id'])
    before=(folder/'post.yaml').read_bytes();save_defaults(store,NS,USER,{'static':'portrait'})
    job=enqueue(store,NS,USER,post['id'],['static','summary'])
    attachment=job['destination_posts']['static']['attachments'][0]
    assert attachment['file']!=attachment['original_file'] and attachment['display_variant']=='portrait'
    assert (folder/'post.yaml').read_bytes()==before
    results=run_worker(store,NS,USER,job['id'])
    assert all(r['status']=='succeeded' for r in results[0]['actions'].values())
    output=store.output/NS/USER
    assert (output/'summary.md').is_file() and attachment['file'] in (output/'index.html').read_text(encoding='utf-8')
    assert (folder/'post.yaml').read_bytes()==before
    from allotment.website import build_site
    build_site(store,NS,USER)
    assert (output/'summary.md').is_file() and attachment['file'] in (output/'index.html').read_text(encoding='utf-8')

def test_overrides_and_stale_static_snapshot(store):
    post=new_post(store);publish_local(store,NS,USER,post['id'])
    job=enqueue(store,NS,USER,post['id'],['static'],{'static':{'framing':'square'}})
    assert job['actions']['static']['framing']=='square'
    store.save_draft(NS,USER,'Changed',post_id=post['id']);publish_local(store,NS,USER,post['id'])
    result=run_worker(store,NS,USER,job['id'])[0]
    assert result['actions']['static']['status']=='failed' and not store.output.joinpath(NS,USER,'index.html').exists()

def test_single_garden_choice_persists_and_rejects_other_account(store):
    app=create_app(store.root,testing=True);client=app.test_client()
    assert client.get('/').location.endswith('/choose-garden/')
    client.get('/choose-garden/')
    with client.session_transaction() as session:csrf=session['csrf']
    assert client.post('/choose-garden/',data={'csrf':csrf,'garden':NS+'/'+USER}).status_code==302
    assert client.get('/g/'+NS+'/'+USER+'/').status_code==200
    assert client.get('/g/'+NS+'/allotment_main/').status_code==404
    assert client.get('/setup/').status_code==404
    assert create_app(store.root,testing=True).config['GARDEN']==(NS,USER)
    html=client.get('/g/'+NS+'/'+USER+'/').text
    assert 'Add account' not in html and 'garden-switcher' not in html

def test_post_page_separates_local_publish_sow_and_overrides(store):
    post=new_post(store);client=create_app(store.root,testing=True,garden=(NS,USER)).test_client();base=f'/g/{NS}/{USER}'
    html=client.get(base+'/p/'+post['id']+'/').text
    from bs4 import BeautifulSoup
    soup=BeautifulSoup(html,'html.parser')
    assert 'Publish locally' in html and 'Overrides for this post' in html and soup.select_one('[data-launch]').has_attr('disabled')
    assert not soup.select_one('[name="actions"][value="git"]').has_attr('disabled')
    for row in soup.select('[data-destination]'):
        if row['data-destination']=='git':continue
        assert row.select_one('[name="actions"]').has_attr('disabled')
        notice=row.select_one('[data-testing-required]')
        assert not notice.has_attr('hidden') and 'Platform needs testing.' in notice.text
        assert notice.a['href'].endswith('#destination-'+row['data-destination'])
    with client.session_transaction() as session:csrf=session['csrf']
    client.post(base+'/p/'+post['id']+'/local/publish',data={'csrf':csrf})
    assert not store.jobs(NS,USER)
    client.application.extensions['job_manager']._launch=lambda *args:'run-fixture'
    response=client.post(base+'/p/'+post['id']+'/launch',data={'csrf':csrf,'actions':'static','frame_static':'square'})
    assert response.status_code==302 and store.jobs(NS,USER)[0]['kind']=='sow'


def test_post_list_excludes_automatic_and_unused_destinations(store):
    from bs4 import BeautifulSoup
    post=new_post(store)
    client=create_app(store.root,testing=True,garden=(NS,USER)).test_client()
    soup=BeautifulSoup(client.get(f'/g/{NS}/{USER}/p/{post["id"]}/').text,'html.parser')
    offered={node['value'] for node in soup.select('[name="actions"]')}
    assert not offered & {'static','summary','facebook','telegram','whatsapp'}
    assert {'facebook_business','telegram_channel','whatsapp_channel','git'}<=offered
    assert not soup.select('[name="frame_static"], [name="frame_summary"]')


def test_every_launch_includes_website_and_summary_even_without_optional_choices(store):
    post=new_post(store);publish_local(store,NS,USER,post['id'])
    job=enqueue(store,NS,USER,post['id'],[])
    assert set(job['actions'])=={'static','summary'}
    run_worker(store,NS,USER,job['id'])
    assert (store.output/NS/USER/'index.html').exists()
    assert (store.output/NS/USER/'summary.md').exists()
    for action in ['facebook','telegram','whatsapp']:
        with pytest.raises(ValueError,match='available sowing'):enqueue(store,NS,USER,post['id'],[action])
    job=enqueue(store,NS,USER,post['id'],['git'])
    assert set(job['actions'])=={'git','static','summary'}
