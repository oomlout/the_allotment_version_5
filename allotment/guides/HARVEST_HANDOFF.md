# Harvesting: implementation and platform handoff

## Current Studio launch flow

Studio uses garden cards and one bound garden. Sowing cards contain the default image crop and an expandable fixed-sample test. There is no Queue or Tests navigation item and no batch/bundle server endpoints. On the post page, select destinations and click Launch. The initial fresh Chrome tab opens at the site before the eight-second countdown; subsequent destinations each use a fresh tab without another countdown. Final submission stays manual. Pause/Resume and Stop remain available. During the initial countdown only, input is allowed for handoff; the same Chrome process/window and focus are checked when input guards are re-armed. The countdown helper is included in approval signatures, so this runtime change requires a fresh test and explicit user approval. Existing queue/bundle details below describe legacy command-line compatibility, not Studio controls.


## Studio model

A post is local and is either unpublished (an ignored `.local/drafts/` folder) or published in the selected garden repository. `lifecycle.py` changes that state without running plugins or queuing work. Sowing is a separate operation on a published post: browser draft preparation, static website generation, Allotment Markdown summaries, or Git sync. Each sow has an immutable snapshot with per-destination framing. Final social submission remains manual.

Studio selects one existing `garden/<namespace>/<user>/` folder and remembers it in ignored `.local/studio.yaml`. Other gardens are inaccessible through that Studio instance. Restart with `python -m allotment serve --choose-garden` to choose another, or `--garden namespace/user` to bind explicitly. There is no account switcher, account creation, or exploration feed. The Posts screen is a local register.

## Current Bluesky workflow

The post page has separate collapsible **Sow** and **Inspect** cards, with Inspect immediately beneath Sow. Inspect contains ID capture, reaction inspection, user approval controls and the saved findings. Its active countdown, handoff and Stop controls appear inside Inspect rather than the collapsed Sow card. Completed findings show the permalink, platform ID, capture time, available counts and reply authors/text. Missing counts display Unavailable, never zero. Successful tests remain unapproved until the user reviews them.

1. The user prepares a draft through an approved sowing recipe and manually submits it on Bluesky.
2. On the local post page, **Capture post ID** opens a fresh ordinary Chrome tab, verifies the configured profile owner, and captures the recent profile posts using keyboard copy. Three profile screens are inspected by default.
3. Require exactly one outer post card with the exact normalized post text, the expected author, and a valid Bluesky post permalink. Restore shortened links from captured anchor destinations for text matching. Nested quotes, other authors, ambiguous duplicates and empty text fail closed. Never infer the ID from a profile URL.
4. Open the candidate post. Verify its own outer card, URL and exact text again. For image posts, use the existing local image/screenshot matcher against the queued crop. Capture ID and permalink. A prepared receipt becomes published only after this public-page verification; no submission is performed.
5. **Inspect reactions** uses the captured ID. Capture the root post counts (likes, reposts, quotes, replies, saves, views, interactions when exposed), visible text, reply authors, reply IDs/URLs, text, links, timestamps and media references.
6. Expand the unique **Show more replies** control only after captured-HTML identification and fresh screenshot/OCR matching. Follow captured **Read more replies** links through address-bar navigation, never a composer. Scroll and collect each loaded branch, deduplicating by permalink. Defaults are 30 branch pages, 20 expansion clicks and 12 scroll captures per page. Record limits rather than claiming completeness. Missing metrics are null; abbreviations retain their displayed value and an approximation flag. Do not calculate a fictional interactions total or infer views from likes.
7. The initial eight-second countdown supports Pause, Resume and Stop. During input, F8, focus/input guards and the desktop mutex apply. Each operation opens a fresh tab, snaps Win+Left then Win+Up, stays at 100% zoom, and leaves existing drafts untouched.

Bluesky is the only registered harvester. Live selectors still require a read-only test and explicit user approval, independently for IDs and reactions. A test captures existing public content; it never makes a test post. Successful fixture runs do not approve an actual account. Stop or changes to the recipe, engine, harvest implementation, account, computer, checkout or worker settings invalidate approval.

## Storage

- `<post-folder>/harvest/bluesky/id.yaml`: verified platform ID, author, URL, timestamp, source revision and run ID.
- `<post-folder>/harvest/bluesky/reactions/<run-id>.yaml`: dated immutable structured reaction snapshot.
- `<post-folder>/harvest/bluesky/latest.yaml`: latest structured reaction result.
- `.local/job-runs/<namespace>/<user>/<run-id>/`: worker metadata, progress, result, control flags, screenshots and copied HTML/text. Browser captures and test evidence stay ignored.
- `.local/harvest-approvals/<namespace>/<user>/bluesky.yaml`: explicit, mode-specific approvals.

Harvesting preserves original post text/media. Unpublishing carries findings into the private draft folder; republishing restores them. Historic queued jobs remain supported for compatibility. A queued sow never implicitly publishes a draft.

## Files and extension boundaries

- `allotment/plugins/bluesky_harvest.yaml`: separate read-only selectors, labels, counts and crawl limits. Do not edit the posting YAML to add harvesting; that would invalidate preparation approvals.
- `allotment/harvest.py`: identity checks, offline parsing, restricted desktop inspection, gate and persistence.
- `allotment/job_runs.py`: process launch, countdown, pause/resume/Stop and single-use execution.
- `allotment/lifecycle.py`, `sowing.py`, `framing.py`: local state, destination snapshots, framing defaults.
- `tests/test_harvest.py`, `tests/test_sowing.py`: offline safety, state, framing and approval checks.
- `tests/desktop_harvest_practice.py`: opt-in real Chrome loopback fixture. Post, Send and page Enter are traps; expected count is zero.

## Handoff checklist for another platform

Read `AI_PLATFORM_GUIDE.md` and `PREPARATION_ONLY_GUIDE.md` first. Preserve all submission and approval boundaries.

1. Obtain the exact user-authorized account/destination and an already published sample. For private groups, explicitly decide which findings may enter the user data repository; raw account/session evidence stays local. Do not create or send sample content.
2. Add a separate `kind: harvest` YAML. Register the platform only after building a strict URL/ID codec and profile/thread card identity adapter. The current outer-card prefixes and `/profile/<author>/post/<id>` codec are Bluesky-specific; do not reuse them blindly.
3. Inspect normal Chrome with screenshots, OCR and keyboard-copied page HTML. Record account ownership checks, recent-post selectors, direct permalink patterns, root/reply text selectors and visible metric labels. No CDP, Playwright, browser debug APIs, API shortcuts or AI service may be needed by the runtime.
4. Match exact content and author; bind the ID to an actual captured post link. Handle duplicate text, edited content, media-only posts, pinned posts, reposts and nested quotes explicitly. Ambiguous matching stops for review. Do not select the newest card merely because it is first.
5. Allowlist each non-mutating expansion action. Prove in captured HTML that it expands replies, then require a fresh unique OCR match before clicking. Prefer validated captured links and keyboard navigation for branch traversal. Never add arbitrary click coordinates, arbitrary hotkeys, script execution, or a permissive “click anything” operation. Like, reaction, repost, reply, follow, send and submit controls are prohibited.
6. Bound scrolling and branching, preserve parent/branch provenance, deduplicate by platform ID, and record unavailable/partial data. Inspect explicit zero separately from missing information; keep abbreviated counts approximate. Preserve timestamps and reply URLs, and do not invent engagement totals.
7. Add offline cases for the wrong author, nested quotes, ambiguous matches, missing fields, unsupported media, fake URLs, multiple identical labels, inaccessible replies, stale captures, cancellation and approval invalidation. Use loopback native fixtures with Post/Send/Enter traps and more than one reply branch; expect zero attempts.
8. Only then run a user-authorized read-only live test. Present the captured identity and findings for review. The user explicitly approves each mode; never approve for them, copy an old signature, or treat a preparation approval as harvest approval. Report fixture verification and live verification separately.
9. Document selectors and limits with evidence paths. Fix layout changes in YAML when possible. If an interpreter change is necessary, keep it generic and reject unknown actions before any desktop input.

## Source references and verification limits

The initial selector mapping was checked against the official Bluesky web client source on 2026-11-07:

- https://github.com/bluesky-social/social-app/blob/main/src/view/com/posts/PostFeedItem.tsx
- https://github.com/bluesky-social/social-app/blob/main/src/screens/PostThread/components/ThreadItemAnchor.tsx
- https://github.com/bluesky-social/social-app/blob/main/src/screens/PostThread/components/ThreadItemShowOtherReplies.tsx
- https://github.com/bluesky-social/social-app/blob/main/src/screens/PostThread/components/ThreadItemReadMore.tsx
- https://github.com/bluesky-social/social-app/blob/main/src/components/PostControls/index.tsx
- https://github.com/bluesky-social/social-app/blob/main/src/components/RichText.tsx

Source review does not establish live compatibility. The native fixture validates the desktop path and no-submission boundary, not Bluesky's current layout or availability of every statistic. Views/interactions may not be exposed. All reaction captures are explicitly partial; inaccessible or hidden content is not bypassed. Media-only ID matching is deliberately blocked until a dedicated identity matcher is implemented.

## Read-only verification, 8 November 2026

The native fixture passed profile discovery, exact ID capture, visible metrics, one expansion and two replies across two pages, with zero Post/Send/Enter attempts. Evidence: `.local/harvest-work/native-d1aaba2f/result.yaml`.

The user-authorized existing post `20261105-193825-997b0107` was then checked in normal Chrome. ID test `run-139f8cdbea8f42c7b482599a7766780e` matched exact text, account and the queued image crop to Bluesky ID `3mzr5g7ajfs2m`. Reaction test `run-9d2d7b33d54b4d5d98194131ddf874e2` captured the same post, explicit zero likes/replies, no visible replies, and no crawl-limit warnings. Reposts, quotes, saves, views and interactions had no exposed numeric values and remain unavailable. No reaction or submission controls were clicked; both results record `sent: false`. The post had no replies, so live reply expansion remains unverified; expansion and nested branches were verified on the native fixture only. Both modes remain pending explicit user approval. No preparation recipes or shared preparation runtime were changed.

Evidence for each live run is under `.local/job-runs/the_allotment/oomlout_main/<run-id>/`. Findings are stored in the post's `harvest/bluesky/` directory. Use these captured pages for offline selector diagnosis before another live run; do not weaken identity matching or copy approval signatures.
