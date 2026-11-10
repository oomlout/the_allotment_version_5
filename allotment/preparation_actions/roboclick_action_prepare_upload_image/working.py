"""Unsent-draft action dispatched by the oomlout framework."""
def define():
    return {"name":"roboclick_action_prepare_upload_image","name_short":["prepare_upload_image"],"category":"Preparation only"}
def action(**kwargs):
    config={k:v for k,v in kwargs["action"].items() if k not in {"command","label","when"}}
    return kwargs["_preparation_session"].upload_image(**config)
def test(**kwargs):
    return True
