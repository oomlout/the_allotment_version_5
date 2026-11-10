"""Local draft-only clients. Their Send buttons and Enter keys are traps."""
import copy
import html
from pathlib import Path
import threading

from flask import Flask, request
from werkzeug.serving import make_server
from allotment.storage import Store, read_yaml


def account_for(platform, base):
    account = {"expected_account":"right","profile_url":base+"/right"}
    if platform != "twitter":
        paths = {"discord":"/channels/123/456","telegram":"/k/#-12345","whatsapp":"/"}
        account.update(target_name="Test group",target_url=base+paths[platform])
    return account


def fixture_recipe(platform, base):
    recipe = read_yaml(Path(__file__).parent/'fixtures/preparation'/(platform+'.yaml'))
    recipe["base_url"] = base
    recipe["preparation_ready"] = True  # Only this disposable, controlled mock layout.
    for step in recipe["steps"]:
        command = step["command"]
        if command == "prepare_identity":
            step["selector"] = "#account"
            if step.get("owner_selector"): step["owner_selector"] = "#owner"
        elif command == "prepare_target":
            step.update(selector="#target-title",kind_selector="#target-kind",kind_attribute="data-kind",kind_pattern="^group$")
        elif command == "prepare_search_target":
            step.update(selector="#search",results_selector="#results .result",name_selector=".result-name",
                        kind_selector=".result-kind",kind_attribute="data-kind",kind_pattern="^group$")
        elif command == "prepare_focus":
            step["selector"] = "#caption" if step.get("when") == "media" else "#editor"
    return recipe


def fixture_html(platform):
    twitter = platform == "twitter"
    placeholder = {"twitter":"What is happening?!","discord":"Message #Test group",
                   "telegram":"Message","whatsapp":"Type a message"}[platform]
    caption = "Add a comment (optional)" if platform == "discord" else "Add a caption"
    editor = f'<div id="editor" contenteditable="true" role="textbox" data-placeholder="{placeholder}"></div>'
    # All selectors in this fixture are fixture-specific. The live YAML labels
    # and operation order remain unchanged, but are not claimed live-verified.
    return '''<!doctype html><html><head><meta charset="utf-8"><title>Allotment draft fixture</title><style>
    body{font:18px Arial;background:#eee;color:#111;margin:0}header{margin:60px auto;width:600px}
    aside{position:fixed;left:30px;top:180px;width:390px}#search{border:1px solid #aaa;padding:12px;background:#fff;min-height:35px;width:100%;box-sizing:border-box;font:18px Arial}
    .result{background:white;border:1px solid #aaa;padding:15px;cursor:pointer;margin-top:10px}.result-kind{display:block;font-size:12px;color:#444}
    .modal{position:fixed;top:18%;left:calc(50% - 310px);width:580px;padding:20px;background:white;border:1px solid #888;border-radius:15px;box-shadow:0 0 0 100vmax #0006}
    #editor,#caption{font:20px Arial;min-height:90px;white-space:pre-wrap;outline:1px solid #bbb;padding:12px;background:white;box-sizing:border-box}
    [contenteditable]:empty::before{content:attr(data-placeholder);color:#666;pointer-events:none}
    .normal{position:fixed;bottom:80px;left:calc(50% - 290px);width:580px}
    button{padding:10px 20px;background:#007aff;color:white;border:0;border-radius:18px;font:700 17px Arial;float:right}
    .preview{display:block;width:540px;height:304px;object-fit:contain;margin:12px 0}[hidden]{display:none!important}
    #target-kind{font-size:14px}
    </style></head><body><header><div id="account">Signed in as @right</div>
    <a id="owner" href="/settings/profile">Edit profile</a><h1 id="target-title">Test group</h1><span id="target-kind" data-kind="group">12 members</span></header>
    <aside><input id="search" type="search" placeholder="Search or start new chat"><div id="results" hidden>
    <div class="result"><span class="result-name">Test group</span><span class="result-kind" data-kind="group"> Group</span></div></div></aside>'''+(
        '<section class="modal"><button class="send">Post</button><h2>Draft</h2>'+editor+'<img id="preview" class="preview" hidden></section>' if twitter else
        '<section class="normal">'+editor+'</section><section id="media-modal" class="modal" hidden><button class="send">Send</button><h2>Image draft</h2><img id="preview" class="preview" hidden><div id="caption" contenteditable="true" role="textbox" data-placeholder="'+caption+'"></div></section>'
    )+'''<script>
    const editor=document.querySelector('#editor');
    document.querySelector('#search').addEventListener('input',()=>document.querySelector('#results').hidden=false);
    document.querySelector('.result').onclick=()=>{document.querySelector('#results').hidden=true;};
    editor.addEventListener('paste',event=>{
      for(const item of event.clipboardData.items){if(item.type.startsWith('image/')){
        event.preventDefault();const reader=new FileReader();reader.onload=()=>{
          const preview=document.querySelector('#preview');preview.src=reader.result;preview.hidden=false;
          const modal=document.querySelector('#media-modal');if(modal)modal.hidden=false;
        };reader.readAsDataURL(item.getAsFile());
      }}
    });
    const trap=why=>fetch('/unexpected-send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({why})});
    document.querySelectorAll('.send').forEach(button=>button.onclick=()=>trap('button'));
    document.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();trap('Enter');}});
    </script></body></html>'''


def serve_fixture(platform):
    app = Flask(__name__)
    sends = []
    @app.get('/',defaults={'path':''})
    @app.get('/<path:path>')
    def page(path):
        return fixture_html(platform)
    @app.post('/unexpected-send')
    def unexpected_send():
        sends.append(request.get_json())
        return {"error":"Sending is forbidden in this fixture"},400
    server = make_server('127.0.0.1',0,app,threaded=True)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    return server,sends
