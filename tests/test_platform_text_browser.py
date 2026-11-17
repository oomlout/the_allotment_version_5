import json
from playwright.sync_api import expect
from test_studio_browser import studio
from test_translations import prepared
from test_sow_review import NS,USER
from allotment.translations import execute,versions_for
from allotment.storage import write_yaml,now
from allotment.job_runs import folder_for


def test_import_edit_and_saved_copy_survive_reload(studio,monkeypatch):
    store,origin,browser=studio;post,run,pack=prepared(store,monkeypatch)
    page=browser.new_page(viewport={'width':1280,'height':1000})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto(f'{origin}/g/{NS}/{USER}/p/{post["id"]}/')
    panel=page.locator('#platform-text-panel');panel.locator(':scope > summary').click()
    assert panel.get_by_role('button',name='Save platform text',exact=True).bounding_box()['y'] < panel.locator('.platform-import').bounding_box()['y']
    panel.locator('.platform-import > summary').click()
    panel.get_by_label('JSON file',exact=True).set_input_files({'name':'pack.json','mimeType':'application/json','buffer':json.dumps(pack).encode()})
    panel.get_by_role('button',name='Load JSON',exact=True).click()
    expect(panel).not_to_have_attribute('open','')
    panel.locator(':scope > summary').click()
    expect(panel.get_by_text('Loaded for review. Save platform text to use it in Sow.',exact=True)).to_be_visible()
    medium=panel.locator('[data-platform-copy="medium"]');medium.locator(':scope > summary').click()
    expect(medium.get_by_label('Text for Medium',exact=True)).to_have_value(pack['posts']['medium']['message'])
    medium.get_by_label('Title for Medium',exact=True).fill('An independent title')
    medium.get_by_label('Text for Medium',exact=True).fill('A longer platform-specific description.\n\nSecond paragraph 🌱')
    twitter=panel.locator('[data-platform-copy="twitter"]');twitter.locator(':scope > summary').click()
    twitter.get_by_role('checkbox').uncheck()
    panel.get_by_role('button',name='Save platform text',exact=True).click()
    expect(page.get_by_text('Platform text saved for Sow.',exact=True)).to_be_visible()
    page.reload();expect(panel).not_to_have_attribute('open','');panel.locator(':scope > summary').click();medium.locator(':scope > summary').click()
    expect(medium.get_by_label('Text for Medium',exact=True)).to_have_value('A longer platform-specific description.\n\nSecond paragraph 🌱')
    assert set(versions_for(store,NS,USER,post))=={'medium'}
    assert store.draft(NS,USER,post['id'])['text']==post['text']
    with page.expect_download() as download:panel.get_by_role('link',name='Download saved JSON').click()
    from pathlib import Path
    downloaded=json.loads(Path(download.value.path()).read_text(encoding='utf8'))
    assert downloaded['posts']['medium']['title']=='An independent title'
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(store.local/'platform-text-mobile.png'),full_page=True)
    assert not errors
    panel.get_by_role('button',name='Use original for all platforms').click()
    assert versions_for(store,NS,USER,post)=={}
    page.close()


def test_captured_json_is_ready_in_editors_without_upload(studio,monkeypatch):
    store,origin,browser=studio;post,run,pack=prepared(store,monkeypatch)
    write_yaml(folder_for(store,NS,USER,run['id'])/'progress.yaml',{'status':'queued','updated_at':now()})
    monkeypatch.setattr('allotment.translation_browser.run_pipeline',lambda *args:pack)
    assert execute(store,NS,USER,run['id'])['status']=='complete'
    page=browser.new_page()
    page.goto(f'{origin}/g/{NS}/{USER}/p/{post["id"]}/')
    panel=page.locator('#platform-text-panel')
    expect(panel).not_to_have_attribute('open','')
    panel.locator(':scope > summary').click()
    expect(panel.get_by_text('Loaded for review. Save platform text to use it in Sow.',exact=True)).to_be_visible()
    medium=panel.locator('[data-platform-copy="medium"]');medium.locator(':scope > summary').click()
    expect(medium.get_by_label('Text for Medium',exact=True)).to_have_value(pack['posts']['medium']['message'])
    expect(medium.get_by_role('checkbox')).to_be_checked()
    assert versions_for(store,NS,USER,post)=={}
    medium.get_by_label('Text for Medium',exact=True).fill('Manual edit')
    expect(panel).to_have_attribute('open','')
    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))")
    expect(panel).not_to_have_attribute('open','')
    page.close()


def test_overlong_manual_text_saves_without_browser_length_error(studio,monkeypatch):
    store,origin,browser=studio;post,_,_=prepared(store,monkeypatch)
    page=browser.new_page()
    page.goto(f'{origin}/g/{NS}/{USER}/p/{post["id"]}/')
    panel=page.locator('#platform-text-panel');panel.locator(':scope > summary').click()
    medium=panel.locator('[data-platform-copy="medium"]');medium.locator(':scope > summary').click()
    medium.get_by_role('checkbox').check()
    medium.get_by_label('Title for Medium',exact=True).fill('Title')
    body='Body 🌱 '*1700
    medium.get_by_label('Text for Medium',exact=True).fill(body)
    panel.get_by_role('button',name='Save platform text',exact=True).click()
    expect(page.get_by_text('Platform text saved for Sow.',exact=True)).to_be_visible()
    page.reload();expect(panel).not_to_have_attribute('open','');panel.locator(':scope > summary').click();medium.locator(':scope > summary').click()
    expect(medium.get_by_label('Text for Medium',exact=True)).to_have_value(body[:9993])
    assert store.draft(NS,USER,post['id'])['text']==post['text']
    page.close()
