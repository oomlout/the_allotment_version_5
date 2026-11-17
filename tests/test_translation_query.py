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
    session.capture_accessibility.return_value={'capture_id':'fixture','elements':[
        {'control_type':'Button','name':'Remove cropped-preview.png','enabled':True},
        {'control_type':'Button','name':'Send','enabled':True}]}
    run={'id':'run-query','images':[str(SAMPLE_IMAGE)]}
    update=MagicMock()
    recipe=read_yaml(RECIPE)
    steps=recipe['steps'][:next(i for i,s in enumerate(recipe['steps']) if s.get('poll',{}).get('stable_on'))]
    workflow=make_workflow(session,recipe,run,tmp_path,update)
    workflow.operations.capture_accessibility=session.capture_accessibility
    return workflow,steps,session,prompt,update


def test_visual_query_does_not_copy_empty_page_and_submits_once(tmp_path):
    pipeline,steps,session,prompt,update=query_fixture(tmp_path)
    session.find_text.side_effect=[ValueError("Could not find 'Ask anything' on screen."),{'x':100,'y':200}]
    pipeline.run(steps)
    session.capture_page.assert_not_called()
    assert ''.join(c.kwargs['text'] for c in session.paste_text.call_args_list)==prompt
    session.paste_image.assert_called_once_with(image=str(SAMPLE_IMAGE))
    session.find_image.assert_not_called()
    assert session.capture_accessibility.call_count==2
    assert session.capture_text.call_args_list==[
        call(name='ai-before-image'),call(name='ai-prompt-check'),call(name='ai-focus-check')]
    names=[c[0] for c in session.method_calls]
    assert names.index('paste_text')<names.index('capture_text')<names.index('paste_image')
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
    if failure=='image':session.capture_accessibility.return_value={'capture_id':'failed','elements':[
        {'control_type':'Text','name':'Upload failed'}]}
    if failure=='first_copy':session.capture_text.side_effect=['Wrong contents',prompt]
    if failure=='second_copy':session.capture_text.side_effect=[prompt,'Wrong contents']
    if failure=='url':session.check_url.side_effect=['https://chatgpt.com/',ValueError('Wrong page')]
    with pytest.raises(ValueError):pipeline.run(steps)
    assert call(keys=['enter']) not in session.hotkey.call_args_list
    if failure in {'focus','ambiguous'}:
        session.find_text.assert_called_once()
        session.paste_text.assert_not_called()
        session.paste_image.assert_not_called()


@pytest.mark.parametrize('removed',['data_assert_equal','data_elements_check','desktop_capture_text'])
def test_recipe_edits_cannot_remove_submission_checks(tmp_path,removed):
    pipeline,steps,session,prompt,_=query_fixture(tmp_path)
    from copy import deepcopy
    steps=deepcopy(steps)
    if removed=='data_elements_check':
        loop=next(s for s in steps if s.get('as')=='image')
        loop['steps']=[s for s in loop['steps'] if 'poll' not in s]
    else:
        index=next(i for i,s in enumerate(steps) if s.get('command')==removed)
        steps.pop(index)
    with pytest.raises((ValueError,KeyError)):pipeline.run(steps)
    assert call(keys=['enter']) not in session.hotkey.call_args_list


def test_upload_waits_for_busy_and_disabled_send_without_pasting_again(tmp_path):
    pipeline,steps,session,_,_=query_fixture(tmp_path)
    ready=session.capture_accessibility.return_value
    busy={'capture_id':'busy','elements':[*ready['elements'],{'control_type':'ProgressBar','name':'Uploading'}]}
    disabled={'capture_id':'disabled','elements':[ready['elements'][0],{'control_type':'Button','name':'Send','enabled':False}]}
    session.capture_accessibility.side_effect=[busy,disabled,ready,ready]
    pipeline.run(steps)
    assert session.capture_accessibility.call_count==4
    session.paste_image.assert_called_once()
    session.find_image.assert_not_called()
    assert session.hotkey.call_args_list.count(call(keys=['enter']))==1


def test_missing_upload_times_out_without_submission(tmp_path):
    pipeline,steps,session,_,_=query_fixture(tmp_path)
    session.capture_accessibility.return_value={'capture_id':'empty','elements':[]}
    loop=next(s for s in steps if s.get('as')=='image')
    next(s for s in loop['steps'] if 'poll' in s)['poll']['timeout']=.01
    with pytest.raises(ValueError,match='timed out'):pipeline.run(steps)
    session.paste_image.assert_called_once()
    assert call(keys=['enter']) not in session.hotkey.call_args_list


def test_each_upload_requires_the_new_total_count(tmp_path):
    pipeline,steps,session,_,_=query_fixture(tmp_path)
    policy=pipeline.before.__self__
    policy.images.append(str(SAMPLE_IMAGE))
    pipeline.values['images']=policy.images[:]
    def snapshot(**kwargs):
        count=session.paste_image.call_count
        return {'capture_id':f'upload-{count}','elements':[
            *[{'control_type':'Button','name':f'Remove image-{n}.png','enabled':True} for n in range(count)],
            {'control_type':'Button','name':'Send','enabled':True}]}
    session.capture_accessibility.side_effect=snapshot
    pipeline.run(steps)
    assert session.paste_image.call_count==2 and session.capture_accessibility.call_count==4
    assert policy.uploads_ready==2


def test_image_is_not_pasted_into_an_unverified_composer(tmp_path):
    pipeline,steps,session,_,_=query_fixture(tmp_path)
    session.capture_text.return_value='The whole page, not the editor'
    with pytest.raises(ValueError,match='Captured text'):
        pipeline.run(steps)
    session.paste_image.assert_not_called()
    assert call(keys=['enter']) not in session.hotkey.call_args_list
