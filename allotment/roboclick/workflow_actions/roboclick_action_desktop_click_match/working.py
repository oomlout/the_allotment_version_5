"""Reusable desktop_click_match primitive; no platform workflow."""
def define():
    return {'name':'roboclick_action_desktop_click_match','name_short':['desktop_click_match'],'category':'Workflow primitives'}

def action(**kwargs):
    runtime=kwargs.get('_desktop_runtime')
    if runtime is None:
        from roboclick_desktop import runtime_for
        desktop=runtime_for(kwargs)
        runtime=desktop
    options={k:v for k,v in kwargs.get('action',{}).items() if k not in {'command','label'}}
    return runtime.click_match(**options)

def test(**kwargs):
    return define()['name_short']==['desktop_click_match']
