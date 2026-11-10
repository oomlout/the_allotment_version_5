"""Capture a reviewed control template from the foreground Chrome window. No input."""
from pathlib import Path
import argparse
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from allotment.preparation import PreparationSession
from allotment.storage import slug

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('platform');p.add_argument('control',choices=['editor','create','attach','account','profile','add-image','next','close','chats','channels','ready','crop'])
    p.add_argument('--private-set',help='Keep account-specific templates under ignored .local')
    p.add_argument('--region',type=float,nargs=4,required=True);p.add_argument('--title',required=True)
    a=p.parse_args();root=Path(__file__).resolve().parents[1];d=PreparationSession(root/'.local/platform-survey/control-captures')
    d.hwnd=d.wg.GetForegroundWindow()
    if d.wg.GetClassName(d.hwnd)!='Chrome_WidgetWin_1' or a.title not in d.wg.GetWindowText(d.hwnd):raise ValueError('Wrong foreground Chrome page')
    _,d.pid=d.wp.GetWindowThreadProcessId(d.hwnd);proc=d.psutil.Process(d.pid)
    if proc.name().lower()!='chrome.exe':raise ValueError('Not Chrome')
    d.created=proc.create_time();d.input_tick=d.last_input()
    shot=d.screenshot(name=a.platform+'-'+a.control,region=a.region)
    asset_root=root/'.local/platform-assets'/slug(a.private_set) if a.private_set else root/'allotment/platform_assets'
    dest=asset_root/slug(a.platform)/(a.control+'.png');dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_bytes(Path(shot['path']).read_bytes());print(dest)
