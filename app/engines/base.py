"""AI 引擎的通用状态对象。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class EngineStatus:
    name: str
    available: bool
    path: str = ""
    backend: str = ""
    backend_label: str = ""
    version: str = ""
    detail: str = ""
    notes: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
