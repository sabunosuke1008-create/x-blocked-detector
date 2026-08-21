from xblocked.client import build_client
from xblocked.classify import single_check_by_screen_name
from xblocked.config import Config
from xblocked.runner import run_scan
from xblocked.report import diff_state, print_report, save_state
from xblocked.report import to_csv


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Detect X accounts that block you (unofficial internal API).")
    parser.add_argument("--config", default="config.json", help="path to config.json")
    parser.add_argument(
        "--mode",
        choices=["scan", "check", "self"],
        default="scan",
        help="scan: collect candidates then check (default). check: check specific handles. self: resolve your own id.",
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
    parser.add_argument("--dry-run", action="store_true", help="validate config only, no network calls")
    args = parser.parse_args()

    cfg = Config.load(args.config)
    if args.tid:
        cfg.tid_mode = args.tid
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

    outcomes, skipped, me_id = run_scan(cfg, limit_override=args.limit, refresh_query_ids=args.refresh_ids)

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