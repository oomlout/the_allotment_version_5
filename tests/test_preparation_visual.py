from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from allotment.preparation import PreparationSession, fingerprint, validate_preparation, variables_for
from allotment.preparation_visual import safe_control
from allotment.storage import Store


def test_navigation_resets_saved_site_zoom_after_loading(monkeypatch):
    from allotment.roboclick.desktop import Desktop
    d=object.__new__(PreparationSession);calls=[]
    monkeypatch.setattr(Desktop,'navigate',lambda self,**kwargs:calls.append('navigate'))
    d.hotkey=lambda **kwargs:calls.append(kwargs['keys'])
    d.wait=lambda _:None
    d.navigate('about:blank')
    assert calls==['navigate',['ctrl','l'],['ctrl','0'],['esc'],['shift','f6']]
    assert d._navigating is False and d.focused is False


@pytest.mark.parametrize('url', [
    'https://other.example/p/123456789abc/edit',
    'https://medium.com/p/123456789abc',
    'https://medium.com/p/123456789abc/edit?publish=1',
    'https://medium.com/p/123456789abc/edit#other',
    'http://medium.com/p/123456789abc/edit',
])
def test_generated_draft_rejects_wrong_origin_or_non_editor(url):
    d=object.__new__(PreparationSession)
    d.recipe={};d.account_checked=d.target_checked=d.text_checked=True
    d.variables={'base_url':'https://medium.com'};d.allowed_urls=set()
    d.read_url=lambda:url
    with pytest.raises(ValueError,match='autosaved URL'):
        d.accept_generated_draft('^/p/[0-9a-f]{12}/edit$')
    assert not d.allowed_urls


def test_generated_draft_requires_checks_and_preserves_exact_editor():
    d=object.__new__(PreparationSession)
    d.recipe={};d.account_checked=d.target_checked=True;d.text_checked=False
    d.variables={'base_url':'https://medium.com'};d.allowed_urls=set()
    d.read_url=MagicMock(return_value='https://medium.com/p/123456789abc/edit')
    with pytest.raises(ValueError):d.accept_generated_draft('^/p/[0-9a-f]{12}/edit$')
    d.read_url.assert_not_called()
    d.text_checked=True;d.accept_generated_draft('^/p/[0-9a-f]{12}/edit$')
    assert d.url=='https://medium.com/p/123456789abc/edit' and d.allowed_urls=={d.url}
    d.recipe={'target_required':True}
    with pytest.raises(ValueError):d.accept_generated_draft('^/p/[0-9a-f]{12}/edit$')


@pytest.mark.parametrize('label',['Post','Send','Publish','Share','Reply','Upload','Schedule','Submit','Post now'])
def test_final_actions_are_not_open_controls(label):
    with pytest.raises(ValueError): safe_control(label)


def test_public_identity_requires_owner_proof():
    d=object.__new__(PreparationSession);d.variables={'expected_account':'right'}
    d.recipe={};d.check_url=MagicMock();d.find_text=MagicMock();d.screenshot=MagicMock()
    with pytest.raises(ValueError,match='public profile alone'):
        d.visual_identity([.2,.2,.5,.5],profile_url='https://example.com/right')
    assert not getattr(d,'account_checked',False)


def test_group_type_is_required_before_editing():
    d=object.__new__(PreparationSession);d.account_checked=True;d.recipe={'target_kind':'channel'}
    with pytest.raises(ValueError,match='type marker'): d.visual_target([0,0,1,1])


def test_native_picker_refuses_page_without_typing(tmp_path):
    d=object.__new__(PreparationSession)
    d.account_checked=d.target_checked=True;d.image_path=str(tmp_path/'sample.png')
    Path(d.image_path).write_bytes(b'fixture')
    d.hwnd=100;d.wg=SimpleNamespace(GetForegroundWindow=lambda:100)
    d.guard=MagicMock();d.input=MagicMock()
    with pytest.raises(ValueError,match='No native file picker'):d.upload_image()
    d.input.assert_not_called();assert d._picker is False


def test_native_picker_guard_rejects_unowned_dialog():
    d=object.__new__(PreparationSession);d._picker=True;d.hwnd=100;d.pid=42;d.created=3
    d.check_stop=MagicMock();d.psutil=SimpleNamespace(Process=lambda _:SimpleNamespace(create_time=lambda:3,name=lambda:'chrome.exe',parents=lambda:[]))
    d.wg=SimpleNamespace(IsWindow=lambda _:True,GetForegroundWindow=lambda:200,GetWindow=lambda *a:999,GetClassName=lambda _:'#32770',GetWindowText=lambda _:'Open')
    d.wp=SimpleNamespace(GetWindowThreadProcessId=lambda _:(1,42));d.wc=SimpleNamespace(GW_OWNER=4)
    with pytest.raises(ValueError,match='Chrome-owned'): d.guard(dialog=True)


def test_inspection_plugins_cannot_be_enabled_by_flag(tmp_path):
    for recipe in Store(tmp_path).plugins().values():
        if not recipe.get('inspection_only'): continue
        validate_preparation(recipe)
        changed=deepcopy(recipe);changed['preparation_ready']=True
        with pytest.raises(ValueError,match='Inspection-only'): validate_preparation(changed)


def test_browser_profile_redirect_is_bound_to_fingerprint(tmp_path):
    store=Store(tmp_path);r=store.plugins()['pixelfed'];a={'expected_account':'right'}
    assert fingerprint(store,r,a)!=fingerprint(store,r,{**a,'browser_profile_url':'https://pixelfed.social/right'})


def test_telegram_channel_and_group_are_distinct(tmp_path):
    store=Store(tmp_path);channel=store.plugins()['telegram_channel'];group=store.plugins()['telegram']
    a={'target_name':'oomlout','target_url':'https://web.telegram.org/k/#@oomloutT'}
    assert variables_for(channel,a,mode='inspect')['target_url']==a['target_url']
    with pytest.raises(ValueError):variables_for(group,a,mode='inspect')


def test_patreon_is_quip_only_and_cannot_publish_page(tmp_path):
    recipe=Store(tmp_path).plugins()['patreon']
    assert recipe['content_kind']=='quip' and recipe['keep_page_unpublished']
    assert recipe['inspection_only'] and not recipe['sending_enabled']
    assert [s['command'] for s in recipe['steps']]==['prepare_navigate']


def test_private_controls_are_confined_and_fingerprinted(tmp_path):
    store=Store(tmp_path);recipe=store.plugins()['whatsapp']
    account={'local_template_set':'test-account'}
    d=object.__new__(PreparationSession);d.local=store.local;d.recipe=recipe;d.account=account
    root=store.local/'platform-assets/test-account/whatsapp';root.mkdir(parents=True)
    asset=root/'account.png';asset.write_bytes(b'first-template')
    assert d.control_asset('account.png')==asset.resolve()
    before=fingerprint(store,recipe,account);asset.write_bytes(b'replaced-template')
    assert before!=fingerprint(store,recipe,account)
    with pytest.raises(ValueError):d.control_asset('../../../../outside.png')


def test_group_details_require_parent_before_accepting_target():
    d=object.__new__(PreparationSession);d.account_checked=True;d.target_checked=False
    d.recipe={'target_kind':'group'};d.variables={'target_name':'room','target_parent':'community'}
    d.find_text=MagicMock(side_effect=[{'x':1,'y':2},{},{},ValueError('Wrong community')])
    d.click_match=MagicMock();d.wait=MagicMock();d.park_pointer=MagicMock();d.screenshot=MagicMock()
    with pytest.raises(ValueError,match='Wrong community'):
        d.visual_target([0,0,1,1],kind_text='Group info',details=True,
                        detail_region=[0,0,1,1],parent_region=[0,0,1,1])
    assert d.target_checked is False


def test_upload_waits_for_owned_picker_to_return_focus(tmp_path):
    d=object.__new__(PreparationSession);d.account_checked=d.target_checked=True
    d.image_path=str(tmp_path/'sample.png');Path(d.image_path).write_bytes(b'sample')
    d.hwnd=100;d.wg=SimpleNamespace(GetForegroundWindow=MagicMock(side_effect=[200,200,100]))
    d.guard=MagicMock();d.input=MagicMock();d.gui=SimpleNamespace(hotkey=object(),press=object())
    d.text_clip=SimpleNamespace(copy=MagicMock());d.wait=MagicMock()
    d.upload_image()
    assert d.image_pasted and not d.focused and not d._picker
    assert any(call.kwargs.get('dialog') is True for call in d.guard.call_args_list)


@pytest.mark.parametrize('percent',[50,300,True,'200'])
def test_preview_zoom_is_bounded(percent):
    d=object.__new__(PreparationSession);d.hotkey=MagicMock()
    with pytest.raises(ValueError):d.preview_zoom(percent)
    d.hotkey.assert_not_called()


def test_preview_zoom_requires_checked_text_and_has_no_submit_keys():
    d=object.__new__(PreparationSession);d.account_checked=d.target_checked=True;d.text_checked=False
    d.hotkey=MagicMock();d.wait=MagicMock()
    with pytest.raises(ValueError):d.preview_zoom(200)
    d.hotkey.assert_not_called();d.text_checked=True;d.preview_zoom(200)
    assert [c.kwargs['keys'] for c in d.hotkey.call_args_list]==[['ctrl','l'],['ctrl','0']]+[['ctrl','+']]*5+[['esc'],['shift','f6']]
    assert not d.focused


def test_zoom_is_restored_before_leaving_site(monkeypatch):
    from allotment.roboclick.desktop import Desktop
    d=object.__new__(PreparationSession);calls=[]
    d._preview_zoom_changed=True
    d.hotkey=lambda **kw:calls.append(kw['keys']);d.wait=lambda _:None
    monkeypatch.setattr(Desktop,'navigate',lambda self,**kw:calls.append('navigate'))
    d.navigate('about:blank')
    assert calls==[['ctrl','l'],['ctrl','0'],['esc'],['shift','f6'],'navigate',['ctrl','l'],['ctrl','0'],['esc'],['shift','f6']]
    assert not d._preview_zoom_changed


@pytest.mark.parametrize('interrupted',[False,True])
def test_cleanup_restores_zoom_but_obeys_stop_and_releases_lock(monkeypatch,interrupted):
    from allotment.roboclick.desktop import Desktop, DesktopStopped
    d=object.__new__(PreparationSession);d._preview_zoom_changed=True
    d.gui=SimpleNamespace(hotkey=MagicMock());d.wait=MagicMock();d.log=MagicMock()
    d.input_tick=None;d.last_input=lambda:42
    d.guard=MagicMock(side_effect=DesktopStopped('Stopped with F8.') if interrupted else None)
    release=MagicMock();monkeypatch.setattr(Desktop,'release',release)
    d.release()
    release.assert_called_once()
    if interrupted:
        d.gui.hotkey.assert_not_called()
        assert d._preview_zoom_changed
        assert d.log.call_args.args[0]=='zoom-reset-skipped'
    else:
        assert [c.args for c in d.gui.hotkey.call_args_list]==[('ctrl','l'),('ctrl','0'),('esc',),('shift','f6')]
        assert not d._preview_zoom_changed
        d.log.assert_not_called()


def test_zoom_resets_before_final_screenshot():
    d=object.__new__(PreparationSession)
    d.account_checked=d.target_checked=d.text_checked=True
    d.image_path=None;d.url='https://example.com/draft';d._preview_zoom_changed=True
    calls=[];d.hotkey=lambda **kw:calls.append(kw['keys']);d.wait=lambda _:None
    d.check_url=lambda **kw:None
    def screenshot(**kw):
        assert calls==[['ctrl','l'],['ctrl','0'],['esc'],['shift','f6']] and not d._preview_zoom_changed
        return {'path':'prepared-no-send.png'}
    d.screenshot=screenshot
    assert d.finish()['sent'] is False


def test_context_remembers_stop_even_if_f8_edge_has_cleared(tmp_path,monkeypatch):
    from allotment.preparation import preparation_context
    from allotment.roboclick.desktop import Desktop, DesktopStopped
    d=object.__new__(PreparationSession);d._preview_zoom_changed=True
    d.attach=lambda:None;d.hotkey=MagicMock();d.log=MagicMock();d.wait=MagicMock()
    d.screenshot=MagicMock()
    monkeypatch.setattr('allotment.preparation.PreparationSession',lambda *a:d)
    release=MagicMock();monkeypatch.setattr(Desktop,'release',release)
    with pytest.raises(DesktopStopped,match='F8'):
        with preparation_context(Store(tmp_path),tmp_path/'evidence'):
            raise DesktopStopped('Stopped with F8.')
    d.hotkey.assert_not_called()
    assert d.log.call_args.args[0]=='zoom-reset-skipped'
    release.assert_called_once()


@pytest.mark.parametrize('copied,ok',[('First paragraph\n\nLast paragraph',True),('Last paragraph',False)])
def test_block_editor_selection_still_requires_complete_text(copied,ok):
    d=object.__new__(PreparationSession);d.focused=True;d.text_checked=False
    d.post={'text':'First paragraph\n\nLast paragraph'}
    d.hotkey=MagicMock();d.wait=MagicMock();d.capture_text=lambda **kw:copied
    if ok:d.verify_text(select_all=2)
    else:
        with pytest.raises(ValueError,match='exactly'):d.verify_text(select_all=2)
    assert d.text_checked is ok
    d.hotkey.assert_called_once_with(keys=['ctrl','a'])


@pytest.mark.parametrize('count',[0,3,True,'2'])
def test_editor_selection_count_is_bounded(count):
    d=object.__new__(PreparationSession);d.focused=True;d.hotkey=MagicMock()
    with pytest.raises(ValueError):d.verify_text(select_all=count)
    d.hotkey.assert_not_called()


@pytest.mark.parametrize('media,composer',[([], '/new/text?name=oomlout'),
    ([{'type':'image','file':'image.png'}], '/new/photo?name=oomlout')])
def test_tumblr_selects_composer_from_payload(tmp_path,monkeypatch,media,composer):
    from allotment.preparation import run_recipe
    recipe=Store(tmp_path).plugins()['tumblr'];actions=[]
    (tmp_path/'image.png').write_bytes(b'fixture')
    d=object.__new__(PreparationSession);d.check_stop=lambda:None;d.wait=MagicMock()
    def action(**kw):
        actions.append(kw['action'])
        return {'prepared':True,'sent':False}
    monkeypatch.setattr('allotment.preparation.framework.run_action',action)
    run_recipe(d,recipe,{'expected_account':'oomlout'},
               {'path':'.','text':'fixture','attachments':media},tmp_path)
    assert [s['url'] for s in actions if s['command']=='prepare_navigate']==[
        'https://www.tumblr.com/blog/oomlout','https://www.tumblr.com'+composer]
    assert any(s['command']=='prepare_upload_image' for s in actions)==bool(media)


def test_tumblr_unsupported_video_stops_before_browser_action(tmp_path,monkeypatch):
    from allotment.preparation import run_recipe
    recipe=Store(tmp_path).plugins()['tumblr'];action=MagicMock()
    monkeypatch.setattr('allotment.preparation.framework.run_action',action)
    with pytest.raises(ValueError,match='at most one image'):
        run_recipe(object.__new__(PreparationSession),recipe,{'expected_account':'oomlout'},
                   {'path':'.','text':'fixture','attachments':[{'type':'video','file':'video.mp4'}]},tmp_path)
    action.assert_not_called()


@pytest.mark.parametrize('copied,ok',[
    ('https://whatsapp.com/channel/expected',True),
    ('https://whatsapp.com/channel/another',False),
    ('https://whatsapp.com.evil.example/channel/expected',False)])
def test_public_channel_link_requires_exact_match(copied,ok):
    d=object.__new__(PreparationSession)
    d.variables={'profile_url':'https://whatsapp.com/channel/expected'}
    d.find_text=MagicMock(return_value={'fresh':True});d.guard=MagicMock()
    d.click_match=MagicMock();d.log=MagicMock()
    d.text_clip=SimpleNamespace(copy=MagicMock(),paste=lambda:copied)
    if ok:d.verify_public_link([0,0,1,1])
    else:
        with pytest.raises(ValueError,match='does not match'):d.verify_public_link([0,0,1,1])
    assert d.log.called is ok
    d.find_text.assert_called_once_with(text='Copy link',region=[0,0,1,1],exact=True)


def test_channel_link_check_cannot_replace_owner_proof():
    d=object.__new__(PreparationSession);d.account_checked=True;d.target_checked=False
    d.variables={'target_name':'room'};d.recipe={'target_kind':'channel'}
    d.find_text=MagicMock();d.verify_public_link=MagicMock()
    with pytest.raises(ValueError,match='owner control'):
        d.visual_target([0,0,1,1],kind_text='Channel info',copy_link_region=[0,0,1,1])
    assert not d.target_checked
    d.verify_public_link.assert_not_called()



def test_autosave_wait_accepts_only_original_composer_until_exact_editor(monkeypatch):
    d=object.__new__(PreparationSession)
    d.recipe={};d.account_checked=d.target_checked=d.text_checked=True
    d.variables={'base_url':'https://medium.com'}
    d.url='https://medium.com/new-story';d.allowed_urls={d.url}
    d.read_url=MagicMock(side_effect=[d.url,d.url,'https://medium.com/p/9ade8fffe0f5/edit'])
    d.wait=MagicMock();d.log=MagicMock()
    d.accept_generated_draft('^/p/[0-9a-f]{12}/edit$',12)
    assert d.url=='https://medium.com/p/9ade8fffe0f5/edit'
    assert d.wait.call_count==2


@pytest.mark.parametrize('actual',['https://medium.com/new-story?other=1',
    'https://medium.com/new-story#other','https://other.example/new-story',
    'https://medium.com/p/123/edit'])
def test_autosave_wait_does_not_retry_unexpected_urls(actual):
    d=object.__new__(PreparationSession)
    d.recipe={};d.account_checked=d.target_checked=d.text_checked=True
    d.variables={'base_url':'https://medium.com'}
    d.url='https://medium.com/new-story';d.allowed_urls={d.url}
    d.read_url=MagicMock(return_value=actual);d.wait=MagicMock()
    with pytest.raises(ValueError,match='autosaved URL'):
        d.accept_generated_draft('^/p/[0-9a-f]{12}/edit$',12)
    d.wait.assert_not_called()
    assert d.allowed_urls=={'https://medium.com/new-story'}


def test_autosave_wait_is_bounded(monkeypatch):
    d=object.__new__(PreparationSession)
    d.recipe={};d.account_checked=d.target_checked=d.text_checked=True
    d.variables={'base_url':'https://medium.com'}
    d.url='https://medium.com/new-story';d.allowed_urls={d.url}
    d.read_url=MagicMock(return_value=d.url);d.wait=MagicMock();d.log=MagicMock()
    monkeypatch.setattr('allotment.preparation.time.monotonic',MagicMock(side_effect=[0,0,13]))
    with pytest.raises(ValueError,match='autosaved URL'):
        d.accept_generated_draft('^/p/[0-9a-f]{12}/edit$',12)
    assert d.read_url.call_count==2 and d.wait.call_count==1


def test_discord_occupied_composer_is_preserved_with_restart_help():
    d=object.__new__(PreparationSession);d.account_checked=True;d.target_checked=False
    d.recipe={'target_kind':'channel'};d.variables={'target_name':'the_allotment'}
    d.find_text=MagicMock(side_effect=[{},ValueError("Could not find 'Message' on screen.")])
    d.click_match=MagicMock();d.paste_text=MagicMock()
    with pytest.raises(ValueError,match='unsent draft.*Restart'):
        d.visual_target([0,0,1,1],kind_text='Message',empty_editor=True)
    d.click_match.assert_not_called();d.paste_text.assert_not_called()
    assert not d.target_checked


