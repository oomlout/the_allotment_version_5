# Local publication and sowing

## Current Studio launch flow

Studio uses garden cards and one bound garden. Sowing cards contain the default image crop and an expandable fixed-sample test. There is no Queue or Tests navigation item and no batch/bundle server endpoints. On the post page, select destinations and click Launch. The initial fresh Chrome tab opens at the site before the eight-second countdown; subsequent destinations each use a fresh tab without another countdown. Final submission stays manual. Pause/Resume and Stop remain available. During the initial countdown only, input is allowed for handoff; the same Chrome process/window and focus are checked when input guards are re-armed. The countdown helper is included in approval signatures, so this runtime change requires a fresh test and explicit user approval. Existing queue/bundle details below describe legacy command-line compatibility, not Studio controls.


See [HARVEST_HANDOFF.md](HARVEST_HANDOFF.md) for the complete Studio model, storage paths, read-only harvesting and platform extension instructions.

- Save creates or updates a private draft, with a unique post page.
- Publish locally writes that version to the selected garden repository. It does not create a queue job, build a site, push Git, or open Chrome.
- Unpublish locally returns the post and harvest findings to private draft storage. Existing destination posts, exported snapshots and Git history are not erased.
- Sow operates only on a published local post with no unpublished edits. Destinations are independent checkboxes. Queue stores an immutable snapshot; Run now starts the same queue job on this computer.
- Social actions require the unchanged preparation approval gate, prepare a checked browser draft, and stop before submission. Each destination uses its own default crop, with optional per-post framing overrides. Missing crops are generated centrally in the snapshot, preserving source media and historical post folders.
- Static website, Allotment summary and Git sync are sowing destinations. An obsolete local revision cannot overwrite a newer static export; queue a fresh snapshot after changing the post.
- After manual social submission, capture the platform post ID, then harvest reactions. Bluesky is first; neither operation may send or react. Read-only tests and explicit user approval are separate from draft preparation approvals.
- Current worker progress remains visible with countdown controls and Stop. Sowing history and crop overrides start collapsed.

Legacy jobs with an `allotment` action are still understood by the worker and receipt importer. New Studio jobs use `kind: sow` and never include that legacy action. Do not migrate old evidence into new approvals or replay exported jobs.
