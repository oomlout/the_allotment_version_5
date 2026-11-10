def define():
    return {"name":"roboclick_action_desktop_verify_post","name_short":["desktop_verify_post"],"category":"Verification"}
def action(**kwargs):
    config={k:v for k,v in kwargs["action"].items() if k not in {"command","label","when","submit"}}
    return kwargs["_desktop_runtime"].verify_post(**config)
def test(**kwargs):
    return True
