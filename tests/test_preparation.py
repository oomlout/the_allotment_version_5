import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import time

from bs4 import BeautifulSoup
import pytest

from allotment.app import create_app
from allotment.approval import signature
from allotment.preparation_approval import require_approval
from allotment.browser import available, StopRequested
from allotment.preparation import (PreparationSession, PreparationManager, validate_preparation, variables_for,
                                  run_recipe, folder_for, preparation_runs, fingerprint)
from allotment.storage import Store, write_yaml, read_yaml, now
from allotment.test_sample import SAMPLE_TEXT, SAMPLE_IMAGE
from preparation_fixture import fixture_recipe, account_for, fixture_html

NS,USER='the_allotment','oomlout_main'
PLATFORMS=['twitter','discord','whatsapp','telegram']


class FakeSession(PreparationSession):
    def __init__(self,markup):
        self.markup=markup;self.calls=[];self.url='';self.value=''
    def check_stop(self): pass
    def navigate(self,url,seconds=2): self.url=url;self.calls.append(('navigate',url))
    def captured(self,name): return BeautifulSoup(self.markup,'html.parser')
    def click_text(self,**kwargs): self.calls.append(('focus',kwargs['text']))
    def paste_text(self,text): self.value=text;self.calls.append(('paste',text))
    def paste_image(self,image): self.calls.append(('image',image))
    def wait(self,seconds): pass
    def screenshot(self,name): return {'path':name+'.png'}
    def capture_text(self,name): return self.value
    def capture_page(self,name): return 'inspection.html'
    def check_url(self,url): return self.url
    def find_image(self,**kwargs): self.calls.append(('image_check',kwargs['image']))


def payload(tmp_path):
    import shutil
    shutil.copyfile(SAMPLE_IMAGE,tmp_path/'image.png')
    return {'path':'.','text':SAMPLE_TEXT+'\n\n[Allotment test unit0001]',
            'attachments':[{'file':'image.png','type':'image'}]}


@pytest.mark.parametrize('platform',PLATFORMS)
def test_four_recipes_prepare_fixed_text_image_with_no_submit(platform,tmp_path):
    recipe=fixture_recipe(platform,'http://127.0.0.1:7777')
    account=account_for(platform,recipe['base_url']);post=payload(tmp_path)
    desktop=FakeSession(fixture_html(platform))
    result=run_recipe(desktop,recipe,account,post,tmp_path)
    assert result['prepared'] and result['sent'] is False and result['verified'] is False
    assert result['account_checked'] and result['target_checked'] and result['image_checked']
    assert ('paste',post['text']) in desktop.calls
    assert not available(recipe)
    assert all('submit' not in step for step in recipe['steps'])


@pytest.mark.parametrize('platform',PLATFORMS)
def test_inspection_only_navigates_and_captures_without_editing(platform,tmp_path):
    recipe=fixture_recipe(platform,'http://127.0.0.1:7777')
    desktop=FakeSession(fixture_html(platform))
    result=run_recipe(desktop,recipe,account_for(platform,recipe['base_url']),payload(tmp_path),tmp_path,mode='inspect')
    assert result['inspected'] and result['sent'] is False
    assert [name for name,_ in desktop.calls]==['navigate']


def test_inspection_reports_missing_html_without_claiming_account_or_group_verification(tmp_path):
    recipe=fixture_recipe('twitter','http://127.0.0.1:7777')
    desktop=FakeSession(fixture_html('twitter'))
    desktop.capture_page=MagicMock(side_effect=ValueError('Could not capture the whole page outside the editor.'))
    result=run_recipe(desktop,recipe,account_for('twitter',recipe['base_url']),payload(tmp_path),tmp_path,mode='inspect')
    assert result['inspected'] and not result['html_captured'] and result['capture_warning']
    assert not result['account_checked'] and not result['target_checked'] and not result['verified']
    assert [name for name,_ in desktop.calls]==['navigate']


@pytest.mark.parametrize('platform',PLATFORMS)
def test_uncalibrated_live_recipes_block_preparation_before_browser_input(platform,tmp_path):
    recipe=Store(tmp_path).plugins()[platform]
    recipe['preparation_ready']=False
    desktop=FakeSession(fixture_html(platform))
    with pytest.raises(ValueError,match='awaits layout calibration'):
        run_recipe(desktop,recipe,account_for(platform,recipe['base_url']),payload(tmp_path),tmp_path)
    assert not desktop.calls


@pytest.mark.parametrize('command',['desktop_hotkey','desktop_click_text','submit','prepare_send','python'])
def test_recipe_cannot_add_a_send_or_generic_input_operation(command,tmp_path):
    recipe=fixture_recipe('twitter','http://127.0.0.1:7777')
    recipe['steps'].insert(-1,{'command':command,'keys':['enter']})
    desktop=FakeSession(fixture_html('twitter'))
    with pytest.raises(ValueError,match='cannot contain'):
        run_recipe(desktop,recipe,account_for('twitter',recipe['base_url']),payload(tmp_path),tmp_path)
    assert not desktop.calls


def test_enter_is_blocked_outside_address_navigation():
    desktop=object.__new__(PreparationSession)
    for keys in [['enter'],['ctrl','enter'],['shift','return']]:
        with pytest.raises(ValueError,match='Enter cannot'): desktop.hotkey(keys)


def test_page_capture_moves_focus_without_resetting_to_empty_editor(tmp_path):
    from test_roboclick import clipboard_html
    desktop=object.__new__(PreparationSession)
    desktop.recipe={'steps':[{'command':'prepare_identity','selector':'#account'}]}
    desktop.account={};desktop.url='http://127.0.0.1:7777/right';desktop.evidence=tmp_path
    desktop.check_url=MagicMock();desktop.check_stop=MagicMock();desktop.hotkey=MagicMock();desktop.wait=MagicMock();desktop.log=MagicMock();desktop.focus_document=MagicMock();desktop.guard=MagicMock()
    desktop.capture_text=MagicMock(side_effect=[ValueError('Keyboard copy returned no fresh text'),'Page text'])
    raw=clipboard_html(desktop.url,b'<html><body><div id="account">@right</div></body></html>')
    desktop.clip=SimpleNamespace(RegisterClipboardFormat=lambda _:1,OpenClipboard=lambda:None,CloseClipboard=lambda:None,GetClipboardData=lambda _:raw)
    captured=Path(desktop.capture_page())
    assert BeautifulSoup(captured.read_text(encoding='utf-8'),'html.parser').select_one('#account').text=='@right'
    desktop.hotkey.assert_called_once_with(keys=['shift','tab'])
    assert desktop.focus_document.call_count==1
    assert desktop.check_url.call_count==2  # before and after capture, not between focus attempts


def test_busy_windows_clipboard_retries_without_more_keyboard_input():
    desktop=object.__new__(PreparationSession)
    desktop.guard=MagicMock();desktop.wait=MagicMock()
    opened=MagicMock(side_effect=[OSError('busy'),None]);closed=MagicMock()
    desktop.clip=SimpleNamespace(RegisterClipboardFormat=lambda _:1,OpenClipboard=opened,
                                 CloseClipboard=closed,GetClipboardData=lambda _:b'rendered HTML')
    assert desktop.clipboard_html()==b'rendered HTML'
    assert opened.call_count==2 and closed.call_count==1


def test_changed_group_fragment_is_rejected(monkeypatch):
    desktop=object.__new__(PreparationSession)
    monkeypatch.setattr('allotment.preparation.Desktop.check_url',lambda *args,**kwargs:'https://web.telegram.org/k/#-999')
    with pytest.raises(ValueError,match='changed the configured group fragment'):
        desktop.check_url('https://web.telegram.org/k/#-123')


def test_navigation_cannot_open_an_unconfigured_send_to_page(monkeypatch):
    desktop=object.__new__(PreparationSession)
    desktop.variables={'base_url':'https://web.whatsapp.com'};desktop.allowed_urls={'https://web.whatsapp.com'}
    navigate=MagicMock();monkeypatch.setattr('allotment.preparation.Desktop.navigate',navigate)
    with pytest.raises(ValueError,match='only the configured destination'):
        desktop.navigate('https://web.whatsapp.com/send?phone=123')
    navigate.assert_not_called()


@pytest.mark.parametrize('platform',PLATFORMS)
def test_wrong_account_stops_before_sample_or_image(platform,tmp_path):
    recipe=fixture_recipe(platform,'http://127.0.0.1:7777')
    desktop=FakeSession(fixture_html(platform).replace('Signed in as @right','Signed in as @wrong'))
    with pytest.raises(ValueError,match='account does not match'):
        run_recipe(desktop,recipe,account_for(platform,recipe['base_url']),payload(tmp_path),tmp_path)
    assert not any(name in {'paste','image'} for name,_ in desktop.calls)


@pytest.mark.parametrize('platform',['discord','telegram','whatsapp'])
def test_same_named_contact_cannot_be_used_as_group(platform,tmp_path):
    recipe=fixture_recipe(platform,'http://127.0.0.1:7777')
    desktop=FakeSession(fixture_html(platform).replace('data-kind="group"','data-kind="contact"'))
    with pytest.raises(ValueError): run_recipe(desktop,recipe,account_for(platform,recipe['base_url']),payload(tmp_path),tmp_path)
    assert not any(name=='image' or name=='paste' and value.startswith(SAMPLE_TEXT) for name,value in desktop.calls)


@pytest.mark.parametrize('platform,url',[
    ('discord','https://discord.com/channels/@me/123'),
    ('discord','https://discord.com/invite/example'),
    ('telegram','https://web.telegram.org/k/#@oomloutT'),
    ('telegram','http://t.me/oomloutT'),
    ('whatsapp','https://web.whatsapp.com/send?phone=123'),
    ('whatsapp','https://chat.whatsapp.com/example'),
])
def test_direct_messages_channels_and_invites_are_rejected(platform,url,tmp_path):
    recipe=Store(tmp_path).plugins()[platform];recipe['preparation_ready']=True
    account={'expected_account':'right','target_name':'Test group','target_url':url}
    with pytest.raises(ValueError): variables_for(recipe,account)


@pytest.mark.parametrize('platform',PLATFORMS)
def test_worker_gate_rejects_preparation_even_with_enabled_flags(platform,tmp_path):
    store=Store(tmp_path);store.bootstrap()
    with pytest.raises(ValueError):
        require_approval(store,NS,USER,store.plugins()[platform],{'enabled':True,'reviewed':True,'approval':{'test_id':'pretend'}})


def test_http_cannot_send_or_approve_preparation_and_sample_is_fixed(tmp_path,monkeypatch):
    store=Store(tmp_path);store.bootstrap()
    app=create_app(tmp_path,testing=True,garden=('the_allotment','oomlout_main'));client=app.test_client()
    response=client.get(f'/g/{NS}/{USER}/destinations/?platform=twitter')
    soup=BeautifulSoup(response.text,'html.parser')
    assert soup.select_one('button[value="send_test"]') is None
    assert not soup.select_one('#destination-twitter [data-mode="inspect"] [data-start]').has_attr('hidden')
    assert not soup.select_one('#destination-twitter [data-mode="inspect"] [data-start]').has_attr('disabled')
    with client.session_transaction() as session:csrf=session['csrf']
    manager=app.extensions['preparation_manager'];start=MagicMock(return_value='prep-unit')
    monkeypatch.setattr(manager,'start',start)
    for platform in PLATFORMS:
        response=client.post(f'/g/{NS}/{USER}/tests/start',data={'csrf':csrf,'platform':platform,'intent':'send_test'})
        assert response.status_code==400 and 'sending is disabled' in response.text
    start.assert_not_called()
    assert client.post(f'/g/{NS}/{USER}/tests/start',data={'csrf':csrf,'platform':'twitter','intent':'prepare_test','text':'override'}).status_code==400
    assert client.post(f'/g/{NS}/{USER}/tests/start',data={'csrf':csrf,'platform':'twitter','intent':'inspect_test'}).status_code==302
    assert start.call_args.kwargs=={'mode':'inspect'}
    assert client.post(f'/g/{NS}/{USER}/tests/prep-unit/approve',data={'csrf':csrf,'confirm_approval':'yes'}).status_code==400


def test_preparation_cannot_invalidate_a_bluesky_recipe_test(tmp_path):
    store=Store(tmp_path);store.bootstrap()
    account={'expected_account':'oomlout.bsky.social'}
    before=signature(store,store.plugins()['bluesky'],account)
    recipe=store.plugins()['twitter'];recipe['setup_note']='A different preparation note'
    write_yaml(store.local/'plugins/twitter.yaml',recipe)
    assert signature(store,store.plugins()['bluesky'],account)==before


def test_real_child_countdown_stops_before_opening_chrome(tmp_path):
    store=Store(tmp_path);store.bootstrap()
    write_yaml(store.settings_path(NS,USER),{'destinations':{'twitter':{'expected_account':'right'}}})
    manager=PreparationManager(store)
    run_id=manager.start(NS,USER,'twitter',mode='inspect')
    folder=folder_for(store,NS,USER,run_id)
    try:
        snapshot=read_yaml(folder/'run.yaml')
        assert snapshot['post']['text'].startswith(SAMPLE_TEXT)
        assert (folder/'media/test-image.png').read_bytes()==SAMPLE_IMAGE.read_bytes()
        manager.stop(NS,USER,run_id)
        manager.children[run_id].wait(timeout=8)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            run=next(r for r in preparation_runs(store,NS,USER) if r['id']==run_id)
            if run['status']=='stopped':break
            time.sleep(.1)
        assert run['status']=='stopped' and not run['can_approve'] and not run['verified']
        assert not list(folder.glob('*.png'))
    finally:
        process=manager.children[run_id]
        if process.poll() is None:process.kill()
