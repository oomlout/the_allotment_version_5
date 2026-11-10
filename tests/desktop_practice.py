"""Opt-in real desktop walkthrough: python -m tests.desktop_practice is not needed;
run `python tests/desktop_practice.py` from the checkout. Uses ordinary Chrome,
real keyboard/mouse, local OCR, and rendered-page clipboard capture. No-send loopback fixture: Post and page Enter are traps.
"""
from pathlib import Path
import base64
import copy
import html
import json
import sys
import threading
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flask import Flask, request, send_file
from werkzeug.serving import make_server
from allotment.browser import browser_context, run_recipe
from allotment.storage import Store, write_yaml
from allotment.test_sample import SAMPLE_TEXT, SAMPLE_IMAGE


STYLE = '''body{font:18px Arial;margin:0;color:#111;background:#fff}
aside{position:fixed;left:calc(50% - 510px);top:170px;width:160px}
button{font:700 17px Arial;border:0;border-radius:20px;padding:12px 24px;background:#007aff;color:white;cursor:pointer}
main{width:600px;margin:50px auto}h1{margin-bottom:10px}article{border-top:1px solid #bbb;padding:20px 0}
article img{width:540px}#composer{position:fixed;top:12%;left:calc(50% - 310px);width:580px;padding:20px;background:white;border:2px solid #222;border-radius:20px;box-shadow:0 0 0 100vmax #0005}
#composer header{display:flex;justify-content:space-between;align-items:center}textarea{font:20px Arial;width:550px;height:120px;border:0;margin-top:25px;resize:none}#preview{display:block;width:550px;max-height:400px;object-fit:contain}'''


def practice_server(folder):
    app = Flask(__name__)
    import shutil
    shutil.copyfile(SAMPLE_IMAGE,folder/"previous.png")
    posts = [{"id":"previous","text":"Earlier post with the same image"}]
    state = {"attempts": 0, "dismissals": 0, "drafts": {}}
    @app.get('/profile/practice.test')
    def profile():
        feed = ''.join(f'<article data-testid="feedItem-by-practice.test"><span>@practice.test</span>'
                       f'<p data-testid="postText">{html.escape(post["text"])}</p>'
                       f'<div data-testid="contentHider-post"><img src="/image/{post["id"]}.png"></div><a href="/profile/practice.test/post/{post["id"]}">View post</a></article>' for post in posts)
        return '<!doctype html><html><head><meta charset="utf-8"><title>Allotment practice</title><style>'+STYLE+'</style></head><body>'''+'''
        <aside><button onclick="document.querySelector('#composer').hidden=false">New post</button></aside>
        <main><button data-testid="profileHeaderEditProfileButton" style="float:right">Edit profile</button><h1>Practice account</h1><p>@practice.test</p>'''+feed+'''</main>
        <section id="composer" hidden><header><strong>New post</strong><button id="post">Post</button></header>
        <textarea placeholder="What's up?"></textarea><img id="preview" hidden></section>
        <script>const draftId=crypto.randomUUID();let picture='';const editor=document.querySelector('textarea');
        editor.addEventListener('paste',event=>{for(const item of event.clipboardData.items){if(item.type.startsWith('image/')){
        event.preventDefault();const reader=new FileReader();reader.onload=()=>{picture=reader.result;const preview=document.querySelector('#preview');preview.src=picture;preview.hidden=false;saveDraft()};reader.readAsDataURL(item.getAsFile());}}});
        function saveDraft(){fetch('/draft',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:draftId,text:editor.value,image:picture})})}
        editor.addEventListener('input',saveDraft);
        document.querySelector('#post').onclick=()=>fetch('/publish',{method:'POST'});
        document.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();fetch('/publish',{method:'POST'})}});
        document.addEventListener('keydown',event=>{if(event.key==='Escape' && !document.querySelector('#composer').hidden){document.querySelector('#composer').hidden=true;fetch('/dismiss',{method:'POST'})}});
        </script></body></html>'''
    @app.post('/publish')
    def publish():
        state['attempts'] += 1
        write_yaml(folder / 'send-attempts.yaml', state)
        return {'error':'SEND TRAP'}, 409
    @app.post('/draft')
    def draft():
        data=request.get_json()
        state['drafts'][data['id']]={'text':data['text'],'image':bool(data.get('image'))}
        return {'ok':True}
    @app.post('/dismiss')
    def dismiss():
        state['dismissals'] += 1
        return {'ok':True}
    @app.get('/image/<post_id>.png')
    def image(post_id):
        return send_file(folder / f'{post_id}.png')
    server=make_server('127.0.0.1',0,app,threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    return server, state


def main():
    checkout=Path(__file__).resolve().parents[1]
    folder=checkout / '.local' / 'roboclick-walkthrough' / ('practice-'+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    server,state=practice_server(folder)
    store=Store(folder / 'studio')
    recipe=copy.deepcopy(Store(checkout).plugins()['bluesky'])
    base=f'http://127.0.0.1:{server.server_port}'
    recipe.update(id='practice',base_url=base)
    account={'expected_account':'practice.test','profile_url':base+'/profile/practice.test'}
    post={'id':'practice','path':'.','text':SAMPLE_TEXT+'\n\n[Local practice '+folder.name+']',
          'attachments':[{'file':'test-image.png','type':'image'}]}
    import shutil
    shutil.copyfile(SAMPLE_IMAGE,folder / 'test-image.png')
    try:
        results=[]
        for index in range(2):
            evidence=folder / str(index+1)
            with browser_context(store,evidence) as desktop:
                if index==0:
                    from allotment.launch import countdown_in_tab
                    def countdown_update(**values):
                        assert not state['drafts'] and state['attempts']==0
                        print('Tab open; countdown',values['remaining'],flush=True)
                    countdown_in_tab(desktop,evidence,account['profile_url'],countdown_update)
                result=run_recipe(desktop,recipe,account,post,folder,
                                  lambda:(_ for _ in ()).throw(AssertionError('Submission callback invoked')),
                                  testing=True, prepare=False,
                                  on_step=lambda i,s,p:print(i+1,s['command'],p,flush=True))
                result['bounds']=desktop.bounds()
            assert result['prepared'] and result['draft_checked'] and not result['sent']
            results.append(result)
        assert state['attempts']==0 and state['dismissals']==0 and len(state['drafts'])==2
        assert all(d['text']==post['text'] and d['image'] for d in state['drafts'].values())
        write_yaml(folder/'result.yaml',{'results':results,**state})
        print('PASS: two separate draft tabs, top-left snap, text/image checked, zero Post or Enter attempts.',flush=True)
        print(folder)
    finally:
        server.shutdown()

if __name__=='__main__':
    main()
