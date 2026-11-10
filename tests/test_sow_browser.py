from playwright.sync_api import expect
import pytest
from test_studio_browser import studio
from test_sow_review import attempted,NS,USER


def test_review_click_feedback_persists_after_reload_and_handles_errors(studio,monkeypatch):
    monkeypatch.setattr("allotment.app.approval_status",lambda *args:{"valid":True})
    store,origin,browser=studio;post,job=attempted(store)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    page.goto(f'{origin}/g/{NS}/{USER}/p/{post["id"]}/')
    row=page.locator('[data-destination="twitter"]');yes=row.locator('[data-verdict="successful"]');no=row.locator('[data-verdict="unsuccessful"]')
    expect(row.locator('[data-destination-status]')).to_have_text('Draft ready — review in Chrome')
    held=[]
    page.route('**/twitter/review',lambda route:held.append(route))
    yes.click();expect(row.locator('[data-destination-status]')).to_have_text('Saving review…')
    expect(yes).to_be_disabled();expect(no).to_be_disabled()
    held.pop().fulfill(status=400,json={'error':'Temporary write failure'})
    expect(row.locator('[data-destination-error]')).to_have_text('Temporary write failure')
    expect(yes).to_have_attribute('aria-pressed','false')
    page.unroute('**/twitter/review');yes.click()
    expect(yes).to_have_attribute('aria-pressed','true')
    page.reload();expect(page.locator('[data-destination="twitter"] [data-verdict="successful"]')).to_have_attribute('aria-pressed','true')
    expect(yes).to_have_text('✓');expect(no).to_be_hidden()
    assert row.locator('.sow-destination-heading [data-review]').count()==1
    page.screenshot(path=str(store.local/'sow-posted.png'),full_page=True)
    # A platform with current test approval remains selectable after posting.
    expect(row.locator('[name="actions"]')).to_be_enabled()
    expect(yes).to_have_attribute('aria-pressed','true')
    assert page.locator('#sow-panel [name="actions"]:disabled').count()==0
    link=row.locator('.sow-platform-name')
    assert link.get_attribute('target')=='_blank' and link.get_attribute('href').startswith('https://')
    row.locator('[name="actions"]').check()
    expect(page.locator('[data-launch]')).to_be_enabled()
    page.locator('#sow-panel').scroll_into_view_if_needed()
    page.screenshot(path=str(store.local/'sow-reviews.png'))
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.close()


def test_reset_reenables_selection_and_errors_reserve_exactly_two_lines(studio):
    from allotment.storage import write_yaml
    store,origin,browser=studio;post,job=attempted(store)
    job['actions']['git']['error']='A very long failure message. '*70
    write_yaml(store.job_path(NS,USER,job['id']),job)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    page.goto(f'{origin}/g/{NS}/{USER}/p/{post["id"]}/')
    row=page.locator('[data-destination="git"]');error=row.locator('[data-destination-error]')
    empty=page.locator('[data-destination="bluesky"] [data-testing-required]')
    assert abs(error.bounding_box()['height']-empty.bounding_box()['height'])<1
    assert error.evaluate('e => e.clientHeight <= parseFloat(getComputedStyle(e).lineHeight)*2+1')
    assert error.get_attribute('title')==job['actions']['git']['error']
    assert page.locator('#sow-panel [data-destination-reason]').count()==0
    row.locator('[data-verdict="unsuccessful"]').click()
    expect(row.locator('[data-review]')).to_be_hidden()
    expect(error).to_have_text('')
    expect(row.locator('[name="actions"]')).to_be_enabled()
    expect(row.locator('[name="actions"]')).not_to_be_checked()
    page.reload()
    expect(row.locator('[name="actions"]')).to_be_enabled()
    expect(error).to_have_text('')
    row.locator('[name="actions"]').check()
    expect(page.locator('[data-launch]')).to_be_enabled()
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.close()


@pytest.mark.parametrize('cached_template',[False,True])
def test_launch_is_immediate_progress_updates_without_reload(studio,cached_template):
    store,origin,browser=studio;post,job=attempted(store)
    page=browser.new_page(viewport={'width':1440,'height':1000})
    url=f'{origin}/g/{NS}/{USER}/p/{post["id"]}/'
    data=page.request.get(url+'sow/status').json();held=[]
    script_errors=[];page.on('pageerror',lambda error:script_errors.append(str(error)))
    if cached_template:
        from bs4 import BeautifulSoup
        html=BeautifulSoup(page.request.get(url).text(),'html.parser')
        for node in html.select('[data-testing-required], [data-platform-setup]'):node.decompose()
        page.route(url,lambda route:route.fulfill(content_type='text/html',body=str(html)))
        for destination in data['destinations']:
            destination.pop('needs_test',None);destination.pop('test_url',None)
    page.route('**/sow/status',lambda route:route.fulfill(json=data))
    page.route('**/launch',lambda route:held.append(route))
    page.goto(url);row=page.locator('[data-destination="git"]')
    row.locator('[name="actions"]').check();page.locator('[data-launch]').click()
    expect(page.locator('[data-launch]')).to_have_text('Starting…')
    expect(page.locator('[data-launch]')).to_be_disabled()
    data['run']={'id':'run-ui','kind':'launch','status':'countdown','platform':'git','operation':'Opening Chrome','remaining':8,'destinations':['git'],'control_url':url+'fake-control'}
    destination=next(d for d in data['destinations'] if d['id']=='git')
    destination.update(status='running',reviewable=False,blocked=True)
    held.pop().fulfill(json={'id':'run-ui'})
    expect(page.locator('#job-run-state')).to_have_text('Countdown')
    expect(page.locator('#job-run-countdown')).to_have_text(' 8s')
    data['run'].update(status='running',phase='handoff',operation='Click the Chrome window to use',remaining=5)
    expect(page.locator('[data-launch]')).to_have_text('Choose Chrome · 5s')
    expect(page.locator('[data-job-control] [value="pause"]')).to_be_hidden()
    expect(page.locator('[data-job-control] [value="stop"]')).to_be_visible()
    page.evaluate('window.sowPageMarker = "retained"')
    data['run'].update(status='running',phase='running',operation='Checking image',step=7,total_steps=8)
    expect(row.locator('[data-destination-status]')).to_have_text('Checking image')
    expect(page.locator('[data-run-detail]')).to_contain_text('Step 7 of 8')
    data['run'].update(status='failed',error='Image missing')
    destination.update(status='failed',label='Failed',error='Image missing',reviewable=True,blocked=False)
    expect(row.locator('[data-destination-error]')).to_have_text('Image missing')
    expect(row.locator('[data-verdict="unsuccessful"]')).to_be_visible()
    assert page.evaluate('window.sowPageMarker')=='retained'
    assert not script_errors
    expect(page.locator('[data-sow-error]')).to_be_hidden()
    if cached_template:
        medium=page.locator('[data-destination="medium"]')
        expect(medium.locator('[name="actions"]')).to_be_disabled()
        expect(medium.get_by_role('link',name='Open test')).to_have_attribute('href',f'/g/{NS}/{USER}/destinations/#destination-medium')
        page.unroute('**/launch')
        page.route('**/launch',lambda route:route.fulfill(status=400,json={
            'error':'Platform needs testing.','platform':'medium',
            'setup_url':f'/g/{NS}/{USER}/destinations/#destination-medium'}))
        page.locator('[data-launch]').click()
        expect(page.get_by_role('link',name='Open Medium test')).to_be_visible()
        expect(page.locator('[data-launch]')).to_be_enabled()
        assert not script_errors
    page.close()


def test_legacy_bluesky_export_shows_review_icons_and_reset_survives_reload(studio,monkeypatch):
    monkeypatch.setattr("allotment.app.approval_status",lambda *args:{"valid":True})
    from allotment.storage import write_yaml
    store,origin,browser=studio;post,job=attempted(store)
    job.pop('kind',None)
    job['actions']['bluesky']={'status':'exported','attempts':0,'url':'','error':''}
    write_yaml(store.job_path(NS,USER,job['id']),job)
    page=browser.new_page()
    page.goto(f'{origin}/g/{NS}/{USER}/p/{post["id"]}/')
    row=page.locator('[data-destination="bluesky"]')
    expect(row.locator('[data-verdict="successful"]')).to_be_visible()
    expect(row.locator('[data-verdict="unsuccessful"]')).to_be_visible()
    row.locator('[data-verdict="unsuccessful"]').click()
    expect(row.locator('[data-review]')).to_be_hidden()
    page.reload()
    expect(row.locator('[data-review]')).to_be_hidden()
    expect(row.locator('[name="actions"]')).to_be_enabled()
    row.locator('[name="actions"]').check()
    expect(page.locator('[data-launch]')).to_be_enabled()
    page.close()


def test_expired_medium_approval_has_direct_link_to_expanded_test_card(studio):
    from allotment.storage import write_yaml
    store,origin,browser=studio;post,job=attempted(store)
    write_yaml(store.settings_path(NS,USER),{'destinations':{'medium':{'enabled':True,'expected_account':'fixture',
        'approval':{'test_id':'prep-previous','signature':'old'}}}})
    page=browser.new_page()
    page.goto(f'{origin}/g/{NS}/{USER}/p/{post["id"]}/')
    row=page.locator('[data-destination="medium"]')
    expect(row.locator('[name="actions"]')).to_be_disabled()
    expect(row.locator('[data-testing-required]')).to_contain_text('Platform needs testing.')
    page.reload()
    expect(row.locator('[name="actions"]')).to_be_disabled()
    row.get_by_role('link',name='Open test',exact=True).click()
    expect(page.locator('#destination-medium .card-test')).to_have_attribute('open','')
    assert len(store.jobs(NS,USER))==1
    page.close()


def test_test_approval_changes_update_selection_without_reload(studio,monkeypatch):
    store,origin,browser=studio;post,job=attempted(store)
    approval={'valid':False}
    monkeypatch.setattr('allotment.app.approval_status',lambda *args:approval)
    page=browser.new_page()
    page.goto(f'{origin}/g/{NS}/{USER}/p/{post["id"]}/')
    row=page.locator('[data-destination="medium"]');checkbox=row.locator('[name="actions"]')
    expect(checkbox).to_be_disabled()
    approval['valid']=True
    expect(checkbox).to_be_enabled(timeout=10000)
    expect(row.locator('[data-testing-required]')).to_be_hidden()
    checkbox.check()
    approval['valid']=False
    expect(checkbox).to_be_disabled(timeout=10000)
    expect(checkbox).not_to_be_checked()
    expect(row.get_by_role('link',name='Open test')).to_be_visible()
    page.close()


def test_sow_and_inspect_are_collapsible_with_findings_beneath_sow(studio):
    from allotment.lifecycle import post_folder
    from allotment.storage import write_yaml
    store,origin,browser=studio;post,job=attempted(store)
    folder=post_folder(store,NS,USER,post['id'])/'harvest/bluesky'
    url='https://bsky.app/profile/fixture.test/post/abc123'
    write_yaml(folder/'id.yaml',{'post_id':'abc123','url':url,'captured_at':'2026-11-08T00:00:00+00:00'})
    write_yaml(folder/'latest.yaml',{'captured_at':'2026-11-08T00:01:00+00:00',
        'counts':{'likes':{'value':12,'approximate':False},'views':None},
        'replies':[{'url':url,'author':'friend.test','text':'A captured reply'}],
        'availability':'Visible information only.','limits':[]})
    page=browser.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    post_url=f'{origin}/g/{NS}/{USER}/p/{post["id"]}/'
    data=page.request.get(post_url+'sow/status').json()
    page.route('**/sow/status',lambda route:route.fulfill(json=data))
    page.goto(post_url)
    sow=page.locator('#sow-panel');inspect=page.locator('#inspect-panel')
    assert page.locator('#sow-panel + #inspect-panel').count()==1
    sow.locator(':scope > summary').click()
    expect(sow).not_to_have_attribute('open','')
    expect(inspect).not_to_have_attribute('open','')
    inspect.locator(':scope > summary').click()
    expect(inspect.locator('[data-inspect-findings]')).to_contain_text('abc123')
    expect(inspect.locator('.metrics')).to_contain_text('12')
    expect(inspect.locator('.metrics')).to_contain_text('Unavailable')
    expect(inspect.locator('.inspect-reply')).to_contain_text('@friend.test')
    expect(inspect.locator('.inspect-reply')).to_contain_text('A captured reply')
    data['run']={'id':'inspect-fixture','kind':'harvest','mode':'reactions','status':'countdown','remaining':8}
    expect(inspect.locator('#job-run-state')).to_have_text('Countdown',timeout=10000)
    expect(inspect.locator('[data-job-control] [value="stop"]')).to_be_visible()
    expect(sow).not_to_have_attribute('open','')
    page.screenshot(path=str(store.local/'inspect-card.png'),full_page=True)
    assert not errors
    page.close()
