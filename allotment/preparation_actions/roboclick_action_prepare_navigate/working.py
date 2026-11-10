"""Preparation-only action. No send command is available."""
def define():
    return {"name":"roboclick_action_prepare_navigate","name_short":["prepare_navigate"],"category":"Preparation only"}
def action(**kwargs):
    config={k:v for k,v in kwargs["action"].items() if k not in {"command","label","when"}}
    return kwargs["_preparation_session"].navigate(**config)
def test(**kwargs):
    return True
