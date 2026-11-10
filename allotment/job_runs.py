"""Single-job local worker processes, countdown controls and progress."""
from datetime import datetime,timezone
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import psutil

from .storage import BUILTINS,lock,now,read_yaml,write_yaml,slug
from .browser import StopRequested
from .roboclick.desktop import DesktopStopped

ACTIVE={'queued','countdown','paused','running'}


def folder_for(store,namespace,user,run_id):
    return store.local/'job-runs'/slug(namespace)/slug(user)/slug(run_id)


def owned_process(folder):
    meta=read_yaml(folder/'process.yaml');run=read_yaml(folder/'run.yaml')
    try:
        process=psutil.Process(meta['pid'])
        expected=['-m','allotment','--root',meta['root'],'run-job',run['namespace'],run['user'],run['id']]
        return process if abs(process.create_time()-meta['created'])<.1 and process.cmdline()[1:]==expected and process.is_running() else None
    except (KeyError,psutil.Error):return None


def progress_for(folder):
    progress=read_yaml(folder/'progress.yaml')
    if progress.get('status') in ACTIVE and not owned_process(folder):
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(progress['updated_at'])).total_seconds()
        if age>5:return {**progress,'status':'failed','error':'Worker process stopped. Check the destination receipts before retrying.'}
    if progress.get('status') in ACTIVE:
        from .roboclick.desktop import handoff_progress
        run=read_yaml(folder/'run.yaml')
        evidence=folder/'evidence' if run.get('kind')=='harvest' else folder.parents[3]/'evidence'/run.get('job_id','')/progress.get('platform','')
        progress={**progress,**handoff_progress(evidence)}
    return progress


def runs_for(store,namespace,user,post_id):
    results=[]
    for folder in (store.local/'job-runs'/slug(namespace)/slug(user)).glob('*'):
        run=read_yaml(folder/'run.yaml')
        if run.get('post_id')==post_id:results.append({**run,**progress_for(folder)})
    return sorted(results,key=lambda r:r['created_at'],reverse=True)


class JobRunManager:
    def __init__(self,store):self.store=store

    def launch_post(self,namespace,user,post_id,actions,custom=None):
        from .sowing import enqueue
        with lock(self.store.local/'job-launch.lock'):
            for folder in (self.store.local/'job-runs').glob('*/*/*'):
                if progress_for(folder).get('status') in ACTIVE:raise ValueError('Another launch is active. Stop it or wait for it to finish.')
            job=enqueue(self.store,namespace,user,post_id,actions,custom)
            job['launch_only']=True;write_yaml(self.store.job_path(namespace,user,job['id']),job)
            run={'id':'run-'+uuid.uuid4().hex,'kind':'launch','namespace':namespace,'user':user,'job_id':job['id'],
                 'post_id':post_id,'created_at':now(),'destinations':list(job['actions'])}
            try:return self._launch(namespace,user,run)
            except Exception:
                cancel_unstarted(self.store,namespace,user,job['id']);raise

    def start(self,namespace,user,job_id):
        with lock(self.store.local/'job-launch.lock'):
            for folder in (self.store.local/'job-runs').glob('*/*/*'):
                if progress_for(folder).get('status') in ACTIVE:raise ValueError('A Run now worker is already active. Wait or stop it first.')
            job=read_yaml(self.store.job_path(namespace,user,job_id))
            if not job or not any(r['status']=='pending' for r in job['actions'].values()):raise ValueError('This job has no pending actions.')
            run_id='run-'+uuid.uuid4().hex;folder=folder_for(self.store,namespace,user,run_id)
            run={'id':run_id,'namespace':namespace,'user':user,'job_id':job_id,'post_id':job['post']['id'],'created_at':now(),
                 'destinations':[name for name,r in job['actions'].items() if r['status']=='pending']}
            return self._launch(namespace,user,run)

    def _launch(self,namespace,user,run):
        run_id=run['id'];folder=folder_for(self.store,namespace,user,run_id)
        write_yaml(folder/'run.yaml',run)
        write_yaml(folder/'progress.yaml',{'status':'queued','updated_at':now()})
        try:
            with (folder/'process.log').open('w',encoding='utf-8') as log:
                process=subprocess.Popen([sys.executable,'-m','allotment','--root',str(self.store.root),'run-job',namespace,user,run_id],
                    cwd=Path(__file__).resolve().parent.parent,stdout=log,stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            write_yaml(folder/'process.yaml',{'pid':process.pid,'created':psutil.Process(process.pid).create_time(),'root':str(self.store.root)})
            def reap():
                process.wait()
                if run.get('kind')=='launch':cancel_unstarted(self.store,namespace,user,run['job_id'],interrupted=True)
            threading.Thread(target=reap,daemon=True).start()
        except Exception as error:
            write_yaml(folder/'progress.yaml',{'status':'failed','error':str(error),'updated_at':now()});raise ValueError('The launch process could not start. No unstarted actions will run later.') from error
        return run_id

    def start_harvest(self,namespace,user,post_id,platform,mode,url='',test=False):
        from .harvest import recipe_for,account_for,signature,approved
        from .lifecycle import revision,post_folder
        from .storage import inside
        if mode not in {'ids','reactions'}:raise ValueError('Choose ID capture or reactions.')
        recipe_for(platform);account_for(self.store,namespace,user,platform)
        post=self.store.published(namespace,user,post_id)
        if not post:raise ValueError('Publish the local post before harvesting.')
        if not test and not approved(self.store,namespace,user,platform,mode):raise ValueError('Test and approve harvesting first.')
        if mode=='reactions':
            saved=read_yaml(post_folder(self.store,namespace,user,post_id)/'harvest'/platform/'id.yaml')
            if not saved:raise ValueError('Capture the post ID first.')
            url=saved['url']
        else:url='' # Always discover IDs from the recent profile, never trust a supplied permalink.
        payload=post
        for job in self.store.jobs(namespace,user):
            if job['post']['id']==post_id and job['actions'].get(platform,{}).get('status') in {'prepared','uncertain','succeeded'}:
                payload=job.get('destination_posts',{}).get(platform,job['post']);break
        images=[str(inside(self.store.repo(namespace,user),payload['path']+'/'+a['file'])) for a in payload['attachments'] if a['type']=='image']
        with lock(self.store.local/'job-launch.lock'):
            for folder in (self.store.local/'job-runs').glob('*/*/*'):
                if progress_for(folder).get('status') in ACTIVE:raise ValueError('A worker is already active.')
            run={'id':'run-'+uuid.uuid4().hex,'kind':'harvest','namespace':namespace,'user':user,'post_id':post_id,
                'platform':platform,'mode':mode,'url':url,'test':bool(test),'text':payload['text'],'images':images,
                'revision':revision(payload),'signature':signature(self.store,namespace,user,platform),
                'created_at':now(),'destinations':[platform]}
            return self._launch(namespace,user,run)

    def control(self,namespace,user,run_id,action):
        folder=folder_for(self.store,namespace,user,run_id);progress=progress_for(folder)
        if progress.get('status') not in ACTIVE:raise ValueError('This worker is no longer active.')
        if action=='stop':(folder/'stop.flag').write_text(now(),encoding='utf-8')
        elif action=='pause' and progress['status']=='countdown':(folder/'pause.flag').write_text(now(),encoding='utf-8')
        elif action=='resume' and progress['status']=='paused':(folder/'resume.flag').write_text(now(),encoding='utf-8')
        else:raise ValueError('That control is not available now.')


def execute_run(store,namespace,user,run_id,countdown_seconds=8):
    from .publication import run_worker
    folder=folder_for(store,namespace,user,run_id);run=read_yaml(folder/'run.yaml')
    progress=read_yaml(folder/'progress.yaml')
    if not run or progress.get('status')!='queued':raise ValueError('Worker runs are single-use.')
    def persist(**values):
        progress.update(values,updated_at=now());write_yaml(folder/'progress.yaml',progress)
    def check_stop():
        if (folder/'stop.flag').exists():raise StopRequested('Stopped by user. Unstarted launch actions are cancelled.')
    try:
        social=any(name not in BUILTINS for name in run['destinations'])
        if social and run.get('kind')!='launch':
            deadline=time.monotonic()+countdown_seconds
            while time.monotonic()<deadline:
                check_stop();remaining=max(0,deadline-time.monotonic())
                persist(status='countdown',remaining=math.ceil(remaining))
                if (folder/'pause.flag').exists():
                    persist(status='paused')
                    while not (folder/'resume.flag').exists():check_stop();time.sleep(.1)
                    (folder/'pause.flag').unlink(missing_ok=True);(folder/'resume.flag').unlink(missing_ok=True)
                    deadline=time.monotonic()+remaining
                time.sleep(.1)
        check_stop();persist(status='running',remaining=0)
        if run.get('kind')=='harvest':
            from .harvest import run as harvest_run
            result=harvest_run(store,namespace,user,run,check_stop)
            check_stop();persist(status='complete',result=result,finished_at=now(),error='')
            return progress
        if run.get('kind')=='launch':
            from .sowing import execute
            from .launch import countdown_in_tab,starting_url
            first=True
            def on_action(action):
                persist(status='running',platform=action,operation='Starting '+store.plugins().get(action,{}).get('name',BUILTINS.get(action,action)),remaining=0,step=0,total_steps=0)
            def on_step(action,index,step,phase,total):
                if phase=='before':
                    labels={'prepare_text':'Adding post text','prepare_image':'Attaching image','prepare_upload_image':'Attaching image','prepare_verify_text':'Checking exact text','prepare_finish':'Checking draft; submission stays manual'}
                    persist(platform=action,step=index+1,total_steps=total,operation=labels.get(step['command']) or step.get('label') or step['command'].replace('_',' ').capitalize())
            def on_session(session,recipe,account):
                nonlocal first
                if first:
                    def update_countdown(**values):
                        phase=values.pop('phase')
                        persist(status='countdown' if phase=='switching' else phase,**values)
                    countdown_in_tab(session,folder,starting_url(recipe,account),update_countdown,countdown_seconds,resume_flag='resume.flag')
                    first=False
                persist(status='running',operation='Preparing '+recipe['name'])
            with store.garden_lock(namespace,user):
                job=read_yaml(store.job_path(namespace,user,run['job_id']))
                execute(store,namespace,user,job,check_stop,on_session=on_session,on_action=on_action,on_step=on_step)
        else:
            results=run_worker(store,namespace,user,run['job_id'],check_stop=check_stop)
        check_stop()
        job=read_yaml(store.job_path(namespace,user,run['job_id']))
        failed=any(r['status'] in {'failed','uncertain'} for r in job['actions'].values())
        persist(status='failed' if failed else 'complete',finished_at=now(),error='Check the destination receipts below.' if failed else '')
    except Exception as error:
        persist(status='stopped' if isinstance(error,(StopRequested,DesktopStopped)) else 'failed',error=str(error)[:2500],finished_at=now())
    if run.get('kind')=='launch':cancel_unstarted(store,namespace,user,run['job_id'])
    return progress


def cancel_unstarted(store,namespace,user,job_id,interrupted=False):
    from .receipts import save_job
    with store.garden_lock(namespace,user):
        job=read_yaml(store.job_path(namespace,user,job_id))
        for receipt in job.get('actions',{}).values():
            if receipt['status']=='pending':receipt.update(status='cancelled',error='Not started. Select this destination and launch again.',finished_at=now())
            elif interrupted and receipt['status'] in {'running','submitting'}:receipt.update(status='uncertain',error='Launch process stopped. Review its Chrome tab before continuing.',finished_at=now())
        if job:save_job(store,namespace,user,job)
