"""Queue dispatcher for draft-only plugins, preserving the Bluesky implementation."""
from . import worker as original
from .browser import StopRequested, check_payload as check_bluesky
from .preparation import is_preparation, preparation_context, run_recipe, check_payload
from .preparation_approval import require_approval
from .roboclick.desktop import DesktopStopped
from .storage import BUILTINS, commit, git, now, read_yaml
from .receipts import save_job


def execute_job(store, namespace, user, job_id, dry_run=False):
    job=read_yaml(store.job_path(namespace,user,job_id))
    if not job:raise ValueError('Job not found.')
    plugins=store.plugins();accounts=store.settings(namespace,user).get('destinations',{})
    repo=store.repo(namespace,user)
    if dry_run:
        for action,receipt in job['actions'].items():
            if receipt['status']!='pending' or action in BUILTINS:continue
            recipe=plugins[action];account=accounts.get(action,{})
            require_approval(store,namespace,user,recipe,account,job['post'])
            (check_payload if is_preparation(recipe) else check_bluesky)(recipe,account,job['post'],repo)
        return job
    for action,receipt in job['actions'].items():
        recipe=plugins.get(action,{})
        if not is_preparation(recipe):continue
        if receipt['status'] in {'running','submitting'}:
            receipt.update(status='uncertain',error='Worker stopped while preparing this draft. Inspect its tab before resolving it.')
            save_job(store,namespace,user,job)
        if receipt['status']!='pending':continue
        receipt.update(status='running',started_at=now(),attempts=receipt.get('attempts',0)+1,error='')
        save_job(store,namespace,user,job)
        stopped=None
        try:
            account=accounts.get(action,{})
            require_approval(store,namespace,user,recipe,account,job['post'])
            check_payload(recipe,account,job['post'],repo)
            with preparation_context(store,store.local/'evidence'/job_id/action) as session:
                result=run_recipe(session,recipe,account,job['post'],repo)
            if not result.get('draft_checked') or result.get('sent') is not False:
                raise ValueError('The worker did not return a checked unsent draft.')
            receipt.update(status='prepared',sent=False,verified=False,manual_submission_required=True)
        except Exception as error:
            receipt.update(status='failed',error=str(error)[:2500])
            if isinstance(error,(StopRequested,DesktopStopped)):stopped=error
        receipt['finished_at']=now();save_job(store,namespace,user,job)
        if stopped:raise stopped
    # Prepared/failed actions are terminal and the unchanged worker skips them.
    return original.execute_job(store,namespace,user,job_id)


def run_worker(store, namespace=None, user=None, job_id=None, dry_run=False, pull=False):
    results=[]
    for profile in store.profiles():
        ns,username=profile['namespace'],profile['user']
        if namespace and ns!=namespace or user and username!=user:continue
        with store.garden_lock(ns,username):
            repo=store.repo(ns,username)
            if pull and 'origin' in git(repo,'remote').splitlines():
                if git(repo,'status','--porcelain'):raise ValueError('Commit/sync local public changes before pulling worker jobs.')
                git(repo,'pull','--ff-only','origin','main')
            for job in reversed(store.jobs(ns,username)):
                if job_id and job['id']!=job_id:continue
                if any(r['status'] in {'pending','running','submitting'} for r in job['actions'].values()):
                    result=execute_job(store,ns,username,job['id'],dry_run);results.append(result)
                    print(f"{ns}/{username} {job['id']}: "+', '.join(f"{k}={v['status']}" for k,v in result['actions'].items()))
            if pull and not dry_run and 'origin' in git(repo,'remote').splitlines():
                commit(repo,'Sync worker publishing receipts');git(repo,'push','origin','main')
    return results
