from pathlib import Path
from bs4 import BeautifulSoup
from playwright.sync_api import expect
from allotment.workflow_guide import render_yaml_guide
from test_sow_review import store,NS,USER,client_for
from test_studio_browser import studio


def test_guide_is_requested_html_and_never_starts_a_worker(store,monkeypatch):
    post=store.save_draft(NS,USER,'Manual post <script>unsafe()</script> 🌱')
    app,client,_=client_for(store)
    monkeypatch.setattr('allotment.job_runs.JobRunManager._launch',lambda *a:(_ for _ in ()).throw(AssertionError('No worker')))
    before={str(p) for p in store.local.rglob('*') if p.is_file()}
    response=client.get(f'/g/{NS}/{USER}/translation-guide/?post_id={post["id"]}')
    assert response.status_code==200 and response.mimetype=='text/html'
    assert '<script>unsafe()</script>' not in response.text
    soup=BeautifulSoup(response.text,'html.parser')
    assert soup.h1.text=='Translate — manual steps'
    prompt=next(d for d in soup.select('details') if d.summary.text.startswith('Complete prompt')).pre.text
    assert 'ACTUAL TASK' in prompt and 'ALLOTMENT_TRANSLATION_manual-' in prompt
    assert 'unsafe()' in prompt
    assert 'Submit' in response.text or 'Submitting' in response.text
    assert 'Platform text → Load JSON' in response.text
    assert before=={str(p) for p in store.local.rglob('*') if p.is_file()}
    assert client.get(f'/g/{NS}/{USER}/translation-guide.md').mimetype=='text/markdown'


def test_render_is_read_only_and_handles_legacy_yaml(tmp_path):
    path=tmp_path/'working.yaml'
    path.write_text('oomlout_ai_roboclick_1:\n  actions:\n  - command: custom\n',encoding='utf-8')
    html=render_yaml_guide(path,manual_instructions={'custom':'Do this by hand.'})
    assert 'Do this by hand.' in html and 'oomlout_ai_roboclick_1' in html
    assert list(tmp_path.iterdir())==[path]


def test_manual_guide_browser_layout(studio):
    store,origin,browser=studio
    post=store.save_draft(NS,USER,'Guide layout check')
    page=browser.new_page(viewport={'width':1280,'height':900})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(f'{origin}/g/{NS}/{USER}/translation-guide/?post_id={post["id"]}')
    expect(page.get_by_role('heading',name='Translate — manual steps')).to_be_visible()
    page.get_by_text('Complete prompt for this post — copy all text',exact=True).click()
    expect(page.get_by_role('button',name='Copy text',exact=True)).to_be_visible()
    page.locator('.steps input[type=checkbox]').first.check()
    expect(page.locator('.steps input[type=checkbox]').first).to_be_checked()
    page.screenshot(path=str(store.local/'manual-guide-desktop.png'),full_page=False)
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.screenshot(path=str(store.local/'manual-guide-mobile.png'),full_page=False)
    assert not errors
    page.close()
