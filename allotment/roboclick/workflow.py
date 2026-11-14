"""Small YAML sequencer for discovered roboclick primitives; no site workflow.

References preserve types. for_each repeats an explicit list; poll retries only
NotReady observations, never input failures. No eval, browser protocol or AI.
"""
import json
import time
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup


class NotReady(ValueError):
    """A read-only observation is incomplete and may be polled again."""


def resolve(value, values):
    if isinstance(value,dict):
        if set(value)=={'ref'}:
            found=values
            for key in value['ref'].split('.'):
                found=found[key]
            return found
        return {k:resolve(v,values) for k,v in value.items()}
    if isinstance(value,list):return [resolve(v,values) for v in value]
    return value


def html_text(node):
    copy=BeautifulSoup(str(node),'html.parser')
    for br in copy.select('br'):br.replace_with('\n')
    for block in copy.select('p, div, pre, li, h1, h2, h3'):
        block.insert_before('\n');block.insert_after('\n')
    return copy.get_text('',strip=False)


class Operations:
    """Reusable single-purpose desktop and local-data operations."""
    def __init__(self,desktop,root):self.desktop,self.root=desktop,Path(root).resolve()

    def read_text(self,path):
        path=Path(path).resolve()
        if not path.is_relative_to(self.root):raise ValueError('Read path is outside the workflow folder.')
        return path.read_text(encoding='utf-8')

    def text_chunks(self,text,size=2500):
        if not 1<=int(size)<=10000:raise ValueError('Invalid text chunk size.')
        return [text[n:n+int(size)] for n in range(0,len(text),int(size))]

    def assert_equal(self,actual,expected,normalize_whitespace=False):
        if normalize_whitespace:
            actual,expected=' '.join(actual.split()),' '.join(expected.split())
        if actual!=expected:raise ValueError('Captured text does not match the expected text. Nothing was submitted.')
        return actual

    def find_text_any(self,texts,**options):
        for text in texts:
            try:return self.desktop.find_text(text=text,**options)
            except ValueError as error:
                if not str(error).startswith('Could not find '):raise
        raise ValueError('None of the configured text labels is visible.')

    def image_crop(self,image,aspect_ratio=1):
        from PIL import Image
        with Image.open(image) as source:w,h=source.size
        ratio=float(aspect_ratio)
        if ratio<=0:raise ValueError('Invalid crop aspect ratio.')
        cw,ch=min(w,h*ratio),min(h,w/ratio)
        return [(w-cw)/(2*w),(h-ch)/(2*h),(w+cw)/(2*w),(h+ch)/(2*h)]

    def assert_url(self,url,origin,path_prefix=None,pending_paths=None):
        found,base=urlparse(url),urlparse(origin)
        if (found.scheme,found.netloc)!=(base.scheme,base.netloc):raise ValueError('The browser left the expected site.')
        if path_prefix and not found.path.startswith(path_prefix):
            if found.path in (pending_paths or []):raise NotReady('Waiting for the expected page URL.')
            raise ValueError('The browser left the expected page.')
        return url

    def html_absent(self,html,selector):
        if BeautifulSoup(html,'html.parser').select(selector):raise NotReady('The page is still updating.')
        return True

    def html_extract(self,html,selector,contains):
        nodes=BeautifulSoup(html,'html.parser').select(selector);identities={id(n) for n in nodes}
        nodes=[n for n in nodes if not any(id(p) in identities for p in n.parents)]
        candidates=[html_text(n) for n in nodes if all(t in n.get_text() for t in contains)]
        if not candidates:raise NotReady('Waiting for a complete matching response.')
        if len(candidates)!=1:raise ValueError('More than one matching response; no result selected.')
        return candidates[0]

    def text_between(self,text,start,end):
        if text.count(start)!=1 or text.count(end)!=1 or text.index(end)<text.index(start):
            raise ValueError('Expected exactly one complete pair of capture tags.')
        return text.split(start,1)[1].split(end,1)[0].strip()

    def json_parse(self,text):
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError('Duplicate JSON key in response.')
                result[key]=value
            return result
        return json.loads(text,object_pairs_hook=unique)


READ_ONLY={'desktop_read_url','desktop_capture_page','desktop_capture_text','desktop_wait',
           'desktop_screenshot','data_read_text','data_url_assert','data_html_absent',
           'data_html_extract','data_text_between','data_json_parse','data_assert_equal'}


def validate_steps(steps,lookup,in_poll=False):
    if not isinstance(steps,list) or not steps:raise ValueError('Workflow needs explicit steps.')
    for step in steps:
        if not isinstance(step,dict):raise ValueError('Invalid workflow step.')
        if 'for_each' in step:
            if in_poll:raise ValueError('Polling cannot repeat input loops.')
            validate_steps(step['steps'],lookup)
        elif 'poll' in step:
            if in_poll:raise ValueError('Nested polling is not supported.')
            if not 0<float(step['poll']['timeout'])<=1800:raise ValueError('Invalid polling timeout.')
            if not 1<=float(step['poll'].get('interval',5))<=60:raise ValueError('Invalid polling interval.')
            validate_steps(step['steps'],lookup,True)
        else:
            command=step.get('command')
            if command not in lookup:raise ValueError(f'Unknown roboclick primitive: {command}')
            if in_poll and command not in READ_ONLY:raise ValueError('Polling may only repeat read-only primitives.')


class Workflow:
    def __init__(self,desktop,root,values,lookup,dispatch,update=None,before=None,after=None):
        self.desktop,self.values,self.lookup,self.dispatch=desktop,dict(values),lookup,dispatch
        self.operations=Operations(desktop,root)
        self.update=update or (lambda **kw:None)
        self.before,self.after=before,after
        self.step_index=0;self.total_steps=0;self.iteration={};self.polling={}

    def report(self,**values):
        details={'step_index':self.step_index,'total_steps':self.total_steps,
                 'item_index':None,'item_total':None,'item_label':None,
                 'wait_until':None,'wait_seconds':None,'wait_reason':'','wait_kind':'',
                 'poll_until':None,'poll_timeout':None,'poll_attempt':None,'poll_reason':''}
        self.update(**{**details,**self.iteration,**self.polling,**values})

    @staticmethod
    def until(seconds):
        return datetime.fromtimestamp(time.time()+seconds,timezone.utc).isoformat()

    def run(self,steps,prefix=''):
        if not prefix:self.total_steps=len(steps)
        for index,step in enumerate(steps,1):
            self.desktop.check_stop();path=f'{prefix}{index}'
            if not prefix:self.step_index=index
            if 'for_each' in step:
                items=resolve(step['for_each'],self.values)
                if not isinstance(items,list):raise ValueError('for_each requires a list.')
                key=step['as'];had=key in self.values;previous=self.values.get(key)
                previous_iteration=self.iteration
                try:
                    for n,item in enumerate(items,1):
                        self.iteration={'item_index':n,'item_total':len(items),'item_label':step.get('label','Item')}
                        self.values[key]=item;self.run(step['steps'],f'{path}[{n}].')
                finally:
                    self.iteration=previous_iteration
                    if had:self.values[key]=previous
                    else:self.values.pop(key,None)
            elif 'poll' in step:
                config=step['poll'];deadline=time.monotonic()+float(config['timeout'])
                previous=object();stable=0;last='No complete observation.'
                previous_polling=self.polling
                self.polling={'poll_until':self.until(float(config['timeout'])),'poll_timeout':config['timeout'],
                              'poll_attempt':0,'poll_reason':config.get('reason','Waiting for a complete observation')}
                try:
                    while time.monotonic()<deadline:
                        self.polling['poll_attempt']+=1
                        try:
                            self.run(step['steps'],f'{path}.')
                            value=self.values[config['stable_on']] if config.get('stable_on') else True
                            stable=stable+1 if value==previous else 1;previous=value
                            if stable>=int(config.get('observations',1)):break
                            last='Checking that the captured result is stable.'
                        except NotReady as error:stable=0;previous=object();last=str(error)
                        seconds=min(float(config.get('interval',5)),max(0,deadline-time.monotonic()))
                        self.report(operation=step.get('label','Waiting for the next check'),action='poll',step=path,
                                    detail=last,wait_kind='poll',wait_seconds=seconds,wait_until=self.until(seconds),
                                    wait_reason=config.get('reason',last))
                        self.desktop.wait(seconds)
                    else:raise ValueError('Observation timed out: '+last)
                finally:self.polling=previous_polling
            else:
                command=step['command']
                args=resolve({k:v for k,v in step.items() if k not in {'command','label','detail','save_as'}},self.values)
                detail=step.get('detail','')
                wait=({'wait_kind':'settle','wait_seconds':float(args['seconds']),
                       'wait_until':self.until(float(args['seconds'])),
                       'wait_reason':detail or step.get('label','Allowing the page to settle')}
                      if command=='desktop_wait' else {})
                self.report(operation=step.get('label',command),action=command,step=path,detail=detail,**wait)
                self.desktop.log('workflow-action',{'step':path,'command':command,'state':'started'})
                if self.before:self.before(command,args,self.values)
                result=self.dispatch(action={'command':command,**args},_discovered_actions=self.lookup,
                    _desktop_runtime=self.desktop,_workflow_runtime=self.operations)
                if self.after:self.after(command,args,result,self.values)
                if step.get('save_as'):self.values[step['save_as']]=result
                self.desktop.log('workflow-action',{'step':path,'command':command,'state':'complete'})
        return self.values
