# Roboclick primitive workflows

The translation recipe is `allotment/plugins/chatgpt_translation.yaml`. Change
browser order, labels, regions, delays, selectors and polling in that file.
Do not recreate a platform workflow inside a Python action.

Each `command` is discovered and executed by the existing oomlout roboclick
dispatcher. Existing `desktop_*` actions perform normal guarded Chrome input.
The new operations are small reusable primitives, with no ChatGPT or Allotment
selectors, destination names, prompt construction or publication logic.

| Primitive | Operation |
|---|---|
| `desktop_find_text_any` | Find the first visible alternative using screenshot/OCR; ambiguity or a guard failure stops. |
| `desktop_click_match` | Click a prior detection after the existing fresh-pixel check. |
| `desktop_read_url` | Copy the address using normal keyboard input. |
| `data_read_text` | Read a UTF-8 file inside the workflow's private folder. |
| `data_text_chunks` | Split text into an explicit list without changing characters. |
| `data_assert_equal` | Compare values, optionally normalizing whitespace. |
| `data_image_crop` | Calculate a centred reference rectangle for an aspect ratio; never modify the original image. |
| `data_url_assert` | Check an origin/path; only explicitly configured pending paths may be polled. |
| `data_html_absent` | Wait until a selector is absent from already-captured HTML. |
| `data_html_extract` | Select one matching text block from captured HTML, deduplicating nested matches. |
| `data_text_between` | Extract between exactly one pair of delimiters. |
| `data_json_parse` | Decode JSON and reject duplicate keys. |

## Values and repetition

`{ref: name}` passes a typed value; it is not executable template code.
`save_as: name` stores an action result. Dot paths can read dictionary values.
Labels are displayed as the current operation. Evidence records each primitive's
step path, command and start/completion state without copying prompt data into logs.
An optional leaf `detail` explains the operation without becoming an action argument.
Loop `label` names the item being processed. Progress includes `step_index`,
`total_steps`, `item_index`, `item_total` and `item_label`.

```yaml
- command: data_text_chunks
  text: {ref: prompt}
  size: 2500
  save_as: chunks
- for_each: {ref: chunks}
  as: chunk
  steps:
  - command: desktop_paste_text
    label: Entering the prompt
    text: {ref: chunk}
  - command: desktop_wait
    seconds: 0.5
```

`poll` has a finite `timeout` and `interval`. Its optional `stable_on` variable
must match for `observations` consecutive successful passes. Only a `NotReady`
observation triggers another pass. Focus/input cancellation, missing clipboard
data, malformed JSON and ambiguity propagate immediately. Poll blocks reject
clicks, pastes, hotkeys and nested input loops before execution. They cannot
resubmit a prompt.

Use `detail` on a `desktop_wait` and `reason` inside `poll` to explain the wait.
The progress callback receives UTC `wait_until` / `poll_until` deadlines,
`wait_seconds`, `wait_kind`, `wait_reason`, `poll_timeout`, `poll_attempt` and
`poll_reason`. These let a UI show live countdowns without extra desktop input.
Subsequent steps clear finished wait metadata; the UI must hide timers for
terminal runs and user-paused countdowns. Overall poll time includes observation
work as well as the interval between checks.

## Translation boundary

The worker owns desktop reservation, the initial countdown and cleanup. The YAML
owns the subsequent browser sequence. The Allotment adapter supplies saved input
and validates the final content pack. Its submission policy watches actual
primitive results; removing checks from YAML cannot authorize Enter. The policy
permits one checked ChatGPT query, never social submission, editorial approval or
platform-test approval.

The existing page-copy response capture remains explicit in the recipe. Replacing
it with a screenshot-located Copy control should change those capture steps and
use generic primitives; it should not add another translation-specific action.

## Reuse and checks

`workflow.py` is mirrored as `C:/gh/oomlout_roboclick/roboclick_workflow.py`; each
new discovered primitive is mirrored under that library's `actions/` directory.
`Workflow` accepts a desktop, private root, input values, discovered action lookup,
dispatcher, optional progress callback and optional before/after policy hooks.
It can sequence other recipes without importing Allotment. Generic data primitives
also work independently without creating a browser session.

Run `python -m pytest -q` for automated checks. Run
`python tests/desktop_translation_practice.py` with normal Chrome available and
the desktop idle for the native fixture. It uses the production YAML and asserts
one local AI prompt, one image and zero social Post/Send attempts. A mocked or
headless pass is not a native verification result.

## Optional Windows observations

`allotment/workflow_extensions/` adds two generic primitives without changing the
social preparation engine. `desktop_capture_accessibility` takes a read-only
Windows UI Automation snapshot of the reserved browser document (pywinauto,
Windows only). It supplies names, control types and enabled states, never clicks.
`data_elements_check` checks exact names or name patterns and count ranges in that
snapshot. A pending mismatch is retryable; an error mismatch stops immediately.
Both are registered as read-only operations for bounded polls. `ExtendedOperations`
can replace a Workflow's `operations` object. Its standalone mirror is
`roboclick_workflow_extensions.py`, with `roboclick_accessibility.py` and the two
registered actions in the shared library. Install pywinauto>=0.6.9,<0.7 on Windows.

The capture helper has a 15-second default timeout (maximum 30); the parent checks
Stop and focus throughout and terminates only its helper on interruption. Selectors
and their platform meaning belong to YAML. The ChatGPT recipe supplies upload
controls and counts; it does not compare the thumbnail to the source image.
