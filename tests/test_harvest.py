from pathlib import Path
import copy
import pytest
from allotment.harvest import discover,read_thread,recipe_for,valid_url,inspect,approve,approved,signature
from allotment.storage import Store,write_yaml,now
from allotment.job_runs import folder_for,execute_run
from allotment.lifecycle import publish_local

NS,USER='the_allotment','oomlout_main'
TEXT='A post https://example.com/'
ACCOUNT={'expected_account':'test.bsky.social','profile_url':'https://bsky.app/profile/test.bsky.social'}
URL=ACCOUNT['profile_url']+'/post/abc123'

def card(kind='feedItem',author='test.bsky.social',id='abc123',text=TEXT,extra=''):
    return f'<article data-testid="{kind}-by-{author}"><p data-testid="postText">{text}</p><a href="/profile/{author}/post/{id}">Time</a>{extra}</article>'

def test_discovery_requires_exact_text_and_outer_author():
    recipe=recipe_for('bluesky')
    assert list(discover(card(),recipe,ACCOUNT,TEXT))==[URL]
    assert not discover(card(text='Other '+TEXT),recipe,ACCOUNT,TEXT)
    assert not discover(card(author='wrong.test',extra=card()),recipe,ACCOUNT,TEXT)
    assert len(discover(card()+card(id='duplicate'),recipe,ACCOUNT,TEXT))==2
    with pytest.raises(ValueError):valid_url('https://evil.example/profile/test.bsky.social/post/abc123',recipe['base_url'])

def test_reactions_unknowns_are_not_zero_and_replies_are_scoped():
    recipe=recipe_for('bluesky')
    html=card('postThreadItem',extra='<button data-testid="likeBtn" aria-label="Like (1.2K likes)"></button><button data-testid="replyBtn" aria-label="Reply (0 replies)"></button>')+card('postThreadItem',author='friend.test',id='reply1',text='Reply text')
    counts,replies,text=read_thread(html,recipe,URL,TEXT)
    assert counts['likes']['value']==1200 and counts['likes']['approximate']
    assert counts['replies']['value']==0 and counts['views'] is None and counts['interactions'] is None
    assert len(replies)==1 and next(iter(replies.values()))['author']=='friend.test'
    with pytest.raises(ValueError):read_thread(html,recipe,URL,'Different text')

class FakeSession:
    def __init__(self,tmp_path):self.tmp=tmp_path;self.url='';self.expanded=False;self.inputs=[]
    def navigate(self,url,seconds=0):self.url=url;self.inputs.append(('navigate',url))
    def check_owner(self,**kw):pass
    def check_stop(self):pass
    def wait(self,n):pass
    def screenshot(self,**kw):pass
    def hotkey(self,keys):assert keys==['pagedown'];self.inputs.append(('key',keys))
    def find_text(self,**kw):assert kw['text']=='Show more replies'
    def click_text(self,**kw):assert kw['text']=='Show more replies';self.expanded=True;self.inputs.append(('expand',kw['text']))
    def capture_page(self,name):
        if '/post/' not in self.url:html=card()
        else:
            html=card('postThreadItem')
            html+=card('postThreadItem',author='friend.test',id='reply1',text='Expanded reply') if self.expanded else '<button>Show more replies</button>'
        path=self.tmp/(name+'.html');path.write_text(html);return path

def test_read_only_runtime_expands_without_submission(tmp_path):
    session=FakeSession(tmp_path);recipe=recipe_for('bluesky')
    result=inspect(session,recipe,ACCOUNT,TEXT,'reactions')
    assert result['post_id']=='abc123' and result['verified'] and not result['sent']
    assert result['expansions']==1 and len(result['replies'])==1 and result['complete'] is False
    assert all(kind in {'navigate','key','expand'} for kind,value in session.inputs)

def test_harvest_approval_is_explicit_mode_and_signature_bound(tmp_path):
    store=Store(tmp_path);store.bootstrap();write_yaml(store.settings_path(NS,USER),{'destinations':{'bluesky':ACCOUNT}})
    run={'id':'run-fixture','namespace':NS,'user':USER,'kind':'harvest','platform':'bluesky','mode':'ids','test':True,'signature':signature(store,NS,USER,'bluesky'),'created_at':now(),'post_id':'test'}
    folder=folder_for(store,NS,USER,run['id']);write_yaml(folder/'run.yaml',run);write_yaml(folder/'progress.yaml',{'status':'complete','result':{'verified':True}})
    assert not approved(store,NS,USER,'bluesky','ids')
    approve(store,NS,USER,run['id']);assert approved(store,NS,USER,'bluesky','ids') and not approved(store,NS,USER,'bluesky','reactions')
    (folder/'stop.flag').write_text('stop');assert not approved(store,NS,USER,'bluesky','ids')

def test_worker_stop_prevents_harvest_input(tmp_path,monkeypatch):
    store=Store(tmp_path);store.bootstrap();folder=folder_for(store,NS,USER,'run-stop')
    write_yaml(folder/'run.yaml',{'id':'run-stop','kind':'harvest','destinations':['bluesky']});write_yaml(folder/'progress.yaml',{'status':'queued'});(folder/'stop.flag').write_text('stop')
    monkeypatch.setattr('allotment.harvest.run',lambda *args:pytest.fail('Desktop must not start'))
    assert execute_run(store,NS,USER,'run-stop',countdown_seconds=0)['status']=='stopped'


def test_quote_text_cannot_identify_outer_post_and_root_page_needs_no_self_link():
    recipe=recipe_for('bluesky')
    quoted='<a title="Post by test.bsky.social" href="/profile/test.bsky.social/post/quote123"><p data-word-wrap="1">'+TEXT+'</p></a>'
    assert not discover(card(text='Different outer text',extra=quoted),recipe,ACCOUNT,TEXT)
    root=card('postThreadItem').replace('<a href="/profile/test.bsky.social/post/abc123">Time</a>','')
    assert read_thread(root,recipe,URL,TEXT)[2]==TEXT
    with pytest.raises(ValueError):read_thread(card('postThreadItem',id='another123'),recipe,URL,TEXT)


def test_harvest_persists_findings_and_resolves_only_matching_manual_receipt(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from allotment.harvest import run
    from allotment.lifecycle import post_folder,revision
    from allotment.storage import read_yaml
    store=Store(tmp_path/'store');store.bootstrap();write_yaml(store.settings_path(NS,USER),{'destinations':{'bluesky':ACCOUNT}})
    post=store.save_draft(NS,USER,TEXT);publish_local(store,NS,USER,post['id']);post=store.published(NS,USER,post['id'])
    job={'id':'job-fixture','post':post,'actions':{'bluesky':{'status':'prepared','sent':False,'manual_submission_required':True}}}
    write_yaml(store.job_path(NS,USER,job['id']),job)
    @contextmanager
    def context(store,evidence,**kwargs):
        evidence.mkdir(parents=True);yield FakeSession(evidence)
    monkeypatch.setattr('allotment.browser.browser_context',context)
    data={'id':'run-persist','platform':'bluesky','mode':'ids','test':True,'signature':signature(store,NS,USER,'bluesky'),
          'text':TEXT,'post_id':post['id'],'revision':revision(post)}
    result=run(store,NS,USER,data,lambda:None)
    assert result['verified'] and read_yaml(post_folder(store,NS,USER,post['id'])/'harvest/bluesky/id.yaml')['post_id']=='abc123'
    receipt=store.jobs(NS,USER)[0]['actions']['bluesky']
    assert receipt['status']=='succeeded' and receipt['submitted_manually'] and not receipt['manual_submission_required'] and receipt['sent'] is False
    assert not approved(store,NS,USER,'bluesky','ids')
