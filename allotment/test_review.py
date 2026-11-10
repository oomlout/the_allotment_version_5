"""Separate, revision-bound manual verdicts for preparation and inspection."""
from .storage import read_yaml,write_yaml,now
from .preparation import is_preparation,fingerprint,variables_for,preparation_runs
from .approval import signature
from .preparation_approval import approval_status,approve_run


def catalog(store,namespace,user):
    settings=store.settings(namespace,user);accounts=settings.get('destinations',{})
    reviews=settings.get('test_reviews',{});items={}
    for name,recipe in store.plugins().items():
        account=accounts.get(name,{})
        stamp=(fingerprint if is_preparation(recipe) else signature)(store,recipe,account)
        approval=approval_status(store,namespace,user,recipe,account)
        modes={}
        for mode in ('prepare','inspect'):
            reason=''
            try:
                if is_preparation(recipe):variables_for(recipe,account,mode=mode)
                else:
                    from .browser import validate_recipe
                    validate_recipe(recipe)
                    if mode=='inspect':raise ValueError('Inspection is not available for this platform.')
                    if not account.get('expected_account') or not account.get('profile_url'):
                        raise ValueError('Configure the account and profile address first.')
            except (ValueError,KeyError) as error:reason=str(error)
            review=reviews.get(name,{}).get(mode,{})
            current=review.get('signature')==stamp
            state=review.get('verdict','untested') if current else 'untested'
            if mode=='prepare':
                if approval['valid']:state='passed'
                elif state=='passed':state='untested'
            if reason:state='blocked'
            modes[mode]={'state':state,'eligible':not reason,'reason':reason,'signature':stamp,
                         'test_id':review.get('test_id') if current else None}
        items[name]={'name':recipe['name'],**modes}
    return items


def review_run(store,namespace,user,test_id,verdict):
    if verdict not in {'passed','not_passed'}:raise ValueError('Choose Pass or Not passed.')
    run=next(iter(preparation_runs(store,namespace,user,test_id=test_id)),None)
    if not run or run['active'] or not run.get('current'):raise ValueError('Review a finished test with current instructions. Changed instructions require a new test.')
    mode=run.get('mode','prepare');name=run['platform']
    if verdict=='passed' and not (run.get('can_approve') if mode=='prepare' else run['status']=='inspected'):
        raise ValueError('This run did not complete its checks. It cannot pass.')
    with store.garden_lock(namespace,user):
        if verdict=='passed' and mode=='prepare':approve_run(store,namespace,user,test_id)
        settings=store.settings(namespace,user);recipe=store.plugins()[name]
        account=settings.get('destinations',{}).get(name,{})
        stamp=(fingerprint if is_preparation(recipe) else signature)(store,recipe,account)
        # Re-read the immutable run signature while holding the account lock.
        from .preparation import folder_for
        from .approval import run_folder
        folder=(folder_for if test_id.startswith('prep-') else run_folder)(store,namespace,user,test_id)
        if read_yaml(folder/'run.yaml').get('signature')!=stamp:raise ValueError('Instructions changed. Test again.')
        if verdict=='not_passed' and mode=='prepare':
            account.pop('approval',None);account.update(enabled=False,reviewed=False)
            settings.setdefault('destinations',{})[name]=account
        record={'test_id':test_id,'signature':stamp,'verdict':verdict,'reviewed_at':now()}
        settings.setdefault('test_reviews',{}).setdefault(name,{})[mode]=record
        write_yaml(store.settings_path(namespace,user),settings)
    return record
