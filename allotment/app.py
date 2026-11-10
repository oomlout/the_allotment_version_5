from __future__ import annotations

from datetime import datetime
from io import BytesIO
import os
from pathlib import Path
import re
import secrets
import shutil
import threading
from urllib.parse import urlparse
import zipfile

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_file, send_from_directory, session, url_for
import yaml

from .storage import BUILTINS, IMAGES, Store, git, inside, new_post_id, read_yaml, slug, valid_remote, write_yaml
from .website import build_site
from .worker import resolve_action
from .approval import run_folder
from .preparation_approval import approval_status, approve_run
from .testlab import TestManager
from .preparation import PreparationManager, preparation_runs as test_runs, is_preparation, folder_for as preparation_folder
from .config import save_studio_settings, studio_settings
from .chrome import browser_mode, launch_chrome
from .sowing import SOW_BUILTINS


def create_app(root=None, testing=False, garden=None):
    store = Store(root or Path(__file__).resolve().parent.parent)
    app = Flask(__name__)
    key = store.local / "session-secret"
    if not key.exists():
        key.write_text(secrets.token_hex(32), encoding="ascii")
    app.config.update(SECRET_KEY=key.read_text(encoding="ascii"), MAX_CONTENT_LENGTH=250 * 1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict", TESTING=testing,
                      TEMPLATES_AUTO_RELOAD=True)
    app.extensions["store"] = store
    selected = garden or studio_settings(store).get('garden')
    if selected:
        if isinstance(selected,str): selected=selected.split('/')
        store.profile(*selected)
    app.config['GARDEN'] = tuple(selected) if selected else None
    if garden:
        settings=studio_settings(store);settings['garden']=list(selected);write_yaml(store.local/'studio.yaml',settings)
    test_manager = TestManager(store)
    app.extensions["test_manager"] = test_manager
    preparation_manager = PreparationManager(store)
    app.extensions["preparation_manager"] = preparation_manager
    from .job_runs import JobRunManager,runs_for,folder_for as job_run_folder,progress_for
    job_manager=JobRunManager(store)
    app.extensions['job_manager']=job_manager
    run_start_guard = threading.Lock()

    def manager_for(test_id):
        return preparation_manager if test_id.startswith("prep-") else test_manager

    def test_folder(namespace,user,test_id):
        return preparation_folder(store,namespace,user,test_id) if test_id.startswith("prep-") else run_folder(store,namespace,user,test_id)

    from .test_batches import TestBatches
    from .test_review import catalog,review_run

    def ensure_desktop_free():
        if any(progress_for(folder).get('status') in {'queued','countdown','paused','running'} for folder in (store.local/'job-runs').glob('*/*/*')):
            raise ValueError('A post launch is active. Stop it or wait for it to finish.')
        if any(run['active'] for profile in store.profiles() for run in test_runs(store,profile['namespace'],profile['user'])):
            raise ValueError('Another browser run is active. Stop it or wait for it to finish.')

    def start_test_run(namespace,user,platform,mode):
        ensure_desktop_free()
        recipe=store.plugins().get(platform,{})
        if is_preparation(recipe):return preparation_manager.start(namespace,user,platform,mode=mode)
        if mode=='inspect':raise ValueError('Inspection is available for preparation-only platforms.')
        return test_manager.start(namespace,user,platform,guided=True,prepare=True)

    batches=TestBatches(store,start_test_run,lambda ns,u:test_runs(store,ns,u),
        lambda ns,u,tid:manager_for(tid).stop(ns,u,tid),
        lambda tid:tid not in manager_for(tid).children or manager_for(tid).children[tid].poll() is not None,run_start_guard)
    app.extensions['test_batches']=batches

    @app.before_request
    def csrf_protect():
        session.setdefault("csrf", secrets.token_urlsafe(24))
        if request.method == "POST":
            if not secrets.compare_digest(session["csrf"], request.form.get("csrf", "")):
                abort(400, "This form expired. Reload the page and try again.")
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                abort(400, "Cross-origin form submissions are not allowed.")

    @app.before_request
    def garden_binding():
        selected=app.config['GARDEN']
        if request.endpoint in {'static','choose_garden'}:return
        if not selected:return redirect(url_for('choose_garden'))
        args=request.view_args or {}
        if 'namespace' in args and (args['namespace'],args.get('user'))!=selected:abort(404)
        if request.endpoint=='setup':abort(404)

    @app.route('/choose-garden/',methods=['GET','POST'])
    def choose_garden():
        if app.config['GARDEN']:return redirect(url_for('index'))
        if request.method=='POST':
            selected=request.form.get('garden','').split('/')
            if len(selected)!=2:raise ValueError('Choose a garden folder.')
            store.profile(*selected)
            settings=studio_settings(store);settings['garden']=selected
            write_yaml(store.local/'studio.yaml',settings);app.config['GARDEN']=tuple(selected)
            return redirect(url_for('index'))
        return render_template('choose_garden.html',profiles=store.profiles())

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "same-origin"
        if not request.path.startswith(("/static/", "/allotment_static/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ValueError)
    def value_error(error):
        if request.headers.get("Accept")=="application/json":
            from .sowing import PlatformSetupRequired
            extra={}
            if isinstance(error,PlatformSetupRequired):
                args=request.view_args or {}
                extra={'platform':error.platform,'setup_url':url_for('destinations',namespace=args['namespace'],user=args['user'],_anchor='destination-'+error.platform)}
            return jsonify(error=str(error),**extra),400
        return render_template("error.html", error=str(error)), 400

    @app.errorhandler(413)
    def too_large(error):
        return render_template("error.html", error="Uploads are limited to 250 MB per request."), 413

    @app.template_filter("date")
    def date_filter(value):
        if not value:
            return ""
        try:
            return datetime.fromisoformat(value).strftime("%d %b %Y")
        except ValueError:
            return value

    @app.context_processor
    def common():
        return {"gardens": store.profiles(), "csrf": session.get("csrf"), "builtin_names": BUILTINS}

    def context(namespace, user, active):
        return {"profile": store.profile(namespace, user), "namespace": namespace, "user": user,
                "active": active, "repo_path": str(store.repo(namespace, user))}

    def garden_url(namespace, user, **kwargs):
        return url_for("garden", namespace=namespace, user=user, **kwargs)

    def asset_url(namespace, user, post, attachment):
        if post.get("status") == "draft":
            return url_for("draft_media", namespace=namespace, user=user, post_id=post["id"], filename=attachment["file"])
        return url_for("garden_media", namespace=namespace, user=user, filename=f"{post['path']}/{attachment['file']}")

    app.jinja_env.globals["asset_url"] = asset_url

    @app.get("/")
    def index():
        return redirect(garden_url(*app.config['GARDEN']))

    @app.get("/g/<namespace>/<user>/")
    def garden(namespace, user):
        tab = request.args.get("tab", "published")
        posts = store.drafts(namespace, user) if tab == "drafts" else store.posts(namespace, user)
        from .tags import catalogue
        query=request.args.get('q','').strip();selected_tag=request.args.get('tag','')
        if selected_tag:posts=[p for p in posts if selected_tag in p.get('tags',[])]
        if query:posts=[p for p in posts if query.casefold() in p.get('text','').casefold()]
        return render_template("garden.html", **context(namespace, user, "garden"), posts=posts, tab=tab,
                               post_count=len(store.posts(namespace, user)), draft_count=len(store.drafts(namespace, user)),
                               catalogue=catalogue(store,namespace,user)['tags'],query=query,selected_tag=selected_tag)

    def sow_testing(namespace,user,platform,recipe,accounts):
        needs_test=platform not in SOW_BUILTINS and not approval_status(store,namespace,user,recipe,accounts.get(platform,{}))['valid']
        return {'needs_test':needs_test,'test_url':url_for('destinations',namespace=namespace,user=user,_anchor='destination-'+platform) if needs_test else ''}

    def compose_context(namespace, user, post_id=None, publishing=False):
        post = store.draft(namespace, user, post_id) if post_id else {"id": new_post_id(), "text": "", "status": "draft", "attachments": []}
        from .tags import catalogue
        view_context={**context(namespace,user,'compose'),'post':post,'is_new':not post_id,
              'catalogue':catalogue(store,namespace,user)['tags']}
        if not publishing:return view_context
        states = store.destination_states(namespace, user, post_id) if post_id else {}
        accounts = store.settings(namespace, user).get("destinations", {})
        destinations = []
        from .sow_review import blocks_retry,destination_view
        from .sowing import selectable_destinations
        for name, data in selectable_destinations(store).items():
            state = states.get(name, {})
            blocked = blocks_retry(name,state,SOW_BUILTINS)
            configured = name in BUILTINS or (accounts.get(name, {}).get("enabled") and approval_status(store, namespace, user, data, accounts.get(name, {}))["valid"])
            reason = "" if configured else "Test required"
            if blocked:
                reason = "Published" if state.get("status") == "succeeded" else f"{state.get('status', 'queued').title()} — review the previous run"
            destinations.append({"id": name, "name": data["name"], "state": state, "disabled": blocked or not configured,
                                 "reason": reason, "selected": False, 'configured':bool(configured), 'view':destination_view(name,state),
                                 **sow_testing(namespace,user,name,data,accounts),
                                 'target':accounts.get(name,{}).get('target_url') or accounts.get(name,{}).get('profile_url') or data.get('base_url','')})
        return {**view_context,'destinations':destinations}

    @app.get("/g/<namespace>/<user>/compose/")
    @app.get("/g/<namespace>/<user>/compose/<post_id>/")
    def compose(namespace, user, post_id=None):
        return render_template("compose.html", **compose_context(namespace, user, post_id))

    @app.post("/g/<namespace>/<user>/save")
    def save(namespace, user):
        post_id = request.form.get("post_id") or None
        intent=request.form.get('intent','save')
        inline=request.accept_mimetypes.best=='application/json'
        from .links import prepare_links
        existing=store.draft(namespace,user,post_id) if post_id and ((store.draft_dir(namespace,user,post_id)/'post.yaml').exists() or store.published(namespace,user,post_id)) else {}
        github_repo,links=prepare_links(request.form.get('github_repo',existing.get('github_repo','')),
            request.form.getlist('links') if 'links_present' in request.form else [v['url'] for v in existing.get('links',[]) if v.get('kind')!='github'],
            existing.get('links',[]),fetch=not inline)
        edit_file=None
        with store.garden_lock(namespace, user):
            if intent.startswith('image:'):
                from .images import checked_attachment
                if not post_id:raise ValueError('Save the image before editing it.')
                try:index=int(intent.split(':',1)[1])
                except ValueError:raise ValueError('Image not found.')
                edit_file=checked_attachment(store.draft(namespace,user,post_id),index)['file']
                if edit_file not in request.form.getlist('keep_media'):
                    raise ValueError('Keep this image selected to edit it.')
            post = store.save_draft(namespace, user, request.form.get("text", ""),
                                    request.files.getlist("media"), post_id, keep=request.form.getlist("keep_media"),
                                    tags=request.form.getlist('tags'),github_repo=github_repo,links=links)
        if inline:
            index=next((i for i,a in enumerate(post['attachments']) if a['type']=='image' and (edit_file is None or a['file']==edit_file)),None)
            return jsonify(post_id=post['id'],compose_url=url_for('compose',namespace=namespace,user=user,post_id=post['id']),
                media_html=render_template('post_media.html',namespace=namespace,user=user,post=post),
                editor_url=url_for('creation.image_editor',namespace=namespace,user=user,post_id=post['id'],index=index,embedded=1) if index is not None else None)
        if intent in {'publish','publishing'}:
            return redirect(url_for('post_page',namespace=namespace,user=user,post_id=post['id']))
        if intent=='images' or intent.startswith('image:'):
            index=next((i for i,a in enumerate(post['attachments']) if a['type']=='image' and (edit_file is None or a['file']==edit_file)),None)
            if index is not None:
                return redirect(url_for('creation.image_editor',namespace=namespace,user=user,post_id=post['id'],index=index))
        flash("Draft saved locally.", "success")
        return redirect(url_for("post_page", namespace=namespace, user=user, post_id=post["id"]))

    @app.get('/g/<namespace>/<user>/p/<post_id>/')
    def post_page(namespace,user,post_id):
        data=compose_context(namespace,user,post_id,publishing=True)
        data['active']='garden'
        jobs=[job for job in store.jobs(namespace,user) if job['post']['id']==post_id]
        runs=runs_for(store,namespace,user,post_id)
        from .framing import defaults,CHOICES
        from .harvest import overview
        return render_template('post.html',**data,jobs=jobs,plugins=store.plugins(),framing=defaults(store,namespace,user),framing_choices=CHOICES,
            harvest=overview(store,namespace,user,post_id),
            published=store.published(namespace,user,post_id),current_run=next((r for r in runs if r['status'] in {'queued','countdown','paused','running'}),runs[0] if runs else None))

    @app.post('/g/<namespace>/<user>/p/<post_id>/launch')
    def launch_post(namespace,user,post_id):
        with run_start_guard:
            if batches.active() or any(run['active'] for run in test_runs(store,namespace,user)):
                raise ValueError('A platform test is active. Stop it or wait for it to finish.')
            custom=save_overrides(namespace,user,post_id)
            run_id=job_manager.launch_post(namespace,user,post_id,request.form.getlist('actions'),custom)
        if request.accept_mimetypes.best=='application/json':return jsonify(id=run_id)
        return redirect(url_for('post_page',namespace=namespace,user=user,post_id=post_id))

    @app.get('/g/<namespace>/<user>/p/<post_id>/sow/status')
    def sow_status(namespace,user,post_id):
        from .sow_review import destination_view,blocks_retry
        from .lifecycle import post_folder
        post_folder(store,namespace,user,post_id)
        runs=runs_for(store,namespace,user,post_id)
        run=next((r for r in runs if r['status'] in {'queued','countdown','paused','running'}),runs[0] if runs else None)
        states=store.destination_states(namespace,user,post_id)
        values=[]
        plugins=store.plugins();accounts=store.settings(namespace,user).get('destinations',{})
        for action in [*SOW_BUILTINS,*plugins]:
            state=states.get(action,{})
            view=destination_view(action,state)
            view['blocked']=blocks_retry(action,state,SOW_BUILTINS)
            view.update(sow_testing(namespace,user,action,plugins.get(action,{}),accounts))
            view['review_url']=url_for('sow_review',namespace=namespace,user=user,post_id=post_id,job_id=view['job_id'],action=action) if view['job_id'] else ''
            values.append(view)
        if run:
            run={k:run.get(k) for k in ('id','kind','mode','status','phase','platform','operation','step','total_steps','remaining','countdown_until','error','destinations')}
            run['control_url']=url_for('job_run_control',namespace=namespace,user=user,run_id=run['id'])
        return jsonify(run=run,destinations=values)

    @app.post('/g/<namespace>/<user>/p/<post_id>/sow/<job_id>/<action>/review')
    def sow_review(namespace,user,post_id,job_id,action):
        from .sow_review import save_review
        record=save_review(store,namespace,user,post_id,job_id,action,request.form.get('verdict'))
        if request.accept_mimetypes.best=='application/json':return jsonify(review=record)
        return redirect(url_for('post_page',namespace=namespace,user=user,post_id=post_id))

    @app.post('/g/<namespace>/<user>/p/<post_id>/local/<action>')
    def local_publication(namespace,user,post_id,action):
        from .lifecycle import publish_local,unpublish_local
        if action not in {'publish','unpublish'}:abort(404)
        (publish_local if action=='publish' else unpublish_local)(store,namespace,user,post_id)
        return redirect(url_for('post_page',namespace=namespace,user=user,post_id=post_id))

    def save_overrides(namespace,user,post_id):
        from .framing import overrides,defaults
        from .lifecycle import post_folder
        custom=overrides({k.removeprefix('frame_'):v for k,v in request.form.items() if k.startswith('frame_')},defaults(store,namespace,user))
        with store.garden_lock(namespace,user):
            folder=post_folder(store,namespace,user,post_id)
            post=read_yaml(folder/'post.yaml');post['sowing_overrides']=custom;write_yaml(folder/'post.yaml',post)
        return custom

    @app.route('/g/<namespace>/<user>/defaults/',methods=['GET','POST'])
    def framing_defaults(namespace,user):
        from .framing import defaults,save_defaults,CHOICES
        from .sowing import SOW_BUILTINS
        if request.method=='POST':
            save_defaults(store,namespace,user,{k.removeprefix('frame_'):v for k,v in request.form.items() if k.startswith('frame_')})
            flash('Defaults saved.','success')
            return redirect(url_for('framing_defaults',namespace=namespace,user=user))
        return render_template('defaults.html',**context(namespace,user,'defaults'),framing=defaults(store,namespace,user),choices=CHOICES,
            names={**SOW_BUILTINS,**{k:v['name'] for k,v in store.plugins().items()}})

    @app.post('/g/<namespace>/<user>/p/<post_id>/harvest')
    def harvest_post(namespace,user,post_id):
        with run_start_guard:
            if batches.active():raise ValueError('All untested is running. Stop it or wait for it to finish.')
            ensure_desktop_free()
            job_manager.start_harvest(namespace,user,post_id,request.form.get('platform','bluesky'),request.form.get('mode','ids'),
                request.form.get('url','').strip(),request.form.get('test')=='1')
        return redirect(url_for('post_page',namespace=namespace,user=user,post_id=post_id))

    @app.post('/g/<namespace>/<user>/harvest/<run_id>/approve')
    def approve_harvest(namespace,user,run_id):
        from .harvest import approve
        run=approve(store,namespace,user,run_id)
        return redirect(url_for('post_page',namespace=namespace,user=user,post_id=run['post_id']))

    @app.get('/g/<namespace>/<user>/runs/<run_id>/')
    def job_run_status(namespace,user,run_id):
        return jsonify(progress_for(job_run_folder(store,namespace,user,run_id)))

    @app.post('/g/<namespace>/<user>/runs/<run_id>/control')
    def job_run_control(namespace,user,run_id):
        job_manager.control(namespace,user,run_id,request.form.get('action'))
        run=read_yaml(job_run_folder(store,namespace,user,run_id)/'run.yaml')
        if request.accept_mimetypes.best=='application/json':return jsonify(ok=True)
        return redirect(url_for('post_page',namespace=namespace,user=user,post_id=run['post_id']))

    @app.post("/g/<namespace>/<user>/records/<job_id>/resolve/<action>")
    def resolve(namespace, user, job_id, action):
        resolve_action(store, namespace, user, job_id, action, request.form.get("url", ""))
        flash("Published link recorded. This action will not be repeated.", "success")
        return redirect(url_for('post_page',namespace=namespace,user=user,post_id=read_yaml(store.job_path(namespace,user,job_id))['post']['id']))

    @app.route("/g/<namespace>/<user>/profile/", methods=["GET", "POST"])
    def profile_settings(namespace, user):
        if request.method == "POST":
            with store.garden_lock(namespace, user):
                profile = store.profile(namespace, user)
                website = request.form.get("website", "").strip()
                if website and urlparse(website).scheme not in {"http", "https"}:
                    raise ValueError("Website must be an HTTP(S) URL.")
                profile.update(display_name=request.form.get("display_name", user).strip() or user,
                               bio=request.form.get("bio", "").strip(), website=website)
                repo = store.repo(namespace, user)
                for field in ["avatar", "banner"]:
                    upload = request.files.get(field)
                    if upload and upload.filename:
                        suffix = Path(upload.filename).suffix.lower()
                        if suffix not in IMAGES:
                            raise ValueError("Profile assets must be JPG, PNG, GIF or WebP images.")
                        relative = f"profile/{field}-{secrets.token_hex(5)}{suffix}"
                        target = inside(repo, relative)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        upload.save(target)
                        profile[field] = relative
                write_yaml(repo / "profile.yaml", profile)
            flash("Profile saved.", "success")
            return redirect(url_for("profile_settings", namespace=namespace, user=user))
        return render_template("profile.html", **context(namespace, user, "profile"))

    @app.route("/g/<namespace>/<user>/destinations/", methods=["GET", "POST"])
    def destinations(namespace, user):
        if request.method == "POST":
            settings = store.settings(namespace, user)
            previous = settings.get("destinations", {})
            settings["destinations"] = dict(previous)
            selected=request.form.get('platform')
            if selected and selected not in store.plugins():raise ValueError('Unknown platform.')
            from .framing import save_defaults
            save_defaults(store,namespace,user,{k.removeprefix('frame_'):v for k,v in request.form.items() if k.startswith('frame_')})
            profile = store.profile(namespace, user)
            for name, recipe in store.plugins().items():
                if selected and name!=selected:continue
                profile_url = request.form.get(f"{name}_profile_url", "").strip()
                base_url = request.form.get(f"{name}_base_url", "").strip()
                browser_profile_url = request.form.get(f"{name}_browser_profile_url", previous.get(name,{}).get('browser_profile_url','')).strip()
                target_url = request.form.get(f"{name}_target_url", "").strip()
                if any(v and urlparse(v).scheme not in {"http", "https"} for v in [profile_url, base_url, target_url, browser_profile_url]):
                    raise ValueError("Destination URLs must use HTTP(S).")
                settings["destinations"][name] = {
                    **previous.get(name, {}),
                    "expected_account": request.form.get(f"{name}_account", "").strip(),
                    "profile_url": profile_url, "base_url": base_url,
                    "browser_profile_url": browser_profile_url,
                    "identity_locator": request.form.get(f"{name}_identity", "").strip() or None,
                    "target_url": target_url, "target_name": request.form.get(f"{name}_target_name", "").strip(),
                    "target_parent": request.form.get(f"{name}_target_parent",previous.get(name,{}).get('target_parent','')).strip(),
                    "target_locator": request.form.get(f"{name}_target_locator", "").strip() or None}
                account = settings["destinations"][name]
                account["publish_profile_link"] = f"{name}_publish_profile_link" in request.form
                valid = approval_status(store, namespace, user, recipe, account)["valid"]
                account.update(reviewed=valid, enabled=valid and f"{name}_enabled" in request.form)
                # Publish only the explicitly opted-in public address, never the browser target URL.
                if profile_url and account["publish_profile_link"]:
                    profile.setdefault("social", {})[name] = profile_url
                    profile.setdefault("social_names", {})[name] = recipe["name"]
                else:
                    profile.setdefault("social", {}).pop(name, None)
            write_yaml(store.settings_path(namespace, user), settings)
            with store.garden_lock(namespace, user):
                write_yaml(store.repo(namespace, user) / "profile.yaml", profile)
            flash("Destinations saved.", "success")
            return redirect(url_for("destinations", namespace=namespace, user=user))
        from .framing import defaults,CHOICES
        return render_template("destinations.html", framing=defaults(store,namespace,user),framing_choices=CHOICES,selected=request.args.get('platform',''),focus=request.args.get('focus',''), **context(namespace, user, "destinations"), plugins=store.plugins(),
                               accounts=store.settings(namespace, user).get("destinations", {}),
                               approvals={name: approval_status(store, namespace, user, recipe, store.settings(namespace, user).get("destinations", {}).get(name, {})) for name, recipe in store.plugins().items()})

    @app.get("/g/<namespace>/<user>/tests/")
    def platform_tests(namespace, user):
        return redirect(url_for('destinations',namespace=namespace,user=user,platform=request.args.get('platform','bluesky'),focus=request.args.get('focus','')))

    @app.get("/g/<namespace>/<user>/tests/status")
    def tests_status(namespace, user):
        store.profile(namespace, user)
        runs=[r for r in test_runs(store,namespace,user) if not request.args.get('platform') or r['platform']==request.args['platform']]
        if request.args.get('compact')=='1':
            seen=set();latest=[]
            for run in runs:
                key=(run['platform'],run.get('mode','prepare'))
                if key not in seen or run['active']:
                    latest.append({k:v for k,v in run.items() if k not in {'text','screenshots','events','result'}})
                    seen.add(key)
            runs=latest
        return jsonify(runs=runs,catalog=catalog(store,namespace,user),batch=batches.current(namespace,user))

    @app.post("/g/<namespace>/<user>/tests/start")
    def start_test(namespace, user):
        if request.form.get("intent") not in {"prepare_test", "inspect_test"}:
            raise ValueError("Automatic sending is disabled. Use Prepare only; click Post or Send yourself.")
        if "text" in request.form or request.files:
            raise ValueError("Test text and image are fixed. Reload the Tests page.")
        platform = request.form.get("platform", "")
        recipe = store.plugins().get(platform,{})
        intent = request.form["intent"]
        if is_preparation(recipe) and intent == "prepare_test" and recipe.get("preparation_ready") is not True:
            raise ValueError("Draft preparation awaits layout calibration. Use Inspect only; sending remains disabled.")
        if is_preparation(recipe) and intent == "send_test":
            raise ValueError("Sending is disabled for this platform. Use Inspect only or Prepare only.")
        if not is_preparation(recipe) and intent == "inspect_test":
            raise ValueError("Inspection is available for preparation-only platforms.")
        with run_start_guard:
            if batches.active():raise ValueError('All untested is running. Stop it or wait for it to finish.')
            test_id=start_test_run(namespace,user,platform,'inspect' if intent=='inspect_test' else 'prepare')
        if request.headers.get("Accept")=="application/json":return jsonify(id=test_id)
        flash("Test started.", "success")
        return redirect(url_for("platform_tests", namespace=namespace, user=user, focus=test_id,platform=platform,embedded=1))

    @app.post('/g/<namespace>/<user>/tests/all-untested/start')
    def start_untested(namespace,user):
        with run_start_guard:
            ensure_desktop_free()
            if 'text' in request.form or request.files:raise ValueError('Test payloads are fixed.')
            batch=batches.start(namespace,user,request.form.get('mode'))
        return jsonify(batch=batch)

    @app.post('/g/<namespace>/<user>/tests/all-untested/stop')
    def stop_untested(namespace,user):
        batches.stop(namespace,user)
        return jsonify(ok=True)

    @app.post('/g/<namespace>/<user>/tests/<test_id>/review')
    def review_test(namespace,user,test_id):
        return jsonify(review=review_run(store,namespace,user,test_id,request.form.get('verdict')))

    @app.post("/g/<namespace>/<user>/tests/<test_id>/countdown")
    def test_countdown(namespace, user, test_id):
        store.profile(namespace, user)
        manager_for(test_id).countdown_control(namespace, user, test_id, request.form.get("action"))
        return jsonify(ok=True)

    @app.post("/g/<namespace>/<user>/tests/<test_id>/stop")
    def stop_test(namespace, user, test_id):
        store.profile(namespace, user)
        manager_for(test_id).stop(namespace, user, test_id)
        if request.headers.get("Accept") == "application/json":
            return jsonify(ok=True)
        flash("Stop requested.", "success")
        return redirect(url_for("platform_tests", namespace=namespace, user=user,platform=read_yaml(test_folder(namespace,user,test_id)/'run.yaml')['platform'],embedded=1,approved='1' if request.endpoint=='approve_test' else ''))

    @app.post("/g/<namespace>/<user>/tests/<test_id>/approve")
    def approve_test(namespace, user, test_id):
        store.profile(namespace, user)
        if request.form.get("confirm_approval") != "yes":
            raise ValueError("Check the resulting test message and confirm it is correct before approval.")
        approve_run(store, namespace, user, test_id)
        if request.headers.get("Accept")=="application/json":return jsonify(ok=True)
        flash("Draft preparation approved. Post and Send remain manual.", "success")
        return redirect(url_for("platform_tests", namespace=namespace, user=user,platform=read_yaml(test_folder(namespace,user,test_id)/'run.yaml')['platform'],embedded=1,approved='1' if request.endpoint=='approve_test' else ''))

    @app.get("/g/<namespace>/<user>/tests/<test_id>/evidence/<filename>")
    def test_evidence(namespace, user, test_id, filename):
        store.profile(namespace, user)
        if not re.fullmatch(r"[a-zA-Z0-9_-]+\.(?:png|html|txt|jsonl|log)",filename):
            abort(404)
        path = test_folder(namespace,user,test_id) / filename
        if not path.is_file():
            abort(404)
        return send_file(path, as_attachment=path.suffix != ".png", download_name=filename)

    @app.get("/ai-platform-guide")
    def ai_platform_guide():
        platform = request.args.get("platform", "")
        filename = "PREPARATION_ONLY_GUIDE.md" if is_preparation(store.plugins().get(platform,{})) else "AI_PLATFORM_GUIDE.md"
        return send_file(Path(__file__).parent / "guides" / filename,as_attachment=True,download_name=filename)

    @app.route("/g/<namespace>/<user>/website/", methods=["GET", "POST"])
    def website(namespace, user):
        output = inside(store.output, f"{slug(namespace)}/{slug(user)}")
        if request.method == "POST":
            with store.garden_lock(namespace, user):
                profile = store.profile(namespace, user)
                accent, background = request.form.get("accent", ""), request.form.get("background", "")
                if not all(re.fullmatch(r"#[0-9a-fA-F]{6}", c) for c in [accent, background]):
                    raise ValueError("Theme colours must use six-digit hex values.")
                width = int(request.form.get("width", 760))
                layout, density = request.form.get("layout"), request.form.get("density")
                if not 480 <= width <= 1400 or layout not in {"timeline", "cards"} or density not in {"comfortable", "compact"}:
                    raise ValueError("Choose a valid layout, density and width (480–1400).")
                profile["theme"] = {"accent": accent, "background": background, "width": width,
                                    "layout": layout, "density": density, "custom_css": request.form.get("custom_css", "")}
                write_yaml(store.repo(namespace, user) / "profile.yaml", profile)
                build_site(store, namespace, user)
            flash("Website built.", "success")
            return redirect(url_for("website", namespace=namespace, user=user))
        return render_template("website.html", **context(namespace, user, "website"), built=(output / "index.html").exists(),
                               output_path=str(output))

    @app.get("/g/<namespace>/<user>/website/download")
    def download_site(namespace, user):
        folder = inside(store.output, f"{slug(namespace)}/{slug(user)}")
        if not (folder / "index.html").exists():
            raise ValueError("Build the website before downloading it.")
        memory = BytesIO()
        with zipfile.ZipFile(memory, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in folder.rglob("*"):
                if file.is_file():
                    archive.write(file, file.relative_to(folder))
        memory.seek(0)
        return send_file(memory, as_attachment=True, download_name=f"{namespace}-{user}-static.zip", mimetype="application/zip")

    @app.route("/setup/", methods=["GET", "POST"])
    def setup():
        if request.method == "POST":
            namespace, user = request.form.get("namespace", ""), request.form.get("user", "")
            store.create(namespace, user, request.form.get("display_name"), request.form.get("remote") or None,
                         clone=request.form.get("mode") == "clone")
            flash("Account added.", "success")
            return redirect(url_for("profile_settings", namespace=namespace, user=user))
        return render_template("setup.html", active="setup", profile=None)

    @app.post("/g/<namespace>/<user>/remote")
    def set_remote(namespace, user):
        remote = valid_remote(request.form.get("remote", "").strip())
        with store.garden_lock(namespace, user):
            repo = store.repo(namespace, user)
            verb = "set-url" if "origin" in git(repo, "remote").splitlines() else "add"
            git(repo, "remote", verb, "origin", remote)
        flash("Remote saved.", "success")
        return redirect(url_for("worker_settings", namespace=namespace, user=user))

    @app.route("/g/<namespace>/<user>/worker/", methods=["GET", "POST"])
    def worker_settings(namespace, user):
        if request.method == "POST":
            tesseract = request.form.get("tesseract", "").strip()
            if tesseract and not Path(tesseract).is_file():
                raise ValueError("Tesseract executable does not exist on this computer.")
            write_yaml(store.local / "worker.yaml", {"mode":"roboclick", "window_title":request.form.get("window_title", "").strip(), "tesseract":tesseract})
            flash("Browser settings saved.", "success")
            return redirect(url_for("worker_settings", namespace=namespace, user=user))
        repo = store.repo(namespace, user)
        remote = git(repo, "remote", "get-url", "origin") if "origin" in git(repo, "remote").splitlines() else ""
        worker = read_yaml(store.local / "worker.yaml")
        return render_template("worker.html", **context(namespace, user, "worker"), worker=worker, mode=browser_mode(worker), remote=remote,
                               studio=studio_settings(store))

    @app.post("/g/<namespace>/<user>/open-chrome-setup")
    def open_chrome_setup(namespace, user):
        store.profile(namespace, user)
        launch_chrome("https://bsky.app")
        flash("Chrome tab opened.", "success")
        return redirect(url_for("worker_settings", namespace=namespace, user=user))

    @app.post("/g/<namespace>/<user>/studio-settings")
    def studio_config(namespace, user):
        store.profile(namespace, user)
        settings = save_studio_settings(store, request.form.get("port"))
        flash(f"Studio port saved as {settings['port']}. Restart the Studio to use the new port.", "success")
        return redirect(url_for("worker_settings", namespace=namespace, user=user))

    @app.get("/media/<namespace>/<user>/<path:filename>")
    def garden_media(namespace, user, filename):
        path = inside(store.repo(namespace, user), filename)
        if not path.is_file() or path.suffix.lower() not in IMAGES | {".mp4", ".webm", ".mov"}:
            abort(404)
        return send_file(path, conditional=True)

    @app.get("/draft-media/<namespace>/<user>/<post_id>/<path:filename>")
    def draft_media(namespace, user, post_id, filename):
        path = inside(store.draft_dir(namespace, user, post_id), filename)
        if path.suffix.lower() not in IMAGES | {".mp4", ".webm", ".mov"}:
            abort(404)
        if not path.exists():
            post = store.published(namespace, user, post_id)
            if post:
                path = inside(store.repo(namespace, user), f"{post['path']}/{filename}")
        if not path.is_file():abort(404)
        return send_file(path, conditional=True)

    @app.get("/allotment_static/<namespace>/<user>/", defaults={"filename": "index.html"})
    @app.get("/allotment_static/<namespace>/<user>/<path:filename>")
    def public_site(namespace, user, filename):
        folder = inside(store.output, f"{slug(namespace)}/{slug(user)}")
        path = inside(folder, filename)
        if path.is_dir():
            path = path / "index.html"
        if not path.exists():
            abort(404)
        return send_file(path, conditional=True)

    from .creation import register_creation
    register_creation(app,store,context,asset_url)
    return app
