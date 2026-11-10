# Post creation, images and tags

## Workflow

Create a post with text, attachments and tags. **Save draft** stores it privately;
Uploading an image opens the crop dashboard inside the same post form. **Edit
image** reopens it there. Uploading first saves the private draft without leaving
the page. Saving the post also saves pending crops in its open editor. Creation never creates a publishing
job. **Save & view post** opens the post's unique page, with destinations and image
version choices underneath. Existing platform checks and explicit approval still apply.

The image dashboard saves four independent ratios: square (1:1), portrait (4:5),
landscape (16:9) and story (9:16). Drag the image or crop, rotate, flip, zoom, or
enter exact crop coordinates. Brightness, contrast and saturation have manual
sliders and Original, Pop, Soft and Mono presets. Adjustments apply to all ratios
by default; uncheck that option for separate looks. Save one crop or all four.
Reopening a saved crop restores its framing and adjustments from the original.
Every ratio starts centred and has an independently draggable, resizable box.
Select its ratio tab or saved thumbnail to edit it. The inline frame reuses the
same editor and API as the standalone route; do not maintain a second crop engine.

Original attachments are never overwritten. Exported JPEGs have a maximum long
edge of 2048 pixels without upscaling. Transparency is flattened onto white and
EXIF orientation is normalised. Animated images and videos remain original
attachments; this dashboard edits still images only. No browser zoom is applied.

**Tags** provides search, groups, usage counts, rename, regroup and merge. Create
tags there or in the post editor. Click a tag to filter the post library. Tags are
metadata, not automatically appended hashtags. Static builds include tag pages.
Tag names are normalised to lowercase with underscores for whitespace, e.g.
`workshop_storage`. Groups retain their readable labels. `tools/normalize_tags.py`
migrates existing catalogues with backups; stable IDs preserve post references,
and any resulting duplicate names are merged with metadata backups.

## Attached links

Posts can have one optional `github_repo` URL and up to 12 total `links` entries,
including that repository. The repository field accepts a GitHub owner/repository
URL; it does not clone, authenticate to or modify the repository. Add/remove link
rows in the creation form. A normal **Save draft** or **Save & view post**
fetches titles, descriptions and site names. Inline image upload saves defer that
fetch until the post is saved. Successful metadata is cached with the post.

`links.py` fetches public HTTP(S) HTML with the Python HTTP client and parses
Open Graph / Twitter metadata or HTML title/description with BeautifulSoup.
It checks public IPs and pins each connection to a checked address, validates
every redirect, verifies TLS, sends no credentials/cookies and runs no scripts.
There are at most four concurrent requests, four redirect attempts, five-second
socket timeouts and a one-megabyte HTML cap. Unsupported/private/login-required
pages retain their URL with an unavailable preview; they do not prevent saving.
Failed metadata is retried on the next post save. Remote images/embeds are not
loaded by cards. Titles and descriptions are escaped by templates.

Each entry records `url`, `kind` (`link` or `github`), `title`, `description`,
`site_name`, `status`, and, after a fetch, `fetched_at` plus optional `resolved_url`
or `error`. Rich cards appear in Studio and static pages. The metadata travels
with canonical post/queue snapshots. Social preparation recipes are unchanged;
no links are silently appended to their fixed text or submitted anywhere.

## Code boundaries

- `creation.py`: editor and tag routes, CSRF-protected saves and stale-editor checks.
- `images.py`: original validation, variant persistence and offline centre crops.
- `tags.py`: stable tag IDs, catalogue and backed-up merges.
- `static/image-editor.js`: the only Cropper.js API adapter; canvas adjustments/export.
- `static/creation.js`: tag filtering, creation and publishing image previews.
- `templates/editor_embed.html`: isolated inline editor shell; its parent saves
  crops before saving the post or switching images and validates frame messages.
- `links.py`, `templates/link_cards.html`: metadata retrieval and shared cards.
- `templates/image_editor.html`, `compose.html`, `post.html`, `tags.html`: views.
- `storage.py`: copies originals and variants with drafts/public snapshots; selects
  the chosen variant in the queue snapshot without changing browser workers.
- `website.py`: selected image version and static tag pages.

Keep image-editor changes isolated from platform YAML, preparation runners and
approval fingerprints. The fixed platform test sample is unrelated to this editor.

## Data

Draft manifests and media live in ignored `.local/drafts/<namespace>/<user>/`.
Published manifests and media belong to the independent user repository in
`garden/<namespace>/<user>/`. Its `tags.yaml` stores `schema_version: 1` and a list
of `{id, name, group}` records. Posts store a `tags` list of stable IDs, so a rename
does not require rewriting every post.

An attachment keeps its original `file`. Its optional `variants` map uses
`square`, `portrait`, `landscape`, `story` keys. Each entry has a new immutable JPEG
path, width, height, source SHA-256, creation time, `needs_review` and a recipe:

```yaml
engine: cropperjs-1
crop: {x: 0, y: 0, width: 800, height: 800, rotate: 0, scaleX: 1, scaleY: 1}
adjustments: {brightness: 1, contrast: 1, saturation: 1}
```

Coordinates refer to the orientation-normalised source and Cropper.js 1.x
transforms. Cropper recipes are private implementation data; a major library
upgrade must explicitly migrate them or retain compatibility. The server checks
finite values, ranges, ratio, dimensions, source hash and attachment revision
before writing exports. Saving creates new filenames so previous queue snapshots
keep their files. Old variants are not garbage-collected automatically.

Publishing sets `display_variant`; absent or `original` uses the original.
All versions travel with the canonical public post. The queued attachment's
`file` is the selected version and `original_file` records its source attachment.
Changing tags or generating crops alone never publishes or queues a post.

## Updating the library

Cropper.js is MIT licensed: https://github.com/fengyuanchen/cropperjs
The editor pins maintained 1.x version **1.7.0**. Version 2 uses a different API;
do not replace files with 2.x without adapting and testing the integration.
Pillow handles local validation and legacy exports using the existing Python
dependency. Browser colour adjustments use Canvas 2D.

Vendored files, licence, upstream URL and SHA-256 hashes are in
`static/vendor/cropper/`. No CDN, npm install or runtime download is required.
Do not edit vendor files. To upgrade to a reviewed stable 1.x release:

```powershell
python tools/update_cropper.py --version 1.7.0
python -m pytest tests/test_creation.py tests/test_creation_browser.py tests/test_publishing.py tests/test_studio_browser.py -q
```

Choose the intended version explicitly, inspect upstream release notes, review
the downloaded files/manifest and preserve the licence. Verify rotated/flipped
crop restoration, viewport resizing, all four exports, original hashes, stale
editor rejection, draft privacy and selected-version publishing. UI tests use an
isolated local fixture, never logged-in social platforms. Run the full suite
before handing back an update. Package data includes the vendored files.

## Legacy enrichment

`tools/enrich_legacy.py` applies a reviewed YAML plan with `tags: {Name: Group}`
and `posts: {legacy-id: [Name, ...]}`. Review text and original images when choosing
tags; do not infer unsupported subjects. Only imported legacy IDs are accepted.

```powershell
python tools/enrich_legacy.py .local/image-editor-work/legacy-plan.yaml
python tools/enrich_legacy.py .local/image-editor-work/legacy-plan.yaml --apply
```

Without `--apply` it reports the plan only. Applying backs up affected manifests
and any existing catalogue under `.local/library-backups/legacy-<id>/`, then adds
tags and missing centre crops. Repeating the same plan preserves existing crops.
Automatic centre crops have `needs_review: true`; they do not imply subject-aware
framing. Existing text, timestamps, posted status, revision and original files
are preserved. The report verifies original and queue hashes and lists warnings.

To undo an enrichment, stop Studio/workers, review the backup report, then restore
the backed-up manifests/catalogue to their original relative locations. If no
catalogue existed before the run, remove only the newly created catalogue after
checking that it has no later changes. Generated JPEGs become unreferenced; do
not delete files referenced by newer manifests or queue snapshots. Never stage
garden repositories into the tools repository.
