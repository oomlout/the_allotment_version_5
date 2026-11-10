"""Unsent-draft action dispatched by the oomlout framework."""
def define():
    return {"name":"roboclick_action_prepare_search_group","name_short":["prepare_search_group"],"category":"Preparation only"}
def action(**kwargs):
    config={k:v for k,v in kwargs["action"].items() if k not in {"command","label","when"}}
    return kwargs["_preparation_session"].search_group(**config)
def test(**kwargs):
    return True
