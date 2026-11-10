import base64
from pathlib import Path
import threading

import pytest
from playwright.sync_api import sync_playwright,expect
from werkzeug.serving import make_server

from allotment.app import create_app
from allotment.storage import Store
from allotment.publication import run_worker


@pytest.fixture
def studio(tmp_path):
    store = Store(tmp_path); store.bootstrap()
    server = make_server("127.0.0.1", 0, create_app(tmp_path, testing=True,garden=('the_allotment','oomlout_main')), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield store, f"http://127.0.0.1:{server.server_port}", browser
        browser.close()
    server.shutdown(); server.server_close(); thread.join()


def test_browser_draft_media_publish_worker_theme_and_static_download(studio):
    store, origin, browser = studio
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    base = origin + "/g/the_allotment/oomlout_main"
    page.goto(base + "/compose/")
    page.get_by_label("Post text", exact=True).fill("A real browser test 🌱")
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aK1EAAAAASUVORK5CYII=")
    page.locator('input[name="media"]').set_input_files({"name": "test.png", "mimeType": "image/png", "buffer": png})
    page.get_by_role("button", name="Save draft", exact=True).click()
    page.get_by_text("Draft saved locally.", exact=True).wait_for()
    assert len(store.drafts("the_allotment", "oomlout_main")) == 1
    assert store.posts("the_allotment", "oomlout_main") == []
    assert page.locator(".single-post img").evaluate("image => image.complete && image.naturalWidth > 0")
    assert page.get_by_role('heading',name='Sow',exact=True).count()==1
    page.get_by_role('button',name='Publish locally',exact=True).click()
    page.get_by_role("button", name="Launch", exact=True).click()
    page.get_by_role("heading", name="Post", exact=True).wait_for()
    expect(page.locator('#job-run-state')).to_have_text('Complete',timeout=15000)
    assert len(store.jobs("the_allotment", "oomlout_main")) == 1
    assert store.jobs('the_allotment','oomlout_main')[0]['actions']['static']['status']=='succeeded'
    assert store.jobs('the_allotment','oomlout_main')[0]['actions']['summary']['status']=='succeeded'
    page.goto(base + "/website/")
    page.get_by_label("Cards", exact=True).check()
    page.get_by_text("Custom CSS", exact=True).first.click()
    page.get_by_label("Custom CSS", exact=True).fill(".post-text { color: #123456; }")
    page.get_by_role("button", name="Save & build website", exact=True).click()
    page.get_by_text("Website built.", exact=True).wait_for()
    with page.expect_download() as download:
        page.get_by_role("link", name="Download website", exact=True).click()
    assert download.value.suggested_filename == "the_allotment-oomlout_main-static.zip"
    page.goto(origin + "/allotment_static/the_allotment/oomlout_main/")
    assert page.locator("body").get_attribute("class") == "layout-cards density-comfortable"
    assert page.locator(".post-text").inner_text() == "A real browser test 🌱"
    assert page.locator(".post-text").evaluate("el => getComputedStyle(el).color") == "rgb(18, 52, 86)"
    assert page.locator(".media-grid img").evaluate("image => image.complete && image.naturalWidth > 0")
    page.get_by_role("link", name="Permalink ↗", exact=True).click()
    assert page.locator(".post-text").inner_text() == "A real browser test 🌱"
    page.close()


def test_mobile_studio_pages_and_public_feed_have_no_horizontal_overflow(studio):
    store, origin, browser = studio
    from allotment.website import build_site
    build_site(store, "the_allotment", "oomlout_main")
    page = browser.new_page(viewport={"width": 390, "height": 844})
    for suffix in ["", "compose/", "tags/", "profile/", "destinations/", "tests/", "website/", "worker/"]:
        page.goto(origin + "/g/the_allotment/oomlout_main/" + suffix)
        assert page.locator("h1").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), suffix
    page.goto(origin + "/allotment_static/the_allotment/oomlout_main/")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.close()


