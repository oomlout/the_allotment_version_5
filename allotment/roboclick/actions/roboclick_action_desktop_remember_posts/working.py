def define():
    return {"name":"roboclick_action_desktop_remember_posts","name_short":["desktop_remember_posts"],"category":"Verification"}
def action(**kwargs):
    config={k:v for k,v in kwargs["action"].items() if k not in {"command","label","when","submit"}}
    return kwargs["_desktop_runtime"].remember_posts(**config)
def test(**kwargs):
    return True
