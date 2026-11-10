"""Opt-in native Chrome harvest fixture. No platform traffic; Post/Send/Enter are traps."""
from pathlib import Path
import sys,threading,uuid,time,html
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flask import Flask
from werkzeug.serving import make_server
from allotment.storage import Store,write_yaml
from allotment.browser import browser_context
from allotment.harvest import recipe_for,inspect
from allotment.test_sample import SAMPLE_TEXT


def practice_server(folder):
    app=Flask(__name__);state={'attempts':0,'expansions':0}
    def post(author,id,text,extra='',kind='postThreadItem'):
        link=f'<a href="/profile/{author}/post/{id}">7 Nov 2026</a>' if kind=='feedItem' or id!='abc123' else '<span>7 Nov 2026</span>'
        return f'<article data-testid="{kind}-by-{author}"><strong>@{author}</strong><p data-testid="postText">{html.escape(text)}</p>{link}{extra}</article>'
    def page(content):
        return '<!doctype html><html><head><meta charset="utf-8"><title>Allotment harvest practice</title><style>body{font:18px Arial;margin:20px;background:white;color:#111}main{max-width:650px;margin:auto}button{font:bold 18px Arial;padding:12px 24px;margin:5px;border:0;border-radius:20px;background:#007aff;color:white}article{padding:12px 0;border-bottom:1px solid #ccc}p{margin:8px 0}</style></head><body><main>'+content+"""<button onclick="trap()">Post</button><button onclick="trap()">Send</button></main><script>function trap(){fetch('/trap',{method:'POST'})}document.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();trap()}});function expand(){document.querySelector('#more').hidden=true;document.querySelector('#reply').hidden=false;fetch('/expand',{method:'POST'})}</script></body></html>"""
    @app.get('/profile/practice.test')
    def profile():
        return page('<button data-testid="profileHeaderEditProfileButton">Edit profile</button><h1>Practice</h1><p>@practice.test</p>'+post('practice.test','abc123',SAMPLE_TEXT,kind='feedItem')+post('practice.test','older123','Older unrelated post',kind='feedItem'))
    @app.get('/profile/practice.test/post/abc123')
    def root():
        return page(post('practice.test','abc123',SAMPLE_TEXT,'<span data-testid="likeCount-expanded">12 likes</span> <span data-testid="repostCount-expanded">3 reposts</span> <span data-testid="quoteCount-expanded">2 quotes</span> <span data-testid="viewCount">104 views</span> <button data-testid="replyBtn" aria-label="Reply (1 reply)">Reply</button>')+'<button id="more" onclick="expand()">Show more replies</button><div id="reply" hidden>'+post('friend.test','reply123','A reply expanded by the fixture')+'<a aria-label="Read more replies" href="/profile/friend.test/post/reply123">Read more replies</a></div>')
    @app.get('/profile/friend.test/post/reply123')
    def branch():
        return page(post('friend.test','reply123','A reply expanded by the fixture')+post('other.test','nested123','A nested reply on another page'))
    @app.post('/trap')
    def trap():state['attempts']+=1;return {'ok':False},409
    @app.post('/expand')
    def expand():state['expansions']+=1;return {'ok':True}
    server=make_server('127.0.0.1',0,app,threaded=True);threading.Thread(target=server.serve_forever,daemon=True).start()
    return server,state


def main():
    root=Path(__file__).resolve().parents[1];folder=root/'.local/harvest-work'/('native-'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True)
    server,state=practice_server(folder);store=Store(folder/'studio');recipe=recipe_for('bluesky')
    base=f'http://127.0.0.1:{server.server_port}';recipe.update(base_url=base,profile_pages=2,max_pages=3,max_scrolls=5)
    account={'expected_account':'practice.test','profile_url':base+'/profile/practice.test'}
    try:
        print('Local harvest fixture starts in 8 seconds. Leave Chrome and input idle; F8 stops.',flush=True)
        time.sleep(8)
        with browser_context(store,folder/'ids') as session:ids=inspect(session,recipe,account,SAMPLE_TEXT)
        assert ids['post_id']=='abc123' and ids['verified'] and not ids['sent']
        with browser_context(store,folder/'reactions') as session:reactions=inspect(session,recipe,account,SAMPLE_TEXT,'reactions',ids['url'])
        assert reactions['counts']['likes']['value']==12 and reactions['counts']['views']['value']==104
        assert len(reactions['replies'])==2 and {r['author'] for r in reactions['replies']}=={'friend.test','other.test'}
        assert len(reactions['pages_visited'])==2
        assert state['attempts']==0 and state['expansions']==1
        write_yaml(folder/'result.yaml',{'ids':ids,'reactions':reactions,**state})
        print('PASS: native profile discovery, post ID, expanded reply and metrics; zero Post/Send/Enter attempts.',flush=True);print(folder,flush=True)
    finally:server.shutdown()

if __name__=='__main__':main()
