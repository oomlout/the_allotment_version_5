"""Unsent-draft action dispatched by the oomlout framework."""
def define():
    return {"name":"roboclick_action_prepare_preview_zoom","name_short":["prepare_preview_zoom"],"category":"Preparation only"}
def action(**kwargs):
    config={k:v for k,v in kwargs["action"].items() if k not in {"command","label","when"}}
    return kwargs["_preparation_session"].preview_zoom(**config)
def test(**kwargs):
    return True
