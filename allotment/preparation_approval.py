"""Approval for isolated draft workflows; delegate Bluesky to its unchanged gate."""
from . import approval as bluesky
from .storage import now, read_yaml, write_yaml


def checked(progress):
    result=progress.get('result',{})
    return (progress.get('status')=='prepared' and progress.get('sent') is False
            and progress.get('verified') is False and not progress.get('submission_started')
            and result.get('draft_checked') is True and result.get('sent') is False
            and result.get('verified') is False and result.get('manual_submission_required') is True
            and all(result.get(key) is True for key in ('account_checked','target_checked','text_checked','image_checked')))


def evidence(store, namespace, user, recipe, account, test_id):
    from .preparation import folder_for, fingerprint, validate_preparation
    folder=folder_for(store,namespace,user,test_id)
    run=read_yaml(folder/'run.yaml');progress=read_yaml(folder/'progress.yaml')
    try: validate_preparation(recipe)
    except (ValueError,KeyError):return None
    if (not recipe.get('preparation_ready') or run.get('mode')!='prepare'
            or (run.get('namespace'),run.get('user'),run.get('platform'))!=(namespace,user,recipe['id'])
            or run.get('signature')!=fingerprint(store,recipe,account) or not checked(progress)
            or (folder/'stop.flag').exists() or (folder/'termination.yaml').exists()):return None
    return run


def approval_status(store, namespace, user, recipe, account):
    from .preparation import is_preparation, fingerprint
    if not is_preparation(recipe):return bluesky.approval_status(store,namespace,user,recipe,account)
    approval=account.get('approval',{});test_id=approval.get('test_id','')
    valid=bool(test_id and test_id.startswith('prep-') and approval.get('signature')==fingerprint(store,recipe,account)
               and evidence(store,namespace,user,recipe,account,test_id))
    reason=('Draft preparation approved; submission is manual' if valid else
            'Prepare and review a checked draft, then approve preparation.' if recipe.get('preparation_ready') else
            recipe.get('setup_note','Draft calibration pending.'))
    return {'valid':valid,'reason':reason,'capabilities':['text','image'] if valid else [],'test_id':test_id,
            'test_ids':[test_id] if valid else []}


def require_approval(store, namespace, user, recipe, account, post=None):
    from .preparation import is_preparation
    if not is_preparation(recipe):return bluesky.require_approval(store,namespace,user,recipe,account,post)
    status=approval_status(store,namespace,user,recipe,account)
    if not account.get('enabled') or not status['valid']:
        raise ValueError(f"{recipe['name']}: {status['reason']}")
    if not {a['type'] for a in (post or {}).get('attachments',[])}.issubset(status['capabilities']):
        raise ValueError('This draft workflow has not been tested with that media type.')
    return status


def approve_run(store, namespace, user, test_id):
    if not test_id.startswith('prep-'):return bluesky.approve_run(store,namespace,user,test_id)
    from .preparation import folder_for, fingerprint
    run=read_yaml(folder_for(store,namespace,user,test_id)/'run.yaml')
    recipe=store.plugins().get(run.get('platform'),{})
    settings=store.settings(namespace,user);account=settings.get('destinations',{}).get(recipe.get('id'),{})
    if not recipe or not evidence(store,namespace,user,recipe,account,test_id):
        raise ValueError('Only a current, completed and checked unsent draft can approve preparation. Inspect runs cannot.')
    account.update(enabled=True,reviewed=True,approval={'test_id':test_id,'test_ids':[test_id],
                   'signature':fingerprint(store,recipe,account),'approved_at':now()})
    settings.setdefault('destinations',{})[recipe['id']]=account
    write_yaml(store.settings_path(namespace,user),settings)
    return account
