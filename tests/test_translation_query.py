"""Query guards: screenshot startup, exact prompt checks, one AI-only submission."""
from unittest.mock import MagicMock, call

import pytest

from allotment.roboclick.desktop import DesktopStopped
from allotment.storage import read_yaml
from allotment.test_sample import SAMPLE_IMAGE
from allotment.translation_browser import make_workflow, RECIPE


def query_fixture(tmp_path):
    prompt='A complete translation prompt 🌱\n' * 100
    (tmp_path/'prompt.txt').write_text(prompt,encoding='utf-8')
    session=MagicMock()
    session.find_text.return_value={'x':100,'y':200}
    session.check_url.return_value='https://chatgpt.com/'
    session.capture_text.return_value=prompt
    session.read_url.return_value='https://chatgpt.com/c/new-conversation'
    run={'id':'run-query','images':[str(SAMPLE_IMAGE)]}
    update=MagicMock()
    recipe=read_yaml(RECIPE)
    steps=recipe['steps'][:next(i for i,s in enumerate(recipe['steps']) if s.get('poll',{}).get('stable_on'))]
    workflow=make_workflow(session,recipe,run,tmp_path,update)
    return workflow,steps,session,prompt,update


def test_visual_query_does_not_copy_empty_page_and_submits_once(tmp_path):
    pipeline,steps,session,prompt,update=query_fixture(tmp_path)
    session.find_text.side_effect=[ValueError("Could not find 'Ask anything' on screen."),{'x':100,'y':200}]
    pipeline.run(steps)
    session.capture_page.assert_not_called()
    assert ''.join(c.kwargs['text'] for c in session.paste_text.call_args_list)==prompt
    session.paste_image.assert_called_once_with(image=str(SAMPLE_IMAGE))
    session.find_image.assert_called_once()
    assert session.capture_text.call_args_list==[
        call(name='ai-prompt-check'),call(name='ai-focus-check')]
    assert session.check_url.call_args_list==[call(url='https://chatgpt.com/')]*2
    assert session.hotkey.call_args_list.count(call(keys=['enter']))==1
    names=[c.kwargs['operation'] for c in update.call_args_list]
    assert 'Verifying the complete prompt' in names
    assert 'Confirming ChatGPT received the prompt' in names
    assert any(c.args[0]=='translation-conversation-opened' for c in session.log.call_args_list)


@pytest.mark.parametrize('failure',['focus','ambiguous','image','first_copy','second_copy','url'])
def test_query_never_submits_after_failed_check(tmp_path,failure):
    pipeline,steps,session,prompt,_=query_fixture(tmp_path)
    if failure=='focus':session.find_text.side_effect=DesktopStopped('Chrome lost focus')
    if failure=='ambiguous':session.find_text.side_effect=ValueError('More than one match')
    if failure=='image':session.find_image.side_effect=ValueError('Reference image not found')
    if failure=='first_copy':session.capture_text.side_effect=['Wrong contents',prompt]
    if failure=='second_copy':session.capture_text.side_effect=[prompt,'Wrong contents']
    if failure=='url':session.check_url.side_effect=['https://chatgpt.com/',ValueError('Wrong page')]
    with pytest.raises(ValueError):pipeline.run(steps)
    assert call(keys=['enter']) not in session.hotkey.call_args_list
    if failure in {'focus','ambiguous'}:
        session.find_text.assert_called_once()
        session.paste_text.assert_not_called()
        session.paste_image.assert_not_called()


@pytest.mark.parametrize('removed',['data_assert_equal','desktop_find_image','desktop_capture_text'])
def test_recipe_edits_cannot_remove_submission_checks(tmp_path,removed):
    pipeline,steps,session,prompt,_=query_fixture(tmp_path)
    from copy import deepcopy
    steps=deepcopy(steps)
    if removed=='desktop_find_image':
        loop=next(s for s in steps if s.get('as')=='image')
        loop['steps']=[s for s in loop['steps'] if s.get('command')!=removed]
    else:
        index=next(i for i,s in enumerate(steps) if s.get('command')==removed)
        steps.pop(index)
    with pytest.raises((ValueError,KeyError)):pipeline.run(steps)
    assert call(keys=['enter']) not in session.hotkey.call_args_list
