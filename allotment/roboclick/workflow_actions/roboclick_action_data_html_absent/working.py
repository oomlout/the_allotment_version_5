"""Reusable data_html_absent primitive; no platform workflow."""
def define():
    return {'name':'roboclick_action_data_html_absent','name_short':['data_html_absent'],'category':'Workflow primitives'}

def action(**kwargs):
    runtime=kwargs.get('_workflow_runtime')
    if runtime is None:
        from roboclick_workflow import Operations
        desktop=kwargs.get('_desktop_runtime')
        runtime=Operations(desktop,kwargs.get('directory','.'))
    options={k:v for k,v in kwargs.get('action',{}).items() if k not in {'command','label'}}
    return runtime.html_absent(**options)

def test(**kwargs):
    return define()['name_short']==['data_html_absent']
