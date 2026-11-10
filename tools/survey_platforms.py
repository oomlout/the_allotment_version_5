"""Read-only layout evidence through normal Chrome; never enters a composer.

Run from the checkout with one or more platform IDs. No debug connection or AI.
Evidence is private under .local/platform-survey/native/.
"""
from pathlib import Path
import sys
import time
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from allotment.roboclick.desktop import Desktop, DesktopStopped
from allotment.storage import Store, read_yaml, write_yaml, lock


def survey(platforms):
    store = Store(Path(__file__).resolve().parents[1])
    accounts = store.settings('the_allotment', 'oomlout_main').get('destinations', {})
    recipes = store.plugins()
    defaults = {'twitter': 'https://x.com/oomlout',
                'discord': 'https://discord.com/channels/1174680718467674142/1174680787380092938',
                'whatsapp': 'https://web.whatsapp.com/',
                'whatsapp_channel': 'https://web.whatsapp.com/',
                'telegram': 'https://web.telegram.org/k/#@oomloutT',
                'patreon': 'https://www.patreon.com/c/oomlout'}
    for platform in platforms:
        folder = store.local / 'platform-survey' / 'native' / platform / str(int(time.time()))
        account = accounts.get(platform, {})
        url = defaults.get(platform) or account.get('profile_url') or recipes[platform]['base_url']
        result = {'platform': platform, 'requested_url': url, 'sent': False}
        with lock(store.local / 'browser.lock'):
            session = Desktop(folder, read_yaml(store.local / 'worker.yaml'))
            try:
                session.attach()
                try:
                    session.navigate(url, seconds=3)
                except DesktopStopped:
                    raise
                except ValueError as error:
                    actual = session.read_url()
                    if urlparse(actual).netloc != urlparse(url).netloc:
                        raise
                    session.url = actual
                    result['redirect'] = str(error)
                result['url'] = session.url
                result['screenshot'] = session.screenshot(name='page')['path']
                try:
                    result['html'] = session.capture_page(name='page')
                except ValueError as error:
                    result['capture_error'] = str(error)
            except Exception as error:
                result['error'] = str(error)
                if type(error).__name__ == 'DesktopStopped':
                    write_yaml(folder / 'survey.yaml', result)
                    raise
            finally:
                session.release()
        write_yaml(folder / 'survey.yaml', result)
        print(platform, result, flush=True)


if __name__ == '__main__':
    survey(sys.argv[1:])
