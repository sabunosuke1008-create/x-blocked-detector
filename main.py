from xblocked.client import build_client
from xblocked.classify import single_check_by_screen_name
from xblocked.config import Config
from xblocked.runner import run_scan
from xblocked.report import diff_state, print_report, save_state
from xblocked.report import to_csv

from pathlib import Path


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Detect X accounts that block you (unofficial internal API).")
    parser.add_argument("--config", default="config.json", help="path to config.json")
    parser.add_argument(
        "--mode",
        choices=["scan", "check", "self", "login"],
        default="scan",
        help="scan: collect candidates then check (default). check: check specific handles. self: resolve your own id. login: password login -> write cookies to config (twikit).",
    )
    parser.add_argument("--handles", nargs="+", default=[], help="handles to check in check mode (without @)")
    parser.add_argument("--limit", type=int, default=None, help="max candidates to check")
    parser.add_argument("--output", default=None, help="override output CSV path")
    parser.add_argument(
        "--tid",
        choices=["auto", "off"],
        default=None,
        help="x-client-transaction-id handling. auto: try normal, fall back to no-header (default from config). off: never send it.",
    )
    parser.add_argument(
        "--refresh-ids",
        action="store_true",
        help="force refresh of GraphQL query ids from the live web client before running",
    )
    parser.add_argument(
        "--cache-ttl",
        type=int,
        default=None,
        metavar="HOURS",
        help="verdict cache TTL in hours; 0 disables caching (default from config, 168)",
    )
    parser.add_argument("--clear-cache", action="store_true", help="delete the state file before scanning")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="collection page budget across all sources (0=unlimited, default from config)",
    )
    parser.add_argument(
        "--time-budget",
        type=int,
        default=None,
        metavar="SECONDS",
        help="wall-clock cap on the collection phase (0=unlimited)",
    )
    parser.add_argument("--dry-run", action="store_true", help="validate config only, no network calls")
    args = parser.parse_args()

    cfg = Config.load(args.config)
    if args.tid:
        cfg.tid_mode = args.tid
    if args.cache_ttl is not None:
        cfg.cache_ttl_hours = args.cache_ttl
    if args.max_pages is not None:
        cfg.max_pages = args.max_pages
    if args.time_budget is not None:
        cfg.time_budget_seconds = args.time_budget
    if args.mode == "login":
        from pathlib import Path as _Path

        from xblocked.auth_login import LoginError, run_login

        print("[login] X password login via twikit (request-based, no browser).")
        print("[login] if X asks for a verification code, type it in this terminal.")
        print("[login] warning: password logins can trigger X risk control. "
              "prefer a secondary account for testing.")
        try:
            result = run_login(cfg)
        except LoginError as exc:
            print(f"[login] failed: {exc}")
            print("[login] manual fallback: log in at x.com in your normal browser, "
                  "then copy auth_token / ct0 into config.json.")
            return 1
        path = _Path(args.config)
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        raw.setdefault("cookies", {}).update(result.cookies)
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[login] success as @{result.screen_name or '?'} (id={result.user_id or '?'})")
        print(f"[login] cookies written to {args.config}")
        if cfg.me_screen_name:
            try:
                client = build_client(Config.load(args.config).cookies, cfg.tid_mode)
                uid, sn = resolve_me(client, cfg.me_screen_name, cfg.me_user_id)
                print(f"[login] validated via internal API: @{sn} ({uid})")
            except Exception as exc:  # noqa: BLE001
                print(f"[login] cookie validation skipped: {exc}")
        return 0

    problems = cfg.validate()
    if problems:
        print("config problems:")
        for p in problems:
            print(f"  - {p}")
        if args.dry_run:
            print("dry-run: config invalid -> aborting")
            return 2
        return 2

    if args.dry_run:
        print("dry-run: config OK")
        print(f"  me.screen_name = @{cfg.me_screen_name}")
        print(f"  limits = {cfg.limits}")
        print(f"  delay_seconds = {cfg.delay_seconds}")
        print(f"  batch_size = {cfg.batch_size}")
        print(f"  tid_mode = {cfg.tid_mode}")
        print(f"  output_csv = {args.output or cfg.output_csv}")
        return 0

    if args.mode == "self":
        client = build_client(cfg.cookies, cfg.tid_mode, refresh_query_ids=args.refresh_ids)
        from xblocked.client import resolve_me

        try:
            uid, sn = resolve_me(client, cfg.me_screen_name, cfg.me_user_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] could not resolve your account: {exc}")
            print("hint: check cookies (auth_token/ct0), or try --tid off / tid_mode=off")
            return 1
        print(f"your id = {uid}")
        print(f"your screen_name = @{sn}")
        return 0

    if args.mode == "check":
        client = build_client(cfg.cookies, cfg.tid_mode, refresh_query_ids=args.refresh_ids)
        for handle in args.handles:
            result = single_check_by_screen_name(client, handle.lstrip("@"))
            print(
                f"@{handle}: status={result.status} blocked_by={result.blocked_by} "
                f"name={result.name or ''} detail={result.detail} error={result.error}"
            )
        return 0

    if args.clear_cache and cfg.state_file:
        Path(cfg.state_file).unlink(missing_ok=True)
        print(f"state cleared: {cfg.state_file}")

    def _alert(o) -> None:
        from xblocked.model import STATUS_BLOCKED, STATUS_SMART_BLOCKED

        if o.result.status in (STATUS_BLOCKED, STATUS_SMART_BLOCKED):
            tag = o.result.status
            cached = " (cached)" if o.cached else ""
            print(f"  [!!] @{o.candidate.screen_name or '?'} {tag} sources={','.join(sorted(o.candidate.sources))}{cached}", flush=True)

    last = {"n": 0}

    def _tick(done: int, total: int) -> None:
        if done - last["n"] >= 25 or done == total:
            last["n"] = done
            print(f"[runner] progress {done}/{total}", flush=True)

    outcomes, skipped, me_id = run_scan(
        cfg,
        limit_override=args.limit,
        refresh_query_ids=args.refresh_ids,
        progress=_tick,
        on_result=_alert,
    )

    output = args.output or cfg.output_csv
    to_csv(outcomes, output)
    print_report(outcomes, skipped)
    print(f"csv written to {output}")

    if cfg.state_file:
        newly_blocked, newly_unblocked = diff_state(cfg.state_file, outcomes)
        save_state(cfg.state_file, outcomes)
        if newly_blocked:
            print(f"newly blocked (vs previous scan): {len(newly_blocked)}")
        if newly_unblocked:
            print(f"no longer blocked (vs previous scan): {len(newly_unblocked)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())