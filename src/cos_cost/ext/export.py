"""报表导出接口（M2：PDF / Excel）。M1 不实现。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from cos_cost.models import RankingResult


class RankingExporter(Protocol):
    def export(self, ranking: RankingResult, dest: Path) -> None:
        """将排行结果写成文件。"""


class UnsupportedExporter:
    def export(self, ranking: RankingResult, dest: Path) -> None:
        raise NotImplementedError("PDF / Excel 导出属于 Phase M2，本版本未实现。")
