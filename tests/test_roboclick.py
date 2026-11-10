"""Deterministic runner contracts; desktop_practice.py covers actual Windows I/O."""
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import time

import pytest

from allotment.browser import Session, canonical_post_text, check_payload, run_recipe, validate_recipe
from allotment.roboclick.desktop import Desktop, DesktopStopped, text_matches, unique_match
from allotment.roboclick import framework
from allotment.storage import Store


@pytest.fixture
def data(tmp_path):
    recipe=Store(tmp_path).plugins()['bluesky']
    post={'id':'test','path':'.','text':'Unicode: 🌱\nhttps://example.com/','attachments':[]}
    account={'expected_account':'oomlout.bsky.social','profile_url':'https://bsky.app/profile/oomlout.bsky.social'}
    return recipe,account,post,tmp_path


class FakeDesktop:
    def __init__(self): self.calls=[]
    def __getattr__(self, name):
        def call(**kwargs):
            self.calls.append((name,kwargs))
            if name == 'check_owner': self.owner_verified=True
            if name == 'ready': return {'prepared':True,'draft_checked':True,'verified':False,'sent':False}
            if name == 'find_text': return {'x':10,'y':20,'time':time.monotonic()}
        return call
    def click_match(self, match): self.calls.append(('physical_click',match))


def test_runner_uses_real_framework_discovery_without_submission(data,monkeypatch):
    recipe,account,post,repo=data
    desktop=FakeDesktop();events=[]
    dispatch=framework.run_action
    commands=[]
    def traced(**kwargs):
        commands.append(kwargs['action']['command']);return dispatch(**kwargs)
    monkeypatch.setattr(framework,'run_action',traced)
    result=run_recipe(desktop,recipe,account,post,repo,lambda:events.append('intent'),testing=True)
    assert result['prepared'] and result['draft_checked'] and result['sent'] is False and events==[]
    assert len([c for c in desktop.calls if c[0]=='physical_click'])==0
    assert ('paste_text',{'text':post['text']}) in desktop.calls
    assert 'desktop_navigate' in commands and commands[-1]=='desktop_ready'


def test_prepare_never_invokes_submission_or_published_verifier(data):
    desktop=FakeDesktop();intent=MagicMock()
    result=run_recipe(desktop,*data[:3],data[3],intent,testing=True,prepare=True)
    assert result['prepared'] and not result['verified']
    intent.assert_not_called()
    assert not any(name in {'physical_click','verify_post'} for name,_ in desktop.calls)


def test_missing_submit_control_does_not_persist_intent(data):
    desktop=FakeDesktop();intent=MagicMock()
    desktop.ready=MagicMock(side_effect=ValueError('missing Post'))
    with pytest.raises(ValueError,match='missing Post'):
        run_recipe(desktop,*data[:3],data[3],intent,testing=True)
    intent.assert_not_called()


def test_cancel_before_action_has_no_input(data):
    desktop=FakeDesktop();intent=MagicMock()
    desktop.check_stop=MagicMock(side_effect=DesktopStopped('cancelled'))
    with pytest.raises(DesktopStopped): run_recipe(desktop,*data[:3],data[3],intent,testing=True)
    assert not desktop.calls
    intent.assert_not_called()


def test_recipe_rejects_code_unknown_actions_and_legacy_platforms(data):
    recipe,*_=data
    validate_recipe(recipe)
    for command in ['python','browser_debug','desktop_nonexistent']:
        bad=copy.deepcopy(recipe);bad['steps'][0]['command']=command
        with pytest.raises(ValueError,match='unknown'): validate_recipe(bad)
    for platform,legacy in Store(data[3]).plugins().items():
        if platform!='bluesky':
            with pytest.raises(ValueError,match='not been migrated'): validate_recipe(legacy)


def test_recipe_requires_owner_submit_and_verification(data):
    recipe,*_=data
    for removed in ['desktop_check_owner','desktop_ready']:
        bad=copy.deepcopy(recipe);bad['steps']=[s for s in bad['steps'] if s['command']!=removed]
        with pytest.raises(ValueError): validate_recipe(bad)
    bad=copy.deepcopy(recipe);bad['steps'][-1]['submit']=True
    with pytest.raises(ValueError): validate_recipe(bad)


def test_payload_rejects_video_and_multiple_images_before_desktop(data):
    recipe,account,post,repo=data
    for media in [[{'file':'a.mp4','type':'video'}],[{'file':'a.png','type':'image'}]*2]:
        with pytest.raises(ValueError): check_payload(recipe,account,{**post,'attachments':media},repo,testing=True)


def test_shortened_visible_url_is_verified_against_its_actual_link_target():
    from bs4 import BeautifulSoup
    content=BeautifulSoup('<p>URL:<a href="https://example.com/">example.com</a>\nmarker</p>','html.parser').p
    expected='URL:https://example.com/\nmarker'
    assert canonical_post_text(content,expected)=='URL:https://example.com/ marker'
    content.a['href']='https://wrong.example/'
    assert canonical_post_text(content,expected)!='URL:https://example.com/ marker'


@pytest.mark.parametrize('handle',['wrong.bsky.social','oomlout.bsky','oomlout.bsky.social.evil'])
def test_profile_owner_is_exact_and_failure_precedes_input(handle):
    session=object.__new__(Session)
    session.check_url=MagicMock();session.capture_text=MagicMock(return_value='@'+handle)
    session.find_text=MagicMock();session.screenshot=MagicMock()
    with pytest.raises(ValueError,match='do not match'):
        session.check_owner('https://bsky.app/profile/oomlout.bsky.social','oomlout.bsky.social','Edit profile','button')
    session.find_text.assert_not_called()


def ocr(words):
    return {'text':words,'conf':[95]*len(words),'block_num':[1]*len(words),'par_num':[1]*len(words),
            'line_num':[1]*len(words),'left':[i*20 for i in range(len(words))],
            'top':[5]*len(words),'width':[15]*len(words),'height':[12]*len(words)}


def test_ocr_maps_current_screenshot_and_exact_post_excludes_new_post():
    assert text_matches(ocr(['New','post']),'New post',[100,200,500,500])[0]['box']==[100,205,135,217]
    assert not text_matches(ocr(['New','post']),'Post',[0,0,100,100],exact=True)
    assert len(text_matches(ocr(['Post']),'Post',[0,0,100,100],exact=True))==1
    with pytest.raises(ValueError,match='found 2'): unique_match([{},{}],'Post')


def test_expired_coordinates_cannot_click():
    session=object.__new__(Desktop);session.input=MagicMock()
    with pytest.raises(ValueError,match='expired'):
        session.click_match({'x':10,'y':10,'time':time.monotonic()-4})
    session.input.assert_not_called()


def test_focus_loss_cancels_before_input():
    session=object.__new__(Desktop)
    session.check_stop=MagicMock();session.hwnd=123;session.pid=45;session.created=1
    session.wg=SimpleNamespace(IsWindow=lambda _:True,GetForegroundWindow=lambda:999)
    session.psutil=SimpleNamespace(Process=lambda _:SimpleNamespace(create_time=lambda:1))
    with pytest.raises(DesktopStopped,match='lost focus'): session.guard()


def test_user_input_interrupts_reserved_window():
    session=object.__new__(Desktop)
    session.check_stop=MagicMock();session.hwnd=123;session.pid=45;session.created=1
    session.wg=SimpleNamespace(IsWindow=lambda _:True,GetForegroundWindow=lambda:123)
    session.psutil=SimpleNamespace(Process=lambda _:SimpleNamespace(create_time=lambda:1))
    session.input_tick=5;session.last_input=lambda:6
    with pytest.raises(DesktopStopped,match='activity detected'): session.guard()


def test_wrong_image_address_cannot_verify_the_post():
    session=object.__new__(Desktop)
    session.find_image=lambda **_: {'x':10,'y':20,'score':.99,'screenshot':'image.png'}
    session.guard=MagicMock();session.wait=MagicMock();session.input=MagicMock();session.click_text=MagicMock();session.click_match=MagicMock()
    session.gui=SimpleNamespace(click=MagicMock());session.text_clip=SimpleNamespace(copy=MagicMock(),paste=lambda:'https://other/image')
    with pytest.raises(ValueError,match='does not belong'):
        session.check_image('reference.png',['https://expected/image'])


def test_matching_image_is_located_at_an_unlisted_scale_and_duplicates_are_rejected(tmp_path):
    from PIL import Image
    from allotment.test_sample import SAMPLE_IMAGE
    source=Image.open(SAMPLE_IMAGE).convert('RGB')
    screen=Image.new('RGB',(1280,800),'white')
    screen.paste(source.resize((550,309)),(210,100))
    path=tmp_path/'screen.png';screen.save(path)
    session=object.__new__(Desktop);session.guard=MagicMock();session.log=MagicMock()
    session.screenshot=lambda **_: {'path':str(path),'box':[0,0,1280,800],'time':time.monotonic()}
    match=session.find_image(str(SAMPLE_IMAGE))
    assert abs(match['x']-485)<5 and abs(match['y']-254)<5
    screen=Image.new('RGB',(1280,800),'white')
    for point in [(40,80),(700,80)]: screen.paste(source.resize((400,225)),point)
    screen.save(path)
    with pytest.raises(ValueError,match='More than one'):
        session.find_image(str(SAMPLE_IMAGE),scales=[.5])


def clipboard_html(url,body=b'<html><body>Fresh page</body></html>'):
    template='Version:0.9\r\nStartHTML:{start:010d}\r\nEndHTML:{end:010d}\r\nSourceURL:'+url+'\r\n'
    start=len(template.format(start=0,end=0).encode())
    return template.format(start=start,end=start+len(body)).encode()+body


@pytest.mark.parametrize('source,valid',[('https://bsky.app/profile/owner',True),('https://other.example/',False)])
def test_keyboard_html_capture_binds_fresh_bytes_to_expected_page(tmp_path,source,valid):
    session=object.__new__(Desktop)
    session.url='https://bsky.app/profile/owner';session.evidence=tmp_path
    session.check_url=MagicMock();session.capture_text=MagicMock();session.guard=MagicMock();session.log=MagicMock()
    session.clip=SimpleNamespace(RegisterClipboardFormat=lambda _:1,OpenClipboard=lambda:None,CloseClipboard=lambda:None,
                                 GetClipboardData=lambda _:clipboard_html(source))
    if valid:
        assert Path(session.capture_page()).read_bytes()==b'<html><body>Fresh page</body></html>'
    else:
        with pytest.raises(ValueError,match='source URL'): session.capture_page()


def test_clipboard_copy_rejects_a_stale_value(monkeypatch):
    session=object.__new__(Desktop);session.guard=MagicMock();session.hotkey=MagicMock()
    clipboard={}
    session.text_clip=SimpleNamespace(copy=lambda value:clipboard.update(value=value),paste=lambda:clipboard['value'])
    clock=iter([0,0,11])
    monkeypatch.setattr('allotment.roboclick.desktop.time',SimpleNamespace(monotonic=lambda:next(clock),sleep=lambda _:None))
    with pytest.raises(ValueError,match='Stale clipboard'): session.copy()


def test_runtime_does_not_import_browser_drivers_or_ai_clients():
    package=Path(__file__).parents[1]/'allotment'
    for file in package.rglob('*.py'):
        text=file.read_text(encoding='utf-8')
        assert 'from playwright' not in text and 'import websockets' not in text
        if file.name!='framework.py':
            assert 'import openai' not in text


@pytest.mark.parametrize('command,extra',[
    ('desktop_click_text',{'text':'Post'}),
    ('desktop_click_text',{'text':'Send'}),
    ('desktop_hotkey',{'keys':['ctrl','enter']}),
    ('desktop_click_image',{'image':'button.png'}),
    ('desktop_verify_post',{}),
])
def test_send_capabilities_rejected_before_any_desktop_input(data,command,extra):
    recipe,account,post,repo=data
    recipe=copy.deepcopy(recipe)
    recipe['steps'].insert(-1,{'command':command,**extra})
    desktop=FakeDesktop()
    with pytest.raises(ValueError):run_recipe(desktop,recipe,account,post,repo,MagicMock(),testing=True,prepare=False)
    assert desktop.calls==[]


def test_legacy_publishing_recipe_and_enabled_send_flag_rejected(data):
    recipe,account,post,repo=data
    for mutate in [lambda r:r.pop('sending_enabled'),lambda r:r.update(sending_enabled=True),lambda r:r['steps'][-1].update(submit=True)]:
        old=copy.deepcopy(recipe);mutate(old)
        desktop=FakeDesktop()
        with pytest.raises(ValueError,match='sending is disabled'):run_recipe(desktop,old,account,post,repo,MagicMock(),testing=True)
        assert desktop.calls==[]


def test_ready_locates_post_without_clicking_or_enter():
    session=object.__new__(Session);session.owner_verified=True
    session.find_text=MagicMock();session.screenshot=MagicMock();session.click_match=MagicMock()
    result=session.ready('Post',exact=True)
    assert result['draft_checked'] and result['sent'] is False
    session.click_match.assert_not_called()
    with pytest.raises(ValueError,match='Enter is blocked'):session.hotkey(['ctrl','enter'])
    with pytest.raises(ValueError,match='cannot be clicked'):session.click_text('Post')


@pytest.mark.parametrize('failure',['transient','busy','unrelated','stopped'])
def test_image_clipboard_lock_retries_without_replaying_input(tmp_path,monkeypatch,failure):
    from PIL import Image
    path=tmp_path/'sample.png';Image.new('RGB',(8,8)).save(path)
    d=object.__new__(Desktop);d.guard=MagicMock();d.wait=MagicMock();d.hotkey=MagicMock()
    d.wc=SimpleNamespace(CF_DIB=8)
    busy=OSError(5,'Access is denied')
    d.clip=SimpleNamespace(OpenClipboard=MagicMock(side_effect=[busy,None] if failure=='transient' else busy),
                           EmptyClipboard=MagicMock(),SetClipboardData=MagicMock(),CloseClipboard=MagicMock())
    if failure=='unrelated':d.clip.OpenClipboard.side_effect=OSError(6,'Invalid handle')
    if failure=='busy':monkeypatch.setattr('allotment.roboclick.desktop.time.monotonic',MagicMock(side_effect=[0,11]))
    if failure=='stopped':d.wait.side_effect=DesktopStopped('F8')
    if failure=='transient':
        d.paste_image(str(path))
        assert d.clip.OpenClipboard.call_count==2
        d.clip.EmptyClipboard.assert_called_once();d.clip.SetClipboardData.assert_called_once()
        d.clip.CloseClipboard.assert_called_once();d.hotkey.assert_called_once_with(keys=['ctrl','v'])
    else:
        with pytest.raises((ValueError,OSError,DesktopStopped)):d.paste_image(str(path))
        d.clip.EmptyClipboard.assert_not_called();d.clip.SetClipboardData.assert_not_called()
        d.clip.CloseClipboard.assert_not_called();d.hotkey.assert_not_called()
