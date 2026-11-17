from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from test_publishing import store

from allotment import whatsapp_channel_recovery as recovery
from allotment.preparation_visual import VisualActions
from allotment.roboclick.desktop import DesktopStopped


MISSING = "Could not find 'Search' on screen. See OCR screenshots; no click made."


def session():
    recipe = recovery.bind({'id': 'whatsapp_channel', 'channel_search_recovery': {
        'text': 'Discover more', 'region': [.04,.45,.33,.88], 'search_region': [.045,.13,.33,.23]}})
    return SimpleNamespace(recipe=recipe, account_checked=True, region_box=MagicMock(),
                           find_text=MagicMock(), click_match=MagicMock(), wait=MagicMock(),log=MagicMock())


def invoke(s):
    return recovery.search_group(s,text='Search',region=[.045,.14,.32,.18],result_region=[.07,.2,.32,.4])


def test_existing_search_uses_original_action_without_recovery(monkeypatch):
    original=MagicMock(return_value='original');monkeypatch.setattr(VisualActions,'search_group',original)
    s=session();assert invoke(s)=='original'
    s.click_match.assert_not_called();s.wait.assert_not_called()
    assert original.call_args.kwargs['region']==[.045,.14,.32,.18]


def test_missing_search_opens_discover_once_then_original_checked_search(monkeypatch):
    original=MagicMock();monkeypatch.setattr(VisualActions,'search_group',original)
    s=session();match=object();s.find_text.side_effect=[ValueError(MISSING),match]
    invoke(s)
    s.click_match.assert_called_once_with(match)
    assert s.find_text.call_args.kwargs['text']=='Discover more'
    assert original.call_args.kwargs['region']==[.045,.13,.33,.23]


@pytest.mark.parametrize('error',[ValueError('Ambiguous OCR target'),ValueError('Install local Tesseract'),DesktopStopped('Stopped')])
def test_guards_and_ambiguity_never_trigger_recovery(error,monkeypatch):
    original=MagicMock();monkeypatch.setattr(VisualActions,'search_group',original)
    s=session();s.find_text.side_effect=error
    with pytest.raises(type(error)):invoke(s)
    s.click_match.assert_not_called();original.assert_not_called()


def test_missing_discover_stops_without_typing(monkeypatch):
    original=MagicMock();monkeypatch.setattr(VisualActions,'search_group',original)
    s=session();s.find_text.side_effect=[ValueError(MISSING),ValueError('Discover more absent')]
    with pytest.raises(ValueError,match='absent'):invoke(s)
    s.click_match.assert_not_called();original.assert_not_called()


@pytest.mark.parametrize('change',[{'account_checked':False},{'recipe':{'id':'whatsapp','channel_search_recovery':{'text':'Discover more'}}}])
def test_wrong_identity_or_platform_cannot_recover(change):
    s=session();s.__dict__.update(change)
    with pytest.raises(ValueError):invoke(s)
    s.find_text.assert_not_called();s.click_match.assert_not_called()


def test_extension_change_invalidates_its_recipe(monkeypatch):
    s=session();monkeypatch.setattr(recovery,'stamp',lambda:'changed')
    with pytest.raises(ValueError,match='fresh preparation test'):invoke(s)
    s.find_text.assert_not_called()


def test_other_platforms_and_unextended_channels_use_original_unchanged(monkeypatch):
    original=MagicMock(return_value='original');monkeypatch.setattr(VisualActions,'search_group',original)
    for name in ['discord','whatsapp','telegram','whatsapp_channel']:
        recipe={'id':name};assert recovery.bind(recipe) is recipe
        s=SimpleNamespace(recipe=recipe)
        assert invoke(s)=='original'


def test_store_binds_only_opted_in_channel_and_signs_runtime(store):
    recipe=store.plugins()['whatsapp_channel']
    assert recipe['channel_search_runtime']==recovery.stamp()
    assert all('channel_search_runtime' not in v for k,v in store.plugins().items() if k!='whatsapp_channel')
