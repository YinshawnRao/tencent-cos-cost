"""CLI：collect / rank / serve。默认上一自然月（UTC+8）。"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from cos_cost.cache import FileCache
from cos_cost.clients.factory import build_bundle
from cos_cost.collect import collect
from cos_cost.formatters import collect_json, ranking_json, ranking_table
from cos_cost.monthutil import parse_month, previous_month_utc8
from cos_cost.ranking import build_ranking
from cos_cost.secrets import MissingCredentialsError, load_credentials, load_env_files, redact_secret_id

LOG = logging.getLogger("cos_cost")


def main(argv: list[str] | None = None) -> int:
    load_env_files()
    _configure_logging()
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        return _run_serve(args)
    if args.command == "export":
        return _run_export(args)

    try:
        month = parse_month(args.month) if args.month else previous_month_utc8()
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    cache_dir = Path(args.cache_dir).expanduser()
    cache = FileCache(cache_dir)

    creds = None
    if not args.mock:
        try:
            creds = load_credentials()
        except MissingCredentialsError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 2
        LOG.info("使用 SecretId=%s（SecretKey 已隐藏）", redact_secret_id(creds.secret_id))

    bundle = build_bundle(mock=args.mock, creds=creds)
    snapshot = collect(bundle, month, cache, force=args.force, creds=creds)

    if args.command == "collect":
        hits = ", ".join(snapshot.cache_hits) if snapshot.cache_hits else "无（已回源）"
        if args.json:
            payload = snapshot.to_dict()
            print(collect_json(payload))
        else:
            print(f"已采集 账期 {snapshot.month}  账户 {snapshot.account_key}")
            print(f"桶 {len(snapshot.buckets)}  账单行 {len(snapshot.bill_resources)}")
            ready = snapshot.bill_summary.ready if snapshot.bill_summary else None
            print(f"Ready={ready if ready is not None else 'n/a'}")
            print(f"缓存命中: {hits}")
            if snapshot.notes:
                print("说明:")
                for note in snapshot.notes:
                    print(f"  · {note}")
        return 0

    ranking = build_ranking(snapshot)
    if args.json:
        print(ranking_json(ranking))
    else:
        print(ranking_table(ranking), end="")
    return 0


def _run_export(args: argparse.Namespace) -> int:
    try:
        month = parse_month(args.month) if args.month else previous_month_utc8()
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    if not args.pdf and not args.xlsx:
        print("错误: 请指定 --pdf 和/或 --xlsx 输出路径", file=sys.stderr)
        return 2

    cache_dir = Path(args.cache_dir).expanduser()
    cache = FileCache(cache_dir)
    creds = None
    if not args.mock:
        try:
            creds = load_credentials()
        except MissingCredentialsError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 2
        LOG.info("使用 SecretId=%s（SecretKey 已隐藏）", redact_secret_id(creds.secret_id))

    from cos_cost.ext.export import ReportExporter, build_report_payload
    from cos_cost.web.service import DashboardService

    bundle = build_bundle(mock=args.mock, creds=creds)
    snapshot = collect(bundle, month, cache, force=args.force, creds=creds)
    ranking = build_ranking(snapshot)
    service = DashboardService(
        mock=args.mock, cache_dir=cache_dir, creds=creds, force=args.force
    )
    account = service.account(month)
    payload = build_report_payload(
        snapshot,
        ranking,
        composition={item["key"]: item["value"] for item in account["composition"]["items"]},
        trend=account.get("trend"),
    )
    exporter = ReportExporter()
    if args.pdf:
        dest = Path(args.pdf).expanduser()
        exporter.write_pdf(payload, dest)
        print(f"已写 PDF {dest}")
    if args.xlsx:
        dest = Path(args.xlsx).expanduser()
        exporter.write_xlsx(payload, dest)
        print(f"已写 Excel {dest}")
    return 0


def _run_serve(args: argparse.Namespace) -> int:
    try:
        if args.month:
            parse_month(args.month)
    except ValueError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2

    creds = None
    if not args.mock:
        try:
            creds = load_credentials()
        except MissingCredentialsError as exc:
            print(f"错误: {exc}", file=sys.stderr)
            return 2
        LOG.info("使用 SecretId=%s（SecretKey 已隐藏）", redact_secret_id(creds.secret_id))

    from cos_cost.web.app import create_app
    from cos_cost.web.service import DashboardService

    service = DashboardService(
        mock=args.mock,
        cache_dir=Path(args.cache_dir).expanduser(),
        creds=creds,
        force=args.force,
    )
    app = create_app(service)
    import uvicorn

    host = args.host
    port = args.port
    LOG.info("打开 http://%s:%s/  （mock=%s）", host, port, args.mock)
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cos_cost",
        description="腾讯云 COS 成本分析 Agent（M1 CLI + M2 看板 + M3 规则/导出/问答）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    collect_p = sub.add_parser("collect", help="拉取并写入缓存")
    _add_common(collect_p)

    rank_p = sub.add_parser("rank", help="打印 C5 桶排行（人类表或 --json）")
    _add_common(rank_p)

    serve_p = sub.add_parser("serve", help="启动账号全局 / 桶页（只读 Web）")
    _add_common(serve_p)
    serve_p.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    serve_p.add_argument("--port", type=int, default=18765, help="端口，默认 18765")

    export_p = sub.add_parser("export", help="导出一页 PDF / 五表 Excel")
    _add_common(export_p)
    export_p.add_argument("--pdf", help="PDF 输出路径")
    export_p.add_argument("--xlsx", help="Excel 输出路径")
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--month", help="账期 YYYY-MM，默认 UTC+8 上一自然月")
    parser.add_argument("--mock", action="store_true", help="使用内置 fixture，不访问网络、不需要 AK/SK")
    parser.add_argument(
        "--cache-dir",
        default=os.environ.get("COS_CACHE_DIR") or "cache",
        help="缓存目录（默认 ./cache 或 COS_CACHE_DIR）",
    )
    parser.add_argument("--force", action="store_true", help="忽略缓存，强制回源")
    parser.add_argument("--json", action="store_true", help="JSON 输出")


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("qcloud_cos").setLevel(logging.WARNING)
    logging.getLogger("tencentcloud").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


if __name__ == "__main__":
    raise SystemExit(main())
