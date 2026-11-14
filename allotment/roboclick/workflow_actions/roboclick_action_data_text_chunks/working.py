"""Reusable data_text_chunks primitive; no platform workflow."""
def define():
    return {'name':'roboclick_action_data_text_chunks','name_short':['data_text_chunks'],'category':'Workflow primitives'}

def action(**kwargs):
    runtime=kwargs.get('_workflow_runtime')
    if runtime is None:
        from roboclick_workflow import Operations
        desktop=kwargs.get('_desktop_runtime')
        runtime=Operations(desktop,kwargs.get('directory','.'))
    options={k:v for k,v in kwargs.get('action',{}).items() if k not in {'command','label'}}
    return runtime.text_chunks(**options)

def test(**kwargs):
    return define()['name_short']==['data_text_chunks']
