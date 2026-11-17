"""Translation payload/policy adapter. Browser sequence lives entirely in YAML."""
from pathlib import Path
from urllib.parse import urlparse
from .storage import read_yaml,lock
from .roboclick import framework
from .roboclick.desktop import Desktop
from .roboclick.workflow import Workflow,validate_steps
from .workflow_extensions.operations import ExtendedOperations
from .translations import tags,validate

RECIPE=Path(__file__).parent/'plugins/chatgpt_translation.yaml'
ACTIONS=Path(__file__).parent/'roboclick/workflow_actions'
DESKTOP_ACTIONS=Path(__file__).parent/'roboclick/actions'
EXTENSION_ACTIONS=Path(__file__).parent/'workflow_extensions/actions'
DESKTOP_COMMANDS={'desktop_check_url','desktop_wait','desktop_paste_image',
                  'desktop_paste_text','desktop_capture_text','desktop_hotkey','desktop_screenshot',
                  'desktop_capture_page'}


def action_lookup():
    desktop=framework.build_action_lookup(DESKTOP_ACTIONS)
    return {**{name:desktop[name] for name in DESKTOP_COMMANDS},**framework.build_action_lookup(ACTIONS),
            **framework.build_action_lookup(EXTENSION_ACTIONS)}


def validate_recipe(recipe):
    if recipe.get('kind')!='translation' or recipe.get('url')!='https://chatgpt.com/':
        raise ValueError('Translation is restricted to the configured ChatGPT page.')
    validate_steps(recipe.get('steps'),action_lookup())
    submits=[]
    def visit(steps,nested=False):
        for step in steps:
            if 'steps' in step:visit(step['steps'],True)
            if step.get('command')=='desktop_hotkey':
                keys=step.get('keys')
                if keys in (['enter'],['ctrl','enter']):
                    if nested:raise ValueError('Submission cannot be inside a repeat block.')
                    submits.append(step)
                elif keys!=['tab']:raise ValueError('Unsupported translation keyboard action.')
    visit(recipe['steps'])
    if len(submits)!=1:raise ValueError('Translation requires exactly one AI prompt submission.')
    return recipe


class QueryPolicy:
    """Enforce consent/checks; no clicks, keyboard sequencing or UI selectors."""
    def __init__(self,session,url,prompt,images,run_id,upload_checks):
        self.session,self.url,self.prompt,self.images,self.run_id=session,url,prompt,list(images),run_id
        self.pasted='';self.uploaded=[];self.uploads_ready=0;self.copies=0
        self.upload_checks=upload_checks;self.upload_snapshot=None;self.upload_passes=set()
        self.site_checked=False;self.root_checked=False;self.composer=False;self.match=None
        self.copy=None;self.asserted=False;self.submitted=False;self.conversation=None

    def before(self,command,args,values):
        if command in {'desktop_paste_text','desktop_paste_image','desktop_click_match'}:
            if self.submitted:raise ValueError('No further composer input after AI submission.')
            if not self.site_checked:raise ValueError('Check the configured page before composer input.')
        if command=='desktop_click_match' and args.get('match')!=self.match:
            raise ValueError('Composer click needs the fresh detected placeholder.')
        if command in {'desktop_paste_text','desktop_paste_image'} and not self.composer:
            raise ValueError('Find and focus the empty composer before input.')
        if command=='desktop_paste_image':
            if not self.asserted or self.pasted!=self.prompt:
                raise ValueError('Verify the full prompt in the focused composer before attaching an image.')
            if len(self.uploaded)>=len(self.images) or args.get('image')!=self.images[len(self.uploaded)]:
                raise ValueError('Image is not part of the authorized input.')
        if command=='desktop_paste_text' and not self.prompt.startswith(self.pasted+args['text']):
            raise ValueError('Text is not part of the authorized prompt.')
        if command=='desktop_hotkey' and 'enter' in args['keys']:
            if (self.submitted or not self.asserted or self.copies<2 or not self.root_checked
                    or self.pasted!=self.prompt or self.uploaded!=self.images or self.uploads_ready!=len(self.images)):
                raise ValueError('AI submission needs the full prompt, completed uploads, URL and two fresh text checks.')
            self.submitted=True
            self.session.log('translation-prompt-submission-attempted',{'run_id':self.run_id,'social_submission':False})

    def after(self,command,args,result,values):
        self.asserted=False
        if command=='desktop_check_url':
            if args.get('url')!=self.url or str(result).rstrip('/')!=self.url.rstrip('/'):
                raise ValueError('The new-chat URL was not verified.')
            self.site_checked=True;self.root_checked=True
        elif command=='desktop_find_text_any':self.match=result
        elif command=='desktop_click_match':self.composer=True
        elif command=='desktop_paste_image':
            self.uploaded.append(args['image']);self.copies=0;self.uploads_ready=0
            values['uploaded_count']=len(self.uploaded)
            self.upload_snapshot=None;self.upload_passes.clear()
        elif command=='desktop_capture_accessibility':
            self.upload_snapshot=result;self.upload_passes.clear();self.uploads_ready=0
        elif command=='data_elements_check' and self.upload_snapshot is not None:
            if args.get('snapshot')!=self.upload_snapshot or result.get('capture_id')!=self.upload_snapshot.get('capture_id'):
                raise ValueError('Upload checks need the current accessibility snapshot.')
            for kind,match in self.upload_checks.items():
                expected=len(self.uploaded) if kind=='attachments' else 1 if kind=='ready' else 0
                if args.get('match')==match and args.get('min_count')==args.get('max_count')==expected and result.get('count')==expected:
                    self.upload_passes.add(kind)
            if self.upload_passes=={'attachments','ready','busy','error'}:self.uploads_ready=len(self.uploaded)
        elif command=='desktop_paste_text':
            self.pasted+=args['text'];self.copies=0;self.copy=None;self.root_checked=False
        elif command=='desktop_capture_text':self.copy=result
        elif command=='data_assert_equal':
            if (self.copy is not None and args.get('actual')==self.copy and args.get('expected')==self.prompt
                    and ' '.join(self.copy.split())==' '.join(self.prompt.split())):
                self.copies+=1;self.copy=None;self.asserted=True
        elif command=='desktop_read_url':
            found,base=urlparse(result),urlparse(self.url)
            if (found.scheme,found.netloc)!=(base.scheme,base.netloc) or not (found.path in {'','/'} or found.path.startswith('/c/')):
                raise ValueError('The browser left the authorized AI conversation.')
            if self.conversation and result!=self.conversation:raise ValueError('The AI conversation changed.')
            if self.submitted and found.path.startswith('/c/') and not self.conversation:
                self.conversation=result;self.session.url=result
                self.session.log('translation-conversation-opened',{'url':result})


def make_workflow(session,recipe,run,folder,update):
    prompt=(folder/'prompt.txt').read_text(encoding='utf-8');begin,end=tags(run)
    policy=QueryPolicy(session,recipe['url'],prompt,run['images'],run['id'],recipe['upload_checks'])
    values={'url':recipe['url'],'prompt_file':str((folder/'prompt.txt').resolve()),
            'images':run['images'],'begin_tag':begin,'end_tag':end,'upload_checks':recipe['upload_checks']}
    lookup=action_lookup();validate_steps(recipe['steps'],lookup)
    workflow=Workflow(session,folder,values,lookup,framework.run_action,update,policy.before,policy.after)
    workflow.operations=ExtendedOperations(session,folder)
    return workflow


def run_pipeline(store,folder,run,update,stop):
    from .launch import countdown_in_tab
    recipe=validate_recipe(read_yaml(RECIPE))
    with lock(store.local/'browser.lock'):
        session=Desktop(folder/'evidence',read_yaml(store.local/'worker.yaml'),stop)
        try:
            session.attach()
            def countdown(**values):
                phase=values.pop('phase');update(status='countdown' if phase=='switching' else phase,**values)
            countdown_in_tab(session,folder,recipe['url'],countdown,resume_flag='resume.flag')
            values=make_workflow(session,recipe,run,folder,update).run(recipe['steps'])
            return validate(values['result'],run)
        except Exception:
            try:session.screenshot(name='translation-failure')
            except Exception:pass
            raise
        finally:session.release()
