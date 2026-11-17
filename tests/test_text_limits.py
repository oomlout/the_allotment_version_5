from copy import deepcopy
import json
import pytest
from allotment.translations import catalog,validate,preparation_text,platform_fields
from allotment.platform_text import import_pack,view,save
from allotment.lifecycle import publish_local
from allotment.sowing import enqueue
from test_translations import prepared
from test_platform_text import edit_form
from test_sow_review import store,NS,USER


def test_all_translation_destinations_fit_and_revalidate(store,monkeypatch):
    choices=catalog(store)
    post,run,pack=prepared(store,monkeypatch,tuple(choices))
    source='Original 🌱 '*1200
    run['source']['text']=pack['source']['text']=source
    for key,value in pack['posts'].items():
        value['message']=source if key=='twitter' else key+' '+('body 🌱 '*2200)
    result=validate(pack,run)
    for key,value in result['posts'].items():
        assert len(preparation_text(key,value))==choices[key]['max_chars']
    assert result['source']['text']==source
    assert validate(deepcopy(result),run)==result
    import_pack(store,NS,USER,post['id'],json.dumps({**result,'source':{'text':post['text']}}))
    entries=view(store,NS,USER,post['id'])['entries']
    save(store,NS,USER,post['id'],edit_form(post,entries))
    assert view(store,NS,USER,post['id'])['entries']==entries


@pytest.mark.parametrize('title',['A title','T'*12000])
def test_medium_combined_budget_is_idempotent(store,monkeypatch,title):
    _,run,pack=prepared(store,monkeypatch,('medium',))
    pack['posts']['medium'].update(title=title,message='This post was drafted with AI assistance.\n\n'+'Body '*2500)
    result=validate(pack,run)
    value=result['posts']['medium']
    assert len(preparation_text('medium',value))==10000
    assert value['title'] and value['message']
    assert validate(deepcopy(result),run)==result


def test_truncation_collisions_do_not_fail_a_run(store,monkeypatch):
    _,run,pack=prepared(store,monkeypatch,('bluesky','threads'))
    for key,value in pack['posts'].items():
        run['catalog'][key]['max_chars']=5
        value['message']='Hello '+key
    result=validate(pack,run)
    assert {v['message'] for v in result['posts'].values()}=={'Hello'}
    assert validate(deepcopy(result),run)==result


def test_sow_bounds_every_payload_before_approval_and_keeps_source(store,monkeypatch):
    choices=catalog(store);source=('Source 🌱 '*1500).rstrip()
    post=store.save_draft(NS,USER,source);publish_local(store,NS,USER,post['id'])
    checked={}
    def gate(*args):checked[args[3]['id']]=args[-1]['text']
    monkeypatch.setattr('allotment.preparation_approval.require_approval',gate)
    job=enqueue(store,NS,USER,post['id'],list(choices))
    for key,recipe in choices.items():
        assert checked[key]==job['destination_posts'][key]['text']==source[:recipe['max_chars']]
    assert job['post']['text']==job['destination_posts']['static']['text']==source
    assert store.published(NS,USER,post['id'])['text']==source


def test_short_and_exact_text_are_unchanged():
    for n in (299,300):
        assert platform_fields('bluesky',None,'🌱'*n,300)=={'title':None,'message':'🌱'*n}
