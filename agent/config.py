"""설정 로딩/저장 + CapCut 초안 폴더 자동 탐색.

기본값은 defaults.py 에 있고, 앱 화면에서 바꾼 값만 settings.json 에 저장됩니다.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

from .defaults import DEFAULTS, PRESETS

APP_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = APP_ROOT / "settings.json"


# CapCut / 剪映 이 초안을 저장하는 후보 경로들 (버전마다 다릅니다)
MAC_DRAFT_CANDIDATES = [
    "~/Movies/CapCut/User Data/Projects/com.lveditor.draft",
    "~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft",
    "~/Library/Containers/com.lemon.lvoverseas/Data/Movies/CapCut/User Data/Projects/com.lveditor.draft",
    "~/Library/Application Support/CapCut/User Data/Projects/com.lveditor.draft",
    "~/Library/Application Support/com.lemon.lvoverseas/User Data/Projects/com.lveditor.draft",
]

WIN_DRAFT_CANDIDATES = [
    r"~\AppData\Local\CapCut\User Data\Projects\com.lveditor.draft",
    r"~\AppData\Local\JianyingPro\User Data\Projects\com.lveditor.draft",
]


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config(dict):
    """점 표기로 읽고 쓰는 설정."""

    def get_path(self, dotted: str, default: Any = None) -> Any:
        node: Any = self
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set_path(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node: Any = self
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    # -- 앱이 쓰는 폴더들 --------------------------------------------
    @property
    def work_dir(self) -> Path:
        p = APP_ROOT / "work"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def media_dir(self) -> Path:
        """드래그해서 넣은 영상이 보관되는 곳 (초안이 이 경로를 기억합니다)."""
        p = APP_ROOT / "media"
        p.mkdir(parents=True, exist_ok=True)
        return p


def find_capcut_drafts() -> Path | None:
    candidates = WIN_DRAFT_CANDIDATES if sys.platform == "win32" else MAC_DRAFT_CANDIDATES
    for cand in candidates:
        p = Path(os.path.expanduser(cand))
        if p.is_dir():
            return p
    return None


def load() -> Config:
    raw: Dict[str, Any] = {}
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
                raw = json.load(fh) or {}
        except (OSError, ValueError):
            raw = {}

    merged = _deep_merge(DEFAULTS, raw)
    cfg = Config(merged)

    # 화면에서 고른 프리셋을 실제 숫자로 펼칩니다
    for group, choice in (raw.get("presets") or {}).items():
        mapping = PRESETS.get(group, {}).get(choice)
        if mapping:
            for dotted, value in mapping.items():
                cfg.set_path(dotted, value)

    cfg["presets"] = raw.get("presets") or {
        "cut_strength": "normal", "quality": "balanced", "ratio": "vertical"
    }

    if not cfg.get("capcut_drafts"):
        found = find_capcut_drafts()
        if found:
            cfg["capcut_drafts"] = str(found)

    return cfg


def save_user_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    """앱 화면이 보낸 변경분만 settings.json 에 합쳐 저장합니다."""
    current: Dict[str, Any] = {}
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as fh:
                current = json.load(fh) or {}
        except (OSError, ValueError):
            current = {}

    merged = _deep_merge(current, patch)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=2)
    return merged
