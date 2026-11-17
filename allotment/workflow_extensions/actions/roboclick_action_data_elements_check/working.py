"""Reusable data_elements_check primitive; no platform workflow."""
def define():
    return {'name':'roboclick_action_data_elements_check','name_short':['data_elements_check'],'category':'Workflow primitives'}

def action(**kwargs):
    runtime=kwargs.get('_workflow_runtime')
    if runtime is None:
        from roboclick_workflow_extensions import ExtendedOperations as Operations
        runtime=Operations(kwargs.get('_desktop_runtime'),kwargs.get('directory','.'))
    options={k:v for k,v in kwargs.get('action',{}).items() if k not in {'command','label'}}
    return runtime.elements_check(**options)

def test(**kwargs):
    return define()['name_short']==['data_elements_check']
