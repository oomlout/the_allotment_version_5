from pathlib import Path

import pytest

from allotment.app import create_app
from allotment.browser import check_payload
from allotment.presets import apply_preset
from allotment.storage import Store, read_yaml, write_yaml
from allotment.website import build_site
from allotment.test_sample import SAMPLE_IMAGE, SAMPLE_TEXT
import shutil


NS, USER = "the_allotment", "oomlout_main"
EXPECTED = {
    "bluesky": "https://bsky.app/profile/oomlout.bsky.social",
    "tumblr": "https://www.tumblr.com/oomlout",
    "mastodon": "https://mastodon.social/@oomlout",
    "instagram": "https://www.instagram.com/oomloutoomlout/",
    "threads": "https://www.threads.net/@oomloutoomlout",
    "twitter": "https://twitter.com/oomlout",
    "medium": "https://medium.com/@aaron.oomlout",
    "telegram_channel": "http://t.me/oomloutT",
    "facebook": "https://www.facebook.com/oomlout",
    "facebook_business": "https://www.facebook.com/profile.php?id=61553257906517",
    "makerworld": "https://makerworld.com/en/@oomlout",
    "printables": "https://www.printables.com/@oomlout_642239/activity",
    "pixelfed": "https://pixelfed.social/i/web/profile/784759347584444407",
    "flickr": "https://www.flickr.com/photos/93152031@N07/",
    "whatsapp_channel": "https://whatsapp.com/channel/0029VbDuTPhLI8Yfa5aWQf2y",
}


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path); store.bootstrap()
    return store


def test_preset_preserves_every_exact_address_and_never_publishes_or_approves(store):
    platforms = apply_preset(store)
    assert set(platforms) == set(EXPECTED) | {"patreon"}
    accounts = store.settings(NS, USER)["destinations"]
    assert {key: accounts[key]["profile_url"] for key in EXPECTED} == EXPECTED
    assert store.profile(NS, USER)["social"] == EXPECTED
    assert accounts["patreon"]["keep_page_unpublished"] and not accounts["patreon"]["publish_profile_link"]
    assert accounts["telegram_channel"]["target_url"] == "https://web.telegram.org/k/#@oomloutT"
    assert all(not account["enabled"] and not account["reviewed"] for account in accounts.values())
    assert not store.jobs(NS, USER) and not store.posts(NS, USER) and not store.drafts(NS, USER)
    assert not store.profile(NS, "allotment_main")["social"]
    assert accounts["facebook_business"]["account_id"] == "61553257906517"
    assert accounts["whatsapp_channel"]["account_id"] == "0029VbDuTPhLI8Yfa5aWQf2y"
    assert accounts["flickr"]["expected_account"] == "ooomlout"
    assert accounts["flickr"]["account_id"] == "93152031@N07"


def test_preset_reapplication_keeps_manual_identity_selectors_and_unrelated_destinations(store):
    write_yaml(store.settings_path(NS, USER), {"destinations": {
        "bluesky": {"expected_account": "My observed name", "identity_locator": "#my-account", "enabled": False},
        "discord": {"expected_account": "Keep me", "target_name": "Private group", "target_url": "https://discord.com/channels/1/2"}}})
    apply_preset(store); apply_preset(store)
    accounts = store.settings(NS, USER)["destinations"]
    assert accounts["bluesky"]["expected_account"] == "My observed name"
    assert accounts["bluesky"]["identity_locator"] == "#my-account"
    assert accounts["discord"]["target_name"] == "Private group"
    assert len(store.profile(NS, USER)["social"]) == 15
    assert list((store.local / "backups").glob("*.yaml"))


def test_full_addresses_render_and_public_site_uses_distinct_names(store):
    apply_preset(store)
    client = create_app(store.root, testing=True,garden=('the_allotment','oomlout_main')).test_client()
    html = client.get(f"/g/{NS}/{USER}/destinations/").text
    for platform, address in EXPECTED.items():
        assert f'id="destination-{platform}"' in html
        assert address in html
    assert 'id="destination-whatsapp"' in html
    assert 'id="destination-discord"' in html
    output = build_site(store, NS, USER)
    public = (output / "index.html").read_text(encoding="utf-8")
    for address in EXPECTED.values():
        assert address in public
    assert "Facebook profile ↗" in public and "Facebook page ↗" in public and "WhatsApp channel ↗" in public


def test_public_channel_address_requires_opt_in_and_never_uses_private_browser_target(store):
    client = create_app(store.root, testing=True,garden=('the_allotment','oomlout_main')).test_client()
    client.get(f"/g/{NS}/{USER}/destinations/")
    with client.session_transaction() as session:
        csrf = session["csrf"]
    data = {"csrf": csrf, "telegram_profile_url": EXPECTED["telegram_channel"],
            "telegram_target_url": "https://web.telegram.org/k/#private-chat",
            "telegram_target_name": "Private chat", "telegram_account": "my-account"}
    assert client.post(f"/g/{NS}/{USER}/destinations/", data=data).status_code == 302
    assert "telegram" not in store.profile(NS, USER)["social"]
    data["telegram_publish_profile_link"] = "on"
    client.post(f"/g/{NS}/{USER}/destinations/", data=data)
    assert store.profile(NS, USER)["social"]["telegram"] == EXPECTED["telegram_channel"]
    assert "private-chat" not in (store.repo(NS, USER) / "profile.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("platform", ["instagram", "pixelfed", "flickr"])
def test_photo_destinations_reject_text_only_before_any_browser_action(store, platform):
    recipe = store.plugins()[platform]
    with pytest.raises(ValueError, match="not been migrated"):
        check_payload(recipe, {"expected_account": "test-account"}, {"text": "Hello", "attachments": []}, store.root, testing=True)


def test_fixed_sample_is_valid_for_migrated_recipes_and_other_platforms_are_blocked(store):
    folder = store.local / "sample-validation"
    (folder / "media").mkdir(parents=True)
    shutil.copyfile(SAMPLE_IMAGE, folder / "media" / "test-image.png")
    post = {"path": ".", "text": SAMPLE_TEXT + "\n\n[Allotment test abcdef12]",
            "attachments": [{"file": "media/test-image.png", "type": "image"}]}
    for recipe in store.plugins().values():
        account = {"expected_account": "test-account", "target_name": "Test", "target_url": recipe["base_url"]}
        if recipe.get("engine") == "oomlout_roboclick":
            check_payload(recipe, account, post, folder, testing=True)
        else:
            with pytest.raises(ValueError,match="not been migrated"):
                check_payload(recipe, account, post, folder, testing=True)
