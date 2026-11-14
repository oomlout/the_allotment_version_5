"""Reusable data_url_assert primitive; no platform workflow."""
def define():
    return {'name':'roboclick_action_data_url_assert','name_short':['data_url_assert'],'category':'Workflow primitives'}

def action(**kwargs):
    runtime=kwargs.get('_workflow_runtime')
    if runtime is None:
        from roboclick_workflow import Operations
        desktop=kwargs.get('_desktop_runtime')
        runtime=Operations(desktop,kwargs.get('directory','.'))
    options={k:v for k,v in kwargs.get('action',{}).items() if k not in {'command','label'}}
    return runtime.assert_url(**options)

def test(**kwargs):
    return define()['name_short']==['data_url_assert']
