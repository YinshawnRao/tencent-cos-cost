"""把 M1 collect/rank 组装成账号页 / 桶页 JSON。浏览器不直连腾讯云。"""

from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import threading

from cos_cost.billing_items import (
    CATEGORY_COLORS,
    CATEGORY_LABELS,
    STORAGE_CLASS_COLORS,
    STORAGE_CLASS_LABELS,
    compose_categories,
)
from cos_cost.cache import FileCache
from cos_cost.clients.errors import CollectCancelled
from cos_cost.clients.factory import build_bundle
from cos_cost.clients.mock import load_fixture
from cos_cost.clients.protocols import ClientBundle
from cos_cost.collect import CollectProgress, attach_cancel, collect, load_bucket_configs
from cos_cost.ext.config_lights import SnapshotConfigLights
from cos_cost.ext.opportunity import RuleEngine
from cos_cost.ext.placeholders import COLUMN_LABELS
from cos_cost.formatters import money_text, pct_text, ready_label, volume_text
from cos_cost.local_creds import (
    clear_local_creds,
    default_local_creds_path,
    load_local_creds,
    save_local_creds,
)
from cos_cost.models import BucketInfo, CollectSnapshot, RankingResult, RankingRow
from cos_cost.monthutil import parse_month, previous_month_utc8, shift_month
from cos_cost.ranking import build_ranking
from cos_cost.secrets import (
    Credentials,
    classify_collect_error,
    mask_secret_id,
    sanitize_error_text,
)

MB = 1_000_000.0


class DashboardService:
    def __init__(
        self,
        *,
        mock: bool,
        cache_dir: Path,
        creds: Credentials | None = None,
        force: bool = False,
        creds_path: Path | None = None,
    ) -> None:
        self.cache = FileCache(cache_dir)
        self.force = force
        self.creds_path = Path(creds_path) if creds_path else default_local_creds_path()
        self.preferred_month: str | None = None
        self.model_api_key: str | None = None
        self.last_collect_error: str | None = None
        self.last_collect_warning: str | None = None
        self._job_progress = CollectProgress()
        self._job_cancel = threading.Event()
        self._job_thread: threading.Thread | None = None
        self._job_lock = threading.Lock()
        if creds is None and not mock:
            stored = load_local_creds(self.creds_path)
            if stored:
                creds = stored.credentials
                mock = False
                self.preferred_month = stored.month
                self.model_api_key = stored.model_api_key
        self.mock = mock
        self.creds = creds
        self.bundle: ClientBundle = build_bundle(mock=mock, creds=creds)
        self.fixture = load_fixture() if mock else {}

    def default_month(self) -> str:
        if self.preferred_month:
            try:
                return parse_month(self.preferred_month)
            except ValueError:
                pass
        return previous_month_utc8()

    def settings_status(self) -> dict[str, Any]:
        err = self.last_collect_error
        warn = self.last_collect_warning
        if self.creds:
            err = (
                sanitize_error_text(
                    err, secret_key=self.creds.secret_key, secret_id=self.creds.secret_id
                )
                if err
                else None
            )
            warn = (
                sanitize_error_text(
                    warn, secret_key=self.creds.secret_key, secret_id=self.creds.secret_id
                )
                if warn
                else None
            )
        return {
            "mode": "live" if not self.mock else "mock",
            "secret_id_masked": mask_secret_id(self.creds.secret_id) if self.creds else None,
            "month": self.default_month(),
            "saved": bool(self.creds) and not self.mock,
            "model_key_saved": bool(self.model_api_key),
            "last_collect_error": err,
            "last_collect_warning": warn,
            "local_only": True,
            "job": self.job_status(),
        }

    def save_credentials(
        self,
        *,
        secret_id: str,
        secret_key: str,
        token: str | None = None,
        month: str | None = None,
        model_api_key: str | None = None,
    ) -> dict[str, Any]:
        sid = (secret_id or "").strip()
        skey = (secret_key or "").strip()
        if not skey and self.creds:
            skey = self.creds.secret_key
        if not sid or not skey:
            raise ValueError("需要 SecretId 与 SecretKey")
        tok = (token or "").strip() or (self.creds.token if self.creds else None)
        if month:
            month = parse_month(month)
        model_key = (model_api_key or "").strip() or self.model_api_key
        creds = Credentials(secret_id=sid, secret_key=skey, token=tok)
        save_local_creds(
            creds, self.creds_path, month=month, model_api_key=model_key
        )
        self.creds = creds
        self.mock = False
        self.preferred_month = month
        self.model_api_key = model_key
        self.fixture = {}
        self.last_collect_error = None
        self.last_collect_warning = None
        try:
            self.bundle = build_bundle(mock=False, creds=creds)
        except Exception as exc:  # noqa: BLE001 — 展示给本机 UI，不把密钥带回页面
            self.last_collect_error = classify_collect_error(
                sanitize_error_text(str(exc), secret_key=skey, secret_id=sid)
            )
            status = self.settings_status()
            status["status"] = "error"
            return status
        return self.start_collect_job(month or self.default_month(), force=True)

    def job_status(self) -> dict[str, Any]:
        snap = self._job_progress.snapshot()
        if self.last_collect_error and not snap.get("error"):
            snap["error"] = self.last_collect_error
        return snap

    def job_is_running(self) -> bool:
        thread = self._job_thread
        return thread is not None and thread.is_alive()

    def start_collect_job(self, month: str, *, force: bool = True) -> dict[str, Any]:
        with self._job_lock:
            if self.job_is_running():
                out = self.settings_status()
                out["status"] = "running"
                return out
            self._job_cancel = threading.Event()
            self._job_progress = CollectProgress()
            self._job_progress.update(status="running", phase="列桶", done=False, error=None)
            self.last_collect_error = None
            self.last_collect_warning = None
            thread = threading.Thread(
                target=self._collect_worker,
                args=(month, force),
                name="cos-collect",
                daemon=True,
            )
            self._job_thread = thread
            thread.start()
        out = self.settings_status()
        out["status"] = "running"
        return out

    def cancel_collect(self) -> dict[str, Any]:
        self._job_cancel.set()
        attach_cancel(self.bundle, self._job_cancel)
        self._job_progress.update(phase="正在停止")
        out = self.settings_status()
        out["status"] = "cancelling"
        return out

    def _collect_worker(self, month: str, force: bool) -> None:
        secret_key = self.creds.secret_key if self.creds else None
        secret_id = self.creds.secret_id if self.creds else None
        attach_cancel(self.bundle, self._job_cancel)
        try:
            snapshot = collect(
                self.bundle,
                month,
                self.cache,
                force=force,
                creds=self.creds,
                cancel=self._job_cancel,
                progress=self._job_progress,
            )
            if self._job_cancel.is_set():
                self._job_progress.update(
                    done=True, status="cancelled", phase="已停止", error=None
                )
                return
            self._note_collect_outcome(snapshot, secret_key=secret_key, secret_id=secret_id)
            err = self.last_collect_error
            self._job_progress.update(
                done=True,
                status="error" if err else "done",
                phase="失败" if err else "完成",
                error=err,
            )
        except CollectCancelled:
            self._job_progress.update(done=True, status="cancelled", phase="已停止", error=None)
        except Exception as exc:  # noqa: BLE001
            err = classify_collect_error(
                sanitize_error_text(str(exc), secret_key=secret_key, secret_id=secret_id)
            )
            self.last_collect_error = err
            self._job_progress.update(done=True, status="error", phase="失败", error=err)

    def use_mock(self) -> dict[str, Any]:
        self.cancel_collect()
        clear_local_creds(self.creds_path)
        self.creds = None
        self.mock = True
        self.model_api_key = None
        self.preferred_month = None
        self.last_collect_error = None
        self.last_collect_warning = None
        self.fixture = load_fixture()
        self.bundle = build_bundle(mock=True)
        self._job_progress = CollectProgress()
        self._job_progress.update(done=True, status="idle", phase="")
        return self.settings_status()

    def _snapshot(self, month: str, *, force: bool | None = None) -> CollectSnapshot:
        if (
            self.job_is_running()
            and threading.current_thread() is not self._job_thread
        ):
            return _empty_snapshot(month, mock=self.mock)
        use_force = self.force if force is None else force
        secret_key = self.creds.secret_key if self.creds else None
        secret_id = self.creds.secret_id if self.creds else None
        try:
            snapshot = collect(
                self.bundle, month, self.cache, force=use_force, creds=self.creds
            )
        except CollectCancelled:
            return _empty_snapshot(month, mock=self.mock)
        except Exception as exc:  # noqa: BLE001 — 鉴权/网络错误展示在本机 UI
            self.last_collect_error = classify_collect_error(
                sanitize_error_text(str(exc), secret_key=secret_key, secret_id=secret_id)
            )
            return _empty_snapshot(month, mock=self.mock)
        self._note_collect_outcome(snapshot, secret_key=secret_key, secret_id=secret_id)
        return snapshot

    def _note_collect_outcome(
        self,
        snapshot: CollectSnapshot,
        *,
        secret_key: str | None,
        secret_id: str | None,
    ) -> None:
        if self.mock:
            self.last_collect_error = None
            self.last_collect_warning = None
            return
        notes = " ".join(snapshot.notes or [])
        compact = notes.lower().replace(" ", "")
        if snapshot.bill_summary is None and not snapshot.buckets:
            self.last_collect_error = classify_collect_error(
                sanitize_error_text(
                    notes or "拉取失败：桶列表与账单均为空。",
                    secret_key=secret_key,
                    secret_id=secret_id,
                )
            )
        elif any(
            n in compact
            for n in (
                "authfailure",
                "invalidsecret",
                "invalidaccesskey",
                "signaturedoesnotmatch",
            )
        ):
            self.last_collect_error = "鉴权失败：SecretId / SecretKey 不正确。"
        elif "权限" in notes:
            self.last_collect_error = classify_collect_error(
                sanitize_error_text(notes, secret_key=secret_key, secret_id=secret_id)
            )
        else:
            self.last_collect_error = None
        if snapshot.bill_summary is not None and snapshot.bill_summary.ready != 1:
            self.last_collect_warning = "账单 Ready=0，当前为暂估。"
        else:
            self.last_collect_warning = None

    def account(
        self,
        month: str | None = None,
        *,
        region: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        month = parse_month(month) if month else self.default_month()
        snapshot = self._snapshot(month)
        engine, lights = _engines(snapshot)
        ranking = build_ranking(snapshot, opportunity=engine, lights=lights)
        regions = sorted({r.region for r in ranking.rows if r.region})
        rows = _filter_rows(ranking.rows, region=region, q=q)
        filtered = bool(region or q)
        kpis = _account_kpis(ranking, rows, filtered=filtered)
        return {
            "page": "account",
            "month": month,
            "account_key": snapshot.account_key,
            "mock": self.mock,
            "ready": ranking.ready,
            "estimated": ranking.estimated,
            "ready_label": ready_label(ranking.ready, ranking.estimated),
            "banner": _account_banner(ranking, snapshot),
            "filters": {
                "month": month,
                "region": region or "",
                "q": q or "",
                "regions": regions,
            },
            "kpis": kpis,
            "trend": self._trend(month),
            "treemap": _treemap(rows),
            "composition": _composition(snapshot, self.fixture if self.mock else {}),
            "storage_classes": _storage_classes(snapshot),
            "ranking": [_row_dict(r) for r in rows[:20]],
            "opportunities": _group_opportunities(engine.list_all()),
            "notes": ranking.notes,
            "permission": {
                "bill": snapshot.bill_summary is not None or bool(snapshot.bill_resources),
                "monitor": snapshot.monitor is not None,
            },
            "settings": self.settings_status(),
        }

    def bucket(self, bucket: str, month: str | None = None) -> dict[str, Any]:
        month = parse_month(month) if month else self.default_month()
        snapshot = self._snapshot(month)
        if snapshot.config is None or bucket not in snapshot.config.by_bucket:
            try:
                listed = snapshot.buckets or [BucketInfo(name=bucket, region=None)]
                extra = load_bucket_configs(
                    self.bundle,
                    self.cache,
                    snapshot.account_key,
                    listed,
                    [bucket],
                    creds=self.creds,
                )
                if snapshot.config is None:
                    snapshot.config = extra
                else:
                    snapshot.config.by_bucket.update(extra.by_bucket)
                    if extra.extra_buckets:
                        snapshot.config.extra_buckets = extra.extra_buckets
            except CollectCancelled:
                pass
            except Exception:  # noqa: BLE001 — 桶页配置失败不阻断排行
                pass
        engine, lights = _engines(snapshot)
        ranking = build_ranking(snapshot, opportunity=engine, lights=lights)
        row = next((r for r in ranking.rows if r.bucket == bucket), None)
        meta = next((b for b in snapshot.buckets if b.name == bucket), None)
        extra = None
        if snapshot.config:
            extra = next((b for b in snapshot.config.extra_buckets if b.name == bucket), None)
        if row is None and meta is None and extra is None:
            return {"error": "bucket_not_found", "bucket": bucket, "month": month}
        metrics = None
        if snapshot.monitor:
            metrics = snapshot.monitor.by_bucket.get(bucket)
        cards = engine.cards_for(bucket)
        health = lights.health_for(bucket)
        created = meta.creation_date if meta else None
        az = "单 AZ"
        if metrics and (metrics.maz_std_storage_bytes or 0) > 0:
            az = "多 AZ"
        bucket_rows = [r for r in snapshot.bill_resources if r.resource_id == bucket]
        return {
            "page": "bucket",
            "month": month,
            "mock": self.mock,
            "ready": ranking.ready,
            "estimated": ranking.estimated,
            "ready_label": ready_label(ranking.ready, ranking.estimated),
            "bucket": bucket,
            "region": (row.region if row else None) or (meta.region if meta else None),
            "az": az,
            "created": _created_label(created),
            "kpis": {
                "payable": row.payable if row else None,
                "payable_text": _payable_text(row.payable if row else None, ranking),
                "capacity_bytes": row.capacity_bytes if row else None,
                "capacity_text": volume_text(row.capacity_bytes if row else None),
                "optimizable": (row.opportunity_amount if row else None),
                "optimizable_text": money_text(row.opportunity_amount if row else None),
            },
            "inventory_ready": False,
            "c7": _c7_storage(metrics),
            "c8": _bucket_composition(bucket_rows),
            "c6": _c6_daily(metrics, row.payable if row else None, month),
            "c9": _c9_traffic(metrics),
            "c10": _c10_requests(metrics),
            "health": health,
            "prefix_empty": True,
            "prefix_message": "清单未就绪，对象级建议不可用。不要对全桶 List Objects。桶级结论（碎片 / 生命周期）仍然有效。",
            "opportunities": cards,
            "notes": snapshot.notes,
        }

    def _trend(self, month: str) -> dict[str, Any]:
        months = [shift_month(month, delta) for delta in range(-5, 1)]
        series = {key: [] for key in CATEGORY_LABELS}
        payable: list[float | None] = []
        present = 0
        if self.last_collect_error and not self.mock:
            return {
                "months": months,
                "payable": [None] * len(months),
                "stacks": [
                    {
                        "key": key,
                        "label": CATEGORY_LABELS[key],
                        "color": CATEGORY_COLORS[key],
                        "values": [None] * len(months),
                    }
                    for key in CATEGORY_LABELS
                ],
                "present": 0,
                "note": self.last_collect_error,
            }
        for item in months:
            snap = self._snapshot(item)
            composed = _month_composition(snap, self.fixture if self.mock else {}, item)
            total = None
            if snap.bill_summary and snap.bill_summary.cos_real_total_cost is not None:
                total = snap.bill_summary.cos_real_total_cost
            elif composed:
                total = float(sum(composed.values()))
            if total is not None:
                present += 1
            payable.append(total)
            for key in CATEGORY_LABELS:
                series[key].append(composed.get(key))
        note = None
        if present <= 1:
            note = "仅有 1 个账期的缓存，完整 6 个月趋势请先 collect 更多月份。"
        return {
            "months": months,
            "payable": payable,
            "stacks": [
                {
                    "key": key,
                    "label": CATEGORY_LABELS[key],
                    "color": CATEGORY_COLORS[key],
                    "values": series[key],
                }
                for key in CATEGORY_LABELS
            ],
            "present": present,
            "note": note,
        }


    def report_payload(self, month: str | None = None) -> dict[str, Any]:
        from cos_cost.ext.export import build_report_payload

        month = parse_month(month) if month else self.default_month()
        snapshot = self._snapshot(month)
        engine, lights = _engines(snapshot)
        ranking = build_ranking(snapshot, opportunity=engine, lights=lights)
        account = self.account(month)
        owner = None
        for row in snapshot.bill_resources:
            if row.owner_uin:
                owner = row.owner_uin
                break
        return build_report_payload(
            snapshot,
            ranking,
            cards=engine.list_all(),
            composition={item["key"]: item["value"] for item in account["composition"]["items"]},
            trend=account.get("trend"),
            owner_uin=owner,
        )

    def ask(self, question: str, month: str | None = None) -> dict[str, Any]:
        from cos_cost.ext.chat import answer_question

        month = parse_month(month) if month else self.default_month()
        snapshot = self._snapshot(month)
        engine, lights = _engines(snapshot)
        ranking = build_ranking(snapshot, opportunity=engine, lights=lights)
        return answer_question(
            question, month=month, ranking=ranking, cards=engine.list_all()
        )


def _engines(snapshot: CollectSnapshot) -> tuple[RuleEngine, SnapshotConfigLights]:
    return RuleEngine(snapshot), SnapshotConfigLights(snapshot)


def _empty_snapshot(month: str, *, mock: bool) -> CollectSnapshot:
    return CollectSnapshot(
        account_key="unknown",
        month=month,
        buckets=[],
        bill_summary=None,
        prev_bill_summary=None,
        yoy_bill_summary=None,
        bill_resources=[],
        prev_bill_resources=[],
        monitor=None,
        notes=[],
        collected_at=datetime.now(timezone.utc).isoformat(),
        mock=mock,
    )


def _filter_rows(
    rows: list[RankingRow], *, region: str | None, q: str | None
) -> list[RankingRow]:
    out = rows
    if region:
        out = [r for r in out if r.region == region]
    if q:
        needle = q.strip().lower()
        out = [
            r
            for r in out
            if needle in r.bucket.lower()
            or any(needle in name.lower() for name in r.raw_resource_names)
        ]
    return out


def _account_kpis(
    ranking: RankingResult, rows: list[RankingRow], *, filtered: bool
) -> dict[str, Any]:
    k = ranking.kpis
    payable = k.cos_payable
    traffic = k.internet_traffic_bytes
    std = k.standard_storage_pct
    request_fee = k.request_fee
    if filtered:
        pays = [r.payable for r in rows if r.payable is not None]
        payable = float(sum(pays)) if pays else None
        traffics = [r.internet_traffic_bytes for r in rows if r.internet_traffic_bytes is not None]
        traffic = float(sum(traffics)) if traffics else None
        caps = [r.capacity_bytes for r in rows if r.capacity_bytes]
        stds = [
            (r.standard_pct / 100.0) * (r.capacity_bytes or 0)
            for r in rows
            if r.standard_pct is not None and r.capacity_bytes
        ]
        std = (100.0 * sum(stds) / sum(caps)) if caps and sum(caps) else None
    bill_ok = ranking.ready is not None or payable is not None
    return {
        "cos_payable": payable,
        "cos_payable_text": money_text(payable) if bill_ok else "无权限",
        "mom_text": pct_text(k.mom_pct if not filtered else None),
        "yoy_text": pct_text(k.yoy_pct if not filtered else None),
        "optimizable": k.optimizable_amount,
        "optimizable_text": money_text(k.optimizable_amount),
        "standard_pct": std,
        "standard_text": pct_text(std, signed=False),
        "internet_bytes": traffic,
        "internet_text": volume_text(traffic),
        "request_fee": request_fee if not filtered else None,
        "request_text": money_text(request_fee if not filtered else None),
        "ready_text": "已出账" if ranking.ready == 1 else ("暂估" if ranking.estimated else "无权限"),
        "coverage_text": f"{k.bucket_with_bill}/{k.bucket_listed}",
    }


def _account_banner(ranking: RankingResult, snapshot: CollectSnapshot) -> str:
    ready = ready_label(ranking.ready, ranking.estimated)
    listed = ranking.kpis.bucket_listed
    billed = ranking.kpis.bucket_with_bill
    extra = "金额来自费用中心，可优化来自规则引擎净节省（net≥50，不含 R05–R09 / R06 / 备份桶）。"
    if snapshot.bill_summary is None and not snapshot.bill_resources:
        extra = "账单无权限：应付显示为 — / 无权限，桶列表仍可用。"
    elif snapshot.monitor is None:
        extra = "监控无权限：容量 / 外网为空。金额仍来自费用中心。"
    return f"数据就绪: {ready} · 清单覆盖 {billed} / {listed} 桶 · {extra}"


def _treemap(rows: list[RankingRow]) -> list[dict[str, Any]]:
    items = []
    for row in rows:
        items.append(
            {
                "name": row.bucket,
                "short": row.bucket.rsplit("-", 1)[0] if "-" in row.bucket else row.bucket,
                "value": row.payable or 0,
                "payable_text": money_text(row.payable),
                "mom": row.mom_pct,
                "region": row.region,
            }
        )
    return items


def _composition(snapshot: CollectSnapshot, fixture: dict[str, Any]) -> dict[str, Any]:
    composed = _month_composition(snapshot, fixture, snapshot.month)
    total = sum(composed.values()) if composed else 0.0
    items = []
    for key, label in CATEGORY_LABELS.items():
        value = composed.get(key, 0.0)
        pct = (100.0 * value / total) if total else 0.0
        items.append(
            {
                "key": key,
                "label": label,
                "value": value,
                "pct": pct,
                "color": CATEGORY_COLORS[key],
                "text": f"{label} {pct:.0f}%",
            }
        )
    return {"total": total, "items": items, "mode": "doughnut"}


def _month_composition(
    snapshot: CollectSnapshot, fixture: dict[str, Any], month: str
) -> dict[str, float]:
    months = fixture.get("months") or {}
    block = months.get(month) or {}
    raw = block.get("composition")
    if isinstance(raw, dict) and raw:
        return {str(k): float(v) for k, v in raw.items() if v is not None}
    return compose_categories(snapshot.bill_resources)


def _storage_classes(snapshot: CollectSnapshot) -> list[dict[str, Any]]:
    totals = {key: 0.0 for key in STORAGE_CLASS_LABELS}
    saw = False
    if snapshot.monitor:
        for metrics in snapshot.monitor.by_bucket.values():
            if metrics.standard_bytes:
                totals["standard"] += metrics.standard_bytes
                saw = True
            if metrics.sia_storage_bytes or metrics.maz_ia_storage_bytes:
                totals["ia"] += (metrics.sia_storage_bytes or 0) + (metrics.maz_ia_storage_bytes or 0)
                saw = True
            if metrics.arc_storage_bytes:
                totals["archive"] += metrics.arc_storage_bytes
                saw = True
            if metrics.deep_arc_storage_bytes:
                totals["deep"] += metrics.deep_arc_storage_bytes
                saw = True
    total = sum(totals.values())
    items = []
    for key, label in STORAGE_CLASS_LABELS.items():
        if key == "multipart":
            continue
        value = totals[key]
        pct = (100.0 * value / total) if total else None
        items.append(
            {
                "key": key,
                "label": label,
                "bytes": value if saw else None,
                "pct": pct,
                "color": STORAGE_CLASS_COLORS[key],
                "text": f"{pct:.0f}%" if pct is not None else "—",
            }
        )
    return items


def _row_dict(row: RankingRow) -> dict[str, Any]:
    lights = []
    for key, label in (
        ("lifecycle", "生命周期"),
        ("fragments", "碎片"),
        ("cdn", "CDN"),
        ("versioning", "版本"),
        ("backup", "备份"),
    ):
        status = getattr(row.config_lights, key)
        on = status in {"on", "yes", "true", "risk"}
        lights.append({"key": key, "label": label, "on": on, "status": status})
    return {
        "bucket": row.bucket,
        "region": row.region,
        "payable": row.payable,
        "payable_text": money_text(row.payable),
        "mom_pct": row.mom_pct,
        "mom_text": pct_text(row.mom_pct),
        "capacity_text": volume_text(row.capacity_bytes),
        "standard_text": pct_text(row.standard_pct, signed=False),
        "internet_text": volume_text(row.internet_traffic_bytes),
        "opportunity_text": (
            f"{money_text(row.opportunity_amount)} · {row.opportunity_count}"
            if row.opportunity_count
            else "—"
        ),
        "lights": lights,
        "raw_resource_ids": row.raw_resource_ids,
    }


def _group_opportunities(cards: list[dict[str, Any]]) -> dict[str, Any]:
    groups = {key: [] for key in COLUMN_LABELS}
    for card in cards:
        column = str(card.get("column") or "steady")
        groups.setdefault(column, []).append(card)
    return {
        "columns": [
            {"key": key, "label": label, "cards": groups.get(key) or []}
            for key, label in COLUMN_LABELS.items()
        ]
    }


def _payable_text(value: float | None, ranking: RankingResult) -> str:
    if value is None and ranking.ready is None and not ranking.estimated:
        return "无权限"
    return money_text(value)


def _created_label(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw[:7] if len(raw) >= 7 else raw


def _c7_storage(metrics) -> dict[str, Any]:
    if metrics is None:
        return {"empty": True, "message": "无监控数据，存储类型构成不可用。"}
    dates = metrics.dates or []
    series = []
    mapping = [
        ("StdStorage", "standard", metrics.std_storage_bytes),
        ("SiaStorage", "ia", metrics.sia_storage_bytes),
        ("StdMultipartStorage", "multipart", metrics.multipart_storage_bytes),
    ]
    for metric, key, last_bytes in mapping:
        values = metrics.daily.get(metric)
        if not values and last_bytes is not None and dates:
            values = [last_bytes / MB] * len(dates)
        if values:
            series.append(
                {
                    "key": key,
                    "label": STORAGE_CLASS_LABELS[key],
                    "color": STORAGE_CLASS_COLORS[key],
                    "values": values,
                }
            )
    if not series:
        return {"empty": True, "message": "无监控数据，存储类型构成不可用。"}
    return {"empty": False, "dates": dates, "series": series}


def _bucket_composition(rows) -> dict[str, Any]:
    composed = compose_categories(rows)
    total = sum(composed.values())
    if not composed:
        return {"empty": True, "message": "无按计费项拆分的 ResourceSummary，仅有桶合计。", "items": []}
    items = []
    for key, label in CATEGORY_LABELS.items():
        value = composed.get(key, 0.0)
        if value <= 0:
            continue
        items.append(
            {
                "key": key,
                "label": label,
                "value": value,
                "pct": (100.0 * value / total) if total else 0,
                "color": CATEGORY_COLORS[key],
            }
        )
    return {"empty": False, "total": total, "items": items}


def _c6_daily(metrics, payable: float | None, month: str) -> dict[str, Any]:
    if metrics is None or not metrics.dates:
        return {"empty": True, "message": "无按日监控点，不编造日账单。"}
    cap: list[float | None] = []
    for i, _date in enumerate(metrics.dates):
        std = _at(metrics.daily.get("StdStorage"), i)
        sia = _at(metrics.daily.get("SiaStorage"), i)
        arc = _at(metrics.daily.get("ArcStorage"), i)
        parts = [p for p in (std, sia, arc) if p is not None]
        cap.append(sum(parts) if parts else None)
    year, mon = (int(p) for p in month.split("-"))
    days = monthrange(year, mon)[1]
    daily_avg = (payable / days) if payable is not None and days else None
    return {
        "empty": False,
        "dates": metrics.dates,
        "capacity_mb": cap,
        "payable_daily_avg": daily_avg,
        "note": (
            f"应付日均摊 {money_text(daily_avg)} / 天（月应付 ÷ {days}，不是按日账单）。"
            if daily_avg is not None
            else "无月应付，不画日均摊，也不编造日账单。"
        ),
    }


def _c9_traffic(metrics) -> dict[str, Any]:
    if metrics is None:
        return {"empty": True, "message": "无流量监控。"}
    dates = metrics.dates or []
    series = []
    for metric, label, color in (
        ("InternetTraffic", "外网", "#0891B2"),
        ("InternalTraffic", "内网", "#64748B"),
        ("CdnOriginTraffic", "CDN 回源", "#7C3AED"),
    ):
        values = metrics.daily.get(metric)
        if values:
            series.append({"key": metric, "label": label, "color": color, "values": values})
    if not series:
        return {"empty": True, "message": "无流量监控。"}
    return {"empty": False, "dates": dates, "series": series}


def _c10_requests(metrics) -> dict[str, Any]:
    if metrics is None:
        return {"empty": True, "message": "无请求监控。"}
    dates = metrics.dates or []
    series = []
    for metric, label, color in (
        ("GetRequests", "读", "#2563EB"),
        ("PutRequests", "写", "#16A34A"),
    ):
        values = metrics.daily.get(metric)
        if values:
            series.append({"key": metric, "label": label, "color": color, "values": values})
    err = []
    if metrics.daily.get("4xxResponse") or metrics.daily.get("5xxResponse"):
        n = len(dates)
        for i in range(n):
            a = _at(metrics.daily.get("4xxResponse"), i) or 0
            b = _at(metrics.daily.get("5xxResponse"), i) or 0
            err.append(a + b)
        series.append({"key": "errors", "label": "4xx+5xx", "color": "#DC2626", "values": err, "dashed": True})
    if not series:
        return {"empty": True, "message": "无请求监控。"}
    return {
        "empty": False,
        "dates": dates,
        "series": series,
        "note": "失败请求仍计费。",
    }


def _at(values: list[float | None] | None, index: int) -> float | None:
    if not values or index >= len(values):
        return None
    return values[index]

