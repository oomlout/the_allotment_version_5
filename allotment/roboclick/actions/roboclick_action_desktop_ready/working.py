def define():
    return {"name": "roboclick_action_desktop_ready", "name_short": ["desktop_ready"],
            "category": "Desktop", "description": "Leave a checked draft for manual submission"}


def action(**kwargs):
    config = {k: v for k, v in kwargs["action"].items() if k not in {"command", "label", "when"}}
    return kwargs["_desktop_runtime"].ready(**config)


def test(**kwargs):
    return define()["name"] == "roboclick_action_desktop_ready"
