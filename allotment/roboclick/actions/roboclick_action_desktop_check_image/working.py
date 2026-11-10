def define():
    return {"name":"roboclick_action_desktop_check_image","name_short":["desktop_check_image"],"category":"Desktop"}
def action(**kwargs):
    config={k:v for k,v in kwargs["action"].items() if k not in {"command","label","when","submit"}}
    runtime=kwargs.get("_desktop_runtime")
    if runtime is None:
        from roboclick_desktop import runtime_for
        runtime=runtime_for(kwargs)
    return runtime.check_image(**config)
def test(**kwargs):
    return True
