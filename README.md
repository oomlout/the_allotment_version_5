# The Allotment

Folder-based posts, independent user Git repositories, a Flask Studio and a separate publishing worker.

## Start

On Windows with Python 3.11+ and Git, run `setup.bat`, then `start_studio.bat`. The Studio uses `http://127.0.0.1:8879`. Settings persists the port; restart after a change. `python -m allotment serve --port 8879` also saves it.

The tools live in this repository. Public data lives in separate repositories:

```
garden/the_allotment/oomlout_main/
garden/the_allotment/allotment_main/
```

The namespace is `the_allotment`. Additional namespaces can coexist. Each user repository contains `profile.yaml`, `assets/`, `posts/<id>/post.yaml`, post media and `.allotment/queue/` records. Drafts and private configuration stay under ignored `.local/` in the tools checkout. Ten imported historical posts remain posted without queuing new social sends.

## Create and edit

Create a post with text, media and tags, then save it as a private draft. Uploading an image opens its editor inside the post form. Four independently draggable crops start centred (1:1, 4:5, 16:9 and 9:16), with rotate/flip tools, brightness/contrast/saturation and quick looks. Originals stay intact. Tags use `lower_case_with_underscores` and can be searched, grouped, renamed and merged. Attach a GitHub repository and additional links; saving fetches titles and descriptions for cards in Studio and static pages. Local publication and sowing controls live on the post page.

The editor uses locally pinned Cropper.js. See the [image editor guide](allotment/guides/IMAGE_EDITOR_GUIDE.md) for the data format, update process, tests and legacy enrichment tool. Automatically generated legacy crops are marked for review.

## Publish locally, then sow

Saving opens the post's unique page at `/g/<namespace>/<user>/p/<post-id>/`. **Publish locally** moves the draft into the selected garden repository; **Unpublish locally** returns it to the private draft area. Neither action queues or runs any destination.

**Sow** selects separate outputs: social draft preparation, a static website, an Allotment Markdown summary, or Git sync. Select destinations on the post page and click **Launch**. The first Chrome tab opens at the site, then an eight-second countdown begins. Later platforms each open a fresh tab and leave a checked draft for manual submission. Each Sowing destination card sets its default picture crop and contains its expandable test workflow; the collapsed per-post overrides card changes it for an individual post. Snapshots preserve the exact destination crop and content. Social submission is always manual.

After submitting on Bluesky, use **Capture post ID**, then **Harvest reactions**. These are read-only roboclick operations with separate successful-test and explicit-approval gates. Results live under the post's `harvest/` folder; screenshots and copied page evidence stay ignored under `.local/`. See the [harvest and platform handoff guide](allotment/guides/HARVEST_HANDOFF.md).

Studio asks for one existing garden folder and remembers it. No account switcher or exploration feed is included. Restart with `python -m allotment serve --choose-garden` to select another folder, or `--garden namespace/user` to bind explicitly. Configure the repository remote in Settings. `run_worker.bat --pull` fast-forwards a clean public repository before processing its queue and synchronizes receipts afterward.

## Roboclick worker: Bluesky first

Publishing now uses the actual modular `oomlout_roboclick` framework, pinned inside `allotment/roboclick/`. It controls a normal logged-in Chrome tab with mouse and keyboard input. Local Tesseract finds text coordinates in screenshots; OpenCV checks images. Page text and rendered HTML are captured through keyboard copy and Windows clipboard formats. No runtime AI service, browser debugging, extension or browser driver is required.

Install Chrome and Tesseract OCR with English language data. Tesseract is a separate application. Settings can specify its executable and an optional unique Chrome window-title filter. Keep Chrome visible on the primary screen and sign in manually. The worker opens a new tab in the existing window, snaps it to the top-left quarter using Win+Left then Win+Up and uses 100% zoom.

In Sowing, expand **Test Bluesky** and use **Prepare only**. It checks the account, fills the fixed text/image and leaves the composer open for your Post click. **Automatic sending is disabled in both tests and the worker**, including old send-test requests and publishing recipes. Tests start after an eight-second countdown with pause/resume and Stop. Leave mouse, keyboard and clipboard idle during input. F8, a mouse corner, focus loss and unexpected user input cancel the run. After reviewing a checked draft, explicitly **Approve preparation** to enable queued drafting on this computer. Approval never enables sending. Each run opens a fresh tab and leaves existing drafts untouched.

Bluesky supports checked text/image drafts. Additional platforms use an isolated preparation runner, with calibrated workflows and inspection-only entries shown separately in Studio. Every social action stops before submission. Only a current checked draft and the user's explicit approval enable queued preparation. Public profile links remain separate from exact group/channel destinations. Telegram channels and groups have separate plugins; Patreon regular items require a Quip composer and must not publish an unpublished creator page.

Platform workflows are YAML in `allotment/plugins/`. The [AI repair guide](allotment/guides/AI_PLATFORM_GUIDE.md) documents evidence, actions, checks, repair and testing. AI is used for development and repair, not as part of a running worker.

## Legacy command-line workers (not exposed in Studio)

Queue pages, deferred launch controls, bundle downloads, receipt imports and batch setup are removed from the Studio server. Existing command-line worker/bundle code and old receipts remain for compatibility. Studio launches are immediate and cannot be picked up by a general batch worker. Unstarted actions are cancelled after Stop or launch failure.

## Static website

Website builds one user's feed into `allotment_static/<namespace>/<user>/`. Download its ZIP and upload the contents to `/allotment` on a website. Relative links work under a subfolder. Choose timeline/cards, density, colours, width and custom CSS in Studio. A static build does not run an aggregator or combine user repositories.

## Verification

Install optional test tooling with `python -m pip install -e ".[test]"`. Run `python -m pytest -q`. The optional Playwright dependency checks Studio UI in an isolated test browser only; it is absent from runtime requirements and exported workers.

`python tests/desktop_practice.py` runs an explicit real-desktop walkthrough against a disposable local website and saves evidence under `.local/roboclick-walkthrough/`. It uses normal Chrome and the same YAML/actions as Bluesky, with two separate draft tabs, top-left snapping, text/image checks and Post/Enter traps that must record zero attempts. Keep the desktop idle while it runs. Local fixture success does not establish live-platform delivery.

## Preparation-only groundwork

Additional platform recipes use a separate runner with a restricted action list. It has no submit operation and blocks Enter outside address-bar navigation and a verified Chrome-owned file picker. Account and exact group/channel checks must pass before it enters the fixed text/image. Wrong account, duplicate group results, a contact in place of a group, changed chat fragments, missing controls and Stop all fail closed. A current checked draft enables the user's approval control; it never approves itself.

Inspect only records the configured page without entering content or proving account ownership. Prepare only is available for calibrated recipes (`preparation_ready: true`); it verifies identity, destination, exact text and image preview. Other recipes remain inspection-only with a concrete setup note. Both use normal Chrome tabs, local roboclick/OCR, countdown, pause and Stop. No AI service or debugging connection is required.

WhatsApp permits only one active web tab. Review its existing draft before starting another WhatsApp preparation; an older tab may show **Use here** instead of its composer. Do not use that control to take over while another WhatsApp draft awaits review. Other platforms may also restore drafts across tabs; a missing empty-editor placeholder stops preparation rather than replacing content.

Run `python tests/desktop_preparation_practice.py --prepare` for the original four local mock workflows, and `python tests/desktop_visual_practice.py` for screenshot, clipboard and native image-picker checks. Page Enter and Send buttons are traps; require zero attempts. These fixtures do not prove live support. Use `python tools/check_draft.py PLATFORM` for an authorized fixed-sample live draft test, then review it in Chrome. See the [preparation guide](allotment/guides/PREPARATION_ONLY_GUIDE.md). Regenerate old portable bundles to get the current manual-submission worker.
