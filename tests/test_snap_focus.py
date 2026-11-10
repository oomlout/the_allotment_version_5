from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from allotment.roboclick.desktop import Desktop,DesktopStopped


def desktop(monkeypatch,foreground,bounds=None):
    clock=[0.0];tick=[10]
    monkeypatch.setattr('allotment.roboclick.desktop.time',SimpleNamespace(monotonic=lambda:clock[0],sleep=lambda seconds:clock.__setitem__(0,clock[0]+seconds)))
    session=object.__new__(Desktop);session.hwnd=1;session.pid=11;session.created=123;session.input_tick=10
    session.click_snap_caption=MagicMock(return_value=True)
    session.snap_position_matches=MagicMock(return_value=True)
    session.check_stop=MagicMock();session.last_input=lambda:tick[0];session.guard=MagicMock();session.log=MagicMock()
    process=lambda pid:SimpleNamespace(create_time=lambda:123,name=lambda:{11:'chrome.exe',22:'explorer.exe',33:'notepad.exe'}[pid])
    session.psutil=SimpleNamespace(Process=process)
    session.wp=SimpleNamespace(GetWindowThreadProcessId=lambda hwnd:(1,{1:11,2:22,3:33,4:22}[hwnd]))
    session.wg=SimpleNamespace(IsWindow=lambda hwnd:True,GetForegroundWindow=lambda:foreground(clock[0]),
        GetClassName=lambda hwnd:'CabinetWClass' if hwnd==4 else 'MultitaskingViewFrame',
        GetWindowRect=lambda hwnd:bounds(clock[0]) if bounds else (0,0,960,540),SetForegroundWindow=MagicMock())
    session.gui=SimpleNamespace(press=MagicMock(side_effect=lambda key:tick.__setitem__(0,tick[0]+1)))
    return session,clock,tick


def test_snap_waits_for_late_shell_and_stable_chrome_bounds(monkeypatch):
    def foreground(t):
        if .1<=t<.3:return 0
        if .3<=t<.6 or .9<=t<1.2:return 2
        return 1
    session,clock,tick=desktop(monkeypatch,foreground,lambda t:(0,0,950,540) if 1.3<t<1.5 else (0,0,960,540))
    session.settle_snap('left')
    assert clock[0]>=2.3 and clock[0]<5
    assert session.gui.press.call_count==2
    assert all(call.args==('esc',) for call in session.gui.press.call_args_list)
    session.guard.assert_called_once()
    assert session.input_tick==tick[0]


@pytest.mark.parametrize('hwnd',[3,4])
def test_snap_never_refocuses_over_another_app_or_file_explorer(monkeypatch,hwnd):
    session,clock,tick=desktop(monkeypatch,lambda t:hwnd)
    with pytest.raises(DesktopStopped,match='Another app'):session.settle_snap('up')
    session.gui.press.assert_not_called();session.wg.SetForegroundWindow.assert_not_called();session.guard.assert_not_called()


def test_snap_user_activity_cancels_before_layout_input(monkeypatch):
    session,clock,tick=desktop(monkeypatch,lambda t:1)
    session.last_input=lambda:99 if clock[0]>.2 else 10
    with pytest.raises(DesktopStopped,match='Input detected'):session.settle_snap('left')
    session.gui.press.assert_not_called();session.wg.SetForegroundWindow.assert_not_called()


def test_snap_null_focus_times_out_without_any_input(monkeypatch):
    session,clock,tick=desktop(monkeypatch,lambda t:0)
    with pytest.raises(DesktopStopped,match='stable focus'):session.settle_snap('left',timeout=1)
    session.gui.press.assert_not_called();session.wg.SetForegroundWindow.assert_not_called()
    assert 1<=clock[0]<1.1


def test_snap_does_not_continue_if_other_app_arrives_after_escape(monkeypatch):
    session,clock,tick=desktop(monkeypatch,lambda t:1 if t<.6 else 3)
    with pytest.raises(DesktopStopped,match='Another app'):session.settle_snap('up')
    session.gui.press.assert_called_once_with('esc');session.wg.SetForegroundWindow.assert_not_called()
    session.guard.assert_not_called()


def test_snap_keeps_stop_and_process_identity_checks(monkeypatch):
    session,clock,tick=desktop(monkeypatch,lambda t:1)
    session.check_stop.side_effect=DesktopStopped('Stopped with F8.')
    with pytest.raises(DesktopStopped,match='F8'):session.settle_snap('up')
    session.gui.press.assert_not_called()
    session.check_stop.side_effect=None;session.created=999
    with pytest.raises(DesktopStopped,match='window changed'):session.settle_snap('up')
    session.gui.press.assert_not_called()


def test_snap_shell_that_never_returns_focus_has_bounded_retries(monkeypatch):
    session,clock,tick=desktop(monkeypatch,lambda t:2)
    with pytest.raises(DesktopStopped,match='stable focus'):session.settle_snap('left',timeout=3)
    assert session.gui.press.call_count==3 and session.click_snap_caption.call_count==3
    session.wg.SetForegroundWindow.assert_not_called()
    session.guard.assert_not_called()
    assert 3<=clock[0]<3.1


def test_snap_escape_return_to_previous_app_reactivates_only_reserved_chrome(monkeypatch):
    focused=[False]
    session,clock,tick=desktop(monkeypatch,lambda t:1 if focused[0] else 2 if t<.05 else 3)
    session.wg.SetForegroundWindow.side_effect=lambda hwnd:focused.__setitem__(0,True)
    session.settle_snap('up')
    session.wg.SetForegroundWindow.assert_called_once_with(1)
    session.gui.press.assert_called_once_with('esc')
    session.click_snap_caption.assert_called_once()
    session.guard.assert_called_once()


def test_snap_return_exception_never_ignores_user_input(monkeypatch):
    session,clock,tick=desktop(monkeypatch,lambda t:2 if t<.05 else 3)
    session.last_input=lambda:tick[0] if clock[0]<.05 else 999
    with pytest.raises(DesktopStopped,match='Input detected'):session.settle_snap('up')
    session.wg.SetForegroundWindow.assert_not_called();session.click_snap_caption.assert_not_called()


def test_snap_return_exception_expires_and_does_not_send_keys_to_other_app(monkeypatch):
    session,clock,tick=desktop(monkeypatch,lambda t:2 if t<.05 else 3)
    with pytest.raises(DesktopStopped,match='Another app'):session.settle_snap('up')
    session.gui.press.assert_called_once_with('esc')
    session.wg.SetForegroundWindow.assert_called_once_with(1)
    session.click_snap_caption.assert_not_called()
    assert clock[0]<1.2


def caption_session(monkeypatch,hit=2,root=1):
    session,clock,tick=desktop(monkeypatch,lambda t:1)
    del session.click_snap_caption
    session.user32=SimpleNamespace(GetDoubleClickTime=lambda:500,GetDpiForWindow=lambda hwnd:96)
    session.wg.WindowFromPoint=MagicMock(return_value=1)
    session.wg.GetAncestor=MagicMock(return_value=root)
    session.wg.SendMessageTimeout=MagicMock(return_value=(1,hit))
    session.gui.click=MagicMock(side_effect=lambda **kwargs:tick.__setitem__(0,tick[0]+1))
    return session,clock,tick


def test_snap_click_is_single_fresh_verified_caption_and_avoids_double_click(monkeypatch):
    session,clock,tick=caption_session(monkeypatch)
    assert session.click_snap_caption(MagicMock(),'up')
    session.gui.click.assert_called_once_with(x=720,y=12,clicks=1)
    assert session.wg.SendMessageTimeout.call_count==2 and session.input_tick==tick[0]
    assert not session.click_snap_caption(MagicMock(),'up')
    assert session.gui.click.call_count==1


@pytest.mark.parametrize('hit,root',[(1,1),(20,1),(9,1),(2,2)])
def test_snap_never_clicks_page_buttons_or_occluding_window(monkeypatch,hit,root):
    session,clock,tick=caption_session(monkeypatch,hit,root)
    assert not session.click_snap_caption(MagicMock(),'up')
    session.gui.click.assert_not_called()


def test_snap_click_rejects_changed_window_bounds(monkeypatch):
    session,clock,tick=caption_session(monkeypatch)
    session.wg.GetWindowRect=MagicMock(side_effect=[(0,0,960,540),(50,50,1000,600)])
    assert not session.click_snap_caption(MagicMock(),'up')
    session.gui.click.assert_not_called()


def test_snap_caption_busy_window_never_uses_unverified_point(monkeypatch):
    session,clock,tick=caption_session(monkeypatch)
    session.wg.SendMessageTimeout.side_effect=OSError(1460,'Timed out')
    assert not session.click_snap_caption(MagicMock(),'up')
    session.gui.click.assert_not_called()


def test_null_focus_after_snap_reactivates_only_reserved_window(monkeypatch):
    recovered=[False]
    session,clock,tick=desktop(monkeypatch,lambda t:1 if recovered[0] else 2 if t<.1 else 1 if t<.2 else 0)
    session.wg.SetForegroundWindow.side_effect=lambda hwnd:recovered.__setitem__(0,True)
    session.settle_snap('up')
    session.wg.SetForegroundWindow.assert_called_once_with(session.hwnd)
    session.snap_position_matches.assert_called_with('up')
    assert session.click_snap_caption.call_count>=2
    session.guard.assert_called_once()
    assert any(call.args[0]=='snap-null-recovery' for call in session.log.call_args_list)


def test_null_recovery_attempts_are_bounded(monkeypatch):
    session,clock,tick=desktop(monkeypatch,lambda t:2 if t<.1 else 0)
    with pytest.raises(DesktopStopped,match='2 recovery attempts'):session.settle_snap('up',timeout=3)
    assert session.wg.SetForegroundWindow.call_count==2
    session.guard.assert_not_called()
    assert clock[0]<3.1


def test_null_recovery_rejects_wrong_window_position(monkeypatch):
    session,clock,tick=desktop(monkeypatch,lambda t:2 if t<.1 else 0)
    session.snap_position_matches.return_value=False
    with pytest.raises(DesktopStopped,match='stable focus'):session.settle_snap('up',timeout=1)
    session.wg.SetForegroundWindow.assert_not_called()


def test_null_recovery_never_resets_user_input_baseline(monkeypatch):
    session,clock,tick=desktop(monkeypatch,lambda t:2 if t<.1 else 0)
    session.last_input=lambda:tick[0] if clock[0]<.3 else 999
    with pytest.raises(DesktopStopped,match='Input detected'):session.settle_snap('up')
    session.wg.SetForegroundWindow.assert_not_called()


@pytest.mark.parametrize('rect,visible,iconic,direction,expected',[
    ((-7,0,967,540),True,False,'up',True),
    ((-7,0,967,1040),True,False,'left',True),
    ((-7,0,967,1040),True,False,'up',False),
    ((960,0,1920,540),True,False,'up',False),
    ((0,0,1920,1080),True,False,'up',False),
    ((0,0,960,540),False,False,'up',False),
    ((0,0,960,540),True,True,'up',False),
])
def test_recovery_position_requires_visible_reserved_snap(monkeypatch,rect,visible,iconic,direction,expected):
    session,clock,tick=desktop(monkeypatch,lambda t:1)
    del session.snap_position_matches
    session.wg.GetWindowRect=lambda hwnd:rect
    session.wg.IsWindowVisible=lambda hwnd:visible
    session.wg.IsIconic=lambda hwnd:iconic
    session.gui.size=lambda:(1920,1080)
    assert session.snap_position_matches(direction)==expected
