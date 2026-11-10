"""Immediate sequential fixed-sample tests; no submission and no automatic approval."""
import os,threading,time,uuid
import psutil
from .storage import read_yaml,write_yaml,slug,now
from .test_review import catalog

ACTIVE={'running','stopping'}

class TestBatches:
    def __init__(self,store,start_run,runs,stop_run,settled,guard):
        self.store,self.start_run,self.runs,self.stop_run,self.settled,self.guard=store,start_run,runs,stop_run,settled,guard

    def folder(self,namespace,user):return self.store.local/'test-batches'/slug(namespace)/slug(user)

    def current(self,namespace,user):
        path=self.folder(namespace,user)/'current.yaml';batch=read_yaml(path)
        if batch.get('status') in ACTIVE:
            try:alive=abs(psutil.Process(batch['pid']).create_time()-batch['created'])<.1
            except (psutil.Error,KeyError):alive=False
            if not alive:
                batch.update(status='interrupted',error='Studio stopped. Remaining tests were not started.')
                write_yaml(path,batch)
        return batch

    def active(self):
        for profile in self.store.profiles():
            if self.current(profile['namespace'],profile['user']).get('status') in ACTIVE:return True
        return False

    def start(self,namespace,user,mode):
        if mode not in {'prepare','inspect'}:raise ValueError('Choose Prepare or Inspect.')
        if self.active():raise ValueError('An all-untested run is already active.')
        choices=catalog(self.store,namespace,user)
        items=[{'platform':name,'name':item['name'],'signature':item[mode]['signature'],
                'status':'pending' if item[mode]['eligible'] else 'blocked',
                **({'error':item[mode].get('reason') or 'Configure this platform before testing.'} if not item[mode]['eligible'] else {})}
               for name,item in choices.items() if item[mode]['state']!='passed']
        if not any(item['status']=='pending' for item in items):raise ValueError('No available untested platforms for this mode. Resolve the listed blockers first.')
        batch={'id':'batch-'+uuid.uuid4().hex,'mode':mode,'status':'running','items':items,'created_at':now(),
               'pid':os.getpid(),'created':psutil.Process().create_time()}
        path=self.folder(namespace,user)/'current.yaml';write_yaml(path,batch)
        try:self._spawn(namespace,user,batch)
        except Exception:
            batch.update(status='failed',error='Could not start the test sequence.')
            for item in items:
                if item['status']=='pending':item['status']='cancelled'
            write_yaml(path,batch)
            raise
        return batch

    def _spawn(self,namespace,user,batch):
        threading.Thread(target=self.execute,args=(namespace,user,batch),daemon=True).start()

    def stop(self,namespace,user):
        batch=self.current(namespace,user)
        if batch.get('status') not in ACTIVE:return
        (self.folder(namespace,user)/(batch['id']+'.stop')).write_text(now(),encoding='utf-8')
        for item in batch['items']:
            if item.get('test_id') and item['status']=='running':self.stop_run(namespace,user,item['test_id'])

    def execute(self,namespace,user,batch):
        folder=self.folder(namespace,user);path=folder/'current.yaml';flag=folder/(batch['id']+'.stop')
        def save():write_yaml(path,batch)
        try:
            for item in batch['items']:
                if item['status']!='pending':continue
                with self.guard:
                    if flag.exists():break
                    latest=catalog(self.store,namespace,user).get(item['platform'],{}).get(batch['mode'],{})
                    if not latest.get('eligible'):
                        item.update(status='blocked',error=latest.get('reason') or 'Platform unavailable. Check its configuration.');save();continue
                    if latest.get('signature')!=item['signature']:
                        item.update(status='skipped',error='Instructions or configuration changed. Test again.');save();continue
                    if latest.get('state')=='passed':item['status']='skipped';save();continue
                    try:
                        test_id=self.start_run(namespace,user,item['platform'],batch['mode'])
                    except ValueError as error:
                        item.update(status='failed',error=str(error));save();continue
                    item.update(status='running',test_id=test_id);save()
                while True:
                    if flag.exists():self.stop_run(namespace,user,test_id)
                    run=next((r for r in self.runs(namespace,user) if r['id']==test_id),None)
                    if run and not run['active'] and self.settled(test_id):break
                    if run is None and self.settled(test_id):raise ValueError('Test process ended without a result.')
                    time.sleep(.3)
                item['status']=run['status']
                if run.get('error') or run.get('warning'):item['error']=run.get('error') or run['warning']
                save()
                if run['status']=='stopped':flag.touch()
                if flag.exists():break
            batch['status']='stopped' if flag.exists() else 'complete'
        except Exception as error:
            batch.update(status='failed',error=str(error))
            for item in batch['items']:
                if item.get('test_id') and item['status']=='running':
                    try:self.stop_run(namespace,user,item['test_id'])
                    except Exception:pass
        finally:
            for item in batch['items']:
                if item['status']=='pending':item['status']='cancelled'
            batch['finished_at']=now();save()
