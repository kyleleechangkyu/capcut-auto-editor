"""컷 리포트 작성 — 무엇을 왜 잘랐는지 눈으로 확인하기 위한 산출물."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from .audio import MediaInfo
from .detect import CutPlan
from .subtitles import Cue


def _mmss(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:05.2f}"


def write_json(plan: CutPlan, cues: Sequence[Cue], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "duration": plan.duration,
        "kept_duration": plan.kept_duration,
        "removed_duration": plan.removed_duration,
        "keeps": [asdict(k) for k in plan.keeps],
        "cuts": [asdict(c) for c in plan.cuts],
        "cues": [asdict(c) for c in cues],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    return path


def write_markdown(
    info: MediaInfo, plan: CutPlan, cues: Sequence[Cue], path: Path, *, draft_name: str, style_note: str
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    ratio = (plan.removed_duration / plan.duration * 100) if plan.duration else 0.0

    lines = [
        f"# {info.path.name} 자동 편집 리포트",
        "",
        f"- 원본 {_mmss(plan.duration)} → 편집본 **{_mmss(plan.kept_duration)}** "
        f"({ratio:.0f}% 덜어냄)",
        f"- 클립 {len(plan.keeps)}개 / 자막 {len(cues)}줄",
        f"- 초안 이름: `{draft_name}`",
        f"- 자막 스타일: {style_note}",
        "",
        "## 사유별 요약",
        "",
        "| 사유 | 횟수 | 총 길이 |",
        "| --- | ---: | ---: |",
    ]

    from .detect import REASON_LABEL
    for reason, (count, dur) in sorted(
        plan.summary_by_reason().items(), key=lambda kv: -kv[1][1]
    ):
        lines.append(f"| {REASON_LABEL.get(reason, reason)} | {count} | {dur:.1f}초 |")

    lines += ["", "## 잘라낸 구간", "", "| 원본 구간 | 길이 | 사유 | 메모 |", "| --- | ---: | --- | --- |"]
    for c in plan.cuts:
        lines.append(
            f"| {_mmss(c.start)} – {_mmss(c.end)} | {c.duration:.2f}초 | {c.label} | {c.detail} |"
        )

    lines += ["", "## 남긴 구간", "", "| 편집본 위치 | 원본 구간 | 길이 |", "| --- | --- | ---: |"]
    for k in plan.keeps:
        lines.append(
            f"| {_mmss(k.timeline_start)} | {_mmss(k.start)} – {_mmss(k.end)} | {k.duration:.2f}초 |"
        )

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path
