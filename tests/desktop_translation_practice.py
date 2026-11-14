"""Native AI-query fixture: local prompt submission is allowed, social Send/Post are traps."""
import sys,json,threading,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flask import Flask,request
from werkzeug.serving import make_server
from allotment.storage import Store,write_yaml
from allotment.translation_browser import make_workflow,RECIPE
from allotment.storage import read_yaml
from allotment.roboclick.desktop import Desktop
from allotment.translations import make_prompt,tags
from allotment.test_sample import SAMPLE_TEXT


def fixture(run,result):
    app=Flask(__name__);state={'prompts':0,'social_attempts':0}
    a,b=tags(run);response=a+'\n'+json.dumps(result,ensure_ascii=False)+'\n'+b
    @app.get('/')
    def home():
        return '''<!doctype html><html><head><title>Translation practice</title><style>
body{font:20px Arial;padding:25px;background:white;color:black;max-width:700px;margin:100px auto}#prompt-textarea{border:1px solid #999;padding:20px;min-height:70px;max-height:180px;overflow:auto;white-space:pre-wrap}#prompt-textarea:empty:before{content:'Ask ChatGPT';color:#555}button{margin:15px;padding:15px}article{white-space:pre-wrap}#uploads img{width:110px;height:110px;object-fit:cover}</style></head><body><h1>Translation practice</h1><div id="answers"></div><div id="uploads"></div><div id="prompt-textarea" contenteditable="true"></div><p>Private local fixture</p><button onclick="trap()">Post</button><button onclick="trap()">Send</button><script>
const editor=document.getElementById('prompt-textarea');function trap(){fetch('/trap',{method:'POST'})}
editor.focus();
editor.addEventListener('paste',e=>{const image=[...e.clipboardData.items].find(i=>i.type.startsWith('image/'));if(image){e.preventDefault();const img=document.createElement('img');img.src=URL.createObjectURL(image.getAsFile());document.getElementById('uploads').append(img);fetch('/image',{method:'POST'})}});
editor.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();if(!e.ctrlKey&&!e.shiftKey){history.pushState({},'', '/c/fixture');fetch('/prompt',{method:'POST'});const u=document.createElement('article');u.dataset.messageAuthorRole='user';u.textContent=editor.innerText;document.getElementById('answers').append(u);editor.innerText='';setTimeout(()=>{const a=document.createElement('article');a.dataset.messageAuthorRole='assistant';a.textContent=RESPONSE;document.getElementById('answers').append(a)},1000)}else trap()}});
</script></body></html>'''.replace('RESPONSE',json.dumps(response,ensure_ascii=False))
    @app.post('/prompt')
    def prompt():state['prompts']+=1;return 'ok'
    @app.post('/trap')
    def trapped():state['social_attempts']+=1;return 'blocked',409
    @app.post('/image')
    def image():state['images']=state.get('images',0)+1;return 'ok'
    server=make_server('127.0.0.1',0,app,threaded=True);threading.Thread(target=server.serve_forever,daemon=True).start()
    return server,state


def main():
    root=Path(__file__).resolve().parents[1];folder=root/'.local/translation-practice'/uuid.uuid4().hex;folder.mkdir(parents=True)
    run={'id':'run-'+uuid.uuid4().hex,'source_revision':'fixture','source':{'text':SAMPLE_TEXT},'destinations':['twitter'],
         'catalog':{'twitter':{'name':'Twitter','max_chars':10000,'ready':True}},'images':[str(root/'allotment/static/test-image.png')],'context':'Disposable test'}
    result={'schema_version':'2.0','run_id':run['id'],'source_revision':'fixture','source':{'text':SAMPLE_TEXT},'destinations':['twitter'],
        'posts':{'twitter':{'title':None,'message':SAMPLE_TEXT,'tags':[],'strategy':'Original source','editorial_status':'draft','publication_status':'not_published'}},
        'image':{'alt_text':None},'questions':[],'delivery_notes':[]}
    (folder/'prompt.txt').write_text(make_prompt(run,'Local fixture guidance.\n'*200),encoding='utf-8')
    server,state=fixture(run,result);recipe=read_yaml(RECIPE);recipe.update(url=f'http://127.0.0.1:{server.server_port}/')
    for step in recipe['steps']:
        if 'poll' in step:step['poll'].update(timeout=30,interval=2)
    session=Desktop(folder/'evidence')
    try:
        session.attach();session.navigate(recipe['url'])
        workflow=make_workflow(session,recipe,run,folder,lambda **kw:print(kw,flush=True))
        captured=workflow.run(recipe['steps'])['result']
        assert captured['source']['text']==SAMPLE_TEXT
        assert state=={'prompts':1,'social_attempts':0,'images':1}
        write_yaml(folder/'result.yaml',{'state':state,'captured':captured})
        print('PASS: one local AI prompt, captured tagged Unicode JSON, zero social Post/Send attempts.',flush=True)
        print(folder,flush=True)
    finally:session.release();server.shutdown()

if __name__=='__main__':main()
