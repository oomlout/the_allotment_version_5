"""Screenshot-based draft actions for pages whose clipboard omits controls.

No arbitrary keys, coordinates or submission actions are accepted from YAML.
File-dialog Enter is isolated from webpage input and bound to Chrome's own dialog.
"""
from pathlib import Path
import re
import time


def safe_control(text):
    label = str(text).strip().casefold()
    allowed = {'new post', 'create post', 'create', 'new', 'next', 'text', 'photo', 'photos',
               'image', 'images', 'add photos', 'add photos/video', 'select from computer',
               'choose photos and videos to upload', 'choose files', 'browse', 'write',
               'write a story', 'quip', 'quips', 'account', 'profile', 'your profile',
               'settings', 'your blogs', 'close', 'got it', 'not now', 'all', 'channels',
               'chats', 'communities', 'more', 'add', 'attach', 'photos & videos',
               'photos and videos', 'upload photo', 'create a post', 'original'}
    if label not in allowed:
        raise ValueError(f'Unapproved preparation control: {text!r}. Post, Send, Share and Publish are manual.')


class VisualActions:
    def control_asset(self,name):
        from .storage import slug
        root=Path(__file__).parent/'platform_assets'/slug(self.recipe['id'])
        if self.account.get('local_template_set') and getattr(self,'local',None):
            private=self.local/'platform-assets'/slug(self.account['local_template_set'])/slug(self.recipe['id'])
            if (private/name).is_file():root=private
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()) or path.suffix!='.png' or not path.is_file():
            raise ValueError('Use a calibrated platform control template.')
        return path

    def park_pointer(self):
        left,top,right,bottom=self.bounds()
        self.input(self.gui.moveTo,left+2,(top+bottom)//2)
        self.wait(.3)

    def visual_identity(self, region, owner_text='', owner_region=None, profile_url='', text_prefix=''):
        if profile_url:
            self.check_url(url=profile_url)
        identity = self.variables['expected_account'].lstrip('@')
        if not identity:
            raise ValueError('Configure the exact posting identity first.')
        self.find_text(text=text_prefix+identity, region=region, exact=False)
        if owner_text:
            self.find_text(text=owner_text, region=owner_region or region, exact=True)
        elif profile_url:
            raise ValueError('A public profile alone is not signed-in identity proof; require an owner control.')
        self.account_checked = True
        self.target_checked = not self.recipe.get('target_required')
        self.identity_config = {'visual': True}
        self.screenshot(name='visual-account-checked')

    def visual_target(self, region, kind_text='', kind_region=None, details=False, detail_region=None, parent_region=None,
                      owner_text='', owner_region=None, copy_link_region=None, empty_editor=False):
        if not self.account_checked:
            raise ValueError('Check the signed-in account before the destination.')
        if self.recipe.get('target_kind') in {'group', 'channel'} and not kind_text:
            raise ValueError('Require a group/channel type marker.')
        found=self.find_text(text=self.variables['target_name'], region=region, exact=False)
        if details:
            self.click_match(found)
            self.wait(.7)
            self.park_pointer()
            self.find_text(text=self.variables['target_name'],region=detail_region,exact=True)
        if kind_text:
            try:
                self.find_text(text=kind_text, region=kind_region or region, exact=False)
            except ValueError as error:
                if not empty_editor or not str(error).startswith('Could not find'):
                    raise
                raise ValueError('No empty message box found. An unsent draft may already be open, '
                                 'or the layout has changed. Review the existing draft in Chrome '
                                 'and clear it manually if finished, then Restart. Nothing was overwritten.') from error
        if parent_region and self.variables.get('target_parent'):
            self.find_text(text='Group in '+self.variables['target_parent'],region=parent_region)
        if owner_text:
            self.find_text(text=owner_text,region=owner_region or detail_region or region,exact=True)
        if copy_link_region:
            if not owner_text or self.recipe.get('target_kind')!='channel':
                raise ValueError('Channel link verification requires an owner control and channel type.')
            self.verify_public_link(copy_link_region)
        self.target_checked = True
        self.screenshot(name='visual-destination-checked')

    def verify_public_link(self, region):
        """Read only the channel's Copy link control and compare the exact address."""
        from urllib.parse import urlparse
        import uuid
        expected=self.variables.get('profile_url','').rstrip('/')
        parsed=urlparse(expected)
        if parsed.scheme!='https' or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Configure an exact public channel URL before copying its link.')
        match=self.find_text(text='Copy link',region=region,exact=True)
        self.guard()
        marker='ALLOTMENT-LINK-'+uuid.uuid4().hex
        self.text_clip.copy(marker)
        self.click_match(match)
        deadline=time.monotonic()+3
        while True:
            self.guard()
            value=self.text_clip.paste().strip()
            if value!=marker:break
            if time.monotonic()>=deadline:raise ValueError('The channel did not copy its public link.')
            self.wait(.1)
        if value.rstrip('/')!=expected:
            raise ValueError('The selected channel does not match its configured public URL. No sample entered.')
        self.log('channel-link-checked',{'url':value})

    def open_control(self, text='', region=None, template='', threshold=.93, panel=False, seconds=.7, file_picker=False):
        self.park_pointer()
        if text:
            safe_control(text)
        elif not template:
            raise ValueError('A preparation control requires an approved label or template.')
        if template:
            path = self.control_asset(template)
            if Path(template).stem not in {'create', 'attach', 'account', 'profile', 'add-image', 'next', 'close','chats','channels','crop'}:
                raise ValueError('A submission control cannot be a template target.')
            match=self.find_image(image=str(path), region=region, threshold=max(.9,float(threshold)),scales=[1])
        else:
            match=self.find_text(text=text, region=region, exact=True, panel=panel)
        self.click_match(match,allow_dialog=file_picker)
        self.focused = False
        if file_picker:
            self._picker=True
            deadline=time.monotonic()+8
            while self.wg.GetForegroundWindow()==self.hwnd and time.monotonic()<deadline:
                super().guard()
                time.sleep(.1)
            self.guard(dialog=True)
        else:
            self.wait(seconds)
            self.park_pointer()

    def visual_focus(self, region, text='', panel=False, template=''):
        if not self.account_checked or not self.target_checked:
            raise ValueError('Check account and destination before editing.')
        if re.search(r'\b(send|publish|share|submit|schedule)\b',text.casefold()) or text.strip().casefold() in {'post','upload'}:
            raise ValueError('A submission control cannot be an editor target.')
        if template:
            path=self.control_asset(template)
            if path.name!='editor.png':
                raise ValueError('Use the bundled empty-editor template.')
            match=self.find_image(image=str(path),region=region,threshold=.95,scales=[1])
            self.click_match(match)
        else:
            if not text.strip():raise ValueError('An editor needs a visible placeholder or a calibrated frame.')
            self.click_text(text=text,region=region,panel=panel)
        self.focused=True

    def search_group(self, text, region, result_region, community_text='', community_region=None):
        if not self.account_checked:
            raise ValueError('Check the account before searching.')
        if text.casefold() not in {'search', 'search or start a new chat'}:
            raise ValueError('Use the platform search field.')
        self.click_text(text=text,region=region)
        self.paste_text(text=self.variables['target_name'])
        self.wait(1)
        if community_text:
            self.find_text(text=community_text,region=community_region or result_region)
        self.click_text(text=self.variables['target_name'],region=result_region,exact=True)
        self.wait(.7)

    def preview_zoom(self, percent=200):
        """Enlarge a checked draft for visual inspection using normal Chrome keys."""
        if percent not in (100,125,150,175,200) or isinstance(percent,bool):
            raise ValueError('Preview zoom must be 100, 125, 150, 175 or 200 percent.')
        if not self.account_checked or not self.target_checked or not self.text_checked:
            raise ValueError('Check the draft text before preview zoom.')
        self.hotkey(keys=['ctrl','l'])
        self.hotkey(keys=['ctrl','0'])
        # Chrome zoom steps: 110, 125, 150, 175, 200 percent.
        for _ in range({100:0,125:2,150:3,175:4,200:5}[percent]):
            self._preview_zoom_changed = True
            self.hotkey(keys=['ctrl','+'])
        self.hotkey(keys=['esc'])
        self.hotkey(keys=['shift','f6'])
        self.focused=False
        self.wait(1)

    def reset_browser_zoom(self):
        """Keep Ctrl+0 in Chrome's toolbar; pages may bind it to chat actions."""
        self.hotkey(keys=['ctrl','l'])
        self.hotkey(keys=['ctrl','0'])
        self.hotkey(keys=['esc'])
        self.hotkey(keys=['shift','f6'])
        self.focused=False

    def restore_preview_zoom(self):
        """Restore the reserved tab without bypassing Stop or focus/input guards."""
        if not getattr(self, '_preview_zoom_changed', False):
            return
        self.reset_browser_zoom()
        self._preview_zoom_changed = False
        self.focused = False
        self.wait(.5)

    def upload_image(self):
        """Choose the queued image in an already-open native file picker only."""
        if not self.account_checked or not self.target_checked or not self.image_path:
            raise ValueError('Check identity and destination before selecting the queued image.')
        if not Path(self.image_path).is_file():
            raise ValueError('Queued image is missing.')
        self._picker=True
        try:
            self.guard(dialog=True)
            foreground=self.wg.GetForegroundWindow()
            if foreground==self.hwnd:
                raise ValueError('No native file picker is open. No path was entered.')
            # Alt+N is File name in the Windows Open dialog; this cannot type in a webpage.
            self.input(self.gui.hotkey,'alt','n',dialog=True)
            self.input(self.gui.hotkey,'ctrl','a',dialog=True)
            self.text_clip.copy(str(Path(self.image_path).resolve()))
            self.input(self.gui.hotkey,'ctrl','v',dialog=True)
            self.input(self.gui.press,'enter',dialog=True)
            # Chrome returns focus asynchronously after accepting a file. Wait only
            # while the verified picker remains foreground; never steal focus.
            deadline=time.monotonic()+8
            while self.wg.GetForegroundWindow()!=self.hwnd:
                self.guard(dialog=True)
                if time.monotonic()>=deadline:
                    raise ValueError('Chrome did not return from its file picker.')
                time.sleep(.05)
        finally:
            self._picker=False
        self.wait(2)
        self.image_pasted=True
        self.focused=False

    def guard(self, dialog=False):
        if not (dialog and getattr(self,'_picker',False)):
            if getattr(self,'hwnd',None):
                blocked=[]
                def inspect(hwnd,_):
                    if (self.wg.IsWindowVisible(hwnd) and self.wg.GetClassName(hwnd)=='#32770'
                            and self.wg.GetWindow(hwnd,self.wc.GW_OWNER)==self.hwnd):
                        blocked.append(hwnd)
                self.wg.EnumWindows(inspect,None)
                if blocked:
                    raise ValueError('Close the existing Chrome dialog before starting or continuing preparation.')
            return super().guard(dialog=dialog)
        self.check_stop()
        if not self.hwnd or not self.wg.IsWindow(self.hwnd):
            raise ValueError('Reserved Chrome window disappeared.')
        if abs(self.psutil.Process(self.pid).create_time()-self.created)>.1:
            raise ValueError('Chrome process identity changed.')
        foreground=self.wg.GetForegroundWindow()
        _,pid=self.wp.GetWindowThreadProcessId(foreground)
        process=self.psutil.Process(pid)
        # Current Chrome hosts Open in a sandboxed child chrome.exe process.
        same_browser=(pid==self.pid or process.name().casefold()=='chrome.exe'
                      and self.pid in [parent.pid for parent in process.parents()])
        if (not same_browser or self.wg.GetWindow(foreground,self.wc.GW_OWNER)!=self.hwnd
                or self.wg.GetClassName(foreground)!='#32770'
                or self.wg.GetWindowText(foreground).casefold() not in {'open','file upload'}):
            raise ValueError('Expected Chrome-owned Windows Open file dialog. No input sent. '
                             f'window={foreground}, pid={pid}/{self.pid}, owner={self.wg.GetWindow(foreground,self.wc.GW_OWNER)}/{self.hwnd}, '
                             f'class={self.wg.GetClassName(foreground)}, title={self.wg.GetWindowText(foreground)!r}')
        if self.input_tick is not None and self.last_input()!=self.input_tick:
            from .browser import StopRequested
            raise StopRequested('User input detected in file picker.')
