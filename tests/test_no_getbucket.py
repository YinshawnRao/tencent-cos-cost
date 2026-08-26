from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from cos_cost.clients.live_cos import LiveCosClient
from cos_cost.secrets import Credentials

SRC = Path(__file__).resolve().parents[1] / "src" / "cos_cost"


def test_source_never_lists_objects_or_mutates() -> None:
    forbidden = (
        ".list_objects(",
        ".get_bucket(",
        "put_bucket_lifecycle",
        "put_bucket_versioning",
        "put_bucket_logging",
        "PutBucketLifecycle",
        "GetBucket(",
    )
    hits: list[str] = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                hits.append(f"{path.relative_to(SRC)}:{token}")
    assert hits == []


def test_live_cos_config_reads_do_not_list_objects() -> None:
    fake_s3 = MagicMock()
    fake_s3.list_buckets.return_value = {
        "Owner": {"ID": "1250000000"},
        "Buckets": {"Bucket": [{"Name": "logs-prod-1250000000", "Location": "ap-guangzhou"}]},
    }
    fake_s3.get_bucket_lifecycle.return_value = {}
    fake_s3.get_bucket_versioning.return_value = {"Status": "Suspended"}
    fake_s3.get_bucket_logging.return_value = {}
    fake_s3.list_bucket_inventory_configurations.return_value = {}
    with (
        patch("qcloud_cos.CosConfig"),
        patch("qcloud_cos.CosS3Client", return_value=fake_s3),
    ):
        client = LiveCosClient(Credentials("id", "key"))
        client._client = fake_s3
        client.get_bucket_lifecycle("logs-prod-1250000000", "ap-guangzhou")
        client.get_bucket_versioning("logs-prod-1250000000", "ap-guangzhou")
        client.get_bucket_logging("logs-prod-1250000000", "ap-guangzhou")
        client.list_bucket_inventory("logs-prod-1250000000", "ap-guangzhou")
    fake_s3.list_objects.assert_not_called()
    fake_s3.get_bucket.assert_not_called()
    assert not fake_s3.put_bucket_lifecycle.called
