"""Local desktop navigate action; no AI or debugging connection."""
def define():
    return {"name": "roboclick_action_desktop_navigate", "name_short": ["desktop_navigate"], "category": "Desktop", "description": "navigate using ordinary desktop input and local evidence"}

def action(**kwargs):
    runtime = kwargs.get("_desktop_runtime")
    if runtime is None:
        from roboclick_desktop import runtime_for
        runtime = runtime_for(kwargs)
    config = {k:v for k,v in kwargs.get("action", {}).items() if k not in {"command","label","when","submit"}}
    return runtime.navigate(**config)

def test(**kwargs):
    # Pure metadata check; never click the user's desktop during action discovery.
    return define()["name"] == "roboclick_action_desktop_navigate"
