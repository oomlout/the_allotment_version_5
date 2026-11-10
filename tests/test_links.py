import pytest
from allotment import links
from allotment.app import create_app
from allotment.storage import Store
from allotment.website import build_site


@pytest.mark.parametrize('url',['javascript:alert(1)','file:///etc/passwd','https://a:b@example.com','https://example.com:8000/','https://example.com/\nheader','https://example.com\\@localhost'])
def test_reject_invalid_links(url):
    with pytest.raises(ValueError):links.clean_url(url)


def test_metadata_and_repository_validation(monkeypatch):
    monkeypatch.setattr(links,'download',lambda url:(b'<html><title>Fallback</title><meta property="og:title" content="A &amp; B"><meta name="description" content="Project details"></html>',url))
    repo,items=links.prepare_links('https://github.com/oomlout/project.git',['https://example.com/article','https://example.com/article'])
    assert repo=='https://github.com/oomlout/project' and len(items)==2
    assert items[0]['kind']=='github' and items[1]['title']=='A & B' and items[1]['description']=='Project details'
    monkeypatch.setattr(links,'download',lambda url:(_ for _ in ()).throw(AssertionError('Cached URLs must not refetch')))
    assert links.prepare_links(repo,['https://example.com/article'],items)[1]==items
    with pytest.raises(ValueError):links.github_url('https://github.com/oomlout/project/issues')


def test_private_destination_and_redirect_are_blocked(monkeypatch):
    monkeypatch.setattr(links.socket,'getaddrinfo',lambda host,port,**kw:[(None,None,None,None,('93.184.215.14' if host=='example.com' else '127.0.0.1',port))])
    calls=[]
    class Sock:
        def close(self):pass
    monkeypatch.setattr(links.socket,'create_connection',lambda address,**kw:calls.append(address) or Sock())
    class Response:
        status=302
        def getheader(self,name,default=None):return 'http://localhost/private' if name=='Location' else default
    class Connection:
        def __init__(self,*args,**kw):pass
        def request(self,*args,**kw):pass
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setattr(links.http.client,'HTTPConnection',Connection)
    result=links.metadata('http://example.com/')
    assert result['status']=='unavailable' and calls==[('93.184.215.14',80)]
    with pytest.raises(ValueError):links.public_address('localhost',80)


def test_link_failure_keeps_url(monkeypatch):
    monkeypatch.setattr(links,'download',lambda url:(_ for _ in ()).throw(OSError('Unavailable')))
    result=links.metadata('https://example.com/')
    assert result['url']=='https://example.com/' and result['status']=='unavailable'


def test_save_fetches_cards_and_static_build_keeps_them(tmp_path,monkeypatch):
    monkeypatch.setattr(links,'download',lambda url:(b'<title>Useful project</title><meta name="description" content="A description">',url))
    store=Store(tmp_path);store.bootstrap();client=create_app(tmp_path,testing=True,garden=('the_allotment','oomlout_main')).test_client()
    base='/g/the_allotment/oomlout_main'
    client.get(base+'/compose/')
    with client.session_transaction() as session:csrf=session['csrf']
    payload={'csrf':csrf,'text':'','github_repo':'https://github.com/oomlout/project','links_present':'1','links':['https://example.com/']}
    response=client.post(base+'/save',data=payload,headers={'Accept':'application/json'})
    post_id=response.json['post_id']
    assert store.draft('the_allotment','oomlout_main',post_id)['links'][0]['status']=='pending'
    payload['post_id']=post_id
    assert client.post(base+'/save',data=payload).status_code==302
    post=store.draft('the_allotment','oomlout_main',post_id)
    assert all(link['title']=='Useful project' for link in post['links']) and not store.jobs('the_allotment','oomlout_main')
    store.publish('the_allotment','oomlout_main',post_id,['static'])
    output=build_site(store,'the_allotment','oomlout_main')
    html=(output/'index.html').read_text(encoding='utf-8')
    assert 'Useful project' in html and 'GitHub repository' in html and 'A description' in html
    payload['github_repo']='';payload['links']=[]
    client.post(base+'/save',data=payload)
    assert store.draft('the_allotment','oomlout_main',post_id)['links']==[]
