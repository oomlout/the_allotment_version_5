"""Inspection and draft preparation only. This runner has no send operation.

Kept separate from the verified publishing engine so preparation work on a new
platform does not invalidate a successful Bluesky publishing test.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import urlparse

import psutil
from bs4 import BeautifulSoup

from .approval import signature
from .browser import StopRequested, expand
from .roboclick import framework
from .roboclick.desktop import Desktop, DesktopStopped
from .storage import inside, lock, now, read_yaml, slug, write_yaml
from .test_sample import SAMPLE_IMAGE, SAMPLE_TEXT
from .testlab import ACTIVE, test_runs as publishing_runs
from .preparation_visual import VisualActions, safe_control


ENGINE = "oomlout_roboclick_prepare"
ACTIONS_ROOT = Path(__file__).with_name("preparation_actions")
COMMANDS = {"prepare_navigate", "prepare_identity", "prepare_target", "prepare_search_target",
            "prepare_focus", "prepare_text", "prepare_image", "prepare_verify_text", "prepare_finish",
            "prepare_visual_identity", "prepare_visual_target", "prepare_open", "prepare_visual_focus",
            "prepare_search_group", "prepare_upload_image", "prepare_preview_zoom"}
TERMINAL = {"inspected", "prepared", "failed", "stopped"}


def is_preparation(recipe):
    return recipe.get("engine") == ENGINE


def folder_for(store, namespace, user, run_id):
    return store.local / "preparation-tests" / slug(namespace) / slug(user) / slug(run_id)


def fingerprint(store, recipe, account):
    files = [Path(__file__), *[Path(__file__).with_name(name) for name in
             ('preparation_visual.py','preparation_approval.py','preparation_worker.py')], *sorted(ACTIONS_ROOT.glob("*/working.py"))]
    files += sorted((Path(__file__).parent / 'platform_assets' / recipe.get('id','unknown')).glob('*.png'))
    if account.get('local_template_set'):
        files += sorted((store.local/'platform-assets'/slug(account['local_template_set'])/slug(recipe.get('id','unknown'))).glob('*.png'))
    digest = hashlib.sha256(b"".join(p.name.encode()+p.read_bytes() for p in files)).hexdigest()
    extra = json.dumps({k:account.get(k, '') for k in ('browser_profile_url','local_template_set','target_parent')}, sort_keys=True)
    return hashlib.sha256((signature(store, recipe, account)+digest+extra).encode()).hexdigest()


def validate_preparation(recipe):
    if not is_preparation(recipe) or recipe.get("sending_enabled") is not False:
        raise ValueError("This is not a preparation-only recipe. Sending must be disabled.")
    steps = recipe.get("steps", [])
    if recipe.get('inspection_only'):
        if recipe.get('preparation_ready') or len(steps) != 1 or set(steps[0]) - {'command','label','url','seconds'} or steps[0].get('command') != 'prepare_navigate':
            raise ValueError('Inspection-only recipes can only navigate; drafting must remain blocked.')
        return recipe
    if not steps or steps[0].get("command") != "prepare_navigate" or steps[-1].get("command") != "prepare_finish":
        raise ValueError("Preparation needs navigation and a final draft check.")
    if any(s.get("command") not in COMMANDS or any(k in s for k in ("submit", "key", "keys", "action")) for s in steps):
        raise ValueError("Preparation recipes cannot contain Send, Post, keyboard shortcuts or unknown actions.")
    commands = [s["command"] for s in steps]
    if not set(commands) & {"prepare_identity", "prepare_visual_identity"} or "prepare_verify_text" not in commands:
        raise ValueError("Preparation requires account and editor-text checks.")
    if recipe.get("target_required") and not set(commands) & {"prepare_target", "prepare_visual_target"}:
        raise ValueError("A group/channel recipe requires an exact destination check.")
    for i, command in enumerate(commands):
        if command == 'prepare_open' and steps[i].get('text'):
            safe_control(steps[i]['text'])
        if command in {"prepare_text", "prepare_image", "prepare_upload_image"}:
            earlier = commands[:i]
            if not set(earlier) & {"prepare_identity", "prepare_visual_identity"} or (command != 'prepare_upload_image' and not set(earlier) & {"prepare_focus", "prepare_visual_focus"}):
                raise ValueError("Check the account and focus an editor before entering the sample.")
            if recipe.get("target_required") and not set(earlier) & {"prepare_target", "prepare_visual_target"}:
                raise ValueError("Check the destination before entering the sample.")
    return recipe


def variables_for(recipe, account, *, mode="prepare"):
    validate_preparation(recipe)
    if mode not in {"inspect", "prepare"}:
        raise ValueError("Only Inspect only and Prepare only are supported. Sending is disabled.")
    if mode == "prepare" and recipe.get("preparation_ready") is not True:
        raise ValueError("Draft preparation awaits layout calibration. " + recipe.get('setup_note', 'Use Inspect only; sending remains disabled.'))
    base = (account.get("base_url") or recipe["base_url"]).rstrip("/")
    parsed = urlparse(base)
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Set a valid browser base URL.")
    expected = account.get("expected_account", "").strip()
    if mode == "prepare" and not expected:
        raise ValueError("Configure the signed-in account in Destinations first.")
    handle = expected.lstrip("@")
    if expected and recipe.get("handle_pattern") and not re.fullmatch(recipe["handle_pattern"], handle):
        raise ValueError("Configure the exact account handle, without a display name.")
    target_url = account.get("target_url", "").strip()
    target_name = account.get("target_name", "").strip()
    if recipe.get("target_required"):
        if not target_url or not target_name:
            raise ValueError("Configure the exact existing group/channel name and browser URL first.")
        target = urlparse(target_url)
        if target.scheme != parsed.scheme or target.netloc != parsed.netloc or target.username or target.password:
            raise ValueError("The destination URL must belong to the configured browser platform.")
        if target.query and not recipe.get('target_query_pattern'):
            raise ValueError("Use the normal group/channel URL without query parameters.")
        if recipe.get('target_query_pattern') and not re.fullmatch(recipe['target_query_pattern'],target.query):
            raise ValueError('Destination query does not match the configured page type.')
        # Patterns are deliberately conservative; public links and invite links
        # are not substitutes for the existing private group/channel browser URL.
        if not re.fullmatch(recipe["target_path_pattern"], target.path):
            raise ValueError("Use the existing group/channel browser URL, not a profile, DM or invite link.")
        if recipe.get("target_fragment_pattern") and not re.fullmatch(recipe["target_fragment_pattern"], target.fragment):
            raise ValueError("Use the configured Telegram Web K destination type: numeric group ID for groups, or the channel username for channels.")
        if target.fragment and not recipe.get("target_fragment_pattern"):
            raise ValueError("Use the normal group/channel URL without a fragment.")
        if recipe.get("target_no_query") and (target.query or target.fragment):
            raise ValueError("Use the normal WhatsApp Web address; invite and send-to links are not supported.")
    profile_url = account.get('browser_profile_url') or account.get('profile_url') or base+'/'+handle
    return {"base_url":base,"handle":handle,"expected_account":expected,"profile_url":profile_url,
            "target_url":target_url,"target_name":target_name,"target_parent":account.get('target_parent','').strip()}


def _single(soup, selector, label):
    if not selector or selector.strip() in {"*", "body", "html"}:
        raise ValueError(f"Set a scoped captured-HTML selector for {label}.")
    nodes = soup.select(selector)
    if len(nodes) != 1:
        raise ValueError(f"Expected one {label} in captured HTML; found {len(nodes)}. No sample entered.")
    return nodes[0]


def _editable(node):
    return (node.get("contenteditable") == "true" or node.name == "textarea"
            or node.name == "input" and node.get("type", "text") in {"text", "search"})


class PreparationSession(VisualActions, Desktop):
    """Enter is allowed solely inside address-bar navigation, never in editors."""
    def configure(self, recipe, account, post, repo, variables):
        self.recipe, self.account, self.post, self.variables = recipe, account, post, variables
        self.image_path = str(inside(repo, f"{post['path']}/{post['attachments'][0]['file']}")) if post["attachments"] else None
        self.account_checked = self.target_checked = self.text_checked = self.image_pasted = self.focused = False
        self._navigating = False
        self.identity_config = self.target_config = None
        self.allowed_urls = ({variables["target_url"].rstrip("/")} if recipe.get("target_required") else
                             {variables["base_url"]+"/"+variables["handle"],variables["base_url"]+"/compose/post"})
        for step in recipe['steps']:
            if step['command']=='prepare_navigate':
                self.allowed_urls.add(expand(step['url'],variables).rstrip('/'))

    def hotkey(self, keys):
        if {str(k).casefold() for k in keys} & {"enter", "return"} and not getattr(self,"_navigating",False):
            raise ValueError("Sending is disabled: Enter cannot be used outside URL navigation.")
        return super().hotkey(keys)

    def navigate(self, url, seconds=2):
        # attach() opens about:blank before configure(); it is still ordinary URL
        # navigation. Recipe URLs are constrained to the configured platform.
        if url != "about:blank" and hasattr(self,"variables"):
            actual, expected = urlparse(url), urlparse(self.variables["base_url"])
            if (actual.scheme,actual.netloc) != (expected.scheme,expected.netloc):
                raise ValueError("Preparation cannot navigate outside the configured platform.")
            if url.rstrip("/") not in self.allowed_urls:
                raise ValueError("Preparation can open only the configured destination or its draft composer.")
        self._navigating = True
        try:
            self.restore_preview_zoom()
            result=super().navigate(url=url,seconds=seconds)
            # Chrome remembers zoom per site, including across new tabs.
            # Reset after navigation, not just on the initial about:blank tab.
            self.reset_browser_zoom()
            self.wait(.4)
            return result
        finally:
            self._navigating = False
            self.focused = False

    def check_url(self, url, exact=True):
        actual = super().check_url(url=url,exact=exact)
        expected,found = urlparse(url),urlparse(actual)
        if (expected.query,expected.fragment) != (found.query,found.fragment):
            raise ValueError(f"The browser changed the configured group fragment or query. Expected {url}, found {actual}. Preparation stopped.")
        if hasattr(self,"allowed_urls") and actual.rstrip("/") not in self.allowed_urls:
            raise ValueError("The browser left the configured destination. Preparation stopped.")
        return actual

    def captured(self, name):
        path = Path(self.capture_page(name=name))
        return BeautifulSoup(path.read_text(encoding="utf-8"),"html.parser")

    def focus_document(self):
        """Leave a visible editor with Shift+Tab, without activating a control."""
        for step in reversed(self.recipe["steps"]):
            if step["command"] not in {"prepare_focus", "prepare_visual_focus"}: continue
            if step.get("when") == "media" and not self.image_pasted: continue
            text=expand(step.get("text", ""),self.variables)
            if not text.strip(): continue
            if text.strip().casefold() in {"send","post","publish","upload"}:
                raise ValueError("A send control cannot be used to focus the document.")
            try:
                match=self.find_text(text=text,region=step.get("region"),panel=step.get("panel",False),timeout=.2)
            except StopRequested:
                raise
            except ValueError as error:
                if "Could not find" not in str(error) and "No unique light dialog panel" not in str(error): raise
                continue
            self.click_match(match)
            self.hotkey(keys=["shift","tab"])
            self.wait(.2)
            return
        # An inspection page may have no composer. A right click on a blank page
        # margin focuses its context without activating a link or send control.
        import numpy as np
        from PIL import Image
        region=self.recipe.get("page_focus_region",[.01,.18,.15,.85])
        shot=self.screenshot(name="document-focus",region=region)
        pixels=np.array(Image.open(shot["path"]).convert("RGB"))
        candidates=[]
        for y in range(8,max(9,pixels.shape[0]-40),32):
            for x in range(8,max(9,pixels.shape[1]-40),32):
                patch=pixels[y:y+24,x:x+24]
                if patch.shape[:2]==(24,24): candidates.append((float(patch.var(axis=(0,1)).mean()),x,y))
        if not candidates: raise ValueError("No page margin is available for document focus.")
        variance,x,y=min(candidates)
        if variance>5: raise ValueError("The page margin has no blank focus area. Inspect the screenshot and adjust page_focus_region.")
        left,top=shot["box"][0]+x,shot["box"][1]+y
        match={"x":left+12,"y":top+12,"box":[left,top,left+24,top+24],
               "time":shot["time"],"screen_box":shot["box"],"screenshot":shot["path"]}
        self.click_match(match,button="right")
        self.hotkey(keys=["esc"])
        self.wait(.2)

    def capture_page(self, name="page"):
        # Reading the address may restore an empty editor. Focus a blank part of
        # the web document before Copy, then require the account area in CF_HTML.
        captures = []
        selector = self.account.get("identity_locator") or next(
            (step["selector"] for step in self.recipe["steps"] if step["command"] == "prepare_identity"),'html')
        self.check_url(url=self.url)
        self.focus_document()
        attempts=2 if getattr(self,"inspection_only",False) else 5
        for attempt in range(attempts):
            self.check_stop()
            try:
                self.capture_text(name=f"{name}-{attempt+1}")
                raw=self.clipboard_html()
                if isinstance(raw,str): raw=raw.encode("utf-8")
                header=raw[:1024].decode("utf-8",errors="replace")
                start=re.search(r"StartHTML:(\d+)",header)
                end=re.search(r"EndHTML:(\d+)",header)
                source=re.search(r"SourceURL:([^\r\n]+)",header)
                if not start or not end or not source or source[1].rstrip("/") != self.url.rstrip("/"):
                    raise ValueError("Copied HTML has no matching source URL.")
                first,last=int(start[1]),int(end[1])
                if not 0<=first<last<=len(raw): raise ValueError("Copied HTML offsets are invalid.")
                path=self.evidence/f"{self.safe_name(name)}-{uuid.uuid4().hex[:8]}.html"
                path.write_bytes(raw[first:last])
                self.log("captured-page",{"path":path.name,"source":source[1],"bytes":last-first})
                captures.append(path)
                soup=BeautifulSoup(path.read_text(encoding="utf-8"),"html.parser")
                if len(soup.select(selector)) == 1:
                    self.check_url(url=self.url)
                    return str(path)
            except StopRequested:
                raise
            except ValueError as error:
                if not any(message in str(error) for message in ("Keyboard copy", "rendered HTML", "Copied HTML")):
                    raise
            if attempt < attempts-1:
                self.hotkey(keys=["shift","tab"])
                self.wait(.2)
        if getattr(self,"inspection_only",False) and captures:
            self.check_url(url=self.url)
            return str(max(captures,key=lambda path:path.stat().st_size))
        raise ValueError("Could not capture the whole page outside the editor. Inspect the saved screenshots before preparing a draft.")

    def capture_text(self, name="page", contains=None):
        # Give a rich editor's selection update a frame before invoking Copy.
        self.hotkey(keys=["ctrl","a"])
        self.wait(.35)
        value=self.copy()
        self.hotkey(keys=["right"])
        path=self.evidence/f"{self.sequence:03d}-{self.safe_name(name)}.txt"
        path.write_text(value,encoding="utf-8")
        for text in contains or []:
            if " ".join(text.split()) not in " ".join(value.split()):
                raise ValueError("Captured text is missing the expected content.")
        return value

    def _identity(self, soup, selector, owner_selector="", owner_text=""):
        selector = self.account.get("identity_locator") or selector
        node = _single(soup,selector,"signed-in account")
        expected = self.variables["expected_account"].lstrip("@").casefold()
        content = node.get_text(" ",strip=True)+" "+node.get("aria-label", "")
        if not expected or not re.search(r"(?<![\w.@#+-])@?"+re.escape(expected)+r"(?![\w.@#+-])",content.casefold()):
            raise ValueError("The captured signed-in account does not match the configured account.")
        if owner_selector:
            owner = _single(soup,owner_selector,"profile owner control")
            if owner.get_text(" ",strip=True).casefold() != owner_text.casefold():
                raise ValueError("The profile does not show the expected owner control.")

    def identity(self, selector, owner_selector="", owner_text=""):
        self.identity_config = {"selector":selector,"owner_selector":owner_selector,"owner_text":owner_text}
        self._identity(self.captured("account"),**self.identity_config)
        self.account_checked = True
        if not self.recipe.get("target_required"):
            self.target_checked = True
        self.screenshot(name="account-checked")

    def _target(self, soup, selector, kind_selector="", kind_pattern="", kind_attribute=""):
        title = _single(soup,self.account.get("target_locator") or selector,"group/channel heading")
        if " ".join(title.get_text(" ",strip=True).split()).casefold() != " ".join(self.variables["target_name"].split()).casefold():
            raise ValueError("The open destination title does not exactly match the configured group/channel.")
        if self.recipe.get("target_kind") == "group" and not (kind_selector and kind_pattern):
            raise ValueError("Group preparation requires a group-type check; a matching contact name is insufficient.")
        if kind_selector:
            marker = _single(soup,kind_selector,"destination type")
            value = marker.get(kind_attribute, "") if kind_attribute else marker.get_text(" ",strip=True)
            if not re.search(kind_pattern,value):
                raise ValueError("The destination type check failed. No sample entered.")

    def target(self, selector, kind_selector="", kind_pattern="", kind_attribute=""):
        if not self.account_checked:
            raise ValueError("Check the signed-in account first.")
        self.target_config = {"selector":selector,"kind_selector":kind_selector,"kind_pattern":kind_pattern,"kind_attribute":kind_attribute}
        self._target(self.captured("destination"),**self.target_config)
        self.target_checked = True
        self.screenshot(name="destination-checked")

    def search_target(self, selector, text, results_selector, name_selector, kind_selector,
                      kind_pattern, kind_attribute="", region=None):
        if not self.account_checked:
            raise ValueError("Check the account before searching for a group.")
        if text.strip().casefold() in {"send", "post", "publish", "upload"}:
            raise ValueError("A Send/Post/Upload control cannot be a search target.")
        soup = self.captured("before-search")
        node = _single(soup,selector,"group search box")
        if not _editable(node):
            raise ValueError("The search selector is not an editable search field.")
        self.click_text(text=text,region=region)
        self.paste_text(text=self.variables["target_name"])
        self.wait(1)
        results = self.captured("group-search-results")
        matches = []
        for row in results.select(results_selector):
            names = row.select(name_selector)
            if len(names) != 1 or names[0].get_text(" ",strip=True).casefold() != self.variables["target_name"].casefold():
                continue
            markers = row.select(kind_selector)
            if len(markers) == 1:
                value = markers[0].get(kind_attribute,"") if kind_attribute else markers[0].get_text(" ",strip=True)
                if re.search(kind_pattern,value):
                    matches.append(row)
        if len(matches) != 1:
            raise ValueError(f"Expected one exact group search result with a group marker; found {len(matches)}.")
        self.click_text(text=self.variables["target_name"],region=region,exact=True)
        self.wait(.5)

    def focus(self, selector, text, region=None, panel=False):
        if not self.account_checked or not self.target_checked:
            raise ValueError("Account and destination checks must pass before editing a draft.")
        soup = self.captured("before-editor")
        # Re-check the account; the profile owner control may be behind the
        # composer, so only the signed-in account selector is needed here.
        self._identity(soup,self.identity_config["selector"])
        if self.target_config:
            self._target(soup,**self.target_config)
        node = _single(soup,selector,"draft editor")
        if not _editable(node):
            raise ValueError("The editor selector points to a non-editable control. No click made.")
        if text.strip().casefold() in {"send", "post", "publish", "upload"}:
            raise ValueError("A Send/Post/Upload control cannot be an editor focus target.")
        self.click_text(text=text,region=region,panel=panel)
        self.focused = True

    def text(self):
        if not self.focused:
            raise ValueError("Focus a verified editor before entering the fixed sample.")
        self.paste_text(text=self.post["text"])
        self.text_checked = False

    def image(self):
        if not self.focused or not self.account_checked or not self.target_checked:
            raise ValueError("Check the account, destination and editor before pasting an image.")
        self.paste_image(image=self.image_path)
        self.image_pasted = True
        self.focused = False

    def verify_text(self, select_all=1):
        if not self.focused:
            raise ValueError("The sample editor is not focused.")
        if type(select_all) is not int or select_all not in (1,2):
            raise ValueError('Editor selection supports one or two Select All shortcuts.')
        if select_all == 2:
            # Block editors can select the current paragraph on the first Ctrl+A.
            # The second expands selection; exact copy-back still has to match.
            self.hotkey(keys=['ctrl','a'])
            self.wait(.2)
        value = self.capture_text(name="editor-text")
        if " ".join(value.split()) != " ".join(self.post["text"].split()):
            raise ValueError("The editor does not contain exactly the fixed sample. Preparation stopped.")
        self.text_checked = True

    def finish(self, region=None, panel=False, reference_region=None, threshold=.82, ready_text='', ready_region=None, ready_template='', generated_draft_path='', generated_draft_wait=0):
        if not self.account_checked or not self.target_checked or not self.text_checked:
            raise ValueError("Draft checks are incomplete.")
        if self.image_path:
            if not self.image_pasted:
                raise ValueError("The sample image was not pasted.")
            self.find_image(image=self.image_path,region=region,panel=panel,reference_region=reference_region,threshold=threshold)
        if generated_draft_path:
            self.accept_generated_draft(generated_draft_path, generated_draft_wait)
        self.check_url(url=self.url)
        if ready_text:
            self.find_text(text=ready_text,region=ready_region or region,exact=True)
        if ready_template:
            root=Path(__file__).parent/'platform_assets'/self.recipe['id']
            template=(root/ready_template).resolve()
            if not template.is_relative_to(root.resolve()) or template.name!='ready.png':
                raise ValueError('Use a bundled detection-only ready button.')
            self.find_image(image=str(template),region=ready_region or region,threshold=.95,scales=[1])
        self.restore_preview_zoom()
        shot = self.screenshot(name="prepared-no-send")
        return {"prepared":True,"verified":False,"sent":False,"url":self.url,"url_type":"draft",
                "draft_checked":True,"manual_submission_required":True,
                "account_checked":True,"target_checked":True,"text_checked":True,
                "image_checked":bool(self.image_path),"screenshot":Path(shot["path"]).name}

    def release(self):
        try:
            if getattr(self, '_preview_zoom_changed', False) and getattr(self, '_cleanup_input_blocked', ''):
                raise DesktopStopped(self._cleanup_input_blocked)
            self.restore_preview_zoom()
        except Exception as error:
            # Never steal focus, ignore Stop, or mask the original run failure.
            self.log('zoom-reset-skipped', {'reason':str(error),
                     'help':'Focus Chrome\'s address bar, then use Ctrl+0 to restore 100% zoom.'})
        finally:
            super().release()

    def accept_generated_draft(self, pattern, timeout=0):
        """Bind an autosaved editor URL after checking its content; never navigate."""
        if (self.recipe.get('target_required') or not self.account_checked
                or not self.target_checked or not self.text_checked
                or not pattern.startswith('^/') or not pattern.endswith('$')):
            raise ValueError('Generated draft URLs require a checked personal draft and an anchored path.')
        if type(timeout) not in (int,float) or not 0 <= timeout <= 15:
            raise ValueError('Draft autosave wait must be between zero and 15 seconds.')
        deadline=time.monotonic()+timeout
        while True:
            actual=self.read_url()
            found,base=urlparse(actual),urlparse(self.variables['base_url'])
            # Wait only while still on the exact original composer. A different
            # origin, query, fragment or unexpected editor must fail immediately.
            if (actual != getattr(self, 'url', None) or actual.rstrip('/') not in self.allowed_urls
                    or (found.scheme,found.netloc)!=(base.scheme,base.netloc)
                    or found.query or found.fragment or found.params or re.fullmatch(pattern,found.path)):
                break
            if time.monotonic() >= deadline: break
            self.log('draft-autosave-wait', {'url':actual})
            self.wait(.5)
        if ((found.scheme,found.netloc)!=(base.scheme,base.netloc)
                or found.query or found.fragment or found.params
                or not re.fullmatch(pattern,found.path)):
            raise ValueError(f'The autosaved URL is not the expected draft editor: {actual}. Draft left untouched; no navigation made.')
        self.allowed_urls.add(actual.rstrip('/'))
        self.url=actual


@contextmanager
def preparation_context(store, folder, check_stop=None):
    with lock(store.local / "browser.lock"):
        session = PreparationSession(folder,read_yaml(store.local / "worker.yaml"),check_stop)
        session.local=store.local
        try:
            session.attach()
            yield session
        except Exception as error:
            if isinstance(error, (StopRequested, DesktopStopped)):
                # F8's edge-triggered flag may have been consumed by the first
                # check. A later guard must not restart input during cleanup.
                session._cleanup_input_blocked = str(error)
            try:
                session.screenshot(name="failure")
            except Exception:
                pass
            raise
        finally:
            session.release()


def run_recipe(session, recipe, account, post, repo, *, mode="prepare", on_step=None):
    variables = variables_for(recipe,account,mode=mode)
    check_payload(recipe,account,post,repo,mode=mode)
    session.configure(recipe,account,post,repo,variables)
    session.inspection_only = mode == "inspect"
    def resolve(value):
        if isinstance(value,dict): return {k:resolve(v) for k,v in value.items()}
        if isinstance(value,list): return [resolve(v) for v in value]
        return expand(value,variables)
    lookup = framework.build_action_lookup(ACTIONS_ROOT)
    steps = recipe["steps"][:1] if mode == "inspect" else recipe["steps"]
    result = None
    for index,step in enumerate(steps):
        session.check_stop()
        if step.get("when") == "media" and not session.image_path: continue
        if step.get("when") == "no_media" and session.image_path: continue
        if on_step: on_step(index,step,"before")
        if step['command']=='prepare_upload_image':session.wait(seconds=1.25,dialog=True)
        else:session.wait(seconds=1.25)
        result = framework.run_action(action=resolve(step),_discovered_actions=lookup,_preparation_session=session)
        if on_step: on_step(index,step,"after")
    if mode == "inspect":
        capture,warning = None,""
        try:
            capture = session.capture_page(name="inspection")
        except (StopRequested,DesktopStopped):
            raise
        except ValueError as error:
            if not any(term in str(error) for term in ("Keyboard copy","HTML","whole page","page margin","light dialog")):
                raise
            warning = str(error)
        shot = session.screenshot(name="inspection")
        return {"inspected":True,"prepared":False,"verified":False,"sent":False,"url":session.url,"url_type":"inspection",
                "capture":Path(capture).name if capture else "","html_captured":bool(capture),"capture_warning":warning,
                "account_checked":False,"target_checked":False,"screenshot":Path(shot["path"]).name}
    if not isinstance(result,dict) or not result.get("prepared") or result.get("sent") is not False:
        raise ValueError("The recipe did not complete its draft checks.")
    return result


def check_payload(recipe, account, post, repo, *, mode='prepare'):
    variables_for(recipe,account,mode=mode)
    if len(post.get("attachments",[])) > 1 or any(a["type"] != "image" for a in post.get("attachments",[])):
        raise ValueError("Preparation currently supports text and at most one image.")
    if len(post.get('attachments',[]))<recipe.get('min_media',0):
        raise ValueError('This destination requires an image.')
    for attachment in post.get('attachments',[]):
        if not inside(repo,f"{post['path']}/{attachment['file']}").is_file():
            raise ValueError('A queued image is missing.')
    if len(post["text"]) > recipe.get("max_chars",1000):
        raise ValueError("The sample exceeds this recipe's text limit.")


def owned_process(folder):
    meta,run = read_yaml(folder / "process.yaml"),read_yaml(folder / "run.yaml")
    try:
        process = psutil.Process(meta["pid"])
        expected = ["-m","allotment","--root",meta["root"],"platform-prepare",run["namespace"],run["user"],run["id"]]
        if abs(process.create_time()-meta["created"]) > .1 or process.cmdline()[1:] != expected:
            return None
        return process if process.is_running() and process.status() != psutil.STATUS_ZOMBIE else None
    except (KeyError,psutil.Error):
        return None


def preparation_progress(folder):
    progress = read_yaml(folder / "progress.yaml",{"status":"queued","events":[]})
    termination = read_yaml(folder / "termination.yaml")
    if termination: return {**progress,**termination}
    if (folder / "stop.flag").exists() and progress["status"] not in TERMINAL:
        return {**progress,"status":"stopping"}
    if progress["status"] in ACTIVE and not owned_process(folder):
        return {**progress,"status":"failed","verified":False,"error":"The preparation process stopped. No send operation is available in this runner."}
    if progress['status'] in ACTIVE:
        from .roboclick.desktop import handoff_progress
        progress={**progress,**handoff_progress(folder)}
    return progress


def preparation_runs(store, namespace, user, test_id=None):
    from .preparation_approval import approval_status, checked
    results = publishing_runs(store,namespace,user,test_id=test_id) if not test_id or not test_id.startswith('prep-') else []
    accounts,plugins = store.settings(namespace,user).get("destinations",{}),store.plugins()
    parent = store.local / 'preparation-tests' / slug(namespace) / slug(user)
    paths = [parent / slug(test_id) / 'run.yaml'] if test_id else parent.glob('*/run.yaml')
    checks = {}
    for path in paths:
        if not path.is_file():continue
        run = read_yaml(path)
        progress = preparation_progress(path.parent)
        if run['platform'] not in checks:
            recipe,account=plugins.get(run['platform'],{}),accounts.get(run['platform'],{})
            checks[run['platform']] = (fingerprint(store,recipe,account),approval_status(store,namespace,user,recipe,account))
        stamp,approval=checks[run['platform']]
        current = run['signature'] == stamp
        results.append({"id":run["id"],"platform":run["platform"],"name":run["recipe"]["name"],"kind":"preparation",
                        "mode":run["mode"],"prepare":True,"guided":True,"browser_mode":"roboclick",
                        "expected_account":run["account"].get("expected_account") or "Not configured",
                        "site_url":run["variables"]["base_url"],"site_host":urlparse(run["variables"]["base_url"]).netloc,
                        "target":run["variables"]["target_name"] or run["variables"]["handle"],
                        "created_at":run["created_at"],"text":run["post"]["text"],"media_types":["image"],
                        **progress,"verified":False,"sent":False,"active":progress["status"] in ACTIVE,
                        "current":current,"can_approve":bool(current and checked(progress) and run['mode']=='prepare'
                            and not (path.parent/'stop.flag').exists() and not (path.parent/'termination.yaml').exists()),
                        "approved":approval['valid'] and run['id'] in approval.get('test_ids',[]),
                        "screenshots":sorted(p.name for p in path.parent.glob("*.png"))})
    return sorted(results,key=lambda run:run["created_at"],reverse=True)


class PreparationManager:
    def __init__(self, store):
        self.store,self.guard,self.children = store,threading.Lock(),{}
        for flag in (store.local / "preparation-tests").glob("*/*/*/stop.flag"):
            if not (flag.parent / "termination.yaml").exists():
                threading.Thread(target=self._stop_process,args=(flag.parent,),daemon=True).start()

    def start(self, namespace, user, platform, *, mode="prepare"):
        with self.guard:
            self.store.profile(namespace,user)
            recipe = self.store.plugins().get(platform,{})
            account = self.store.settings(namespace,user).get("destinations",{}).get(platform,{})
            variables = variables_for(recipe,account,mode=mode)
            for profile in self.store.profiles():
                if any(r["active"] for r in preparation_runs(self.store,profile["namespace"],profile["user"])):
                    raise ValueError("Another browser run is active. Stop it or wait for it to finish.")
            if (self.store.local / "browser.lock").exists():
                raise ValueError("The browser worker is busy.")
            run_id = "prep-"+uuid.uuid4().hex
            folder = folder_for(self.store,namespace,user,run_id)
            (folder / "media").mkdir(parents=True)
            shutil.copyfile(SAMPLE_IMAGE,folder / "media/test-image.png")
            post = {"id":run_id,"path":".","text":SAMPLE_TEXT+f"\n\n[Allotment test {run_id[-8:]}]",
                    "attachments":[{"type":"image","file":"media/test-image.png"}]}
            run = {"id":run_id,"namespace":namespace,"user":user,"platform":platform,"kind":"preparation",
                   "mode":mode,"created_at":now(),"recipe":recipe,"account":account,"variables":variables,
                   "post":post,"signature":fingerprint(self.store,recipe,account)}
            write_yaml(folder / "run.yaml",run)
            write_yaml(folder / "progress.yaml",{"status":"queued","phase":"switching","events":[],"verified":False,"submission_started":False})
            with (folder / "process.log").open("w",encoding="utf-8") as log:
                process = subprocess.Popen([sys.executable,"-m","allotment","--root",str(self.store.root),"platform-prepare",namespace,user,run_id],
                                           cwd=Path(__file__).resolve().parent.parent,stdout=log,stderr=log,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            write_yaml(folder / "process.yaml",{"pid":process.pid,"created":psutil.Process(process.pid).create_time(),"root":str(self.store.root)})
            self.children[run_id] = process
            threading.Thread(target=self._reap,args=(process,folder),daemon=True).start()
            return run_id

    def _reap(self, process, folder):
        process.wait()
        self._release_lock(folder)

    def countdown_control(self, namespace, user, run_id, action):
        folder = folder_for(self.store,namespace,user,run_id)
        progress = preparation_progress(folder)
        if progress["status"] not in {"queued","running"}:
            raise ValueError("The preparation countdown is no longer active.")
        if action == "pause" and progress.get("phase") == "switching":
            (folder / "pause.flag").write_text(now(),encoding="utf-8")
        elif action == "resume" and progress.get("phase") == "paused":
            (folder / "continue.flag").write_text(now(),encoding="utf-8")
        else:
            raise ValueError("There is no matching active countdown.")

    def stop(self, namespace, user, run_id):
        folder = folder_for(self.store,namespace,user,run_id)
        if not (folder / "run.yaml").exists(): raise ValueError("Preparation run not found.")
        if preparation_progress(folder)["status"] in TERMINAL: return
        (folder / "stop.flag").write_text(now(),encoding="utf-8")
        threading.Thread(target=self._stop_process,args=(folder,),daemon=True).start()

    def _stop_process(self, folder):
        deadline = time.monotonic()+2
        while owned_process(folder) and time.monotonic()<deadline: time.sleep(.05)
        process = owned_process(folder)
        if process:
            try:
                children = []
                for child in process.children(recursive=True):
                    # attach() never launches Chrome. Protect it regardless.
                    if child.name().casefold() not in {"chrome.exe","chrome","google-chrome","chrome_crashpad_handler.exe","crashpad_handler"}:
                        children.append(child)
                process.terminate()
                for child in reversed(children):
                    try: child.terminate()
                    except psutil.Error: pass
                _,alive = psutil.wait_procs([process,*children],timeout=1)
                for child in alive:
                    try: child.kill()
                    except psutil.Error: pass
            except psutil.Error:
                pass
        self._release_lock(folder)
        write_yaml(folder / "termination.yaml",{"status":"stopped","verified":False,"submission_started":False,
                                               "error":"Preparation stopped. No send operation was run.","finished_at":now()})

    def _release_lock(self, folder):
        owner = self.store.local / "browser.lock/owner.txt"
        meta = read_yaml(folder / "process.yaml")
        if owner.exists() and meta.get("pid") and f"pid={meta['pid']}\n" in owner.read_text(encoding="utf-8") and not owned_process(folder):
            shutil.rmtree(owner.parent,ignore_errors=True)


def run_preparation(store, namespace, user, run_id):
    folder = folder_for(store,namespace,user,run_id)
    run,progress = read_yaml(folder / "run.yaml"),read_yaml(folder / "progress.yaml")
    if not run or run.get("namespace") != namespace or run.get("user") != user or progress.get("status") != "queued":
        raise ValueError("Preparation runs are single-use. Start a fresh run.")
    def persist():
        progress["updated_at"] = now()
        write_yaml(folder / "progress.yaml",progress)
    def check_stop():
        if (folder / "stop.flag").exists(): raise StopRequested("Stop requested")
    def on_step(index, step, phase):
        check_stop()
        current = store.plugins().get(run["platform"],{})
        account = store.settings(namespace,user).get("destinations",{}).get(run["platform"],{})
        if fingerprint(store,current,account) != run["signature"]:
            raise ValueError("Preparation settings changed. Start a fresh run.")
        progress.update(phase="running",operation=step.get("label",step["command"]),step=index+1,
                        total_steps=1 if run["mode"] == "inspect" else len(run["recipe"]["steps"]))
        progress["events"].append({"at":now(),"step":index+1,"action":step["command"],"phase":phase})
        persist()
    try:
        check_stop()
        current=store.plugins().get(run['platform'],{})
        account=store.settings(namespace,user).get('destinations',{}).get(run['platform'],{})
        if fingerprint(store,current,account)!=run['signature']:raise ValueError('Settings changed. Start a fresh test.')
        from .launch import countdown_in_tab,starting_url
        url=starting_url(run['recipe'],run['account'],run['mode'])
        progress.update(status='running',phase='connecting',started_at=now());persist()
        with preparation_context(store,folder,check_stop) as session:
            def update_countdown(**values):
                progress.update(values);persist()
            countdown_in_tab(session,folder,url,update_countdown)
            result = run_recipe(session,run["recipe"],run["account"],run["post"],folder,mode=run["mode"],on_step=on_step)
        check_stop()
        progress.update(status="inspected" if run["mode"] == "inspect" else "prepared",phase="review",result=result,
                        url=result["url"],url_type=result["url_type"],verified=False,sent=False,submission_started=False,finished_at=now())
        if result.get("capture_warning"):
            progress["warning"] = "Screenshot saved. Page HTML is unavailable: "+result["capture_warning"]
    except Exception as error:
        progress.update(status="stopped" if isinstance(error,(StopRequested,DesktopStopped)) else "failed",phase="finished",
                        verified=False,sent=False,submission_started=False,error=str(error)[:3000],finished_at=now())
    persist()
    return progress
