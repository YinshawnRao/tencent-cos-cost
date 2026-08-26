"""只读生命周期 XML 草稿。复制后由人工在控制台确认；本工具绝不应用到桶。"""

from __future__ import annotations

from xml.sax.saxutils import escape

MIN_TRANSITION_DAYS = {
    "STANDARD_IA": 30,
    "ARCHIVE": 90,
    "DEEP_ARCHIVE": 180,
}

ABORT_DAYS = 7
IA_DAYS = 30
NONCURRENT_EXPIRATION_DAYS = 30


def clamp_transition_days(storage_class: str, days: int | None) -> int:
    key = (storage_class or "STANDARD_IA").upper().replace(" ", "_")
    if key in {"IA", "SIA"}:
        key = "STANDARD_IA"
    minimum = MIN_TRANSITION_DAYS.get(key, 30)
    proposed = IA_DAYS if days is None else int(days)
    return max(proposed, minimum)


def abort_xml(*, days: int = ABORT_DAYS, rule_id: str = "abort-incomplete-mpu") -> str:
    return _wrap(
        [
            _rule(
                rule_id,
                [
                    f"    <AbortIncompleteMultipartUpload>",
                    f"      <DaysAfterInitiation>{int(days)}</DaysAfterInitiation>",
                    f"    </AbortIncompleteMultipartUpload>",
                ],
            )
        ]
    )


def transition_xml(
    *,
    storage_class: str = "STANDARD_IA",
    days: int | None = None,
    rule_id: str = "std-transition",
) -> str:
    storage_class = storage_class.upper().replace(" ", "_")
    if storage_class in {"IA", "SIA"}:
        storage_class = "STANDARD_IA"
    use_days = clamp_transition_days(storage_class, days)
    return _wrap(
        [
            _rule(
                rule_id,
                [
                    "    <Transition>",
                    f"      <Days>{use_days}</Days>",
                    f"      <StorageClass>{escape(storage_class)}</StorageClass>",
                    "    </Transition>",
                ],
            )
        ]
    )


def skeleton_xml(*, abort_days: int = ABORT_DAYS, ia_days: int | None = None) -> str:
    ia = clamp_transition_days("STANDARD_IA", ia_days)
    return _wrap(
        [
            _rule(
                "abort-incomplete-mpu-7d",
                [
                    "    <AbortIncompleteMultipartUpload>",
                    f"      <DaysAfterInitiation>{int(abort_days)}</DaysAfterInitiation>",
                    "    </AbortIncompleteMultipartUpload>",
                ],
            ),
            _rule(
                "std-to-ia-30d",
                [
                    "    <Transition>",
                    f"      <Days>{ia}</Days>",
                    "      <StorageClass>STANDARD_IA</StorageClass>",
                    "    </Transition>",
                ],
            ),
        ]
    )


def noncurrent_expiration_xml(*, days: int = NONCURRENT_EXPIRATION_DAYS) -> str:
    return _wrap(
        [
            _rule(
                "noncurrent-expiration",
                [
                    "    <NoncurrentVersionExpiration>",
                    f"      <NoncurrentDays>{int(days)}</NoncurrentDays>",
                    "    </NoncurrentVersionExpiration>",
                ],
            )
        ]
    )


def corrected_transitions_xml(pairs: list[tuple[str, int]]) -> str:
    inner: list[str] = []
    for index, (storage_class, days) in enumerate(pairs):
        klass = storage_class.upper().replace(" ", "_")
        if klass in {"IA", "SIA"}:
            klass = "STANDARD_IA"
        use_days = clamp_transition_days(klass, days)
        inner.append(
            _rule(
                f"fix-min-days-{index + 1}",
                [
                    "    <Transition>",
                    f"      <Days>{use_days}</Days>",
                    f"      <StorageClass>{escape(klass)}</StorageClass>",
                    "    </Transition>",
                ],
            )
        )
    return _wrap(inner)


def _wrap(rules: list[str]) -> str:
    body = "\n".join(rules)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<LifecycleConfiguration>\n"
        f"{body}\n"
        "</LifecycleConfiguration>\n"
        "<!-- 草稿仅供复制。本工具不会把规则写回存储桶。勿对全桶列对象。 -->\n"
    )


def _rule(rule_id: str, children: list[str]) -> str:
    lines = [
        "  <Rule>",
        f"    <ID>{escape(rule_id)}</ID>",
        "    <Filter><Prefix></Prefix></Filter>",
        "    <Status>Enabled</Status>",
        *children,
        "  </Rule>",
    ]
    return "\n".join(lines)
