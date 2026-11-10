"""Opt-in native all-untested walkthrough. Only disposable localhost Post/Enter traps."""
from pathlib import Path
import copy,sys,time,uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from desktop_practice import practice_server
from allotment.storage import Store,write_yaml
from allotment.app import create_app
from allotment.test_sample import SAMPLE_TEXT


def main():
    root=Path(__file__).resolve().parents[1]/'.local/roboclick-walkthrough'/('batch-'+uuid.uuid4().hex[:8]);root.mkdir(parents=True)
    server,state=practice_server(root)
    store=Store(root/'studio');store.bootstrap();ns,user='the_allotment','oomlout_main'
    accounts={}
    for name in ['bluesky','twitter']:
        recipe=copy.deepcopy(store.plugins()['bluesky']);recipe.update(id=name,name='Local '+name,base_url=f'http://127.0.0.1:{server.server_port}')
        write_yaml(store.local/'plugins'/f'{name}.yaml',recipe)
        accounts[name]={'expected_account':'practice.test','profile_url':recipe['base_url']+'/profile/practice.test'}
    write_yaml(store.settings_path(ns,user),{'destinations':accounts})
    app=create_app(store.root,testing=True,garden=(ns,user));client=app.test_client();base=f'/g/{ns}/{user}/tests'
    client.get('/')
    with client.session_transaction() as session:csrf=session['csrf']
    try:
        response=client.post(base+'/all-untested/start',data={'csrf':csrf,'mode':'prepare'})
        assert response.status_code==200,response.text
        assert [item['platform'] for item in response.json['batch']['items'] if item['status']!='blocked']==['bluesky','twitter']
        deadline=time.monotonic()+200;last=None
        while time.monotonic()<deadline:
            batch=app.extensions['test_batches'].current(ns,user)
            summary=(batch['status'],[(item['name'],item['status']) for item in batch['items']])
            if summary!=last:print(summary,flush=True);last=summary
            if batch['status'] not in {'running','stopping'}:break
            time.sleep(.5)
        else:raise AssertionError('Batch timed out')
        assert batch['status']=='complete' and all(item['status'] in {'prepared','blocked'} for item in batch['items']) and sum(item['status']=='prepared' for item in batch['items'])==2,batch
        assert state['attempts']==0 and state['dismissals']==0 and len(state['drafts'])==2,state
        assert all(draft['text'].startswith(SAMPLE_TEXT) and draft['image'] for draft in state['drafts'].values())
        assert not store.settings(ns,user).get('test_reviews')
        assert not any(account.get('approval') for account in store.settings(ns,user)['destinations'].values())
        write_yaml(root/'result.yaml',{'batch':batch,**state})
        print('PASS: sequential all-untested, two retained drafts, fixed text/image, no automatic approval, zero Post/Enter attempts.',flush=True)
        print(root,flush=True)
    finally:
        app.extensions['test_batches'].stop(ns,user)
        server.shutdown()

if __name__=='__main__':main()
