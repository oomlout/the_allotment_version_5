"""Deterministic Windows desktop actions. No browser protocol or AI dependency.

The caller reserves one visible Chrome window. F8, the mouse failsafe, focus
changes, and user input cancel rather than retry. All evidence is local.
"""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import time
import uuid
from urllib.parse import urlparse
from datetime import datetime, timezone
import math


def handoff_progress(folder):
    """Read-only UI projection of the pre-selection window handoff."""
    try:
        data=json.loads((Path(folder)/'window-handoff.json').read_text(encoding='utf-8'))
        if data.get('waiting') and data['until'] >= time.time():
            return {'phase':'handoff','remaining':math.ceil(data['until']-time.time()),
                    'countdown_until':datetime.fromtimestamp(data['until'],timezone.utc).isoformat(),
                    'operation':'Click the Chrome window to use'}
    except (OSError,ValueError,KeyError):
        pass
    return {}


class DesktopStopped(ValueError):
    pass


def ocr_controls(image):
    """Make white text on saturated rounded buttons readable to local OCR."""
    import cv2
    import numpy as np
    from PIL import Image
    data=np.array(image.convert("RGB"))
    hsv=cv2.cvtColor(data,cv2.COLOR_RGB2HSV)
    mask=cv2.inRange(hsv,np.array([0,100,80]),np.array([180,255,255]))
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x,y,w,h=cv2.boundingRect(contour)
        if 35<w<800 and 12<h<120 and w>h*1.4 and cv2.contourArea(contour)/(w*h)>.55:
            gray=cv2.cvtColor(data[y:y+h,x:x+w],cv2.COLOR_RGB2GRAY)
            data[y:y+h,x:x+w]=cv2.cvtColor(np.where(gray>220,0,255).astype("uint8"),cv2.COLOR_GRAY2RGB)
    return Image.fromarray(data)


def light_panel(image):
    import cv2
    import numpy as np
    gray=cv2.cvtColor(np.array(image.convert("RGB")),cv2.COLOR_RGB2GRAY)
    contours,_=cv2.findContours(cv2.inRange(gray,220,255),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    boxes=[cv2.boundingRect(c) for c in contours if cv2.contourArea(c)>10000]
    boxes=[b for b in boxes if b[2]>200 and b[3]>90 and abs(b[0]+b[2]/2-image.width/2)<image.width*.3]
    boxes.sort(key=lambda b:b[2]*b[3],reverse=True)
    if not boxes or (len(boxes)>1 and boxes[0][2]*boxes[0][3]<boxes[1][2]*boxes[1][3]*2):
        raise ValueError("No unique light dialog panel found in the screenshot region.")
    return boxes[0]


def text_matches(data, text, region, min_confidence=55, exact=False):
    """Find whole OCR phrases on one line, retaining coordinates and ambiguity."""
    wanted = re.findall(r"[\w]+", text.casefold())
    if not wanted:
        raise ValueError("OCR target must contain text.")
    lines = {}
    for i, word in enumerate(data["text"]):
        tokens = re.findall(r"[\w]+", word.casefold())
        if not tokens or float(data["conf"][i]) < min_confidence:
            continue
        key = tuple(data[k][i] for k in ("block_num", "par_num", "line_num"))
        for token in tokens:
            lines.setdefault(key, []).append((token, i))
    matches = []
    for words in lines.values():
        if exact and [word for word,_ in words] != wanted:
            continue
        for start in range(len(words) - len(wanted) + 1):
            selected = words[start:start + len(wanted)]
            if [word for word, _ in selected] != wanted:
                continue
            indices = [i for _, i in selected]
            x1 = min(data["left"][i] for i in indices) + region[0]
            y1 = min(data["top"][i] for i in indices) + region[1]
            x2 = max(data["left"][i] + data["width"][i] for i in indices) + region[0]
            y2 = max(data["top"][i] + data["height"][i] for i in indices) + region[1]
            matches.append({"x": (x1+x2)//2, "y": (y1+y2)//2, "box": [x1,y1,x2,y2]})
    return matches


def unique_match(matches, label):
    if len(matches) != 1:
        raise ValueError(f"Expected one {label}; found {len(matches)}. No click made.")
    return matches[0]


class Desktop:
    def __init__(self, evidence, settings=None, check_stop=None):
        if os.name != "nt":
            raise ValueError("The roboclick desktop worker requires Windows and normal Chrome.")
        import sys
        if "robo_roboclick" in sys.modules:
            raise ValueError("Start a fresh process with only desktop actions. The legacy helper replaces the mouse failsafe and must not be loaded.")
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
        import pyautogui
        import psutil
        import win32gui
        import win32process
        import win32con
        import win32clipboard
        import pyperclip
        self.gui, self.psutil = pyautogui, psutil
        self.wg, self.wp, self.wc, self.clip, self.text_clip = win32gui, win32process, win32con, win32clipboard, pyperclip
        self.user32 = ctypes.windll.user32
        self.gui.FAILSAFE = True
        self.gui.PAUSE = .12
        self.evidence = Path(evidence).resolve()
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.settings = settings or {}
        self.stop_callback = check_stop or (lambda: None)
        self.hwnd = None
        self.pid = None
        self.created = None
        self.input_tick = None
        self.mutex = None
        self.sequence = 0
        self.url = ""

    def check_stop(self):
        self.stop_callback()
        if self.user32.GetAsyncKeyState(0x77) & 0x8001:
            raise DesktopStopped("Stopped with F8.")
        if self.gui.position() in self.gui.FAILSAFE_POINTS:
            raise DesktopStopped("Mouse failsafe: move the pointer away from the screen corner before starting again.")

    def last_input(self):
        import ctypes
        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        value = LastInput()
        value.cbSize = ctypes.sizeof(value)
        if not self.user32.GetLastInputInfo(ctypes.byref(value)):
            raise ValueError("Cannot monitor desktop input.")
        return value.dwTime

    def guard(self, dialog=False):
        self.check_stop()
        if not self.hwnd or not self.wg.IsWindow(self.hwnd):
            raise DesktopStopped("The reserved Chrome window was closed.")
        process = self.psutil.Process(self.pid)
        if abs(process.create_time() - self.created) > .1:
            raise DesktopStopped("Chrome process identity changed.")
        foreground = self.wg.GetForegroundWindow()
        allowed = foreground == self.hwnd
        if dialog and foreground != self.hwnd:
            _, pid = self.wp.GetWindowThreadProcessId(foreground)
            owner = self.wg.GetWindow(foreground, self.wc.GW_OWNER)
            title = self.wg.GetWindowText(foreground).casefold()
            allowed = (pid == self.pid and owner == self.hwnd and self.wg.GetClassName(foreground) == "#32770"
                       and title in {"save as", "save webpage", "save web page"})
        if not allowed:
            raise DesktopStopped("Chrome lost focus. Stopped before sending more input.")
        if self.input_tick is not None and self.last_input() != self.input_tick:
            raise DesktopStopped("Keyboard or mouse activity detected. The desktop must stay idle during a run.")

    def input(self, fn, *args, dialog=False, **kwargs):
        self.guard(dialog=dialog)
        try:
            result = fn(*args, **kwargs)
        except self.gui.FailSafeException:
            raise DesktopStopped("Mouse corner failsafe stopped the worker.") from None
        self.input_tick = self.last_input()
        return result

    def wait(self, seconds=.5, dialog=False):
        deadline = time.monotonic() + float(seconds)
        while time.monotonic() < deadline:
            self.guard(dialog=dialog)
            time.sleep(min(.1, max(0, deadline-time.monotonic())))

    def attach(self):
        """Select a normal existing Chrome window, then reserve a NEW tab."""
        import win32event
        import win32api
        self.mutex=win32event.CreateMutex(None,False,"Local\\oomlout_roboclick_desktop")
        acquired=win32event.WaitForSingleObject(self.mutex,0)
        if acquired not in {win32event.WAIT_OBJECT_0,win32event.WAIT_ABANDONED}:
            win32api.CloseHandle(self.mutex)
            self.mutex=None
            raise ValueError("Another roboclick worker is using this Windows desktop.")
        self.window_handoff()
        candidates = []
        def visit(hwnd, _):
            if (not self.wg.IsWindowVisible(hwnd) or self.wg.IsIconic(hwnd) or self.wg.GetClassName(hwnd) != "Chrome_WidgetWin_1"
                or self.wg.GetWindow(hwnd,self.wc.GW_OWNER) or not self.wg.GetWindowText(hwnd).endswith(" - Google Chrome")):
                return
            _, pid = self.wp.GetWindowThreadProcessId(hwnd)
            try:
                process = self.psutil.Process(pid)
                if process.name().casefold() != "chrome.exe":
                    return
                title = self.wg.GetWindowText(hwnd)
                hint = self.settings.get("window_title", "").strip()
                if hint and hint.casefold() not in title.casefold():
                    return
                candidates.append((hwnd, process, title))
            except self.psutil.Error:
                return
        self.wg.EnumWindows(visit, None)
        if not candidates:
            raise ValueError("Open normal Google Chrome and sign in to the destination before starting.")
        foreground = self.wg.GetForegroundWindow()
        current = [x for x in candidates if x[0] == foreground]
        if self.settings.get("window_title") and len(candidates) != 1:
            raise ValueError("Window title matches more than one Chrome window. Use a unique title in Settings.")
        self.hwnd, process, title = (current or candidates)[0]
        self.pid, self.created = process.pid, process.create_time()
        self.wg.ShowWindow(self.hwnd, self.wc.SW_RESTORE)
        try:
            self.wg.SetForegroundWindow(self.hwnd)
        except Exception:
            pass
        deadline = time.monotonic()+2
        while self.wg.GetForegroundWindow() != self.hwnd and time.monotonic() < deadline:
            self.check_stop()
            time.sleep(.1)
        rect=self.wg.GetWindowRect(self.hwnd)
        screen_width,screen_height=self.gui.size()
        if not (0 <= (rect[0]+rect[2])/2 < screen_width and 0 <= (rect[1]+rect[3])/2 < screen_height):
            raise ValueError("Move the selected Chrome window to the primary screen before starting.")
        self.input_tick = self.last_input()
        self.guard()
        # Create the owned tab BEFORE Snap Assist's Escape keys. Escape on the
        # previous tab could dismiss a prepared composer the user wants to keep.
        self.hotkey(keys=["ctrl", "t"])
        self.wait(.5)
        self.snap_top_left()
        self.hotkey(keys=["ctrl", "0"])
        self.navigate(url="about:blank")
        self.screenshot(name="chrome-ready")
        self.log("window", {"hwnd": self.hwnd, "pid": self.pid, "title": title, "bounds": self.bounds()})
        return self

    def window_handoff(self, seconds=5):
        """Allow window selection BEFORE reserving Chrome or injecting input."""
        deadline=time.monotonic()+seconds
        path=self.evidence/'window-handoff.json'
        def save(waiting):
            temp=path.with_suffix('.tmp')
            temp.write_text(json.dumps({'waiting':waiting,'until':time.time()+max(0,deadline-time.monotonic())}),encoding='utf-8')
            os.replace(temp,path)
        save(True)
        self.log('window-handoff',{'seconds':seconds})
        try:
            while time.monotonic()<deadline:
                self.check_stop()  # F8/Stop stay armed; ordinary user input is expected here.
                time.sleep(min(.1,max(0,deadline-time.monotonic())))
            self.check_stop()
        finally:
            save(False)

    def click_snap_caption(self, checkpoint, direction):
        """Click only exposed Chrome title-bar space verified by native hit tests.

        No page, tab, window button or Snap Assist thumbnail is a valid target.
        The callback preserves layout-only focus, identity and input guards.
        """
        checkpoint()
        previous=getattr(self,'_snap_caption_click',None)
        if previous is not None and time.monotonic()-previous < self.user32.GetDoubleClickTime()/1000+.15:
            return False
        rect=tuple(self.wg.GetWindowRect(self.hwnd));left,top,right,bottom=rect
        scale=self.user32.GetDpiForWindow(self.hwnd)/96 or 1
        def caption_at(x,y):
            window=self.wg.WindowFromPoint((x,y))
            if not window or self.wg.GetAncestor(window,2)!=self.hwnd:return False
            try:hit=self.wg.SendMessageTimeout(self.hwnd,0x84,0,(x&0xffff)|((y&0xffff)<<16),2,80)
            except Exception as error:
                if error.args and error.args[0]==1460:return False
                raise
            return hit[1]==2  # WM_NCHITTEST must report HTCAPTION.
        for offset in (12,8,20,28):
            y=max(1,top+int(offset*scale))
            for fraction in (.75,.5,.25,.85,.65,.35):
                checkpoint()
                x=left+int((right-left)*fraction)
                if not caption_at(x,y):continue
                if tuple(self.wg.GetWindowRect(self.hwnd))!=rect:return False
                checkpoint()
                if not caption_at(x,y):return False
                self.gui.click(x=x,y=y,clicks=1)
                self.input_tick=self.last_input()
                self._snap_caption_click=time.monotonic()
                self.log('snap-caption-click',{'direction':direction,'x':x,'y':y,'bounds':rect,'hit':'HTCAPTION'})
                checkpoint()
                return True
        return False

    def snap_position_matches(self, direction):
        """Check the reserved window, never infer ownership from screen position."""
        if not self.wg.IsWindowVisible(self.hwnd) or self.wg.IsIconic(self.hwnd):return False
        left,top,right,bottom=self.wg.GetWindowRect(self.hwnd)
        width,height=self.gui.size()
        return (-20<=left<=20 and -20<=top<=20 and width*.25<=right<=width*.6
                and height*.2<=bottom<=height*(1.05 if direction=='left' else .65))

    def settle_snap(self, direction, timeout=5, stable_seconds=.8):
        """Wait out Snap Assist; never refocus over user input or unrelated switches.

        Windows activation is asynchronous. A single sleep followed by guard()
        races late Snap Assist windows and temporary null foreground handles.
        Only Escape and a verified title-bar click on the reserved Chrome window are
        allowed here. Page input resumes after focus AND bounds stay stable.
        """
        started=time.monotonic();deadline=started+timeout
        stable_since=None;last_bounds=None;last_state=None
        dismissed=False;caption_clicked=False;last_dismissal=-10;last_dismissed_foreground=None;dismissals=0
        shell_seen=False;recovery_for_dismissal=None
        null_since=None;null_attempts=0;last_null_attempt=-10
        def checkpoint():
            self.check_stop()
            if self.last_input()!=self.input_tick:
                raise DesktopStopped("Input detected while snapping Chrome.")
            if (not self.wg.IsWindow(self.hwnd)
                    or self.wp.GetWindowThreadProcessId(self.hwnd)[1]!=self.pid
                    or abs(self.psutil.Process(self.pid).create_time()-self.created)>.1):
                raise DesktopStopped("The reserved Chrome window changed while snapping.")
            foreground=self.wg.GetForegroundWindow()
            process_name=window_class=''
            if foreground and foreground!=self.hwnd:
                _,pid=self.wp.GetWindowThreadProcessId(foreground)
                process_name=self.psutil.Process(pid).name().casefold()
                window_class=self.wg.GetClassName(foreground)
            state=(foreground,process_name,window_class)
            nonlocal last_state,shell_seen
            if state!=last_state:
                self.log("snap-focus",{"direction":direction,"foreground":foreground,
                         "process":process_name,"class":window_class})
                last_state=state
            if process_name=='explorer.exe' and window_class in {'ForegroundStaging','XamlExplorerHostIslandWindow','MultitaskingViewFrame'}:
                shell_seen=True
            if foreground and foreground!=self.hwnd and (process_name!='explorer.exe' or window_class=='CabinetWClass'):
                # Escape from observed Snap Assist can restore the prior app.
                # Permit only reactivation of reserved Chrome during this one-
                # second handoff; no input is ever sent to the returned app.
                if not (shell_seen and dismissals and time.monotonic()-last_dismissal<1):
                    raise DesktopStopped("Another app took focus while snapping Chrome. Keep Chrome focused and restart the test.")
            return foreground
        while time.monotonic()<deadline:
            foreground=checkpoint();stamp=time.monotonic()
            # Windows can remain with no foreground after Snap Assist closes.
            # Recover only the reserved snapped window; never type into the gap.
            if not foreground:
                stable_since=None
                if null_since is None:null_since=stamp
                if (shell_seen and dismissed and stamp-null_since>=.35 and null_attempts<2
                        and stamp-last_null_attempt>=.8 and self.snap_position_matches(direction)):
                    if checkpoint():continue
                    null_attempts+=1;last_null_attempt=stamp
                    self.log('snap-null-recovery',{'direction':direction,'attempt':null_attempts,'bounds':self.wg.GetWindowRect(self.hwnd)})
                    try:self.wg.SetForegroundWindow(self.hwnd)
                    except Exception as error:self.log('snap-activation-pending',{'direction':direction,'error':str(error)})
                    checkpoint()
                    caption_clicked=self.click_snap_caption(checkpoint,direction)
                time.sleep(.05);continue
            null_since=None
            shell=foreground!=self.hwnd and last_state[1]=='explorer.exe' and last_state[2]!='CabinetWClass'
            if foreground!=self.hwnd and not shell:
                if recovery_for_dismissal!=dismissals:
                    self.log('snap-return-focus',{'direction':direction,'foreground':foreground,'process':last_state[1]})
                    try:self.wg.SetForegroundWindow(self.hwnd)
                    except Exception as error:self.log('snap-activation-pending',{'direction':direction,'error':str(error)})
                    recovery_for_dismissal=dismissals
                caption_clicked=False;stable_since=None
                time.sleep(.05);continue
            if (shell or stamp-started>=.35) and (not dismissed or shell) and dismissals<3 and (
                    foreground!=last_dismissed_foreground or stamp-last_dismissal>=.5):
                # Recheck immediately before this one allowed layout key.
                if checkpoint()!=foreground:continue
                self.gui.press("esc")
                self.input_tick=self.last_input()
                dismissed=True;dismissals+=1;last_dismissal=time.monotonic();last_dismissed_foreground=foreground
                stable_since=None
                caption_clicked=False
                time.sleep(.05);continue
            if dismissed and not caption_clicked:
                caption_clicked=self.click_snap_caption(checkpoint,direction)
                stable_since=None
                time.sleep(.05);continue
            if foreground==self.hwnd and dismissed and caption_clicked:
                bounds=tuple(self.wg.GetWindowRect(self.hwnd))
                if bounds!=last_bounds or stable_since is None:
                    last_bounds=bounds;stable_since=stamp
                if stamp-stable_since>=stable_seconds:
                    self.guard()
                    self.log("snap-settled",{"direction":direction,"elapsed":round(stamp-started,3),"bounds":bounds})
                    return
            else:stable_since=None
            time.sleep(.05)
        raise DesktopStopped(f"Chrome did not regain stable focus after Windows Snap ({null_attempts} recovery attempts). Keep Chrome focused and restart the test.")

    def snap_top_left(self):
        """Use Windows Snap, then verify its handoff before further keyboard input."""
        for direction in ("left", "up"):
            self.hotkey(keys=["win", direction])
            self.settle_snap(direction)
        width, height = self.gui.size()
        left, top, right, bottom = self.bounds()
        if left > 20 or top > 20 or right > width * .6 or bottom > height * .65:
            raise ValueError("Windows did not snap Chrome to the top-left quarter. Enable Snap windows in Windows Settings.")

    def bounds(self):
        left, top, right, bottom = self.wg.GetWindowRect(self.hwnd)
        width, height = self.gui.size()
        return [max(0,left),max(0,top),min(width,right),min(height,bottom)]

    def log(self, event, data):
        with (self.evidence / "desktop.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": time.time(), "event": event, **data}, ensure_ascii=False) + "\n")

    def screenshot(self, name="screen", region=None, dialog=False):
        self.guard(dialog=dialog)
        box = self.bounds()
        if region:
            box = self.region_box(region)
        self.sequence += 1
        path = self.evidence / f"{self.sequence:03d}-{self.safe_name(name)}.png"
        image = self.gui.screenshot(region=(box[0],box[1],box[2]-box[0],box[3]-box[1]))
        image.save(path)
        self.guard(dialog=dialog)
        return {"path": str(path), "box": box, "time": time.monotonic()}

    @staticmethod
    def safe_name(name):
        return re.sub(r"[^a-zA-Z0-9_-]", "-", str(name))[:70] or "capture"

    def region_box(self, region=None):
        x1,y1,x2,y2 = self.bounds()
        region = region or [0,.13,1,1]
        if len(region) != 4 or any(not 0 <= float(n) <= 1 for n in region) or region[2] <= region[0] or region[3] <= region[1]:
            raise ValueError("A screenshot region needs [left,top,right,bottom] fractions between 0 and 1.")
        return [int(x1+(x2-x1)*region[0]), int(y1+(y2-y1)*region[1]),
                int(x1+(x2-x1)*region[2]), int(y1+(y2-y1)*region[3])]

    def find_text(self, text, region=None, confidence=55, timeout=30, exact=False, panel=False):
        import pytesseract
        from PIL import Image
        executable = self.settings.get("tesseract") or shutil.which("tesseract") or r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        if not Path(executable).is_file():
            raise ValueError("Install local Tesseract OCR or set its executable in Settings.")
        pytesseract.pytesseract.tesseract_cmd = str(executable)
        deadline = time.monotonic() + min(float(timeout),60)
        while True:
            shot = self.screenshot(name="ocr-"+text, region=region or [0,.13,1,1])
            ocr_image = Image.open(shot["path"])
            origin=list(shot["box"])
            if panel:
                x,y,w,h=light_panel(ocr_image)
                ocr_image=ocr_image.crop((x,y,x+w,y+h))
                origin[0]+=x;origin[1]+=y
            ocr_image = ocr_controls(ocr_image)
            data = pytesseract.image_to_data(ocr_image.resize((ocr_image.width*2,ocr_image.height*2)), config="--psm 11", output_type=pytesseract.Output.DICT, timeout=30)
            for key in ("left","top","width","height"):
                data[key] = [int(value/2) for value in data[key]]
            self.guard()
            matches = text_matches(data,text,origin,float(confidence),exact=exact)
            self.log("ocr", {"text":text,"screenshot":Path(shot["path"]).name,"matches":matches})
            if len(matches) > 1:
                unique_match(matches, repr(text))
            if matches:
                return {**matches[0], "time":shot["time"], "screenshot":shot["path"], "screen_box":shot["box"]}
            if time.monotonic() >= deadline:
                raise ValueError(f"Could not find {text!r} on screen. See OCR screenshots; no click made.")
            self.wait(.4)

    def click_match(self, match, allow_dialog=False, button="left"):
        from PIL import Image, ImageChops, ImageStat
        age=time.monotonic()-match["time"]
        if age > 2 and (not match.get("screenshot") or not match.get("screen_box")):
            raise ValueError("Screenshot coordinates expired. Capture and locate again before clicking.")
        if age > 60:
            raise ValueError("Screenshot coordinates expired. Capture and locate again before clicking.")
        if match.get("screenshot") and match.get("screen_box"):
            self.guard()
            box=match.get("box") or [int(match["x"]-match["width"]/2),int(match["y"]-match["height"]/2),
                                     int(match["x"]+match["width"]/2),int(match["y"]+match["height"]/2)]
            offset=match["screen_box"]
            with Image.open(match["screenshot"]) as original:
                reference=original.crop((box[0]-offset[0],box[1]-offset[1],box[2]-offset[0],box[3]-offset[1])).convert("RGB")
            fresh=self.gui.screenshot(region=(box[0],box[1],box[2]-box[0],box[3]-box[1])).convert("RGB")
            if max(ImageStat.Stat(ImageChops.difference(reference,fresh)).mean) > 4:
                raise ValueError("The target changed after screenshot detection. No click made.")
        self.input(self.gui.click, match["x"],match["y"],button=button)
        if allow_dialog:
            self.check_stop()
            time.sleep(.35)
        else:
            self.wait(1)
        return match

    def click_text(self, **kwargs):
        return self.click_match(self.find_text(**kwargs))

    def find_image(self, image, region=None, threshold=.82, scales=None, reference_region=None, panel=False, allow_multiple=False):
        deadline=time.monotonic()+20
        while True:
            self.guard()
            try:
                return self._find_image_once(image,region,threshold,scales,reference_region,panel,allow_multiple)
            except ValueError as error:
                # Retry observation only, never input or ambiguous/malformed matches.
                if str(error)!='Reference image not found in the screenshot.' or time.monotonic()>=deadline:
                    raise
                self.wait(.75)

    def _find_image_once(self, image, region=None, threshold=.82, scales=None, reference_region=None, panel=False, allow_multiple=False):
        import cv2
        shot = self.screenshot(name="image-match", region=region or [0,.13,1,1])
        screen = cv2.imread(shot["path"])
        origin=list(shot["box"])
        if panel:
            from PIL import Image
            x,y,w,h=light_panel(Image.open(shot["path"]))
            screen=screen[y:y+h,x:x+w]
            origin[0]+=x;origin[1]+=y
        reference = cv2.imread(str(image))
        if reference is None or float(reference.std()) < 8:
            raise ValueError("Reference image is missing or has too little detail for safe matching.")
        if reference_region:
            if len(reference_region)!=4 or any(not 0<=float(n)<=1 for n in reference_region):
                raise ValueError("Reference region must contain four fractions.")
            x1,y1,x2,y2=reference_region
            if x2<=x1 or y2<=y1:
                raise ValueError("Reference region is empty.")
            height,width=reference.shape[:2]
            reference=reference[int(y1*height):int(y2*height),int(x1*width):int(x2*width)]
        if reference.shape[1] > 1600:
            reference=cv2.resize(reference,(1600,int(reference.shape[0]*1600/reference.shape[1])))
        candidates, ranked = [], []
        def evaluate(scale):
            self.guard()
            size=tuple(max(1,int(n*float(scale))) for n in (reference.shape[1],reference.shape[0]))
            if size[0] > screen.shape[1] or size[1] > screen.shape[0]:
                return
            scores=cv2.matchTemplate(screen,cv2.resize(reference,size),cv2.TM_CCOEFF_NORMED)
            _,score,_,pos=cv2.minMaxLoc(scores)
            ranked.append((score,scale))
            for _ in range(3):
                _,score,_,pos=cv2.minMaxLoc(scores)
                if score < float(threshold):
                    break
                candidates.append({"score":score,"x":pos[0]+size[0]//2+origin[0],"y":pos[1]+size[1]//2+origin[1],"width":size[0],"height":size[1]})
                x,y=pos
                scores[max(0,y-size[1]//2):y+size[1]//2+1,max(0,x-size[0]//2):x+size[0]//2+1]=-1
        initial=scales or [.10,.15,.2,.25,.3,.35,.4,.45,.5,.55,.6,.65,.7,.8,.9,1]
        for scale in initial:
            evaluate(scale)
        if not scales and ranked:
            # Refine scale around the strongest coarse candidates; coordinates are
            # still derived from the current screenshot rather than a saved point.
            for _,scale in sorted(ranked,reverse=True)[:2]:
                for offset in range(-9,10):
                    if scale+offset*.005 > 0:
                        evaluate(scale+offset*.005)
        if not candidates:
            raise ValueError("Reference image not found in the screenshot.")
        distinct=[]
        for match in sorted(candidates,key=lambda m:m["score"],reverse=True):
            if not any(abs(match["x"]-old["x"]) < min(match["width"],old["width"])*.4
                       and abs(match["y"]-old["y"]) < min(match["height"],old["height"])*.4 for old in distinct):
                distinct.append(match)
        if len(distinct)>1 and not allow_multiple:
            raise ValueError("More than one reference image is visible. No coordinate was selected.")
        matches=[{**m,"time":shot["time"],"screenshot":shot["path"],"screen_box":shot["box"]} for m in distinct]
        self.log("image",{"screenshot":Path(shot["path"]).name,"matches":distinct})
        return {**matches[0],"matches":matches}

    def click_image(self, **kwargs):
        kwargs.pop("allow_multiple",None)
        return self.click_match(self.find_image(**kwargs,allow_multiple=False))

    def hotkey(self, keys):
        lowered={str(key).casefold() for key in keys}
        debug=("ctrl" in lowered and (("shift" in lowered and bool(lowered & {"i","j","c"})) or "u" in lowered))
        snap = list(keys) in [["win", "left"], ["win", "up"]]
        if debug or not keys or (not snap and any(str(key).casefold() in {"win","winleft","winright","f12"} for key in keys)):
            raise ValueError("Unsupported keyboard shortcut.")
        self.input(self.gui.hotkey,*keys)

    def copy(self, keys=("ctrl","c")):
        marker = "ALLOTMENT-CLIPBOARD-"+uuid.uuid4().hex
        self.guard()
        self.text_clip.copy(marker)
        self.hotkey(keys=list(keys))
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            self.guard()
            value = self.text_clip.paste()
            if value and value != marker:
                return value
            time.sleep(.1)
        raise ValueError("Keyboard copy returned no fresh text. Stale clipboard content was rejected.")

    def paste_text(self, text):
        self.guard()
        self.text_clip.copy(str(text))
        self.hotkey(keys=["ctrl","v"])
        self.wait(.8)

    def paste_image(self, image):
        from PIL import Image
        self.guard()
        stream = BytesIO()
        Image.open(image).convert("RGB").save(stream,"BMP")
        deadline = time.monotonic()+10
        while True:
            self.guard()
            try:
                self.clip.OpenClipboard()
                break
            except Exception as error:
                # Windows briefly locks the clipboard while another app renders
                # copied content. Retry acquisition only, never a paste or write.
                if not error.args or error.args[0] != 5:
                    raise
                if time.monotonic() >= deadline:
                    raise ValueError('Windows clipboard stayed busy. No image was pasted; retry the test.') from error
                self.wait(.1)
        try:
            self.clip.EmptyClipboard()
            self.clip.SetClipboardData(self.wc.CF_DIB,stream.getvalue()[14:])
        finally:
            self.clip.CloseClipboard()
        self.hotkey(keys=["ctrl","v"])
        self.wait(4)

    def read_url(self):
        self.hotkey(keys=["ctrl","l"])
        value = self.copy().strip()
        self.hotkey(keys=["esc"])
        # Reverse focus traversal returns from the omnibox to web content.
        self.hotkey(keys=["shift","f6"])
        return value

    def check_url(self, url, exact=True):
        actual = self.read_url()
        expected, found = urlparse(url), urlparse(actual)
        if (expected.scheme,expected.netloc) != (found.scheme,found.netloc) or (exact and expected.path.rstrip("/") != found.path.rstrip("/")):
            raise ValueError(f"Wrong page: expected {url}, found {actual}.")
        self.url = actual
        return actual

    def navigate(self, url, seconds=2):
        if url != "about:blank" and urlparse(url).scheme not in {"http","https"}:
            raise ValueError("Only ordinary HTTP(S) webpages can be opened.")
        self.hotkey(keys=["ctrl","l"])
        self.paste_text(text=url)
        self.hotkey(keys=["enter"])
        self.wait(max(3,float(seconds)))
        return self.check_url(url=url)

    def capture_text(self, name="page", contains=None):
        self.hotkey(keys=["ctrl","a"])
        value = self.copy()
        self.hotkey(keys=["right"])
        path = self.evidence / f"{self.sequence:03d}-{self.safe_name(name)}.txt"
        path.write_text(value,encoding="utf-8")
        for expected in contains or []:
            if " ".join(expected.split()) not in " ".join(value.split()):
                raise ValueError(f"Captured text is missing {expected!r}.")
        return value

    def clipboard_html(self):
        """Chrome/Windows can still own the clipboard while rendering CF_HTML."""
        deadline=time.monotonic()+10
        format_id=self.clip.RegisterClipboardFormat("HTML Format")
        while time.monotonic()<deadline:
            self.guard()
            opened=False
            try:
                self.clip.OpenClipboard()
                opened=True
                return self.clip.GetClipboardData(format_id)
            except Exception:
                pass
            finally:
                if opened: self.clip.CloseClipboard()
            self.wait(.05)
        raise ValueError("Chrome did not finish copying rendered HTML.")

    def capture_page(self, name="page"):
        """Copy rendered page text and CF_HTML through ordinary keyboard input.

        This captures the rendered DOM, links and image URLs without source view,
        browser APIs, extensions, download settings, or Save As dialogs.
        """
        self.check_url(url=self.url)
        self.capture_text(name=name)
        self.guard()
        raw = self.clipboard_html()
        if isinstance(raw,str):
            raw=raw.encode("utf-8")
        header=raw[:1024].decode("utf-8",errors="replace")
        start=re.search(r"StartHTML:(\d+)",header)
        end=re.search(r"EndHTML:(\d+)",header)
        source=re.search(r"SourceURL:([^\r\n]+)",header)
        if not start or not end or not source or source[1].rstrip("/") != self.url.rstrip("/"):
            raise ValueError("Copied HTML has no matching source URL. Capture rejected.")
        start,end=int(start[1]),int(end[1])
        if not 0 <= start < end <= len(raw):
            raise ValueError("Copied HTML offsets are invalid.")
        path=self.evidence / f"{self.safe_name(name)}-{uuid.uuid4().hex[:8]}.html"
        path.write_bytes(raw[start:end])
        self.log("captured-page",{"path":path.name,"source":source[1],"bytes":end-start})
        return str(path)

    def check_image(self, image, expected_urls, region=None, threshold=.82, menu_text="Copy image address"):
        """Match screenshot pixels, then bind that image to the captured post URL."""
        result=self.find_image(image=image,region=region,threshold=threshold,allow_multiple=True)
        for match in result.get("matches",[result]):
            self.guard()
            marker="ALLOTMENT-IMAGE-"+uuid.uuid4().hex
            self.text_clip.copy(marker)
            self.click_match(match,button="right")
            self.wait(.4)
            self.click_text(text=menu_text,region=[0,.06,1,.95])
            value=self.text_clip.paste().strip()
            if value != marker and value in expected_urls:
                self.log("verified-image",{"source":value,"score":match["score"],"screenshot":Path(match["screenshot"]).name})
                return value
        raise ValueError("The visible image address does not belong to the new post's captured HTML.")


    def release(self):
        if self.mutex is not None:
            import win32api
            import win32event
            win32event.ReleaseMutex(self.mutex)
            win32api.CloseHandle(self.mutex)
            self.mutex=None


_active_runtime=None


def begin(evidence,settings=None):
    global _active_runtime
    if _active_runtime is not None:
        raise ValueError("A desktop session is already active. End it before starting another.")
    runtime=Desktop(evidence,settings)
    try:
        runtime.attach()
    except Exception:
        runtime.release()
        raise
    _active_runtime=runtime
    return {"evidence":str(runtime.evidence),"hwnd":runtime.hwnd}


def runtime_for(kwargs):
    runtime=kwargs.get("_desktop_runtime") or _active_runtime
    if runtime is None:
        raise ValueError("Run desktop_begin first, or pass an attached Desktop as _desktop_runtime.")
    return runtime


def end():
    global _active_runtime
    if _active_runtime is not None:
        _active_runtime.release()
    _active_runtime=None
    return {"ended":True}
