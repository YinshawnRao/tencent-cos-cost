"""配置灯接口（M2：生命周期 / 碎片 / CDN / 版本 / 备份）。M1 全部 unknown。"""

from __future__ import annotations

from typing import Protocol

from cos_cost.models import ConfigLights

LIGHT_LABELS = (
    ("lifecycle", "生命周期"),
    ("fragments", "碎片"),
    ("cdn", "CDN"),
    ("versioning", "版本"),
    ("backup", "备份"),
)


class ConfigLightProvider(Protocol):
    def lights_for(self, bucket: str) -> ConfigLights:
        """只读探测桶配置。禁止 List Objects。"""


class UnknownConfigLights:
    def lights_for(self, bucket: str) -> ConfigLights:
        return ConfigLights()
