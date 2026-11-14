"""Reusable data_read_text primitive; no platform workflow."""
def define():
    return {'name':'roboclick_action_data_read_text','name_short':['data_read_text'],'category':'Workflow primitives'}

def action(**kwargs):
    runtime=kwargs.get('_workflow_runtime')
    if runtime is None:
        from roboclick_workflow import Operations
        desktop=kwargs.get('_desktop_runtime')
        runtime=Operations(desktop,kwargs.get('directory','.'))
    options={k:v for k,v in kwargs.get('action',{}).items() if k not in {'command','label'}}
    return runtime.read_text(**options)

def test(**kwargs):
    return define()['name_short']==['data_read_text']
