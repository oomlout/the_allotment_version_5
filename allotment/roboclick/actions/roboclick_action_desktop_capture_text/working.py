"""Local desktop capture text action; no AI or debugging connection."""
def define():
    return {"name": "roboclick_action_desktop_capture_text", "name_short": ["desktop_capture_text"], "category": "Desktop", "description": "capture text using ordinary desktop input and local evidence"}

def action(**kwargs):
    runtime = kwargs.get("_desktop_runtime")
    if runtime is None:
        from roboclick_desktop import runtime_for
        runtime = runtime_for(kwargs)
    config = {k:v for k,v in kwargs.get("action", {}).items() if k not in {"command","label","when","submit"}}
    return runtime.capture_text(**config)

def test(**kwargs):
    # Pure metadata check; never click the user's desktop during action discovery.
    return define()["name"] == "roboclick_action_desktop_capture_text"
