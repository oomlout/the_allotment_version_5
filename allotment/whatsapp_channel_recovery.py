"""Opt-in channel navigation recovery, signed only by its consuming recipe.

The unchanged preparation interpreter still owns every draft/identity check.
Store.plugins installs this adapter for the existing search primitive. Sessions
without the opt-in use the original method unchanged, including WhatsApp groups.
"""
import hashlib
from pathlib import Path

from .preparation_visual import VisualActions


def stamp():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def search_group(self, *args, **kwargs):
    recovery = getattr(self, 'recipe', {}).get('channel_search_recovery')
    if not recovery:
        return VisualActions.search_group(self, *args, **kwargs)
    if self.recipe.get('id') != 'whatsapp_channel' or recovery.get('text') != 'Discover more':
        raise ValueError('Channel recovery permits only WhatsApp Discover more.')
    if self.recipe.get('channel_search_runtime') != stamp():
        raise ValueError('Channel recovery changed. Run a fresh preparation test.')
    if not self.account_checked:
        raise ValueError('Check the signed-in account before channel recovery.')
    if args or kwargs.get('text') != 'Search':
        raise ValueError('Channel recovery requires the configured Search field.')
    self.region_box(recovery['region'])
    self.region_box(recovery['search_region'])
    try:
        self.find_text(text='Search', region=kwargs['region'], exact=True, timeout=5)
    except ValueError as error:
        # Missing text alone permits recovery. Ambiguity, stale pixels, focus,
        # input guards, Stop and OCR/tool errors must never trigger a click.
        if str(error) != "Could not find 'Search' on screen. See OCR screenshots; no click made.":
            raise
        self.log('channel-search-recovery', {'control': 'Discover more'})
        match = self.find_text(text='Discover more', region=recovery['region'], exact=True)
        self.click_match(match)
        self.wait(1)
        kwargs = {**kwargs, 'region': recovery['search_region']}
    return VisualActions.search_group(self, **kwargs)


def bind(recipe):
    """Include extension bytes in the normal recipe signature; no approval migration."""
    if recipe.get('id') != 'whatsapp_channel' or not recipe.get('channel_search_recovery'):
        return recipe
    from .preparation import PreparationSession
    PreparationSession.search_group = search_group
    return {**recipe, 'channel_search_runtime': stamp()}
