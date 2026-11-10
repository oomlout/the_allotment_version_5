"""Unsent-draft action dispatched by the oomlout framework."""
def define():
    return {"name":"roboclick_action_prepare_visual_target","name_short":["prepare_visual_target"],"category":"Preparation only"}
def action(**kwargs):
    config={k:v for k,v in kwargs["action"].items() if k not in {"command","label","when"}}
    return kwargs["_preparation_session"].visual_target(**config)
def test(**kwargs):
    return True
