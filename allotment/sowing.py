"""Sow published local posts; per-destination snapshots and existing guarded engines."""
from copy import deepcopy
from pathlib import Path
import shutil,uuid
from .storage import BUILTINS,inside,now,read_yaml,write_yaml,git,commit
from .images import media_files,generate_variants
from .framing import defaults,overrides
from .receipts import save_job
from .lifecycle import revision
from .sow_review import blocks_retry

SOW_BUILTINS={'static':'Static website','summary':'Allotment summary','git':'Git sync'}
SOW_AUTOMATIC=('static','summary')
SOW_REMOVED={'facebook','telegram','whatsapp'}


class PlatformSetupRequired(ValueError):
    def __init__(self, platform, message):
        self.platform=platform
        super().__init__(message)


def selectable_destinations(store):
    return {name:data for name,data in {**{k:{'name':v,'editable':True} for k,v in SOW_BUILTINS.items()},**store.plugins()}.items()
            if name not in {*SOW_AUTOMATIC,*SOW_REMOVED}}

def enqueue(store,namespace,user,post_id,actions,custom=None):
    actions=list(dict.fromkeys([*actions,*SOW_AUTOMATIC]));plugins=store.plugins();allowed={**SOW_BUILTINS,**plugins}
    if set(actions)-set(allowed) or set(actions)&SOW_REMOVED:raise ValueError('Choose an available sowing destination.')
    with store.garden_lock(namespace,user):
        post=store.published(namespace,user,post_id)
        if not post or (store.draft_dir(namespace,user,post_id)/'post.yaml').exists():raise ValueError('Publish the local post or its edits before sowing.')
        states=store.destination_states(namespace,user,post_id);accounts=store.settings(namespace,user).get('destinations',{})
        for action in actions:
            state=states.get(action,{})
            if blocks_retry(action,state,SOW_BUILTINS):
                raise ValueError(f'{action} already has a sowing record. Review it before continuing.')
            if action not in SOW_BUILTINS:
                from .preparation_approval import require_approval,approval_status
                recipe=plugins[action];account=accounts.get(action,{})
                try:
                    require_approval(store,namespace,user,recipe,account,post)
                except ValueError:
                    status=approval_status(store,namespace,user,recipe,account)
                    if not status['valid']:
                        if recipe.get('engine')=='oomlout_roboclick_prepare' and not recipe.get('preparation_ready'):
                            raise PlatformSetupRequired(action,f"{recipe['name']}: {status['reason']}") from None
                        reason='Previous platform test approval is out of date' if account.get('approval',{}).get('test_id') else 'Platform test approval is required'
                        raise PlatformSetupRequired(action,f"{recipe['name']}: {reason}. Run Test → Prepare, review the draft, then click Pass. The post’s ✓ records posting; it does not approve platform setup.") from None
                    if not account.get('enabled'):
                        raise PlatformSetupRequired(action,f"{recipe['name']}: Preparation is approved but disabled. Enable it in Configure on its Sowing card.") from None
                    raise
        custom=post.get('sowing_overrides',{}) if custom is None else custom;settings=defaults(store,namespace,user)
        for action,value in custom.items():overrides({action:value.get('framing')},allowed)
        job_id='job-'+uuid.uuid4().hex;relative=f'.allotment/snapshots/{job_id}';repo=store.repo(namespace,user);folder=inside(repo,relative)
        folder.mkdir(parents=True)
        snapshot=deepcopy(post);snapshot.pop('path',None)
        for attachment in snapshot['attachments']:
            for filename in media_files(attachment):
                target=inside(folder,filename);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(inside(repo,post['path']+'/'+filename),target)
        payloads={}
        for action in actions:
            framing=custom.get(action,{}).get('framing') or settings[action]
            for index,attachment in enumerate(snapshot['attachments']):
                if attachment['type']=='image' and framing!='original' and framing not in attachment.get('variants',{}):snapshot=generate_variants(folder,snapshot,index)
            payload=deepcopy(snapshot);payload['path']=relative
            for attachment in payload['attachments']:
                attachment['original_file']=attachment['file']
                attachment['display_variant']=framing
                if attachment['type']=='image' and framing!='original':attachment['file']=attachment['variants'][framing]['file']
            payloads[action]=payload
        write_yaml(folder/'post.yaml',snapshot)
        job={'schema_version':1,'kind':'sow','deferred_publication':True,'id':job_id,'namespace':namespace,'user':user,'created_at':now(),
             'revision':revision(post),'post':{**snapshot,'path':relative},'destination_posts':payloads,
             'actions':{action:{'status':'pending','attempts':0,'url':'','error':'','framing':custom.get(action,{}).get('framing') or settings[action]} for action in actions}}
        write_yaml(store.job_path(namespace,user,job_id),job)
        return job

def execute(store,namespace,user,job,check_stop,on_session=None,on_action=None,on_step=None):
    from . import preparation, browser
    from .preparation_approval import require_approval
    from .website import build_site
    repo=store.repo(namespace,user);accounts=store.settings(namespace,user).get('destinations',{})
    order=[a for a in job['actions'] if a not in SOW_BUILTINS]+[a for a in ('static','summary','git') if a in job['actions']]
    for action in order:
        receipt=job['actions'][action]
        check_stop()
        if receipt['status'] in {'running','submitting'}:
            receipt.update(status='failed' if action in SOW_BUILTINS else 'uncertain',error='Interrupted. Review the previous run before retrying.');save_job(store,namespace,user,job)
        if receipt['status']!='pending':continue
        if on_action:on_action(action)
        receipt.update(status='running',attempts=receipt.get('attempts',0)+1,started_at=now(),error='');save_job(store,namespace,user,job)
        try:
            payload=job['destination_posts'][action]
            if action in SOW_BUILTINS:
                from .lifecycle import revision
                current=store.published(namespace,user,payload['id'])
                if not current or revision(current)!=job['revision']:raise ValueError('Local publication changed since this sow was queued. Queue a new snapshot.')
            if action=='static':
                build_site(store,namespace,user,overrides={payload['id']:payload});receipt.update(status='succeeded',url=f'/allotment_static/{namespace}/{user}/p/{payload["id"]}/')
            elif action=='summary':
                output=store.output/namespace/user;output.mkdir(parents=True,exist_ok=True)
                text='\n\n'.join(f'## {p["id"]}\n\n{p["text"]}' for p in store.posts(namespace,user))
                (output/'summary.md').write_text(text+'\n',encoding='utf-8');receipt.update(status='succeeded',url=f'/allotment_static/{namespace}/{user}/summary.md')
            elif action=='git':
                if 'origin' not in git(repo,'remote').splitlines():raise ValueError('Configure the Git remote in Settings.')
                commit(repo,'Sow published posts');git(repo,'push','-u','origin','main');receipt.update(status='succeeded')
            else:
                recipe=store.plugins()[action];account=accounts.get(action,{})
                require_approval(store,namespace,user,recipe,account,payload)
                native=preparation if preparation.is_preparation(recipe) else browser
                native.check_payload(recipe,account,payload,repo)
                factory=preparation.preparation_context if native is preparation else browser.browser_context
                with factory(store,store.local/'evidence'/job['id']/action,check_stop=check_stop) as session:
                    if on_session:on_session(session,recipe,account)
                    options={'on_step':lambda index,step,phase:on_step(action,index,step,phase,len(recipe['steps']))} if on_step else {}
                    result=native.run_recipe(session,recipe,account,payload,repo,**options)
                if result.get('sent') is not False or not (result.get('draft_checked') or result.get('prepared')):raise ValueError('No checked unsent draft was returned.')
                receipt.update(status='prepared',sent=False,verified=False,manual_submission_required=True)
        except Exception as error:
            receipt.update(status='failed',error=str(error));save_job(store,namespace,user,job)
            if isinstance(error,browser.StopRequested):raise
        receipt['finished_at']=now();save_job(store,namespace,user,job)
    if job['actions'].get('git',{}).get('status')=='succeeded':
        check_stop()
        try:
            commit(repo,'Record sowing receipts');git(repo,'push','origin','main')
        except Exception as error:
            job['actions']['git']['receipt_sync_error']=str(error);save_job(store,namespace,user,job)
    return job
