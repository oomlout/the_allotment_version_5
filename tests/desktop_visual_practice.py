"""Native screenshot/file-picker draft checks. All page Enter/Send clicks are traps."""
from pathlib import Path
import shutil
import sys
import threading
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flask import Flask, request
from werkzeug.serving import make_server
from allotment.preparation import preparation_context, run_recipe
from allotment.storage import Store, write_yaml
from allotment.test_sample import SAMPLE_IMAGE, SAMPLE_TEXT


def recipe(base, picker=False):
    steps = [
        {'command':'prepare_navigate','url':base+'/right','seconds':1},
        {'command':'prepare_visual_identity','region':[.2,.12,.7,.25], 'owner_text':'Edit profile',
         'owner_region':[.2,.12,.7,.25],'profile_url':base+'/right'},
        {'command':'prepare_navigate','url':base+'/editor','seconds':1},
        {'command':'prepare_visual_focus','text':'Write a draft','region':[.2,.25,.8,.4]},
        {'command':'prepare_text'}, {'command':'prepare_verify_text','select_all':2},
    ]
    steps += ([{'command':'prepare_open','text':'Choose files','region':[.29,.4,.37,.46],'file_picker':True},
               {'command':'prepare_upload_image'}] if picker else [{'command':'prepare_image'}])
    steps += ([{'command':'prepare_preview_zoom','percent':200}] if picker else [])
    steps += [{'command':'prepare_finish','region':[0,.1,1,.98], 'reference_region':[.25,.25,.75,.75],
               'generated_draft_path':'^/p/[0-9a-f]{12}/edit$', 'generated_draft_wait':12}]
    return {'id':'fixture','name':'Visual fixture','engine':'oomlout_roboclick_prepare','sending_enabled':False,
            'preparation_ready':True,'base_url':base,'max_chars':1000,'steps':steps}


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--paste-only',action='store_true',help='Check clipboard preparation and navigation recovery without a native file picker')
    args=parser.parse_args()
    app=Flask(__name__); sends=[]; sizes=[]
    @app.get('/right')
    @app.get('/editor')
    def page():
        return '''<html><head><title>Allotment visual fixture</title><style>
        body{font:22px Arial;margin:60px auto;width:800px;background:#eee}
        #editor{height:110px;padding:16px;margin-top:50px;background:white;white-space:pre-wrap}
        #editor:empty:before{content:'Write a draft';color:#555}button{font:20px Arial;margin:18px;padding:10px;border:0;background:#007aff;color:white}
        img{display:block;width:650px;height:366px;object-fit:contain}
        @media(max-width:1000px){img{position:fixed;top:170px;left:20px;width:430px;height:242px}}
        </style></head><body><p>Signed in as @right</p><a href='/right'>Edit profile</a>
        <button style='position:absolute;left:800px;top:140px;margin:0'
          onclick="navigator.clipboard.writeText('https://example.com/channel/right')">Copy link</button>
        <div id=editor contenteditable=true></div><button onclick="file.click()">Choose files</button>
        <input id=file type=file accept='image/*' hidden><button id=send>Send</button><img id=preview>
        <script>
        const show=f=>{const r=new FileReader();r.onload=()=>preview.src=r.result;r.readAsDataURL(f);};
        // A page shortcut must never receive the browser's zoom reset.
        document.addEventListener('keydown',e=>{if(e.ctrlKey && e.key==='0'){trap('page Ctrl+0');}});
        let saveTimer;
        editor.oninput=()=>{if(!saveTimer)saveTimer=setTimeout(()=>history.replaceState(null,'','/p/123456789abc/edit'),20000);};
        file.onchange=()=>show(file.files[0]);editor.onpaste=e=>{for(const i of e.clipboardData.items)
          if(i.type.startsWith('image/')){e.preventDefault();show(i.getAsFile());}};
        const trap=why=>fetch('/trap',{method:'POST',body:why});send.onclick=()=>trap('Send');
        document.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();trap('Enter');}};
        const measure=()=>fetch('/size',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({width:innerWidth,ratio:devicePixelRatio})});
        addEventListener('resize',measure);measure();
        </script></body></html>'''
    @app.post('/trap')
    def trap(): sends.append(request.get_data(as_text=True));return '',400
    @app.post('/size')
    def size(): sizes.append(request.get_json());return '',204
    server=make_server('127.0.0.1',0,app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    root=Path(__file__).resolve().parents[1]
    folder=root/'.local/roboclick-walkthrough'/('visual-'+uuid.uuid4().hex[:8]);folder.mkdir(parents=True)
    shutil.copyfile(SAMPLE_IMAGE,folder/'image.png')
    post={'path':'.','text':SAMPLE_TEXT,'attachments':[{'type':'image','file':'image.png'}]}
    base=f'http://127.0.0.1:{server.server_port}';results={}
    try:
        for picker in ((False,) if args.paste_only else (False,True)):
            sizes.clear()
            with preparation_context(Store(folder/'studio'),folder/str(picker)) as desktop:
                def on_step(i,s,p):
                    print(i,s['command'],p,flush=True)
                    if s['command']=='prepare_visual_identity' and p=='after':
                        desktop.verify_public_link([.40,.20,.5,.27])
                result=run_recipe(desktop,recipe(base,picker),
                                  {'expected_account':'right','profile_url':'https://example.com/channel/right'},
                                  post,folder,on_step=on_step)
            assert result['prepared'] and result['sent'] is False and not sends
            assert sizes and sizes[-1]==sizes[0], f'Page zoom was not restored: {sizes}'
            if picker: assert max(s['ratio'] for s in sizes)>=2*sizes[0]['ratio']
            results['picker' if picker else 'paste']={'result':result,'send_attempts':len(sends),'zoom_sizes':list(sizes)}
            write_yaml(folder/'results.yaml',results)
        print('PASS:', ', '.join(results), '; zero Send/Enter attempts:',folder,flush=True)
    finally: server.shutdown();server.server_close()


if __name__=='__main__':main()
