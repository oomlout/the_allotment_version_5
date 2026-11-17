"""Disposable native checks for Search present/absent; no social site is used."""
from pathlib import Path
import sys
import threading
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flask import Flask,request
from werkzeug.serving import make_server
from allotment.preparation import preparation_context
from allotment.storage import Store
from allotment.whatsapp_channel_recovery import bind


def main():
    app=Flask(__name__);events=[]
    @app.get('/<mode>')
    def page(mode):
        return '''<html><head><title>Channel recovery fixture</title><style>
        body{font:20px Arial;background:white;color:black}button,input{font:20px Arial}
        button{border:0;background:white;padding:4px 8px}
        header{position:absolute;left:100px;top:115px}#search{position:absolute;left:100px;top:195px}
        #discover{position:absolute;left:100px;top:455px}#result{position:absolute;left:100px;top:295px}
        #send{position:absolute;left:100px;top:700px}</style></head><body>
        <header>fixture_owner <button>Edit profile</button></header>
        <input id="search" placeholder="Search" '''+('hidden' if mode=='missing' else '')+'''>
        <button id="discover" '''+('' if mode=='missing' else 'hidden')+'''>Discover more</button>
        <button id="result" hidden>Fixture Channel</button><button id="send">Send</button>
        <script>const record=x=>fetch('/event',{method:'POST',body:x});
        document.querySelector('#discover').onclick=()=>{record('discover');document.querySelector('#search').hidden=false;document.querySelector('#discover').hidden=true;};
        document.querySelector('#search').oninput=e=>{document.querySelector('#result').hidden=e.target.value!=='Fixture Channel';};
        document.querySelector('#result').onclick=e=>{record('selected');e.target.textContent='Channel selected';};
        document.querySelector('#send').onclick=()=>record('SEND');
        document.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();record('ENTER');}});
        </script></body></html>'''
    @app.post('/event')
    def event():events.append(request.get_data(as_text=True));return '',204
    server=make_server('127.0.0.1',0,app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    store=Store(Path(__file__).resolve().parents[1]);base=f'http://127.0.0.1:{server.server_port}'
    evidence=store.local/'roboclick-walkthrough'/('channel-recovery-'+uuid.uuid4().hex[:8])
    try:
        for mode in ('present','missing'):
            url=base+'/'+mode
            recipe=bind({'id':'whatsapp_channel','steps':[{'command':'prepare_navigate','url':url}],
                         'channel_search_recovery':{'text':'Discover more','region':[.04,.48,.33,.61],
                                                    'search_region':[.04,.24,.33,.33]}})
            with preparation_context(store,evidence/mode) as session:
                session.configure(recipe,{}, {'attachments':[]},store.root,
                                  {'base_url':base,'handle':'fixture_owner','expected_account':'fixture_owner','target_name':'Fixture Channel'})
                session.navigate(url,seconds=1)
                session.visual_identity([.04,.18,.33,.25],owner_text='Edit profile',owner_region=[.118,.19,.18,.23])
                session.search_group(text='Search',region=[.04,.24,.33,.33],result_region=[.04,.34,.33,.46])
                session.find_text(text='Channel selected',region=[.04,.34,.33,.46],exact=True)
                session.screenshot(name='checked-no-send')
        assert events==['selected','discover','selected'],events
        print('PASS: Search present and missing; one Discover more click; zero Send/Enter attempts:',evidence,flush=True)
    finally:server.shutdown()


if __name__=='__main__':main()
