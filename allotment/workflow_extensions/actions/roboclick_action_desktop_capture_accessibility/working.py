"""Reusable desktop_capture_accessibility primitive; no platform workflow."""
def define():
    return {'name':'roboclick_action_desktop_capture_accessibility','name_short':['desktop_capture_accessibility'],'category':'Workflow primitives'}

def action(**kwargs):
    runtime=kwargs.get('_workflow_runtime')
    if runtime is None:
        from roboclick_workflow_extensions import ExtendedOperations as Operations
        runtime=Operations(kwargs.get('_desktop_runtime'),kwargs.get('directory','.'))
    options={k:v for k,v in kwargs.get('action',{}).items() if k not in {'command','label'}}
    return runtime.capture_accessibility(**options)

def test(**kwargs):
    return define()['name_short']==['desktop_capture_accessibility']
