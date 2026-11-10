from __future__ import annotations

import argparse
from pathlib import Path

from .storage import Store


def main():
    parser = argparse.ArgumentParser(description="The Allotment publishing tools")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create the two local user Git repositories")
    serve = commands.add_parser("serve", help="Start the local Flask Studio")
    serve.add_argument("--port", type=int, help="Studio port; saves this value as the default for future launches")
    serve.add_argument('--garden',help='Bind Studio to namespace/user')
    serve.add_argument('--choose-garden',action='store_true',help='Choose a garden at this launch')
    worker = commands.add_parser("worker", help="Run queued publishing actions once, then exit")
    worker.add_argument("--namespace")
    worker.add_argument("--user")
    worker.add_argument("--job")
    worker.add_argument("--dry-run", action="store_true", help="Validate only; no posting, writes or browser")
    worker.add_argument("--pull", action="store_true", help="Fast-forward clean user repos from origin before running")
    immediate=commands.add_parser('run-job',help='Run one queued job with countdown and Stop controls')
    for field in ('namespace','user','run_id'):immediate.add_argument(field)
    migrate = commands.add_parser("import-legacy")
    migrate.add_argument("source", type=Path)
    migrate.add_argument("--namespace", default="the_allotment")
    migrate.add_argument("--user", default="oomlout_main")
    migrate.add_argument("--limit", type=int, default=10)
    build = commands.add_parser("build")
    build.add_argument("namespace")
    build.add_argument("user")
    sync = commands.add_parser("sync", help="Commit and sync public data and queue via origin/main")
    sync.add_argument("namespace")
    sync.add_argument("user")
    preset = commands.add_parser("apply-preset", help="Populate account addresses without approving or posting")
    preset.add_argument("preset", choices=["oomlout"])
    preset.add_argument("--namespace")
    preset.add_argument("--user")
    prepare = commands.add_parser("prepare", help="Test a recipe in the browser, stopping BEFORE the Post click")
    prepare.add_argument("namespace")
    prepare.add_argument("user")
    prepare.add_argument("job")
    prepare.add_argument("destination")
    browser = commands.add_parser("browser", help="Open a normal Chrome tab for manual sign-in")
    browser.add_argument("--url", default="https://bsky.app")
    platform_test = commands.add_parser("platform-test", help="Internal single-use test process launched by Studio")
    platform_test.add_argument("namespace")
    platform_test.add_argument("user")
    platform_test.add_argument("test_id")
    platform_prepare = commands.add_parser("platform-prepare", help="Internal preparation-only process; has no send operation")
    for field in ("namespace", "user", "test_id"):
        platform_prepare.add_argument(field)
    receipts_export = commands.add_parser("receipts-export", help="Export job receipts, including private links, for a manual return handoff")
    for field in ("namespace", "user", "job_id", "output"):
        receipts_export.add_argument(field)
    args = parser.parse_args()
    store = Store(args.root)
    try:
        if args.command == "init":
            store.bootstrap()
            print(f"Gardens ready in {store.garden}")
        elif args.command == "serve":
            from .app import create_app
            from .config import save_studio_settings, studio_settings
            port = args.port if args.port is not None else studio_settings(store)["port"]
            save_studio_settings(store, port)
            if args.choose_garden:
                from .storage import read_yaml,write_yaml
                settings=studio_settings(store);settings.pop('garden',None);write_yaml(store.local/'studio.yaml',settings)
            print(f"The Allotment Studio: http://127.0.0.1:{port}")
            create_app(store.root,garden=args.garden).run(host="127.0.0.1", port=port, debug=False)
        elif args.command == 'run-job':
            from .job_runs import execute_run
            execute_run(store,args.namespace,args.user,args.run_id)
        elif args.command == "worker":
            from .publication import run_worker
            results = run_worker(store, args.namespace, args.user, args.job, args.dry_run, args.pull)
            if not results:
                print("No pending jobs. Exported jobs run only in their standalone bundle.")
            if any(a["status"] in {"failed", "uncertain"} for j in results for a in j["actions"].values()):
                return 1
        elif args.command == "import-legacy":
            if args.limit < 1:
                raise ValueError("Limit must be positive.")
            print("Imported:", ", ".join(store.import_legacy(args.source, args.namespace, args.user, args.limit)) or "already imported")
        elif args.command == "build":
            from .website import build_site
            with store.garden_lock(args.namespace, args.user):
                print(build_site(store, args.namespace, args.user))
        elif args.command == "sync":
            store.sync(args.namespace, args.user)
            print("Public garden and queue synced.")
        elif args.command == "apply-preset":
            from .presets import apply_preset
            print("Configured:", ", ".join(apply_preset(store, args.preset, args.namespace, args.user)))
        elif args.command == "prepare":
            from .browser import browser_context, run_recipe
            from .storage import read_yaml
            job = read_yaml(store.job_path(args.namespace, args.user, args.job))
            if not job or args.destination not in store.plugins():
                raise ValueError("Job or destination not found.")
            account = store.settings(args.namespace, args.user).get("destinations", {}).get(args.destination, {})
            with browser_context(store,store.local / "evidence" / args.job / "prepare") as desktop:
                print(run_recipe(desktop,store.plugins()[args.destination],account,job["post"],
                                 store.repo(args.namespace,args.user),lambda:None,prepare=True))
            print("Composer prepared. No Post click was made. The tab remains open.")
        elif args.command == "browser":
            from .chrome import launch_chrome
            launch_chrome(args.url)
            print("Opened a normal Chrome tab. Sign in manually before starting roboclick.")
        elif args.command == "platform-prepare":
            from .preparation import run_preparation
            result = run_preparation(store,args.namespace,args.user,args.test_id)
            return 0 if result["status"] in {"prepared","inspected"} else 1
        elif args.command == "platform-test":
            from .testlab import run_platform_test
            result = run_platform_test(store, args.namespace, args.user, args.test_id)
            return 0 if result["status"] in {"succeeded", "prepared"} else 1
        elif args.command == "receipts-export":
            from .storage import write_yaml
            job = next((j for j in store.jobs(args.namespace, args.user) if j["id"] == args.job_id), None)
            if not job:
                raise ValueError("Job not found.")
            write_yaml(Path(args.output).resolve(), job)
    except (ValueError, OSError) as exc:
        print(f"Allotment: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
