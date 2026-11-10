from __future__ import annotations

import argparse
import concurrent.futures
import copy
import datetime
import json
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import yaml

if sys.platform == "win32":
    import msvcrt
else:
    import select


__all__ = [
    "main",
    "cli",
    "run_single",
    "run_folder",
    "run_folder_recursive",
    "run_folder_recursive_linear",
    "run_folder_recursive_threaded",
    "run_action",
    "discover_actions",
    "build_action_lookup",
    "get_all_actions_documentation",
    "export_actions_documentation_json",
    "export_actions_documentation_html",
    "check_key_pressed",
    "scroll_lock_toggled",
    "delay",
    "robo_delay",
]


###############################################################################
# Shared automation primitives (moved from automation_primitives.py)
###############################################################################


def check_key_pressed() -> str | None:
    try:
        if sys.platform == "win32":
            if msvcrt.kbhit():
                return msvcrt.getch().decode("utf-8", errors="ignore").lower()
        else:
            if select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
                return sys.stdin.read(1).lower()
    except Exception:
        return None
    return None


def scroll_lock_toggled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        # 0x91 = VK_SCROLL
        return (ctypes.windll.user32.GetKeyState(0x91) & 1) == 1
    except Exception:
        return False


def clear_scroll_lock() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        if (ctypes.windll.user32.GetKeyState(0x91) & 1) == 1:
            ctypes.windll.user32.keybd_event(0x91, 0x45, 0, 0)
            ctypes.windll.user32.keybd_event(0x91, 0x45, 2, 0)
    except Exception:
        return


def _as_float(value: Any, default: float = 1.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _config_delay_multiplier() -> float:
    path = Path("config_runtime.yaml").resolve()
    if not path.exists():
        return 1.0
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        pacing = data.get("pacing", {}) if isinstance(data, dict) else {}
        return _as_float(pacing.get("delay_multiplier", 1), 1.0)
    except Exception:
        return 1.0


def _delay_multiplier(kwargs: dict[str, Any]) -> float:
    value = kwargs.get("multiplier", None)
    if value in (None, ""):
        value = _config_delay_multiplier()
    return max(0.0, _as_float(value, 1.0))


def delay(**kwargs: Any) -> str:
    delay_seconds = kwargs.get("delay", 1)
    rand_seconds = kwargs.get("rand", 0)
    message = kwargs.get("message", "")
    mode_skip_key = kwargs.get("mode_skip_key", True)
    mode_scroll_lock_skip = kwargs.get("mode_scroll_lock_skip", True)

    try:
        delay_seconds = float(delay_seconds)
    except Exception:
        delay_seconds = 1.0
    try:
        rand_seconds = int(rand_seconds)
    except Exception:
        rand_seconds = 0

    if message:
        print(message)
    if rand_seconds > 0:
        delay_seconds += random.randint(0, rand_seconds)
    delay_seconds *= _delay_multiplier(kwargs)

    if delay_seconds <= 1.0:
        time.sleep(delay_seconds)
        return ""

    if delay_seconds > 5.0:
        print(f"<<<<<>>>>> waiting for {delay_seconds:.0f} seconds (press 's' to skip)")
        splits = 10
        chunk = max(1, int(delay_seconds // splits))
        for _ in range(splits):
            print(".", end="", flush=True)
            for _ in range(chunk):
                if mode_skip_key and check_key_pressed() == "s":
                    print("\nDelay skipped by 's'")
                    time.sleep(1)
                    return ""
                if mode_scroll_lock_skip and scroll_lock_toggled():
                    print("\nScroll Lock toggled; skipping delay")
                    clear_scroll_lock()
                    time.sleep(1)
                    return ""
                time.sleep(1)
        print("")
        return ""

    print(f"waiting {delay_seconds:.0f} seconds, ", end="", flush=True)
    remaining = max(1, int(round(delay_seconds)))
    for _ in range(remaining):
        print(".", end="", flush=True)
        if mode_skip_key and check_key_pressed() == "s":
            print("\nDelay skipped by 's'")
            time.sleep(1)
            return ""
        if mode_scroll_lock_skip and scroll_lock_toggled():
            print("\nScroll Lock toggled; skipping delay")
            clear_scroll_lock()
            time.sleep(1)
            return ""
        time.sleep(1)
    #print("")
    return ""


def robo_delay(**kwargs: Any) -> str:
    return delay(**kwargs)


###############################################################################
# Action loading and discovery (moved from action_loader.py)
###############################################################################


@dataclass(frozen=True)
class DiscoveredAction:
    name: str
    path: Path
    metadata: dict[str, Any]
    action_fn: Callable[..., Any]
    test_fn: Callable[..., Any]
    aliases: tuple[str, ...] = ()


def resolve_actions_root(actions_root: str | Path | None = None) -> Path:
    if actions_root is not None:
        return Path(actions_root).resolve()
    return (Path(__file__).resolve().parent / "actions").resolve()


def _make_dispatch_shim(source_action_name: str) -> Callable[..., Any]:
    def _dispatch_action(command_name: str, **kwargs: Any) -> Any:
        discovered = kwargs.get("_discovered_actions")
        if discovered is None:
            discovered = build_action_lookup(actions_root=kwargs.get("actions_root"))

        action_info = discovered.get(command_name)
        if action_info is None:
            print(
                f"Warning: Unknown command during local dispatch '{command_name}' "
                f"(source action: {source_action_name})"
            )
            return ""

        run_kwargs = copy.deepcopy(kwargs)
        action_cfg = run_kwargs.get("action", {}) or {}
        if not isinstance(action_cfg, dict):
            action_cfg = {}
        action_cfg = copy.deepcopy(action_cfg)
        action_cfg.setdefault("command", command_name)
        run_kwargs["action"] = action_cfg
        run_kwargs["_discovered_actions"] = discovered
        return action_info.action_fn(**run_kwargs)

    return _dispatch_action


def _load_module_from_file(
    file_path: Path,
    action_name: str,
    inject_dispatch_shim: bool = False,
) -> ModuleType:
    module_name = f"roboclick_action_{action_name}_{abs(hash(str(file_path)))}"
    spec = spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {file_path}")
    module = module_from_spec(spec)

    if inject_dispatch_shim:
        setattr(module, "_dispatch_action", _make_dispatch_shim(action_name))

    spec.loader.exec_module(module)

    if inject_dispatch_shim and not callable(getattr(module, "_dispatch_action", None)):
        setattr(module, "_dispatch_action", _make_dispatch_shim(action_name))
    return module


def discover_actions(actions_root: str | Path | None = None) -> dict[str, DiscoveredAction]:
    root = resolve_actions_root(actions_root)
    actions: dict[str, DiscoveredAction] = {}
    if not root.exists():
        return actions

    entries = sorted(root.iterdir(), key=lambda p: p.name)
    for entry in entries:
        if not entry.is_dir():
            continue
        working_file = entry / "working.py"
        if not working_file.is_file():
            continue

        try:
            module = _load_module_from_file(
                working_file,
                entry.name,
                inject_dispatch_shim=True,
            )
        except Exception as exc:
            print(f"Warning: failed to import action module {working_file}: {exc}")
            continue

        define_fn = getattr(module, "define", None)
        action_fn = getattr(module, "action", None)
        test_fn = getattr(module, "test", None)
        if not callable(define_fn) or not callable(action_fn):
            print(
                f"Warning: skipping action '{entry.name}' because working.py must expose define() and action()"
            )
            continue

        if not callable(test_fn):
            fallback_test_files = (
                entry / "test.py",
                entry / "oomlout_test.py",
            )
            for test_file in fallback_test_files:
                if not test_file.is_file():
                    continue
                try:
                    test_module = _load_module_from_file(
                        test_file,
                        f"{entry.name}_{test_file.stem}",
                        inject_dispatch_shim=False,
                    )
                    test_fn = getattr(test_module, "test", None)
                except Exception as exc:
                    print(f"Warning: failed to import test module {test_file}: {exc}")
                    continue
                if callable(test_fn):
                    break
        if not callable(test_fn):
            print(
                "Warning: skipping action "
                f"'{entry.name}' because no callable test() found in "
                "working.py, test.py, or oomlout_test.py"
            )
            continue

        metadata_raw = define_fn()
        metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
        metadata.setdefault("name", entry.name)
        metadata.setdefault("description", "")
        metadata.setdefault("variables", [])
        metadata.setdefault("category", "Other")

        declared_name = metadata.get("name", entry.name)
        if declared_name != entry.name:
            print(
                f"Warning: action '{entry.name}' define().name='{declared_name}'. Using folder name '{entry.name}'."
            )
            metadata["name"] = entry.name

        actions[entry.name] = DiscoveredAction(
            name=entry.name,
            path=working_file,
            metadata=metadata,
            action_fn=action_fn,
            test_fn=test_fn,
            aliases=tuple(_extract_aliases(metadata)),
        )
    return actions


def _extract_aliases(metadata: dict[str, Any]) -> list[str]:
    raw_aliases: list[str] = []

    for key in ("name_short",):
        value = metadata.get(key)
        if isinstance(value, str):
            raw_aliases.append(value)
        elif isinstance(value, list):
            raw_aliases.extend([item for item in value if isinstance(item, str)])

    cleaned: list[str] = []
    seen: set[str] = set()
    for alias in raw_aliases:
        normal = alias.strip()
        if not normal:
            continue
        if normal in seen:
            continue
        seen.add(normal)
        cleaned.append(normal)
    return cleaned


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_variable_names(raw_variables: Any) -> list[str]:
    if not isinstance(raw_variables, list):
        return []

    names: list[str] = []
    seen: set[str] = set()
    for item in raw_variables:
        name_value = ""
        if isinstance(item, dict):
            name_value = _coerce_text(item.get("name", ""))
        elif isinstance(item, str):
            name_value = _coerce_text(item)
        if not name_value:
            continue
        if name_value in seen:
            continue
        seen.add(name_value)
        names.append(name_value)
    return names


def _build_summary(
    description: str,
    variable_names: list[str],
    returns_text: str,
) -> str:
    description_clean = " ".join(description.split())
    if description_clean:
        first_sentence = re.split(r"(?<=[.!?])\s+", description_clean, maxsplit=1)[0]
        return first_sentence

    if variable_names:
        sample = ", ".join(variable_names[:3])
        if len(variable_names) > 3:
            sample += ", ..."
        return f"Inputs: {sample}."

    if returns_text:
        returns_clean = " ".join(returns_text.split())
        first_sentence = re.split(r"(?<=[.!?])\s+", returns_clean, maxsplit=1)[0]
        return f"Returns: {first_sentence}"

    return "No summary available."


def build_action_lookup(actions_root: str | Path | None = None) -> dict[str, DiscoveredAction]:
    canonical_actions = discover_actions(actions_root=actions_root)
    lookup: dict[str, DiscoveredAction] = {}

    for action_name, discovered_action in canonical_actions.items():
        lookup[action_name] = discovered_action

    for action_name, discovered_action in canonical_actions.items():
        for alias in discovered_action.aliases:
            if alias == action_name:
                continue
            if alias in lookup and lookup[alias].name != action_name:
                print(
                    f"Warning: alias '{alias}' for action '{action_name}' conflicts with action '{lookup[alias].name}'."
                )
                continue
            lookup[alias] = discovered_action
    return lookup


def get_all_actions_documentation(actions_root: str | Path | None = None) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    discovered = discover_actions(actions_root=actions_root)
    for action_name in sorted(discovered.keys()):
        action_info = discovered[action_name]
        metadata = action_info.metadata
        aliases = list(action_info.aliases)
        description = _coerce_text(metadata.get("description", ""))
        returns_text = _coerce_text(metadata.get("returns", ""))
        variable_names = _extract_variable_names(metadata.get("variables", []))
        name_long = _coerce_text(metadata.get("name_long", "")) or action_name

        # Ensure variables are always a list of dicts with name and description
        raw_vars = metadata.get("variables", [])
        variables = []
        for v in raw_vars:
            if isinstance(v, dict):
                name = _coerce_text(v.get("name", ""))
                desc = _coerce_text(v.get("description", ""))
                variables.append({"name": name, "description": desc, "type": v.get("type", ""), "default": v.get("default", "")})
            elif isinstance(v, str):
                variables.append({"name": _coerce_text(v), "description": "", "type": "", "default": ""})
            else:
                variables.append({"name": str(v), "description": "", "type": "", "default": ""})

        docs.append(
            {
                "command": action_name,
                "name_long": name_long,
                "name_short_options": aliases,
                "description": description,
                "summary": _build_summary(
                    description=description,
                    variable_names=variable_names,
                    returns_text=returns_text,
                ),
                "variables": variables,
                "variable_names": variable_names,
                "category": metadata.get("category", "Other"),
                "returns": returns_text,
                "aliases": aliases,
            }
        )
    return docs


###############################################################################
# Runner logic (moved from runner.py)
###############################################################################


def _load_yaml_file(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        print(f"Warning: YAML file not found: {path}")
    except yaml.YAMLError as exc:
        print(f"Warning: failed to parse YAML file {path}: {exc}")
    return {}


def _manual_yaml_path(file_action: str) -> str:
    file_action_path = Path(file_action)
    if file_action_path.suffix:
        return str(file_action_path.with_name(f"{file_action_path.stem}_manual{file_action_path.suffix}"))
    return f"{file_action}_manual.yaml"


def _load_workings(kwargs: dict[str, Any]) -> dict[str, Any]:
    workings = kwargs.get("workings")
    if isinstance(workings, dict) and workings:
        return copy.deepcopy(workings)
    file_action = kwargs.get("file_action", "working.yaml")
    return _load_yaml_file(file_action)


def _merge_manual_into_workings(file_action: str, workings: dict[str, Any]) -> dict[str, Any]:
    manual_file = _manual_yaml_path(file_action)
    if not os.path.exists(manual_file):
        return workings
    manual_data = _load_yaml_file(manual_file)
    if manual_data:
        print(f"loading manual configuration from {manual_file}")
        workings.update(manual_data)
    return workings


def _persist_result_dict(file_action: str, result: dict[str, Any]) -> None:
    manual_file = _manual_yaml_path(file_action)
    details: dict[str, Any] = {}
    if os.path.exists(manual_file):
        loaded = _load_yaml_file(manual_file)
        if isinstance(loaded, dict):
            details = loaded

    print("Updating workings with result dict")
    for key, value in result.items():
        details[key] = value
        print(f"    Updated workings key: {key} with value: {value}")

    with open(manual_file, "w", encoding="utf-8") as file:
        yaml.safe_dump(details, file, sort_keys=False)
    print(f"Updated workings saved to {manual_file}")


def _normalize_mode_list(mode: Any) -> list[str]:
    if isinstance(mode, list):
        raw_modes = mode
    elif mode in (None, ""):
        raw_modes = ["all"]
    else:
        raw_modes = [mode]

    normalized: list[str] = []
    for item in raw_modes:
        mode_name = str(item)
        if mode_name in ("all", ""):
            normalized.extend(["oomlout_ai_roboclick", "oomlout_corel_roboclick"])
        elif mode_name == "ai":
            normalized.append("oomlout_ai_roboclick")
        elif mode_name == "corel":
            normalized.append("oomlout_corel_roboclick")
        else:
            normalized.append(mode_name)
    return normalized


def _expand_numbered_variants(mode_list: list[str]) -> list[str]:
    expanded: list[str] = []
    for mode_name in mode_list:
        expanded.append(mode_name)
        tail = mode_name.rsplit("_", 1)[-1]
        if tail.isdigit():
            continue
        expanded.extend([f"{mode_name}_{i}" for i in range(1, 101)])
    return expanded


def _extract_mode_index(mode_name: str, prefix: str) -> int | None:
    pattern = rf"^{re.escape(prefix)}_?(\d+)$"
    match = re.match(pattern, mode_name)
    if not match:
        return None
    index = int(match.group(1))
    if 1 <= index <= 100:
        return index
    return None


def _discover_oomp_modes(workings: dict[str, Any]) -> list[str]:
    discovered: list[str] = []
    mode_prefixes = ["oomlout_ai_roboclick", "oomlout_corel_roboclick"]
    for prefix in mode_prefixes:
        indexed_modes: list[tuple[int, str]] = []
        for mode_name, mode_cfg in workings.items():
            if not isinstance(mode_cfg, dict):
                continue
            if not isinstance(mode_cfg.get("actions"), list):
                continue
            mode_index = _extract_mode_index(str(mode_name), prefix)
            if mode_index is None:
                continue
            indexed_modes.append((mode_index, str(mode_name)))
        indexed_modes.sort(key=lambda item: item[0])
        discovered.extend([name for _, name in indexed_modes])
    return discovered




def _resolve_mode_names(workings: dict[str, Any], mode_arg: Any) -> list[str]:
    if mode_arg in ("all", "", None):
        return _discover_oomp_modes(workings)

    # A mode such as ``ai`` expands to 101 possible names for compatibility,
    # but only real action blocks should become jobs.  Queuing all of the
    # missing variants once per folder made large recursive runs spend most of
    # their time processing no-ops.
    requested_modes = _expand_numbered_variants(_normalize_mode_list(mode_arg))
    return [
        mode_name
        for mode_name in requested_modes
        if isinstance(workings.get(mode_name), dict)
        and isinstance(workings[mode_name].get("actions"), list)
        and bool(workings[mode_name]["actions"])
    ]


def _resolve_file_action(folder_abs: str, file_action_arg: str) -> str:
    candidate_files: list[str] = []
    if file_action_arg:
        candidate_files.append(file_action_arg)
    candidate_files.extend(["working.oomp", "working.yaml"])

    for candidate in candidate_files:
        candidate_path = candidate if os.path.isabs(candidate) else os.path.join(folder_abs, candidate)
        if os.path.exists(candidate_path):
            return candidate_path
    return ""


def _build_folder_mode_jobs(**kwargs: Any) -> list[dict[str, Any]]:
    folder = kwargs.get("folder", "")
    if not folder:
        print("Error: folder is required for run_folder")
        return []

    folder_abs = os.path.abspath(folder)
    if not os.path.isdir(folder_abs):
        print(f"Error: folder does not exist: {folder_abs}")
        return []

    file_action = _resolve_file_action(folder_abs, kwargs.get("file_action", ""))
    if not file_action:
        print(f"Error: no working file found in {folder_abs}. Expected working.oomp or working.yaml")
        return []

    workings = _load_yaml_file(file_action)
    if not workings:
        print(f"Warning: no workings loaded from {file_action}")
        return []

    modes = _resolve_mode_names(workings, kwargs.get("mode", "all"))
    quiet_queue_build = bool(kwargs.get("_quiet_queue_build", False))
    if not modes:
        if quiet_queue_build:
            return []
        print(
            "Warning: no runnable modes found. "
            "Expected keys like oomlout_ai_roboclick_1..100 or oomlout_corel_roboclick_1..100"
        )
        return []

    if not quiet_queue_build:
        print(f"Running {len(modes)} mode(s) from {file_action}")
    jobs: list[dict[str, Any]] = []
    for mode_name in modes:
        run_kwargs = copy.deepcopy(kwargs)
        run_kwargs.pop("_quiet_queue_build", None)
        run_kwargs["directory"] = folder_abs
        run_kwargs["directory_absolute"] = folder_abs
        run_kwargs["file_action"] = file_action
        run_kwargs["workings"] = workings
        run_kwargs["mode"] = mode_name
        jobs.append(run_kwargs)
    return jobs


_UI_ACTION_HINTS = [
    "mouse",
    "click",
    "cursor",
    "drag",
    "scroll",
    "key",
    "keyboard",
    "hotkey",
    "tab",
    "window",
    "browser",
    "corel",
    "pyautogui",
    "clipboard",
    "focus",
    "select",
    "affinity",
    "google_doc",
]

_UI_ACTION_NAMES = {
    "add_file",
    "add_image",
    "ai_save_image",
    "close_tab",
    "new_chat",
    "query",
    "save_image_search_result",
}

_UI_ACTION_CATEGORIES = {"ai", "browser", "corel", "coreldraw", "google docs"}


def _action_is_thread_safe(
    action_cfg: dict[str, Any],
    discovered: dict[str, DiscoveredAction],
    allow_subprocess: bool = False,
) -> bool:
    if not isinstance(action_cfg, dict):
        return False

    if action_cfg.get("thread_safe") is True:
        return True
    if action_cfg.get("thread_safe") is False:
        return False
    if action_cfg.get("requires_ui") is True:
        return False

    command_name = str(action_cfg.get("command", "")).strip()
    if command_name == "":
        return False

    # Subprocess actions are safe for this repository's per-part scripts, but
    # other callers may use them to drive shared applications or files.  Keep
    # those actions linear unless the caller explicitly opts in.
    if command_name in {"run_python", "run_command"} and not allow_subprocess:
        return False

    normalized_command = command_name.lower()
    if normalized_command.startswith("roboclick_action_"):
        normalized_command = normalized_command[len("roboclick_action_"):]

    # Aliases can dispatch to arbitrary legacy actions, so keep them on the
    # serial path unless their YAML explicitly declares thread_safe: true.
    if normalized_command.startswith("alias_"):
        return False
    if normalized_command in _UI_ACTION_NAMES:
        return False
    if any(token in normalized_command for token in _UI_ACTION_HINTS):
        return False

    discovered_action = discovered.get(command_name)
    if discovered_action is not None:
        metadata = discovered_action.metadata if isinstance(discovered_action.metadata, dict) else {}
        category = str(metadata.get("category", "")).strip().lower()
        if category in _UI_ACTION_CATEGORIES:
            return False
    return True


def _job_is_thread_safe(
    job_kwargs: dict[str, Any],
    discovered: dict[str, DiscoveredAction],
    allow_subprocess: bool = False,
) -> bool:
    workings = job_kwargs.get("workings", {})
    mode_name = job_kwargs.get("mode", "")
    mode_cfg = workings.get(mode_name, {}) if isinstance(workings, dict) else {}
    if not isinstance(mode_cfg, dict):
        return False

    actions = mode_cfg.get("actions", [])
    if not isinstance(actions, list) or not actions:
        return False

    return all(
        _action_is_thread_safe(action_cfg, discovered, allow_subprocess=allow_subprocess)
        for action_cfg in actions
    )


def _job_action_count(job: dict[str, Any]) -> int:
    workings = job.get("workings", {})
    mode = job.get("mode", "")
    mode_cfg = workings.get(mode, {}) if isinstance(workings, dict) else {}
    actions = mode_cfg.get("actions", []) if isinstance(mode_cfg, dict) else []
    return len(actions) if isinstance(actions, list) else 0


def _run_job_group(jobs: list[dict[str, Any]]) -> str:
    """Run one folder's modes in order while other folders run concurrently."""
    for job in jobs:
        result = run_single(**job)
        if result in ("exit", "exit_no_tab"):
            return result
    return ""


def run_folder_recursive(**kwargs: Any) -> None:
    if kwargs.get("recursive_threaded", False):
        run_folder_recursive_threaded(**kwargs)
        return
    run_folder_recursive_linear(**kwargs)


def run_folder_recursive_linear(**kwargs: Any) -> None:
    directory = kwargs.get("directory", "")
    if not directory:
        print("Error: directory is required for run_folder_recursive_linear")
        return

    entries = sorted(os.listdir(directory))
    for entry in entries:
        run_dir = os.path.join(directory, entry)
        if not os.path.isdir(run_dir):
            continue
        print(f"Processing folder: {run_dir}")
        run_kwargs = copy.deepcopy(kwargs)
        run_kwargs.pop("directory", None)
        run_kwargs["folder"] = run_dir
        run_folder(**run_kwargs)


def run_folder_recursive_threaded(**kwargs: Any) -> None:
    directory = kwargs.get("directory", "")
    if not directory:
        print("Error: directory is required for run_folder_recursive_threaded")
        return

    entries = sorted(os.listdir(directory))
    discovered = build_action_lookup(actions_root=kwargs.get("actions_root"))
    allow_subprocess = bool(kwargs.get("threaded_subprocess_actions", False))

    linear_job_groups: list[list[dict[str, Any]]] = []
    threaded_job_groups: list[list[dict[str, Any]]] = []

    for entry in entries:
        run_dir = os.path.join(directory, entry)
        if not os.path.isdir(run_dir):
            continue
        run_kwargs = copy.deepcopy(kwargs)
        run_kwargs.pop("directory", None)
        run_kwargs["folder"] = run_dir
        run_kwargs["_quiet_queue_build"] = True
        jobs = _build_folder_mode_jobs(**run_kwargs)
        if not jobs:
            continue
        for job in jobs:
            job["_discovered_actions"] = discovered
        # Modes within a folder can depend on outputs from earlier modes, so a
        # folder is one ordered unit of work.  Different folders are independent
        # and can safely be consumed by the worker pool.
        if all(
            _job_is_thread_safe(job, discovered, allow_subprocess=allow_subprocess)
            for job in jobs
        ):
            threaded_job_groups.append(jobs)
        else:
            linear_job_groups.append(jobs)

    threaded_mode_count = sum(len(group) for group in threaded_job_groups)
    linear_mode_count = sum(len(group) for group in linear_job_groups)
    action_count = sum(
        _job_action_count(job)
        for group in threaded_job_groups + linear_job_groups
        for job in group
    )
    print(
        "Job queue prepared: "
        f"{action_count} action(s), "
        f"{threaded_mode_count} mode(s) in "
        f"{len(threaded_job_groups)} threaded folder job(s), "
        f"{linear_mode_count} mode(s) in "
        f"{len(linear_job_groups)} linear folder job(s)."
    )

    worker_count = _as_int(kwargs.get("threaded_workers", 6), 6)
    if worker_count <= 0:
        worker_count = 6

    failures: list[str] = []
    if threaded_job_groups:
        print(f"Running threaded queue with {worker_count} worker(s)")
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="roboclick",
        ) as executor:
            futures = {
                executor.submit(_run_job_group, group): group[0]["folder"]
                for group in threaded_job_groups
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                    if result in ("exit", "exit_no_tab"):
                        failures.append(f"{futures[future]} returned {result}")
                except Exception as exc:
                    failures.append(f"{futures[future]} raised {type(exc).__name__}: {exc}")

    if linear_job_groups:
        print("Running linear queue for UI/interactive actions")
        for group in linear_job_groups:
            try:
                result = _run_job_group(group)
                if result in ("exit", "exit_no_tab"):
                    failures.append(f"{group[0]['folder']} returned {result}")
            except Exception as exc:
                failures.append(f"{group[0]['folder']} raised {type(exc).__name__}: {exc}")

    if failures:
        preview = "; ".join(failures[:10])
        if len(failures) > 10:
            preview += f"; ... and {len(failures) - 10} more"
        #raise RuntimeError(f"{len(failures)} recursive folder job(s) failed: {preview}")
        #print errors to the terminal
        print(f"{len(failures)} recursive folder job(s) failed: {preview}")
        


def run_folder(**kwargs: Any) -> str:
    jobs = _build_folder_mode_jobs(**kwargs)
    # Honour stop signals between blocks, as the threaded runner already does.
    return _run_job_group(jobs)


def _directory_matches_filters(directory_name: str, kwargs: dict[str, Any]) -> bool:
    filt = kwargs.get("filter", "")
    filt_all = kwargs.get("filter_all")
    filt_or = kwargs.get("filter_or")
    if filt_all:
        return all(token in directory_name for token in filt_all)
    if filt_or:
        return any(token in directory_name for token in filt_or)
    return (filt in directory_name) or (filt == "")


def run_action(**kwargs: Any) -> Any:
    action_cfg = kwargs.get("action", {}) or {}
    command = action_cfg.get("command")
    if not command:
        print("Warning: action missing 'command' key")
        return ""

    discovered = kwargs.get("_discovered_actions")
    if discovered is None:
        discovered = build_action_lookup(actions_root=kwargs.get("actions_root"))

    action_module = discovered.get(command)
    if action_module is None:
        print(f"Warning: Unknown command '{command}'")
        robo_delay(delay=10)
        return ""
    return action_module.action_fn(**kwargs)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _file_test_items(file_test: Any) -> list[str]:
    if file_test in (None, ""):
        return []
    if isinstance(file_test, (list, tuple)):
        return [str(item) for item in file_test if item not in (None, "")]
    return [str(file_test)]


def _file_test_absolute(directory_absolute: str, file_test: str) -> str:
    if os.path.isabs(file_test):
        return file_test
    return os.path.join(directory_absolute, file_test)


def _file_tests_complete(base: dict[str, Any], directory_absolute: str) -> bool:
    file_tests = _file_test_items(base.get("file_test", ""))
    if not file_tests:
        return True
    file_test_mode = base.get("file_test_mode", "exists")
    existing = [
        os.path.exists(_file_test_absolute(directory_absolute, file_test))
        for file_test in file_tests
    ]
    if file_test_mode == "exists":
        return all(existing)
    return not any(existing)


def _file_tests_allow_run(base: dict[str, Any], directory_absolute: str) -> bool:
    file_tests = _file_test_items(base.get("file_test", ""))
    if not file_tests:
        return True

    file_test_mode = base.get("file_test_mode", "exists")
    absolute_tests = [
        _file_test_absolute(directory_absolute, file_test)
        for file_test in file_tests
    ]
    print(f"file test mode {file_test_mode} on {absolute_tests}")

    if file_test_mode == "exists":
        missing = [path for path in absolute_tests if not os.path.exists(path)]
        if not missing:
            print(f"File test(s) {absolute_tests} complete, skipping actions.")
            return False
        print(f"File test(s) missing {missing}; running actions.")
        return True

    existing = [path for path in absolute_tests if os.path.exists(path)]
    if existing:
        print(f"File test(s) {existing} exist, skipping actions.")
        return False
    print(f"File test(s) {absolute_tests} do not exist; running actions.")
    return True


def run_single(**kwargs: Any) -> Any:
    #print a dot
    print(".", end="", flush=True)
    file_action = kwargs.get("file_action", "working.yaml")
    mode = kwargs.get("mode", "")

    workings = _load_workings(kwargs)
    workings = _merge_manual_into_workings(file_action, workings)

    base = workings.get(mode, {})
    if not isinstance(base, dict):
        return ""

    actions = base.get("actions", [])
    if not isinstance(actions, list) or not actions:
        return ""

    directory_absolute = kwargs.get("directory_absolute", "")
    if not _file_tests_allow_run(base, directory_absolute):
        return ""

    discovered = kwargs.get("_discovered_actions")
    if discovered is None:
        discovered = build_action_lookup(actions_root=kwargs.get("actions_root"))
    print(f"Running with actions: {len(actions)}")

    run_kwargs = copy.deepcopy(kwargs)
    run_kwargs["actions"] = actions
    run_kwargs["_discovered_actions"] = discovered

    result: Any = ""
    retries_until_complete = max(0, _as_int(base.get("retries_until_complete", 0), 0))
    attempts = retries_until_complete + 1
    for attempt in range(attempts):
        if attempt > 0:
            print(f"Retrying action block for missing file_test output(s): attempt {attempt + 1} of {attempts}")
        for action_cfg in actions:
            run_kwargs["action"] = action_cfg
            result = run_action(**run_kwargs)
            if result in ("exit", "exit_no_tab"):
                print("Exiting due to exit command.")
                break
            if isinstance(result, dict):
                _persist_result_dict(file_action=file_action, result=result)
        if result in ("exit", "exit_no_tab"):
            break
        if _file_tests_complete(base, directory_absolute):
            break
        if attempt < retries_until_complete:
            print("Action block did not complete all file_test output(s).")
    return result

def run_single_action(**kwargs: Any) -> Any:
    action = kwargs.get("action", {}) or {}
    if not isinstance(action, dict):
        print("Error: action must be a dict")
        return ""
    result = run_action(**kwargs)
    if isinstance(result, dict):
        file_action = kwargs.get("file_action", "working.yaml")
        _persist_result_dict(file_action=file_action, result=result)
    return result

def main(**kwargs: Any) -> None:
    mode_list = _normalize_mode_list(kwargs.get("mode", "all"))
    folder = kwargs.get("folder", "")
    directory = kwargs.get("directory", "")
    file_action_default = kwargs.get("file_action", "working.yaml")
    if kwargs.get("filter", "") and (kwargs.get("filter_all") or kwargs.get("filter_or")):
        print("Error: Too many filters specified. Use only one of filter/filter_all/filter_or.")
        return

    if folder:
        run_folder(**kwargs)
        return

    if directory:
        for entry in sorted(os.listdir(directory)):
            if not _directory_matches_filters(entry, kwargs):
                continue
            run_dir = os.path.join(directory, entry)
            if not os.path.isdir(run_dir):
                continue

            file_action = os.path.join(run_dir, "working.yaml")
            run_kwargs = copy.deepcopy(kwargs)
            run_kwargs["directory"] = run_dir
            run_kwargs["directory_absolute"] = os.path.abspath(run_dir)
            run_kwargs["file_action"] = file_action
            run_kwargs["workings"] = _load_yaml_file(file_action)

            modes_list = _expand_numbered_variants(mode_list)
            for mode_name in modes_list:
                run_kwargs["mode"] = mode_name
                run_single(**run_kwargs)
        return

    run_kwargs = copy.deepcopy(kwargs)
    run_kwargs["file_action"] = file_action_default
    if "workings" not in run_kwargs:
        run_kwargs["workings"] = _load_yaml_file(file_action_default)
    for mode_name in mode_list:
        run_kwargs["mode"] = mode_name
        run_single(**run_kwargs)


###############################################################################
# Documentation export
###############################################################################


def export_actions_documentation_json(output_file: str, actions_root: str | None = None) -> None:
    docs = get_all_actions_documentation(actions_root=actions_root)
    payload = {
        "actions": docs,
        "generated_date": str(datetime.date.today()),
        "total_actions": len(docs),
    }
    with open(output_file, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
    print(f"Action documentation written to {output_file}")


def export_actions_documentation_html(
    template_file: str,
    output_file: str,
    actions_root: str | None = None,
) -> None:
    docs = get_all_actions_documentation(actions_root=actions_root)
    payload = {
        "actions": docs,
        "generated_date": str(datetime.date.today()),
        "total_actions": len(docs),
    }
    with open(template_file, "r", encoding="utf-8") as file:
        template_content = file.read()

    if "<!-- DOCUMENTATION_DATA_PLACEHOLDER -->" in template_content:
        js = (
            "const DOCUMENTATION_DATA = "
            + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            + ";\nif (typeof window.DOCUMENTATION_DATA_READY === 'function') "
            + "window.DOCUMENTATION_DATA_READY();\n"
        )
        html = template_content.replace("<!-- DOCUMENTATION_DATA_PLACEHOLDER -->", js)
    elif "{{documentation_JSON}}" in template_content:
        html = template_content.replace("{{documentation_JSON}}", json.dumps(docs, indent=2))
    else:
        js = (
            "<script>\nconst DOCUMENTATION_DATA = "
            + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            + ";\n</script>\n"
        )
        html = template_content.replace("<script>", js + "<script>", 1)

    with open(output_file, "w", encoding="utf-8") as file:
        file.write(html)
    print(f"Action documentation HTML written to {output_file}")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RoboClick modular action runner")
    parser.add_argument("--mode", default="all")
    parser.add_argument("--file-action", default="working.yaml")
    parser.add_argument("--folder", default="")
    parser.add_argument("--directory", default="")
    parser.add_argument("--recursive-threaded", action="store_true")
    parser.add_argument("--threaded-workers", type=int, default=6)
    parser.add_argument("--threaded-subprocess-actions", action="store_true")
    parser.add_argument("--actions-root", default=None)
    parser.add_argument("--docs-json", default="")
    parser.add_argument("--docs-html-template", default="")
    parser.add_argument("--docs-html-output", default="")
    parser.add_argument("--docs-only", action="store_true")
    return parser


def cli() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    if args.docs_json:
        export_actions_documentation_json(args.docs_json, actions_root=args.actions_root)
    if args.docs_html_template and args.docs_html_output:
        export_actions_documentation_html(
            template_file=args.docs_html_template,
            output_file=args.docs_html_output,
            actions_root=args.actions_root,
        )
    if not args.docs_only:
        main(
            mode=args.mode,
            file_action=args.file_action,
            folder=args.folder,
            directory=args.directory,
            recursive_threaded=args.recursive_threaded,
            threaded_workers=args.threaded_workers,
            threaded_subprocess_actions=args.threaded_subprocess_actions,
            actions_root=args.actions_root,
        )


##### utility stuff
def get_url(part):
    file_url = "url.yaml"
    directory = get_directory(part)
    file_url = f"{directory}\\url.yaml"
    import yaml
    url = ""
    if os.path.isfile(file_url):
        #url may have multiple lines use the last one
        with open(file_url, 'r', encoding='utf-8') as f:
            try:
                data_list = yaml.safe_load(f)
                url = data_list[len(data_list)-1]
            except Exception as e:
                print(f"Error reading url from {file_url}: {e}")
    return url
                
            

def get_directory(part):
    
    #type, size, color, description_main, description_extra
    tags = []
    #taxonomy_1 to 15
    for i in range(1, 16):
        tags.append(f"taxonomy_{i}")

    directory = ""

    for tag in tags:
        if tag in part:
            if part[tag] != "":
                if directory != "":
                    directory += "_"
                extra = part[tag]
                if extra == None:
                    extra = ""
                directory += str(extra)
    #make lowercase and replace spaces with underscores and slashes with underscores
    directory = directory.replace(" ", "_")
    directory = directory.replace("/", "_")
    directory = directory.replace("\\", "_")
    directory = directory.replace("__", "_")
    directory = directory.replace(")", "_")
    directory = directory.replace("(", "_")
    directory = directory.lower()

    directory = f"parts\\{directory}"

    return directory

def add_action(**kwargs):
    part = kwargs.get("part", {})
    actions = kwargs.get("actions", {})
    action_name = kwargs.get("action_name", "")
    action_type = kwargs.get("action_type", "ai")
    file_test = kwargs.get("file_test", "tag")
    retries_until_complete = kwargs.get("retries_until_complete", 0)

    #get largest existing index for this action_type
    count = 1
    if True:
        while True:
            test_action_name = f"oomlout_{action_type}_roboclick_{count}"
            test_action = part.get(test_action_name, {})
            if test_action != {}:
                count += 1
            else:                
                break

        action_id = f"oomlout_{action_type}_roboclick_{count}"

    #filetest_name and add create file at end if tag
    if True:
        if file_test =="tag":
            file_test = f"aaaa_{action_name}_tag_done.txt"
            action = {}
            action["command"] = "create_text_file"
            action["file_name"] = file_test
            action["content"] = f"Tag for {action_name} completed."
            actions.append(action)

    base = {}
    base["actions"] = actions
    base["file_test"] = file_test
    base["retries_until_complete"] = max(0, _as_int(retries_until_complete, 0))
    part[action_id] = base

################################ utility routines
def ai_query_from_prompts(part,part2,prompts,mode_ai_wait, count, file_destination_yaml="", action_name=""):
    count += 1            
    action_type = "ai" # "corel"
    action_default_name = f"create_prompt_verbose"
    if action_name == "":         
        action_name = f"{action_default_name}" 

    #default to a tag but if an image is created use that instead
    file_test = "tag" #(creates a tag at the end)

    actions = []
    
    ### action 1
    # new chat
    action = {}
    #- command: 'new_chat'
    action["command"] = "new_chat"  
    action["description"] = f"{action_name}"
    actions.append(action)
    
    ### action 2
    
    
    
    for prompt in prompts:                
        file_name_image = prompt.get("file_name_image", "")
        folder_name = prompt.get("folder_name", "")
        prompt.pop("file_name_image", None)
        text = prompt.get("text", "")
        
        if not (text == "" and folder_name == ""):
            action = {}
            action.update(copy.deepcopy(prompt))
            action["command"] = "ai_query"
            action["mode_ai_wait"] = mode_ai_wait
            actions.append(action)
        else:
            pass
    
        if file_name_image != "":
            action = {}
            #- command: 'save_image'
            action["command"] = "save_image_generated"  
            action["file_name"] = file_name_image
            action["mode_ai_wait"] = mode_ai_wait
            actions.append(action)
            #if image is created use that rather than tag
            file_test = file_name_image

    if file_destination_yaml != "":
        action = {}
        action["text"] = "if the above output is yaml please put it in the reply, if it is not please summarize the above information in a yaml format and only return the yaml without any other text. The yaml should be in the format of a dictionary with keys and values. The keys should be descriptive of the information they contain. The values should be the information itself. Please make sure the yaml is between two &&&tag for copy&&& strings"
        action["command"] = "ai_query"
        action["mode_ai_wait"] = mode_ai_wait
        actions.append(action)
        
        action = {}
        action["command"] = "ai_save_text"
        action["file_name_clip"] = file_destination_yaml.replace(".yaml", "_raw.yaml")
        action["file_name_full"] = file_destination_yaml.replace(".yaml", "_full.txt")
        actions.append(action)
        if action_name == "" or action_name == action_default_name:            
            file_test = file_destination_yaml

        action = {}
        action["command"] = "ai_fix_yaml_copy_paste"
        new_item_name = part2.get("new_item_name", "")
        if new_item_name != "":
            action["new_item_name"] = new_item_name
        remove_top_level = part2.get("remove_top_level", "")
        if remove_top_level != "":
            action["remove_top_level"] = remove_top_level
        search_and_replace = part2.get("search_and_replace", {})
        if search_and_replace != {}:
            action["search_and_replace"] = search_and_replace
        action["file_source"] = file_destination_yaml.replace(".yaml", "_raw.yaml")
        action["file_destination"] = file_destination_yaml
        actions.append(action)

    #close tab
    action = {}
    action["command"] = "close_tab"
    actions.append(action)

    add_action(part=part, action_type=action_type, action_name=action_name, actions=actions, file_test=file_test)  
    return count       

def ai_action_from_folder(**kwargs):
    part = kwargs.get("part", {})
    part2 = kwargs.get("part2", {})
    folder_name = part2.get("folder_name", "")    
    count = kwargs.get("count", 1)    
    
    

    for i in range(1,101):
        file_name = f"{folder_name}\\working_{i}.yaml"
        if os.path.isfile(file_name):
            #load yaml file
            with open(file_name, 'r', encoding='utf-8') as f:
                try:
                    data = yaml.safe_load(f)
                    prompts = data.get("prompts", [])
                    if prompts:
                        count = ai_query_from_prompts(part,prompts,mode_ai_wait, count)
                except Exception as e:
                    print(f"Error reading yaml from {file_name}: {e}")
            part2.update(data)
        else:
            print(f"No file found at {file_name}, stopping folder processing.")
            robo_delay(delay=10)


        ####f string format
        #f string update part_2 variables and all actions in part2
        for key, value in part2.items():
            if isinstance(value, str):
                part2[key] = value.format(**part2)
        #for actions
        actions_working = part2.get("actions", [])
        for action in actions_working:
            for key, value in action.items():
                if isinstance(value, str):
                    try:
                        action[key] = value.format(**part2)
                    except Exception as e:
                        print(f"Error formatting action key {key} with value {value}: {e}")
                        action[key] = ""
                        pass

        actions = part2.get("actions", [])       
        action_name = part2.get("action_name", f"{folder_name}_action")
        action_type = part2.get("action_type", "ai")
        file_test = part2.get("file_test", "tag")
        mode_ai_wait = part2.get("mode_ai_wait", "slow")


        file_test = part2.get("file_test", "tag")
        add_action(part=part, action_type=action_type, action_name=action_name, actions=actions, file_test=file_test)  
        count += 1
        return count  

if __name__ == "__main__":
    cli()


