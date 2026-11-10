def define():
    return {"name":"roboclick_action_desktop_check_owner","name_short":["desktop_check_owner"],"category":"Verification"}
def action(**kwargs):
    config={k:v for k,v in kwargs["action"].items() if k not in {"command","label","when","submit"}}
    return kwargs["_desktop_runtime"].check_owner(**config)
def test(**kwargs):
    return True
