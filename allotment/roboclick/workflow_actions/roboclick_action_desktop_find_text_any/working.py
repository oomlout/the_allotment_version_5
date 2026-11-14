"""Reusable desktop_find_text_any primitive; no platform workflow."""
def define():
    return {'name':'roboclick_action_desktop_find_text_any','name_short':['desktop_find_text_any'],'category':'Workflow primitives'}

def action(**kwargs):
    runtime=kwargs.get('_workflow_runtime')
    if runtime is None:
        from roboclick_workflow import Operations
        desktop=kwargs.get('_desktop_runtime')
        if desktop is None:
            from roboclick_desktop import runtime_for
            desktop=runtime_for(kwargs)
        runtime=Operations(desktop,kwargs.get('directory','.'))
    options={k:v for k,v in kwargs.get('action',{}).items() if k not in {'command','label'}}
    return runtime.find_text_any(**options)

def test(**kwargs):
    return define()['name_short']==['desktop_find_text_any']
