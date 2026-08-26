"""Account collect must not 4×GET every bucket; cancel must stop waiters."""

from __future__ import annotations

import threading
import time

import pytest

from cos_cost.cache import FileCache
from cos_cost.clients.errors import CollectCancelled
from cos_cost.clients.mock import mock_bundle
from cos_cost.clients.protocols import ClientBundle
from cos_cost.clients.throttle import RateLimiter
from cos_cost.collect import collect, load_bucket_configs
from cos_cost.models import BucketInfo


class CountingCos:
    def __init__(self, inner, *, delay: float = 0.0, extra: int = 0) -> None:
        self.inner = inner
        self.delay = delay
        self.extra = extra
        self.lifecycle_calls: list[str] = []
        self.versioning_calls: list[str] = []
        self.logging_calls: list[str] = []
        self.inventory_calls: list[str] = []

    def list_buckets(self):
        owner, buckets = self.inner.list_buckets()
        more = [
            BucketInfo(name=f"pad-{i:03d}-1250000000", region="ap-guangzhou")
            for i in range(self.extra)
        ]
        return owner, list(buckets) + more

    def head_bucket_region(self, bucket, fallback_region):
        return self.inner.head_bucket_region(bucket, fallback_region)

    def get_bucket_lifecycle(self, bucket, region):
        self.lifecycle_calls.append(bucket)
        if self.delay:
            time.sleep(self.delay)
        return self.inner.get_bucket_lifecycle(bucket, region)

    def get_bucket_versioning(self, bucket, region):
        self.versioning_calls.append(bucket)
        return self.inner.get_bucket_versioning(bucket, region)

    def get_bucket_logging(self, bucket, region):
        self.logging_calls.append(bucket)
        return self.inner.get_bucket_logging(bucket, region)

    def list_bucket_inventory(self, bucket, region):
        self.inventory_calls.append(bucket)
        return self.inner.list_bucket_inventory(bucket, region)


def test_account_collect_skips_config_for_padded_buckets(cache_dir, fixture_data) -> None:
    base = mock_bundle(fixture_data)
    cos = CountingCos(base.cos, extra=80)
    bundle = ClientBundle(
        account_key=base.account_key,
        cos=cos,
        billing=base.billing,
        monitor=base.monitor,
        mock=True,
    )
    snap = collect(bundle, "2026-07", FileCache(cache_dir), config_limit=30)
    listed = len(snap.buckets)
    assert listed >= 80
    assert len(cos.lifecycle_calls) <= 30
    assert len(cos.lifecycle_calls) < listed
    assert len(cos.versioning_calls) <= 30
    assert "logs-prod-1250000000" in cos.lifecycle_calls

    pad = "pad-079-1250000000"
    assert pad not in cos.lifecycle_calls
    before = len(cos.lifecycle_calls)
    load_bucket_configs(
        bundle,
        FileCache(cache_dir),
        snap.account_key,
        snap.buckets,
        [pad],
    )
    assert pad in cos.lifecycle_calls
    assert len(cos.lifecycle_calls) == before + 1


def test_cancel_stops_slow_config_getter(cache_dir, fixture_data) -> None:
    base = mock_bundle(fixture_data)
    cos = CountingCos(base.cos, delay=0.12, extra=40)
    bundle = ClientBundle(
        account_key=base.account_key,
        cos=cos,
        billing=base.billing,
        monitor=base.monitor,
        mock=True,
    )
    cancel = threading.Event()
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            collect(
                bundle,
                "2026-07",
                FileCache(cache_dir),
                config_limit=40,
                cancel=cancel,
            )
        except CollectCancelled as exc:
            errors.append(exc)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.25)
    cancel.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert errors
    assert isinstance(errors[0], CollectCancelled)
    assert len(cos.lifecycle_calls) < 40


def test_rate_limiter_sleep_checks_cancel() -> None:
    limiter = RateLimiter(qps=0.5)
    limiter.wait()
    ev = threading.Event()
    threading.Timer(0.15, ev.set).start()
    t0 = time.monotonic()
    with pytest.raises(CollectCancelled):
        limiter.wait(cancel=ev)
    assert time.monotonic() - t0 < 1.0
