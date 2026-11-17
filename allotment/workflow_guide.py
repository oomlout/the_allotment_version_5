"""On-demand, read-only YAML workflow to standalone manual HTML. Never runs actions."""
from pathlib import Path
from html import escape
import argparse
import hashlib
import json
import yaml


def value_text(value):
    if isinstance(value, dict) and set(value)=={'ref'}:
        return 'the value of '+str(value['ref'])
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def instruction(step,manual_instructions=None):
    """Describe known primitives; unknown actions retain all data without invented steps."""
    command=step.get('command','')
    get=lambda key,default='':value_text(step.get(key,default))
    instructions={
        'desktop_check_url':lambda:f"Check the address bar matches {get('url')}. Stop if it does not.",
        'desktop_read_url':lambda:'Press Ctrl+L, then Ctrl+C to copy the address. Return focus to the page without navigating.',
        'desktop_wait':lambda:f"Wait {get('seconds')} seconds. {get('detail')}",
        'desktop_find_text_any':lambda:f"Find the first visible label from {get('texts')}. Require one unambiguous match inside the configured screen region. Do not click a lookalike elsewhere.",
        'desktop_click_match':lambda:f"Click the control found in {get('match')}. Check that it is still in the same position immediately before clicking.",
        'desktop_paste_text':lambda:f"Paste {get('text')} into the focused editor. Keep all characters and line breaks intact; do not submit yet.",
        'desktop_paste_image':lambda:f"Copy image pixels from {get('image')} and paste into the focused editor with Ctrl+V. The equivalent manual option is to attach that same local file. Wait for the attachment to appear.",
        'desktop_capture_text':lambda:'In the editor, press Ctrl+A then Ctrl+C. Save or compare the copied text, then press Right to clear the selection without deleting it.',
        'desktop_hotkey':lambda:f"Press {' + '.join(step.get('keys',[]))}. If this submits a message, do it only after all preceding checks pass and you intend to submit to this destination.",
        'desktop_screenshot':lambda:f"Take a screenshot named {get('name','screenshot')} for your records.",
        'desktop_capture_page':lambda:'Move focus outside the editor. Use normal page Select All and Copy to capture the page. For manual work, copy the complete response itself; do not mistake the prompt for the answer.',
        'desktop_capture_accessibility':lambda:'Look at the current page controls and upload indicators. Check the states in the following steps. The automated equivalent reads Windows accessibility, not image identity.',
        'data_read_text':lambda:f"Read the UTF-8 text from {get('path')} in a text editor. Keep the complete contents for the following steps.",
        'data_text_chunks':lambda:f"Split {get('text')} into consecutive chunks of at most {get('size',2500)} characters. Do not add or remove text between chunks.",
        'data_assert_equal':lambda:f"Compare {get('actual')} with {get('expected')}. They must match{' after treating repeated whitespace as a single space' if step.get('normalize_whitespace') else ' exactly'}. Stop on a mismatch.",
        'data_elements_check':lambda:f"Count the visible controls matching {get('match')}. Require at least {get('min_count',0)} and at most {get('max_count','any number')}. {get('message')} {'Stop on mismatch.' if step.get('on_mismatch')=='error' else 'If incomplete, wait and repeat the enclosing observation checks.'}",
        'data_url_assert':lambda:f"Check {get('url')} stays on {get('origin')} and its path begins with {get('path_prefix','the configured path')}. Only the listed pending paths may be waited on; stop if the browser goes elsewhere.",
        'data_html_absent':lambda:f"Wait until the control described by {get('selector')} disappears. For a stop-generation button, this means the answer has finished generating.",
        'data_html_extract':lambda:f"Find the one response matching {get('selector')} and containing every marker in {get('contains')}. Use the assistant response, not a prompt echo or an earlier answer.",
        'data_text_between':lambda:f"Copy only the text between {get('start')} and {get('end')} from {get('text')}. Require exactly one complete pair; exclude the markers themselves.",
        'data_json_parse':lambda:f"Check {get('text')} is one complete valid JSON object, with no duplicate keys or Markdown fences. Save it as UTF-8 .json.",
        'prepare_navigate':lambda:f"Open {get('url')} in a fresh tab and wait {get('seconds',3)} seconds.",
        'prepare_visual_identity':lambda:f"Check the configured account name and owner-only control {get('owner_text')} on {get('profile_url')}. A public profile alone does not prove you are signed in as its owner.",
        'prepare_open':lambda:f"Click {get('text','the configured control')} in the specified region. {'This should open a local file chooser.' if step.get('file_picker') else 'Do not click a final submission button.'}",
        'prepare_upload_image':lambda:'In the browser-owned file chooser, select the authorized image and open it. This only attaches the file; leave the final site Upload/Publish button untouched.',
        'prepare_visual_focus':lambda:f"Click the editor labelled {get('text')}. Confirm its caret is visible before typing.",
        'prepare_text':lambda:'Paste the authorized post text into the focused editor without submitting.',
        'prepare_verify_text':lambda:'Select and copy the editor text. Compare it with the intended post, then clear the selection without deleting anything.',
        'prepare_preview_zoom':lambda:f"Temporarily use {get('percent')}% browser zoom to inspect the attachment. Restore 100% before leaving the tab.",
        'prepare_finish':lambda:'Check the complete unsent draft and attachment, restore 100% zoom and leave the tab open. Do not click Post, Send, Publish or the final Upload button.',
    }
    if command in (manual_instructions or {}):
        return value_text(manual_instructions[command])
    if command in instructions:return instructions[command]()
    return ('No manual procedure is defined for this action. Read its exact parameters below and the action documentation before proceeding. '
            'Supply a manual_instructions entry to the guide renderer for a human procedure; this guide does not execute or guess the action.')


def workflow_groups(data):
    """Top-level lists, steps/actions and legacy named mode blocks; retain YAML order."""
    groups=[]
    def visit(node,path='',ancestors=()):
        if isinstance(node,(dict,list)):
            if id(node) in ancestors or len(ancestors)>40:raise ValueError('Recursive or overly nested YAML cannot be documented.')
            ancestors=(*ancestors,id(node))
        if isinstance(node,list):
            if any(isinstance(s,dict) and any(k in s for k in ('command','for_each','poll')) for s in node):
                groups.append((path or 'Steps',node));return
            for n,item in enumerate(node):visit(item,f'{path}/{n+1}',ancestors)
        elif isinstance(node,dict):
            for key,item in node.items():
                if key in {'actions','steps'} and isinstance(item,list):groups.append((path or 'Steps',item))
                elif isinstance(item,(dict,list)):visit(item,f'{path}/{key}'.strip('/'),ancestors)
    visit(data)
    return groups


def render_yaml_guide(source, *, title=None, introduction=(), inputs=None, appendices=None, manual_instructions=None):
    """Read source YAML and return escaped standalone HTML; no files or actions created."""
    source=Path(source);raw=source.read_text(encoding='utf-8-sig');data=yaml.safe_load(raw)
    groups=workflow_groups(data)
    title=title or (data.get('name') if isinstance(data,dict) else None) or source.stem
    esc=lambda v:escape(str(v),quote=True)
    dump=lambda v:esc(yaml.safe_dump(v,sort_keys=False,allow_unicode=True))
    def steps_html(steps,prefix='',ancestors=()):
        if id(steps) in ancestors or len(ancestors)>40:raise ValueError('Recursive or overly nested workflow.')
        ancestors=(*ancestors,id(steps));out=['<ol class="steps">']
        for n,step in enumerate(steps,1):
            number=f'{prefix}{n}'
            if not isinstance(step,dict):
                out.append(f'<li><h3>{number}. Unrecognized step</h3><pre>{dump(step)}</pre></li>');continue
            label=step.get('label') or step.get('description') or step.get('command') or ('Repeat for each item' if 'for_each' in step else 'Wait and check')
            out.append(f'<li id="step-{number}"><h3><input type="checkbox" aria-label="Mark step {number} done"> {number}. {esc(label)}</h3>')
            if 'for_each' in step:
                out.append(f'<p>For every item in <code>{esc(value_text(step["for_each"]))}</code>, call the current item <code>{esc(step.get("as","item"))}</code> and complete these steps in order.</p>')
            elif 'poll' in step:
                poll=step['poll']
                out.append(f'<p>{esc(poll.get("reason","Wait for the checks below to pass"))}. Repeat the observation checks every {esc(poll.get("interval",5))} seconds, for at most {esc(poll.get("timeout","unspecified"))} seconds. Require {esc(poll.get("observations",1))} consecutive successful checks. Never repeat typing, uploads or submission during this wait.</p>')
                if poll.get('stable_on'):out.append(f'<p>The value of <code>{esc(poll["stable_on"])}</code> must stay unchanged between those checks.</p>')
            else:out.append(f'<p>{esc(instruction(step,manual_instructions))}</p>')
            if step.get('detail'):out.append(f'<p>{esc(step["detail"])}</p>')
            if step.get('save_as'):out.append(f'<p>Keep the result as <code>{esc(step["save_as"])}</code> for later steps.</p>')
            if isinstance(step.get('steps'),list):out.append(steps_html(step['steps'],number+'.',ancestors))
            params={k:v for k,v in step.items() if k!='steps'}
            out.append(f'<details><summary>Exact action settings</summary><pre>{dump(params)}</pre></details></li>')
        return ''.join(out)+'</ol>'
    content=''.join(f'<p>{esc(p)}</p>' for p in introduction)
    if inputs:
        content+='<h2>Before you start</h2><dl>'+''.join(f'<dt>{esc(k)}</dt><dd>{esc(value_text(v))}</dd>' for k,v in inputs.items())+'</dl>'
    content+='<p>Work in order. Tick steps as you finish; ticks last only while this page stays open. A value reference uses an input above or a result saved by an earlier step. Screen regions are left, top, right, bottom fractions of the browser window.</p>'
    for name,body in (appendices or {}).items():content+=f'<details><summary>{esc(name)}</summary><button type="button" data-copy>Copy text</button><pre>{esc(body)}</pre></details>'
    for n,(name,steps) in enumerate(groups,1):content+=f'<h2>{esc(name)}</h2>'+steps_html(steps,'' if len(groups)==1 else f'{n}.')
    if not groups:content+='<p>This YAML contains no recognized action list. Its configuration is shown below; no manual procedure has been inferred.</p>'
    content+=f'<details><summary>Complete source YAML</summary><pre>{esc(raw)}</pre></details>'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} — Manual guide</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f2f3f5;color:#20252b;font:16px/1.5 system-ui,sans-serif}}main{{max-width:1050px;margin:auto;padding:24px}}h1{{font-size:30px;margin:0}}h2{{font-size:21px;margin:24px 0 10px}}h3{{font-size:17px;margin:0 0 8px}}p{{margin:8px 0}}.meta{{font-size:13px;color:#545e68;overflow-wrap:anywhere}}.steps{{list-style:none;padding:0}}.steps>li{{background:white;border:1px solid #cdd3d9;border-radius:6px;padding:16px;margin:10px 0}}.steps .steps>li{{border-left:3px solid #9ca7b0}}input{{width:17px;height:17px;vertical-align:middle}}details{{margin:12px 0}}summary{{cursor:pointer;font-weight:600}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef0f3;padding:12px;font-size:13px}}code{{overflow-wrap:anywhere}}dt{{font-weight:700;margin-top:10px}}dd{{margin:2px 0 12px;white-space:pre-wrap;overflow-wrap:anywhere}}@media(max-width:600px){{main{{padding:12px}}.steps>li{{padding:12px}}}}@media print{{body{{background:white}}main{{max-width:none;padding:0}}details{{display:block}}.steps>li{{break-inside:avoid}}}}
</style></head><body><main><h1>{esc(title)}</h1><p class="meta">Manual guide · {esc(source.name)} · SHA-256 {hashlib.sha256(raw.encode()).hexdigest()[:12]}</p>{content}</main><script>document.querySelectorAll('[data-copy]').forEach(button=>button.addEventListener('click',async()=>{{try{{await navigator.clipboard.writeText(button.nextElementSibling.textContent);button.textContent='Copied';}}catch(error){{button.textContent='Select and copy the text below';}}}}));</script></body></html>'''


def write_yaml_guide(source,output,**options):
    """Create a guide only when explicitly called with a destination."""
    source,output=Path(source),Path(output)
    if source.resolve()==output.resolve():raise ValueError('The guide must not overwrite its source YAML.')
    html=render_yaml_guide(source,**options)
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(html,encoding='utf-8')
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--title')
    args=parser.parse_args()
    print(write_yaml_guide(args.source,args.output,title=args.title))


if __name__=='__main__':main()
