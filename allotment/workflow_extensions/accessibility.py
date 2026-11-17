"""Read-only Windows accessibility snapshots; no browser protocol or input."""
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path


def capture(desktop, control_types=None, timeout=15):
    desktop.guard()
    timeout=float(timeout)
    if not 1<=timeout<=30:raise ValueError('Accessibility timeout must be 1–30 seconds.')
    output=desktop.evidence/('accessibility-'+uuid.uuid4().hex+'.json')
    command=[sys.executable,str(Path(__file__).resolve()),str(desktop.hwnd),str(output),json.dumps(control_types or ['Button','ProgressBar','Text'])]
    # Isolate a slow or hung accessibility provider so F8/focus/Stop stay active.
    process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline=time.monotonic()+timeout
        while process.poll() is None:
            desktop.guard()
            if time.monotonic()>=deadline:raise ValueError('Windows accessibility capture timed out.')
            desktop.wait(.1)
        desktop.guard()
        if process.returncode or not output.exists():raise ValueError('Windows could not read the browser accessibility state.')
        snapshot=json.loads(output.read_text(encoding='utf-8'))
        if snapshot.get('error'):raise ValueError(snapshot['error'])
        desktop.log('captured-accessibility',{'path':output.name,'elements':len(snapshot['elements'])})
        return snapshot
    finally:
        if process.poll() is None:process.kill()
        process.wait()


def snapshot(hwnd, control_types):
    from pywinauto.uia_element_info import UIAElementInfo
    root=UIAElementInfo(hwnd)
    documents=[d for d in root.descendants(control_type='Document') if d.visible]
    if len(documents)!=1:raise ValueError('Expected one visible browser document for accessibility capture.')
    elements=[]
    for role in control_types:
        for item in documents[0].descendants(control_type=role):
            if item.visible:
                elements.append({'control_type':role,'name':item.name,'enabled':bool(item.enabled),'automation_id':item.automation_id})
    return {'capture_id':uuid.uuid4().hex,'window':hwnd,'document':documents[0].name,'elements':elements}


if __name__=='__main__':
    try:result=snapshot(int(sys.argv[1]),json.loads(sys.argv[3]))
    except Exception as error:result={'error':str(error)}
    Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
