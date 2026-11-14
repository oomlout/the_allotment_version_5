"""Reusable data_assert_equal primitive; no platform workflow."""
def define():
    return {'name':'roboclick_action_data_assert_equal','name_short':['data_assert_equal'],'category':'Workflow primitives'}

def action(**kwargs):
    runtime=kwargs.get('_workflow_runtime')
    if runtime is None:
        from roboclick_workflow import Operations
        desktop=kwargs.get('_desktop_runtime')
        runtime=Operations(desktop,kwargs.get('directory','.'))
    options={k:v for k,v in kwargs.get('action',{}).items() if k not in {'command','label'}}
    return runtime.assert_equal(**options)

def test(**kwargs):
    return define()['name_short']==['data_assert_equal']
