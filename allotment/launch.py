"""Initial handoff after a fresh guarded Chrome tab has been reserved."""
from datetime import datetime,timezone
import math,time
from .browser import expand


def starting_url(recipe,account,mode='prepare'):
    if recipe.get('engine')=='oomlout_roboclick_prepare':
        from .preparation import variables_for
        values=variables_for(recipe,account,mode=mode)
    else:
        values={**account,'base_url':account.get('base_url') or recipe['base_url']}
    return expand(recipe['steps'][0]['url'],values)


def countdown_in_tab(session,folder,url,update,seconds=8,resume_flag='continue.flag'):
    """No draft input before countdown. User input is allowed ONLY during this handoff.

    Desktop.attach already reserved the new tab, snapped the window and acquired
    the mutex. Re-arm the idle baseline at the boundary; guard still checks the
    same process/window/focus, F8 and the mouse failsafe before runtime input.
    """
    session.navigate(url,seconds=1)
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        session.check_stop()
        remaining=max(0,deadline-time.monotonic())
        update(phase='switching',remaining=math.ceil(remaining),countdown_remaining=remaining,
               countdown_until=datetime.fromtimestamp(time.time()+remaining,timezone.utc).isoformat())
        if (folder/'pause.flag').exists():
            update(phase='paused',remaining=math.ceil(remaining),countdown_remaining=remaining,countdown_until=None)
            while not (folder/resume_flag).exists():session.check_stop();time.sleep(.1)
            (folder/'pause.flag').unlink(missing_ok=True);(folder/resume_flag).unlink(missing_ok=True)
            deadline=time.monotonic()+remaining
        time.sleep(.1)
    session.check_stop()
    session.input_tick=session.last_input()
    session.guard()
    update(phase='running',remaining=0,countdown_remaining=0,countdown_until=None)
