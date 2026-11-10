"""Oomlout roboclick workflow runner. Ordinary desktop input; no browser driver."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse, unquote

from .storage import inside, lock, read_yaml, slug
from .roboclick import framework
from .roboclick.desktop import Desktop, DesktopStopped

StopRequested = DesktopStopped
ENGINE = "oomlout_roboclick"
ACTIONS_ROOT = Path(__file__).parent / "roboclick" / "actions"


def available(recipe):
    return recipe.get("engine") == ENGINE


def validate_recipe(recipe):
    if not available(recipe):
        raise ValueError("This platform has not been migrated to oomlout roboclick. Bluesky is being implemented first.")
    slug(recipe["id"])
    lookup = framework.build_action_lookup(ACTIONS_ROOT)
    steps = recipe.get("steps", [])
    if not steps or any(step.get("command") not in lookup for step in steps):
        raise ValueError("Recipe has no steps or an unknown roboclick command.")
    allowed = {"desktop_navigate", "desktop_check_owner", "desktop_click_text",
               "desktop_paste_text", "desktop_capture_text", "desktop_paste_image",
               "desktop_find_image", "desktop_wait", "desktop_ready"}
    if recipe.get("sending_enabled") is not False or any(s.get("submit") or s["command"] not in allowed for s in steps):
        raise ValueError("Automatic sending is disabled. Use a preparation-only recipe; legacy publishing recipes are rejected.")
    for step in steps:
        if step["command"] == "desktop_click_text":
            safe_click_label(step.get("text", ""))
    if steps[-1]["command"] != "desktop_ready" or steps[-1].get("when"):
        raise ValueError("Recipe must finish at manual review without clicking Post or Send.")
    if not any(s["command"] == "desktop_check_owner" for s in steps[:-1]):
        raise ValueError("Recipe must check the signed-in owner before preparing a draft.")
    commands = [s["command"] for s in steps]
    required = ["desktop_check_owner", "desktop_paste_text", "desktop_capture_text", "desktop_ready"]
    if any(command not in commands for command in required) or [commands.index(c) for c in required] != sorted(commands.index(c) for c in required):
        raise ValueError("Draft preparation must check the owner, paste text, verify it, then stop for review.")
    if not any(s["command"] == "desktop_capture_text" and s.get("contains") == ["{{ text }}"] for s in steps):
        raise ValueError("Draft text must be checked against the exact queued text.")
    return recipe


def safe_click_label(text):
    label = str(text).strip().casefold()
    if not label or "{{" in label or (re.search(r"\b(send|post|publish|upload|reply|submit)\b", label) and label != "new post"):
        raise ValueError("Post and Send controls cannot be clicked. Final submission is manual.")


def expand(value, variables):
    if not isinstance(value, str):
        return value
    def replacement(match):
        key = match.group(1).strip()
        if key not in variables:
            raise ValueError(f"Recipe variable {key!r} is missing.")
        return str(variables[key])
    return re.sub(r"\{\{\s*([a-z_]+)\s*\}\}", replacement, value)


def check_payload(recipe, account, post, repo, testing=False):
    validate_recipe(recipe)
    if not testing and (not account.get("enabled") or not account.get("reviewed")):
        raise ValueError("Enable this destination and verify its YAML recipe on the worker computer first.")
    if not account.get("expected_account", "").strip():
        raise ValueError("Set the expected signed-in account identity in Destinations.")
    if recipe.get("target_required"):
        if not account.get("target_name", "").strip() or not account.get("target_url", "").strip():
            raise ValueError("Set the exact destination channel/group name and its browser URL in Destinations.")
        target = urlparse(account["target_url"])
        base = urlparse(account.get("base_url") or recipe["base_url"])
        if target.scheme not in {"http", "https"} or target.netloc != base.netloc:
            raise ValueError("The channel/group URL must belong to the configured platform.")
    if len(post["text"]) > recipe.get("max_chars", 100000):
        raise ValueError(f"Text exceeds this recipe's {recipe['max_chars']} character limit. Edit the post or recipe; text is never truncated.")
    if len(post["attachments"]) < recipe.get("min_media", 0):
        raise ValueError(f"{recipe.get('name', recipe['id'])} requires at least {recipe['min_media']} image/video attachment(s).")
    if len(post["attachments"]) > recipe.get("max_media", 0):
        raise ValueError("Too many attachments for this destination's recipe.")
    types = {attachment["type"] for attachment in post["attachments"]}
    if len(types) > 1 and not recipe.get("mixed_media", False):
        raise ValueError("This recipe does not support mixing images and video in the same post.")
    if sum(a["type"] == "video" for a in post["attachments"]) > recipe.get("max_videos", 1):
        raise ValueError("Too many videos for this destination's recipe.")
    for attachment in post["attachments"]:
        if attachment["type"] not in recipe.get("media_types", []):
            raise ValueError(f"This recipe does not support {attachment['type']} uploads yet.")
        if not inside(repo, f"{post['path']}/{attachment['file']}").is_file():
            raise ValueError("A queued attachment is missing.")
    if post["attachments"]:
        commands = [step["command"] for step in recipe["steps"]]
        if "desktop_paste_image" not in commands or "desktop_find_image" not in commands or commands.index("desktop_find_image") < commands.index("desktop_paste_image"):
            raise ValueError("The queued image must be pasted and checked before manual review.")


def canonical_post_text(content, expected):
    """Restore displayed URL labels from their verified link destinations."""
    from bs4 import BeautifulSoup
    copied=BeautifulSoup(str(content),"html.parser")
    urls=re.findall(r"https?://[^\s<>]+",expected)
    def key(url):
        parts=urlparse(url)
        return (parts.scheme.casefold(),parts.netloc.casefold(),parts.path or "/",parts.params,parts.query,parts.fragment)
    for anchor in copied.select("a[href]"):
        for raw in urls:
            target=raw.rstrip(".,;!?")
            while target.endswith(")") and target.count(")")>target.count("("):
                target=target[:-1]
            if key(anchor["href"])==key(target):
                anchor.replace_with(target)
                break
    return " ".join(copied.get_text("",strip=False).split())


class Session(Desktop):
    def capture_text(self, name="page", contains=None):
        value = super().capture_text(name=name, contains=contains)
        if contains and len(contains) == 1 and " ".join(value.split()) != " ".join(contains[0].split()):
            raise ValueError("The composer text does not exactly match the queued text.")
        return value

    def hotkey(self, keys):
        if any(str(key).lower() in {"enter", "return"} for key in keys) and not getattr(self, "_navigating", False):
            raise ValueError("Enter is blocked outside address-bar navigation. Submission is manual.")
        return super().hotkey(keys)

    def navigate(self, url, seconds=2):
        self._navigating = True
        try:
            return super().navigate(url, seconds)
        finally:
            self._navigating = False

    def click_text(self, text, **kwargs):
        safe_click_label(text)
        return super().click_text(text=text, **kwargs)

    def ready(self, text, **kwargs):
        if not self.owner_verified:
            raise ValueError("The posting account has not been verified.")
        # Locate the final control for review, but never click it or move focus to it.
        self.find_text(text=text, **kwargs)
        self.screenshot(name="ready-for-manual-post")
        return {"prepared": True, "draft_checked": True, "verified": False, "sent": False,
                "manual_submission_required": True}

    def check_owner(self, profile_url, expected_account, owner_text, owner_selector, region=None):
        self.check_url(url=profile_url)
        captured = self.capture_text(name="account")
        expected = expected_account.lstrip("@").casefold()
        profile = unquote(urlparse(profile_url).path).rstrip("/").split("/")[-1].casefold()
        if profile != expected or not re.search(r"(?<![\w.@-])@"+re.escape(expected)+r"(?![\w.-])",captured.casefold()):
            raise ValueError("Profile URL and visible account do not match the configured posting identity.")
        from bs4 import BeautifulSoup
        page=Path(self.capture_page(name="owner-page"))
        owners=BeautifulSoup(page.read_text(encoding="utf-8"),"html.parser").select(owner_selector)
        if len(owners)!=1 or owners[0].name != "button" or owners[0].get_text(" ",strip=True).casefold() != owner_text.casefold():
            raise ValueError("The captured profile has no unique owner edit control. Sign in to the expected account first.")
        self.find_text(text=owner_text,region=region)
        self.owner_verified = True
        self.owner_url = profile_url
        self.screenshot(name="account-verified")

    def remember_posts(self, post_selector, permalink_pattern):
        from bs4 import BeautifulSoup
        path = self.capture_page(name="before-posts")
        soup = BeautifulSoup(Path(path).read_text(encoding="utf-8"),"html.parser")
        self.seen_urls = {urljoin(self.owner_url, a.get("href", "")) for post in soup.select(post_selector)
                          for a in post.select("a[href]") if re.search(permalink_pattern,a["href"])}
        return sorted(self.seen_urls)

    def verify_post(self, post_selector, text_selector, permalink_pattern, expected_account, text, image_selector="img", images=None, timeout=35):
        from bs4 import BeautifulSoup
        import time
        deadline = time.monotonic()+float(timeout)
        reason = "New post not found in the saved page."
        while True:
            self.check_url(url=self.owner_url)
            capture = Path(self.capture_page(name="published-page"))
            soup = BeautifulSoup(capture.read_text(encoding="utf-8"),"html.parser")
            found = []
            normalize = lambda value: " ".join(value.split())
            for post in soup.select(post_selector):
                content = post.select_one(text_selector)
                if content is None or canonical_post_text(content,text) != normalize(text):
                    continue
                urls = {urljoin(self.owner_url,a["href"]) for a in post.select("a[href]") if re.search(permalink_pattern,a["href"])}
                urls = {url for url in urls if urlparse(url).netloc == urlparse(self.owner_url).netloc
                        and unquote(urlparse(url).path).split("/")[2].casefold() == expected_account.lstrip("@").casefold()
                        and url not in self.seen_urls}
                if len(urls) == 1:
                    found.append((post,urls.pop()))
            if len(found) > 1:
                raise ValueError("More than one fresh post matches. Delivery is uncertain.")
            if found:
                post,url = found[0]
                try:
                    if images:
                        expected_urls = [urljoin(self.owner_url,img["src"]) for img in post.select(image_selector) if img.get("src")]
                        if len(expected_urls) < len(images):
                            raise ValueError("The new post is missing its attachment image.")
                        for image in images:
                            self.check_image(image=image,expected_urls=expected_urls,region=[.2,.1,.8,.96],threshold=.82)
                    self.screenshot(name="published-verified")
                    return {"url":url,"url_type":"post","verified":True,"capture":capture.name}
                except ValueError as error:
                    reason = str(error)
            if time.monotonic() > deadline:
                raise ValueError(reason+" Check the destination before retrying.")
            self.wait(1)
            self.navigate(url=self.owner_url,seconds=2)



@contextmanager
def browser_context(store, evidence=None, check_stop=None):
    with lock(store.local / "browser.lock"):
        session = Session(evidence or store.local / "evidence" / "manual", read_yaml(store.local / "worker.yaml"),check_stop)
        try:
            session.attach()
            yield session
        except Exception:
            try:
                session.screenshot(name="failure")
            except Exception:
                pass
            raise
        finally:
            # Leave the owned tab visible for inspection. Never close the user's Chrome.
            session.release()


def run_recipe(session, recipe, account, post, repo, before_submit=None, prepare=True, testing=False, check_stop=None, on_step=None):
    validate_recipe(recipe)
    check_payload(recipe,account,post,repo,testing=testing)
    variables = {"base_url":account.get("base_url") or recipe["base_url"], "profile_url":account.get("profile_url", ""),
                 "expected_account":account["expected_account"], "text":post["text"]}
    images = [str(inside(repo, f"{post['path']}/{a['file']}")) for a in post["attachments"]]
    if not variables["profile_url"] or urlparse(variables["profile_url"]).netloc != urlparse(variables["base_url"]).netloc:
        raise ValueError("Set the complete profile URL on the configured platform.")
    session.seen_urls = set()
    session.owner_verified = False
    lookup = framework.build_action_lookup(ACTIONS_ROOT)
    def resolve(value):
        if value == "{{ image }}":
            return images[0] if images else ""
        if value == "{{ images }}":
            return images
        if isinstance(value,dict):
            return {k:resolve(v) for k,v in value.items()}
        if isinstance(value,list):
            return [resolve(v) for v in value]
        return expand(value,variables)
    result = None
    for index, step in enumerate(recipe["steps"]):
        session.check_stop()
        if check_stop:
            check_stop()
        if step.get("when") == "media" and not images:
            continue
        if on_step:
            on_step(index,step,"before")
        session.wait(seconds=1.25)
        cfg = resolve(step)
        result = framework.run_action(action=cfg,_discovered_actions=lookup,_desktop_runtime=session)
        if on_step:
            on_step(index,step,"after")
    if not isinstance(result,dict) or not result.get("prepared") or not result.get("draft_checked") or result.get("sent") is not False:
        raise ValueError("The workflow did not finish a checked draft for manual submission.")
    return result
