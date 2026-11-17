from io import BytesIO
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlparse, unquote
import zipfile
import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage
from allotment.storage import Store, write_yaml
from allotment.lifecycle import publish_local
from allotment.website import build_site, follow_links
from allotment.app import create_app

NS, USER = 'the_allotment', 'oomlout_main'

@pytest.fixture
def store(tmp_path):
    result=Store(tmp_path);result.bootstrap();return result

def make_post(store, text='A published post https://example.com/project'):
    image=BytesIO();Image.new('RGB',(1500,900),'teal').save(image,'PNG');image.seek(0)
    draft=store.save_draft(NS,USER,text,[FileStorage(image,filename='image.png')])
    publish_local(store,NS,USER,draft['id'])
    return store.published(NS,USER,draft['id'])

def test_portable_zip_contains_only_public_site_and_keeps_originals(store):
    post=make_post(store)
    store.save_draft(NS,USER,'PRIVATE DRAFT SECRET')
    source=store.repo(NS,USER)/post['path']/post['attachments'][0]['file']
    original=source.read_bytes()
    output=build_site(store,NS,USER)
    dist=store.root/'website_static'/'dist'
    assert (dist/'index.html').read_bytes()==(output/'index.html').read_bytes()
    with zipfile.ZipFile(store.root/'website_static'/'the_allotment.zip') as archive:
        names=archive.namelist()
        assert 'index.html' in names and 'follow/index.html' in names
        assert f'p/{post["id"]}/index.html' in names
        assert not any(n.startswith(('dist/','.local/','.git/')) or n.endswith('.yaml') for n in names)
        assert all(b'PRIVATE DRAFT SECRET' not in archive.read(n) for n in names if n.endswith('.html'))
        assert archive.testzip() is None
    assert source.read_bytes()==original
    assert list(dist.rglob('*.preview.webp'))
    with Image.open(next(dist.rglob('*.preview.webp'))) as image:
        assert max(image.size)<=960
    assert 'preview' not in store.published(NS,USER,post['id'])['attachments'][0]


def test_follow_uses_public_preferences_and_current_url_without_private_targets(store):
    profile=store.profile(NS,USER)
    profile['social']={'whatsapp_channel':'https://whatsapp.com/channel/old','patreon':'https://patreon.com/private',
                       'unknown':'javascript:alert(1)'}
    write_yaml(store.repo(NS,USER)/'profile.yaml',profile)
    write_yaml(store.settings_path(NS,USER),{'destinations':{
        'whatsapp_channel':{'profile_url':'https://whatsapp.com/channel/new','publish_profile_link':True},
        'patreon':{'profile_url':'https://patreon.com/private','publish_profile_link':False},
        'discord':{'target_url':'https://discord.com/channels/secret','browser_profile_url':'https://example.com/private'}}})
    result=follow_links(store,NS,USER,profile)
    assert [link['url'] for link in result]==['https://whatsapp.com/channel/new']
    output=build_site(store,NS,USER)
    text=(output/'follow/index.html').read_text(encoding='utf-8')
    assert 'channel/new' in text and 'channel/old' not in text and 'secret' not in text and 'patreon.com' not in text


def test_all_relative_links_and_assets_resolve_and_text_is_escaped(store):
    make_post(store,'<script>alert("x")</script> https://example.com/link')
    output=build_site(store,NS,USER)
    class Links(HTMLParser):
        def handle_starttag(self,tag,attrs):
            for key,value in attrs:
                if key in {'src','href'} and not urlparse(value).scheme and not value.startswith('#'):
                    assert (self.path.parent/unquote(value)).is_file(), (self.path,value)
    for path in output.rglob('*.html'):
        parser=Links();parser.path=path;text=path.read_text(encoding='utf-8');parser.feed(text)
        assert '<script>' not in text
        assert '<script src=' not in text
    assert '&lt;script&gt;' in (output/'index.html').read_text(encoding='utf-8')


def test_rebuild_removes_stale_pages_and_download_matches_selected_garden(store):
    make_post(store)
    build_site(store,NS,USER)
    dist=store.root/'website_static/dist';(dist/'stale.html').write_text('stale')
    build_site(store,NS,'allotment_main')
    assert not (dist/'stale.html').exists()
    assert not list((dist/'p').glob('*/index.html'))
    client=create_app(store.root,testing=True,garden=(NS,USER)).test_client()
    response=client.get(f'/g/{NS}/{USER}/website/download')
    assert response.status_code==200 and 'the_allotment.zip' in response.headers['Content-Disposition']
    with zipfile.ZipFile(BytesIO(response.data)) as archive:
        assert b'A published post' in archive.read('index.html')
    assert (dist/'index.html').read_bytes()==(store.output/NS/USER/'index.html').read_bytes()


def test_theme_overrides_and_video_export(store):
    post=store.save_draft(NS,USER,'A video',[FileStorage(BytesIO(b'fixture video'),filename='clip.mp4')])
    publish_local(store,NS,USER,post['id'])
    profile=store.profile(NS,USER);profile['theme'].update(custom_css=':root { --accent: #123456; }',accent='#00b1b7')
    write_yaml(store.repo(NS,USER)/'profile.yaml',profile)
    output=build_site(store,NS,USER);html=(output/'index.html').read_text(encoding='utf-8')
    assert '<video controls preload="metadata" playsinline' in html
    assert html.index('assets/style.css')<html.index('assets/theme.css')<html.index('assets/custom.css')
    assert '--accent: #123456' in (output/'assets/custom.css').read_text()
    assert '--accent: #00b1b7' in (output/'assets/theme.css').read_text()

def test_export_browses_offline_and_under_a_subdirectory_at_mobile_and_desktop(store, tmp_path):
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from playwright.sync_api import sync_playwright
    make_post(store,'First post '+('readable text '*100))
    make_post(store,'Second post')
    profile=store.profile(NS,USER);profile['theme'].update(layout='cards',width=1120)
    profile['social']={'bluesky':'https://bsky.app/profile/example.test'}
    write_yaml(store.repo(NS,USER)/'profile.yaml',profile)
    build_site(store,NS,USER)
    hosted=tmp_path/'hosting'/'allotment';hosted.mkdir(parents=True)
    with zipfile.ZipFile(store.root/'website_static/the_allotment.zip') as archive:archive.extractall(hosted)
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(hosted.parent)))
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            page=browser.new_page(java_script_enabled=False)
            errors=[]
            page.on('response',lambda response:errors.append(response.url) if response.status>=400 else None)
            for width in (320,390,1440):
                page.set_viewport_size({'width':width,'height':900})
                for base in (hosted.as_uri()+'/',f'http://127.0.0.1:{server.server_port}/allotment/'):
                    page.goto(base+'index.html')
                    assert page.locator('.post').count()==2
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.locator('.media-grid a').first.click()
                    assert '/p/' in page.url and page.locator('.feed-single').count()==1
                    assert page.locator('h1').is_visible()
                    page.get_by_role('navigation',name='Main navigation').get_by_role('link',name='Follow').click()
                    assert page.locator('.follow-card').count()==1
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert page.locator('.platform-logo').evaluate('(img)=>img.complete && img.naturalWidth > 0')
            assert errors==[]
            browser.close()
    finally:
        server.shutdown();server.server_close();thread.join()
