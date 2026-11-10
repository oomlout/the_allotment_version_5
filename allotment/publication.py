"""Publish explicit Allotment snapshots without changing the browser engines."""
from contextlib import contextmanager
from datetime import datetime
import shutil

from .storage import BUILTINS, now, read_yaml, write_yaml, inside, slug, commit, git
from .images import digest, media_files
from .receipts import save_job


def publish_snapshot(store,namespace,user,job,publication_time=None):
    repo=store.repo(namespace,user);source=inside(repo,job['post']['path'])
    post=read_yaml(source/'post.yaml')
    if not post or post.get('revision')!=job['revision']:raise ValueError('The queued post snapshot is missing or changed.')
    old=store.published(namespace,user,post['id'])
    if old and old.get('revision')!=job['revision'] and max(old.get('publication_queued_at',''),old.get('updated_at',''))>job['created_at']:
        raise ValueError('A newer version is already published to the Allotment.')
    published_at=(old or {}).get('published_at') or publication_time or now()
    relative=(old or {}).get('path') or f"posts/{datetime.fromisoformat(published_at):%Y/%m/%d}/{slug(post['id'])}"
    target=inside(repo,relative);target.mkdir(parents=True,exist_ok=True)
    for attachment in post['attachments']:
        for filename in media_files(attachment):
            destination=inside(target,filename);destination.parent.mkdir(parents=True,exist_ok=True)
            origin=inside(source,filename)
            if origin!=destination:shutil.copy2(origin,destination)
    post.update(status='published',published_at=published_at,publication_queued_at=job['created_at'])
    post.pop('path',None);write_yaml(target/'post.yaml',post)
    draft=store.draft_dir(namespace,user,post['id'])
    if job.get('source_draft_sha256') and (draft/'post.yaml').exists() and digest(draft/'post.yaml')==job['source_draft_sha256']:
        shutil.rmtree(draft)
    return f'/g/{namespace}/{user}/p/{post["id"]}/'


@contextmanager
def stoppable_contexts(check_stop):
    """Scope extra cancellation to this worker process; engines/gates stay intact."""
    from . import preparation_worker,worker
    original_preparation=preparation_worker.preparation_context
    original_browser=worker.browser_context
    def wrap(factory):
        @contextmanager
        def wrapped(*args,**kwargs):
            check_stop();kwargs['check_stop']=check_stop
            with factory(*args,**kwargs) as session:
                yield session
                check_stop()
        return wrapped
    preparation_worker.preparation_context=wrap(original_preparation)
    worker.browser_context=wrap(original_browser)
    try:yield
    finally:
        preparation_worker.preparation_context=original_preparation
        worker.browser_context=original_browser


def run_worker(store,namespace=None,user=None,job_id=None,dry_run=False,pull=False,check_stop=None):
    from . import preparation_worker
    check_stop=check_stop or (lambda:None)
    results=[]
    for profile in store.profiles():
        ns,name=profile['namespace'],profile['user']
        if namespace and ns!=namespace or user and name!=user:continue
        with store.garden_lock(ns,name):
            repo=store.repo(ns,name)
            if pull and 'origin' in git(repo,'remote').splitlines():
                if git(repo,'status','--porcelain'):raise ValueError('Commit/sync local public changes before pulling worker jobs.')
                git(repo,'pull','--ff-only','origin','main')
            for job in reversed(store.jobs(ns,name)):
                if job_id and job['id']!=job_id:continue
                if job.get('launch_only') and not job_id:continue
                if not any(r['status'] in {'pending','running','submitting'} for r in job['actions'].values()):continue
                check_stop()
                if job.get('kind')=='sow':
                    from .sowing import execute
                    if dry_run:
                        from .preparation_approval import require_approval
                        for action in job['actions']:
                            if action not in BUILTINS:require_approval(store,ns,name,store.plugins()[action],store.settings(ns,name).get('destinations',{}).get(action,{}),job['destination_posts'][action])
                        results.append(job)
                    else:results.append(execute(store,ns,name,job,check_stop))
                    continue
                if dry_run:
                    results.append(preparation_worker.execute_job(store,ns,name,job['id'],True));continue
                receipt=job['actions'].get('allotment')
                if receipt and receipt['status'] in {'pending','running'}:
                    receipt.update(status='running',started_at=now(),attempts=receipt.get('attempts',0)+1,error='')
                    save_job(store,ns,name,job)
                    try:
                        receipt.update(status='succeeded',url=publish_snapshot(store,ns,name,job))
                        receipt['published_at']=store.published(ns,name,job['post']['id'])['published_at']
                    except Exception as error:receipt.update(status='failed',error=str(error))
                    receipt['finished_at']=now();save_job(store,ns,name,job)
                if receipt and receipt['status']!='succeeded':
                    for action in ('static','git'):
                        if job['actions'].get(action,{}).get('status')=='pending':
                            job['actions'][action].update(status='failed',error='Publish to the Allotment successfully before exporting this version.')
                    save_job(store,ns,name,job)
                check_stop()
                with stoppable_contexts(check_stop):
                    result=preparation_worker.execute_job(store,ns,name,job['id'])
                results.append(result)
                print(f"{ns}/{name} {job['id']}: "+', '.join(f"{a}={r['status']}" for a,r in result['actions'].items()))
            if pull and not dry_run and 'origin' in git(repo,'remote').splitlines():
                commit(repo,'Sync worker publishing receipts');git(repo,'push','origin','main')
    return results
