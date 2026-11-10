"""Local native walkthrough with a deterministic null-foreground fault after snap.
The fault exists only in this opt-in fixture process, never in the application.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from allotment.roboclick.desktop import Desktop
from desktop_practice import main

if __name__=='__main__':
    original=Desktop.click_snap_caption
    recovered=[]
    class NullForeground:
        def __init__(self,real,session):self.real,self.session,self.active=real,session,True
        def __getattr__(self,name):return getattr(self.real,name)
        def GetForegroundWindow(self):return 0 if self.active else self.real.GetForegroundWindow()
        def SetForegroundWindow(self,hwnd):
            assert hwnd==self.session.hwnd
            result=self.real.SetForegroundWindow(hwnd)
            if self.active:recovered.append(hwnd)
            self.active=False
            return result
    def inject(session,checkpoint,direction):
        result=original(session,checkpoint,direction)
        if result and direction=='up' and not getattr(session,'fixture_fault_injected',False):
            session.fixture_fault_injected=True
            session.wg=NullForeground(session.wg,session)
            session.log('fixture-null-focus',{'direction':direction})
        return result
    Desktop.click_snap_caption=inject
    try:
        main()
        assert len(recovered)==2,recovered
        print('PASS: both null-foreground faults recovered into the reserved Chrome window; no submission.',flush=True)
    finally:Desktop.click_snap_caption=original
