import json
from copy import deepcopy
from unittest.mock import MagicMock,call
import pytest

from allotment.roboclick.workflow import Operations,NotReady,Workflow,validate_steps,html_text
from allotment.roboclick.desktop import DesktopStopped
from allotment.roboclick import framework
from allotment.translation_browser import RECIPE,action_lookup,validate_recipe,make_workflow
from allotment.storage import read_yaml
from allotment.translations import tags
from test_translations import pack


@pytest.mark.parametrize('markup',['old','current','nested'])
def test_recipe_captures_only_stable_tagged_assistant(tmp_path,markup):
    from html import escape
    run={'id':'run-fixture','source_revision':'r','source':{'text':'Exact 🌱'},'images':[],
         'destinations':['twitter'],'catalog':{'twitter':{'name':'Twitter','ready':True,'max_chars':280}}}
    begin,end=tags(run);payload=begin+'\n'+json.dumps(pack(run),ensure_ascii=False)+'\n'+end
    echo='<article data-message-author-role="user">'+escape(payload)+'</article>'
    answer='<article data-message-author-role="assistant">'+escape(payload)+'</article>'
    current='<div data-markdown-text-style="assistant-message">'+escape(payload)+'</div>'
    if markup=='current':answer=current
    if markup=='nested':answer='<article data-message-author-role="assistant">'+current+'</article>'
    pages=[echo,echo+answer+'<button data-testid="stop-button"></button>',echo+answer,echo+answer]
    session=MagicMock();session.read_url.return_value='https://chatgpt.com/c/fixture'
    def capture(**kw):
        path=tmp_path/'page.html';path.write_text(pages.pop(0),encoding='utf8');return str(path)
    session.capture_page.side_effect=capture
    recipe=read_yaml(RECIPE)
    step=next(s for s in recipe['steps'] if s.get('poll',{}).get('stable_on'))
    worker=Workflow(session,tmp_path,{'url':'https://chatgpt.com/','begin_tag':begin,'end_tag':end},
                    action_lookup(),framework.run_action)
    assert worker.run([step])['result']['source']['text']=='Exact 🌱'
    assert not pages
    session.hotkey.assert_not_called();session.paste_text.assert_not_called()


@pytest.mark.parametrize('command',['desktop_paste_text','desktop_paste_image','desktop_hotkey','desktop_click_match'])
def test_polling_rejects_input_before_starting(command):
    with pytest.raises(ValueError,match='read-only'):
        validate_steps([{'poll':{'timeout':1},'steps':[{'command':command}]}],action_lookup())


def test_poll_does_not_retry_focus_guard_or_clipboard_failure(tmp_path):
    for error in (DesktopStopped('Focus lost'),ValueError('Keyboard copy returned no fresh text.')):
        session=MagicMock();session.capture_page.side_effect=error
        worker=Workflow(session,tmp_path,{},action_lookup(),framework.run_action)
        with pytest.raises(ValueError):worker.run([{'poll':{'timeout':1},'steps':[{'command':'desktop_capture_page'}]}])
        session.capture_page.assert_called_once();session.wait.assert_not_called()


@pytest.mark.parametrize('change',['social','duplicate_submit','nested_submit','old_monolith','other_site'])
def test_translation_recipe_rejects_unsafe_or_obsolete_commands(change):
    recipe=read_yaml(RECIPE)
    if change=='social':recipe['kind']='social'
    if change=='other_site':recipe['url']='https://example.com/'
    if change=='duplicate_submit':recipe['steps'].append({'command':'desktop_hotkey','keys':['enter']})
    if change=='nested_submit':recipe['steps'].append({'for_each':[],'as':'x','steps':[{'command':'desktop_hotkey','keys':['enter']}]})
    if change=='old_monolith':recipe['steps']=[{'command':'translation_query'},{'command':'translation_capture'}]
    with pytest.raises(ValueError):validate_recipe(recipe)


def test_primitives_are_discovered_without_translation_actions(tmp_path):
    lookup=action_lookup()
    assert not {'translation_query','translation_capture','send','post'} & set(lookup)
    worker=Workflow(MagicMock(),tmp_path,{},lookup,framework.run_action)
    assert worker.run([{'command':'data_text_chunks','text':'abc🌱def','size':3,'save_as':'parts'}])['parts']==['abc','🌱de','f']


def test_data_capture_rejects_duplicate_tags_and_json_keys(tmp_path):
    ops=Operations(MagicMock(),tmp_path)
    with pytest.raises(ValueError):ops.text_between('OPEN OPEN {} END','OPEN','END')
    with pytest.raises(ValueError,match='Duplicate'):ops.json_parse('{"a":1,"a":2}')
    with pytest.raises(ValueError,match='outside'):ops.read_text(tmp_path.parent/'outside.txt')


def test_inline_html_preserves_json_tokens_and_image_crop(tmp_path):
    from bs4 import BeautifulSoup
    from allotment.test_sample import SAMPLE_IMAGE
    node=BeautifulSoup('<pre>{<span>"message"</span>:<span>"Unicode 🌱"</span>}</pre>','html.parser')
    assert json.loads(html_text(node))=={'message':'Unicode 🌱'}
    assert Operations(MagicMock(),tmp_path).image_crop(SAMPLE_IMAGE)==[.21875,0,.78125,1]


def test_progress_reports_wait_reason_deadline_and_loop_position(tmp_path):
    from datetime import datetime,timezone
    session=MagicMock();reports=[]
    worker=Workflow(session,tmp_path,{},action_lookup(),framework.run_action,lambda **kw:reports.append(kw))
    worker.run([{'for_each':['a','b'],'as':'chunk','label':'Prompt chunk','steps':[
        {'command':'desktop_wait','seconds':2,'label':'Settling','detail':'Allowing pasted text to settle'}]},
        {'command':'desktop_screenshot','name':'done'}])
    waits=[r for r in reports if r['wait_until']]
    assert [r['item_index'] for r in waits]==[1,2]
    assert all(r['item_total']==2 and r['total_steps']==2 and r['step_index']==1 for r in waits)
    assert all(r['wait_seconds']==2 and r['wait_reason']=='Allowing pasted text to settle' for r in waits)
    assert all(datetime.fromisoformat(r['wait_until'])>datetime.now(timezone.utc) for r in waits)
    assert reports[-1]['wait_until'] is None and reports[-1]['item_index'] is None


def test_poll_progress_explains_rechecks_and_clears_after_success(tmp_path):
    session=MagicMock();reports=[]
    session.read_url.side_effect=['https://example.com/','https://example.com/c/ready']
    worker=Workflow(session,tmp_path,{},action_lookup(),framework.run_action,lambda **kw:reports.append(kw))
    worker.run([{'label':'Waiting for response','poll':{'timeout':30,'interval':5,'reason':'Waiting for the new conversation'},'steps':[
        {'command':'desktop_read_url','save_as':'url'},
        {'command':'data_url_assert','url':{'ref':'url'},'origin':'https://example.com/','path_prefix':'/c/','pending_paths':['/']}]},
        {'command':'desktop_screenshot','name':'done'}])
    wait=next(r for r in reports if r['wait_kind']=='poll')
    assert wait['poll_attempt']==1 and wait['poll_timeout']==30 and wait['wait_seconds']==5
    assert wait['wait_reason']=='Waiting for the new conversation' and wait['poll_until']
    assert reports[-1]['poll_until'] is None
