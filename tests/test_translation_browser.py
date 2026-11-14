"""Headless Studio tests only; native AI input is checked by the desktop fixture."""
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import pytest
from playwright.sync_api import expect
from test_studio_browser import studio
from test_translations import prepared,pack
from test_sow_review import NS,USER
from allotment.translation_browser import validate_recipe,RECIPE
from allotment.translations import tags
from allotment.storage import read_yaml


def test_translate_feedback_review_and_reload(studio,monkeypatch):
    store,origin,browser=studio
    post,run,result=prepared(store,monkeypatch)
    page=browser.new_page(viewport={'width':1280,'height':1000})
    url=f'{origin}/g/{NS}/{USER}/p/{post["id"]}/'
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(url)
    panel=page.locator('#translation-panel')
    expect(panel).not_to_have_attribute('open','')
    panel.locator(':scope > summary').click()
    expect(panel.locator('.translation-result')).to_have_count(0)
    panel.get_by_role('button',name='Use versions for Sow',exact=True).click()
    panel.locator(':scope > summary').click()
    expect(panel.get_by_text('Reviewed versions selected for Sow.',exact=True)).to_be_visible()
    page.reload()
    panel.locator(':scope > summary').click()
    expect(panel.get_by_text('Reviewed versions selected for Sow.',exact=True)).to_be_visible()
    with page.expect_download() as download:panel.get_by_role('link',name='Download JSON').click()
    assert json.loads(Path(download.value.path()).read_text(encoding='utf8'))['posts']==result['posts']
    panel.get_by_role('button',name='Use original',exact=True).click()
    panel.locator(':scope > summary').click()
    expect(panel.get_by_text('Reviewed versions selected for Sow.',exact=True)).to_have_count(0)
    data=page.request.get(url+'sow/status').json();held=[]
    page.route('**/sow/status',lambda route:route.fulfill(json=data))
    page.route('**/translate',lambda route:held.append(route))
    button=panel.locator('[data-translate]')
    button.click();expect(button).to_have_text('Starting…');expect(button).to_be_disabled()
    data['run']={'id':'run-ui','kind':'translation','status':'running','phase':'handoff','remaining':5,'destinations':[],'operation':'Choose Chrome','control_url':url+'control'}
    held.pop().fulfill(json={'id':'run-ui'})
    expect(button).to_have_text('Choose Chrome · 5s')
    expect(panel.locator('#job-run')).to_be_visible()
    expect(panel.locator('[data-job-control] [value="stop"]')).to_be_visible()
    data['run'].update(phase='running',operation='Waiting for tagged platform versions',remaining=0)
    expect(panel.locator('#job-run-state')).to_have_text('Waiting for tagged platform versions')
    stamp=lambda seconds:(datetime.now(timezone.utc)+timedelta(seconds=seconds)).isoformat()
    data['run'].update(operation='Letting the pasted text settle',step_index=8,total_steps=21,
        item_label='Prompt chunk',item_index=2,item_total=30,detail='Allowing the composer to process this prompt chunk',
        wait_until=stamp(5),wait_reason='Allowing the composer to process this prompt chunk',wait_kind='settle')
    expect(panel.locator('[data-run-detail]')).to_contain_text('Step 8 of 21 · Prompt chunk 2 of 30')
    expect(panel.locator('[data-run-wait]')).to_contain_text('Continuing in')
    data['run'].update(operation='Waiting for platform drafts',step_index=20,item_index=None,item_total=None,
        wait_until=stamp(5),wait_kind='poll',wait_reason='ChatGPT is generating the platform drafts',
        poll_until=stamp(900),poll_reason='ChatGPT is generating the platform drafts',poll_attempt=3,detail='Response not complete yet')
    expect(panel.locator('[data-run-wait]')).to_contain_text('Next check in')
    expect(panel.locator('[data-run-deadline]')).to_contain_text('Check 3')
    page.reload()
    expect(panel.locator('[data-run-deadline]')).to_contain_text('until timeout')
    expect(panel.locator('[data-run-wait]')).to_contain_text('ChatGPT is generating')
    page.screenshot(path=str(store.local/'translation-progress.png'),full_page=True)
    data['run'].update(status='paused',remaining=7)
    expect(panel.locator('[data-run-wait]')).to_have_text('Paused by you. 7s of the starting countdown remains. Click Resume to continue.')
    expect(panel.locator('[data-run-deadline]')).to_be_hidden()
    data['run'].update(status='failed',error='No complete tagged response. Nothing was posted.')
    expect(panel.locator('#job-run-error')).to_have_text('No complete tagged response. Nothing was posted.')
    expect(button).to_be_enabled()
    expect(panel.locator('[data-run-wait]')).to_be_hidden()
    expect(panel.locator('[data-run-deadline]')).to_be_hidden()
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(store.local/'translation-ui.png'),full_page=True)
    assert not errors
    page.close()


