"""Preparation-only action. No send command is available."""
def define():
    return {"name":"roboclick_action_prepare_search_target","name_short":["prepare_search_target"],"category":"Preparation only"}
def action(**kwargs):
    config={k:v for k,v in kwargs["action"].items() if k not in {"command","label","when"}}
    return kwargs["_preparation_session"].search_target(**config)
def test(**kwargs):
    return True
