"""Durable user outcomes, separate from automation receipts and platform approval."""
from .storage import lock, now, read_yaml, slug, write_yaml

TERMINAL = {'prepared', 'succeeded', 'failed', 'uncertain', 'exported'}


def reviewable(receipt):
    # Historical exports often have attempts=0: their outcome was never
    # returned from the other computer. Let the user review that uncertainty.
    return receipt.get('status') in TERMINAL and bool(receipt.get('attempts') or receipt.get('started_at')
        or receipt.get('status') in {'prepared','succeeded','uncertain','exported'})


def path_for(store, namespace, user, post_id, job_id, action):
    return store.local / 'sowing-reviews' / slug(namespace) / slug(user) / slug(post_id) / slug(job_id) / (slug(action)+'.yaml')


def attach_reviews(store, namespace, user, job):
    for action, receipt in job.get('actions', {}).items():
        review = read_yaml(path_for(store, namespace, user, job['post']['id'], job['id'], action))
        if review:
            receipt['review'] = review
    return job


def blocks_retry(action, receipt, builtins):
    # A completed attempt never prevents an explicit new launch. Approval is
    # still checked independently by the sowing engine before desktop input.
    return receipt.get('status') in {'pending','running','submitting'}


def save_review(store, namespace, user, post_id, job_id, action, verdict):
    if verdict not in {'successful', 'unsuccessful'}:
        raise ValueError('Choose successful or not successful.')
    from .job_runs import runs_for, ACTIVE
    if any(r['status'] in ACTIVE for r in runs_for(store, namespace, user, post_id)):
        raise ValueError('Review after the launch finishes.')
    path = path_for(store, namespace, user, post_id, job_id, action)
    with lock(path.with_suffix('.lock')):
        job = read_yaml(store.job_path(namespace, user, job_id))
        receipt = job.get('actions', {}).get(action, {})
        if job.get('post', {}).get('id') != post_id or not reviewable(receipt):
            raise ValueError('Only an attempted or exported destination can be reviewed.')
        record = {'verdict':verdict, 'reviewed_at':now(), 'job_id':job_id, 'platform':action,
                  'post_id':post_id, 'worker_status':receipt['status']}
        write_yaml(path, record)
    return record


def destination_view(action, state):
    verdict = state.get('review', {}).get('verdict', '')
    status = state.get('status', '')
    labels = {'pending':'Waiting', 'running':'Working', 'submitting':'Working', 'prepared':'Draft ready — review in Chrome',
              'succeeded':'Completed', 'failed':'Failed', 'uncertain':'Interrupted — check Chrome', 'cancelled':'Not started', 'exported':'Exported — outcome not recorded'}
    return {'id':action, 'job_id':state.get('job_id',''), 'status':status, 'verdict':verdict,
            'label':{'successful':'Posted — confirmed by you', 'unsuccessful':'Ready to select again'}.get(verdict, labels.get(status,'Not attempted')),
            'error':'' if verdict else state.get('error',''), 'url':state.get('url',''),
            'reviewable':verdict!='unsuccessful' and reviewable(state)}
