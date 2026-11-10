from urllib.parse import parse_qs
import pytest
from playwright.sync_api import expect
from test_studio_browser import studio


def setup_page(studio, mode='prepare', expand=True):
    store,origin,browser=studio;page=browser.new_page(viewport={'width':1280,'height':1000})
    catalog={name:{'name':name.title(),**{m:{'state':'untested','eligible':True} for m in ['prepare','inspect']}}
             for name in ['bluesky','discord','twitter','patreon']}
    catalog['patreon']['prepare'].update(state='blocked',eligible=False,reason='Quip composer unavailable.')
    state={'runs':[],'catalog':catalog,'batch':{},'requests':[],'held':None,'reject_review':False}
    def run(name):
        return {'id':'test-'+name,'platform':name,'name':name.title(),'mode':mode,'active':True,'current':True,
                'status':'running','phase':'switching','countdown_until':'2099-01-01T00:00:00Z','countdown_remaining':8,
                'site_host':'fixture.test','expected_account':'fixture','target':'Local channel','can_approve':False}
    def endpoint(route):
        req=route.request;body=parse_qs(req.post_data or '')
        if '/tests/status' in req.url:
            if state['held']=='status':state['held']=route;return
            result={k:state[k] for k in ['runs','catalog','batch']}
        else:
            state['requests'].append((req.url,body))
            if req.url.endswith('/all-untested/start'):
                state['batch']={'id':'batch-new','mode':mode,'status':'running','items':[
                    {'platform':n,'name':n.title(),'status':'running' if i==0 else 'pending','test_id':'test-'+n if i==0 else None}
                    for i,n in enumerate(['bluesky','twitter'])]}
                state['runs']=[run('bluesky')];result={'batch':state['batch']}
            elif req.url.endswith('/tests/start'):
                name=body['platform'][0];state['runs'].insert(0,run(name));result={'id':'test-'+name}
            elif req.url.endswith('/countdown'):
                state['runs'][0]['phase']='paused' if body['action']==['pause'] else 'switching';result={'ok':True}
            elif req.url.endswith('/stop'):
                for r in state['runs']:r.update(active=False,status='stopped',error='Stopped by user.')
                if state['batch']:state['batch']['status']='stopped'
                result={'ok':True}
            elif req.url.endswith('/review'):
                if state['held']=='review':state['held']=route;return
                if state['reject_review']:route.fulfill(status=400,json={'error':'Instructions changed. Test again.'});return
                name=req.url.split('/')[-2].removeprefix('test-');verdict=body['verdict'][0]
                record={'test_id':'test-'+name,'verdict':verdict,'signature':'current'}
                catalog[name][mode].update(state=verdict,**record);result={'review':record}
            else:raise AssertionError(req.url)
        route.fulfill(json=result)
    page.route('**/tests/**',endpoint)
    page.goto(origin+'/g/the_allotment/oomlout_main/destinations/')
    row=lambda name:page.locator(f'#destination-{name} [data-mode="{mode}"]')
    expect(row('bluesky').locator('[data-status]')).to_have_text('Untested')
    if expand:
        for name in ['bluesky','patreon']:
            page.locator(f'#destination-{name} .card-test > summary').click()
    return page,state,row,run


def test_cards_start_immediately_despite_pending_poll_and_keep_controls(studio):
    page,state,row,run=setup_page(studio)
    assert page.locator('dialog, iframe, .test-sample').count()==0
    state['held']='status'
    page.wait_for_timeout(3200)
    assert state['held']!='status'
    row('bluesky').get_by_role('button',name='Run',exact=True).click()
    expect(row('bluesky').locator('[data-status]')).to_have_text('Opening Chrome')
    assert len(state['requests'])==1  # Start is not waiting on the held status request.
    state['held'].fulfill(json={'runs':[],'catalog':state['catalog'],'batch':{}});state['held']=None
    expect(row('bluesky').locator('[data-status]')).to_have_text('Countdown')
    row('bluesky').get_by_role('button',name='Pause',exact=True).click()
    expect(row('bluesky').get_by_role('button',name='Resume',exact=True)).to_be_visible()
    row('bluesky').get_by_role('button',name='Resume',exact=True).click()
    expect(row('bluesky').get_by_role('button',name='Pause',exact=True)).to_be_visible()
    state['runs'][0].update(phase='running',operation='Checking image',step=4,total_steps=9)
    expect(row('bluesky').locator('[data-status]')).to_have_text('Checking image')
    expect(row('bluesky').locator('[data-detail]')).to_have_text('Step 4 of 9')
    row('bluesky').get_by_role('button',name='Stop',exact=True).click()
    expect(row('bluesky').locator('[data-status]')).to_have_text('Stopped')
    expect(row('bluesky').locator('[data-error]')).to_have_text('Stopped by user.')
    expect(row('bluesky').get_by_role('button',name='Pass',exact=True)).to_be_disabled()
    row('bluesky').get_by_role('button',name='Run again',exact=True).click()
    expect(row('bluesky').locator('[data-status]')).to_have_text('Countdown')
    assert sum(url.endswith('/tests/start') for url,_ in state['requests'])==2
    page.close()


@pytest.mark.parametrize('mode',['prepare','inspect'])
def test_sequence_advances_cards_and_reviews_stay_with_their_platform(studio,mode):
    page,state,row,run=setup_page(studio,mode,expand=False)
    page.locator(f'[data-test-all="{mode}"]').click()
    expect(page.locator('#destination-bluesky')).to_have_class('panel platform-card test-active-card')
    expect(row('twitter').locator('[data-status]')).to_have_text('Queued')
    assert page.locator('dialog').count()==0
    first=state['runs'][0];first.update(active=False,status='prepared' if mode=='prepare' else 'inspected',can_approve=mode=='prepare')
    state['runs'].insert(0,run('twitter'))
    state['batch']['items'][0]['status']=first['status'];state['batch']['items'][1].update(status='running',test_id='test-twitter')
    expect(page.locator('#destination-twitter')).to_have_class('panel platform-card test-active-card')
    expect(row('bluesky').get_by_role('button',name='Pass',exact=True)).to_be_visible()
    expect(row('bluesky').get_by_role('button',name='Pass',exact=True)).to_be_disabled()
    second=state['runs'][0];second.update(active=False,status=first['status'],can_approve=mode=='prepare')
    state['batch']['status']='complete';state['batch']['items'][1]['status']=first['status']
    expect(row('bluesky').get_by_role('button',name='Pass',exact=True)).to_be_enabled()
    row('bluesky').get_by_role('button',name='Pass',exact=True).click()
    expect(row('bluesky').locator('[data-status]')).to_have_text('Passed')
    if mode=='prepare':assert page.locator('[name="bluesky_enabled"]').is_checked()
    expect(row('twitter').locator('[data-status]')).to_have_text('Ready to review')
    row('twitter').get_by_role('button',name='Not passed',exact=True).click()
    expect(row('twitter').locator('[data-status]')).to_have_text('Not passed')
    assert len([url for url,_ in state['requests'] if url.endswith('/review')])==2
    page.locator('#destination-bluesky').scroll_into_view_if_needed()
    page.screenshot(path=str(studio[0].local/f'card-tests-{mode}.png'))
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.close()


def test_review_shows_saving_prevents_double_click_and_does_not_fake_pass(studio):
    page,state,row,run=setup_page(studio)
    r=run('bluesky');r.update(active=False,status='prepared',can_approve=True);state['runs']=[r]
    expect(row('bluesky').get_by_role('button',name='Pass',exact=True)).to_be_visible()
    state['held']='review'
    row('bluesky').get_by_role('button',name='Pass',exact=True).click()
    expect(row('bluesky').locator('[data-status]')).to_have_text('Saving Pass…')
    expect(row('bluesky').get_by_role('button',name='Pass',exact=True)).to_be_disabled()
    state['held'].fulfill(status=400,json={'error':'Instructions changed. Test again.'});state['held']=None
    expect(row('bluesky').locator('[data-error]')).to_have_text('Instructions changed. Test again.')
    expect(row('bluesky').locator('[data-status]')).to_have_text('Ready to review')
    assert state['catalog']['bluesky']['prepare']['state']=='untested'
    expect(row('patreon').locator('[data-status]')).to_have_text('Blocked')
    expect(row('patreon').get_by_role('button',name='Run',exact=True)).to_be_disabled()
    assert len(state['requests'])==1
    page.close()


def test_prior_approval_does_not_hide_a_new_failure(studio):
    page,state,row,run=setup_page(studio)
    state['catalog']['bluesky']['prepare'].update(state='passed',test_id='test-old')
    latest=run('bluesky');latest.update(active=False,status='failed',error='Image check failed.',approved=False)
    state['runs']=[latest]
    expect(row('bluesky').locator('[data-status]')).to_have_text('Failed')
    expect(row('bluesky').get_by_role('button',name='Pass',exact=True)).to_be_disabled()
    expect(row('bluesky').get_by_role('button',name='Not passed',exact=True)).to_be_enabled()
    page.close()


def test_window_selection_countdown_is_on_run_button(studio):
    from datetime import datetime,timezone,timedelta
    page,state,row,run=setup_page(studio)
    r=run('bluesky');r.update(phase='handoff',countdown_until=(datetime.now(timezone.utc)+timedelta(seconds=8)).isoformat())
    state['runs']=[r]
    expect(row('bluesky').locator('[data-start]')).to_contain_text('Choose Chrome')
    expect(row('bluesky').locator('[data-pause]')).to_be_hidden()
    expect(row('bluesky').locator('[data-stop]')).to_be_visible()
    r.update(phase='running',operation='Checking account')
    expect(row('bluesky').locator('[data-status]')).to_have_text('Checking account')
    expect(row('bluesky').locator('[data-start]')).not_to_contain_text('Choose Chrome')
    page.close()


def test_collapsed_test_results_and_changed_instructions(studio):
    page,state,row,run=setup_page(studio,expand=False)
    assert page.locator('.card-test[open]').count()==0
    result=page.locator('#destination-bluesky [data-result-mode="prepare"]')
    expect(result).to_be_hidden()
    state['catalog']['bluesky']['prepare'].update(state='passed',test_id='test-bluesky')
    expect(result).to_be_visible()
    expect(result).to_have_attribute('data-outcome','passed')
    assert result.locator('.test-dot').evaluate('el => getComputedStyle(el).backgroundColor')=='rgb(22, 128, 59)'
    assert page.locator('.card-test[open]').count()==0
    r=run('bluesky');r.update(active=False,status='failed',error='Image check failed.')
    state['catalog']['bluesky']['prepare'].update(state='untested',test_id=None)
    state['runs']=[r]
    expect(result).to_have_attribute('data-outcome','failed')
    assert result.locator('.test-dot').evaluate('el => getComputedStyle(el).backgroundColor')=='rgb(198, 45, 50)'
    page.locator('#destination-bluesky .card-test > summary').click()
    expect(row('bluesky').locator('[data-status]')).to_be_visible()
    assert page.locator('#destination-bluesky').evaluate('el => getComputedStyle(el).gridColumn')=='auto'
    r['current']=False
    expect(result).to_be_hidden()
    expect(row('bluesky').locator('[data-status]')).to_have_text('Instructions changed')
    page.locator('#destination-bluesky .card-test > summary').click()
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.close()
