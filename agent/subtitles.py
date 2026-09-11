"""편집본 타임라인 기준 자막 생성."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from .detect import CutPlan
from .transcribe import Utterance

_TRAILING_PUNCT = re.compile(r"[.,!?…·]+$")
_MULTISPACE = re.compile(r"\s+")


@dataclass
class Cue:
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _clean(text: str, strip_punct: bool) -> str:
    t = _MULTISPACE.sub(" ", (text or "").strip())
    if strip_punct:
        t = _TRAILING_PUNCT.sub("", t).strip()
    return t


def build_cues(
    utterances: Sequence[Utterance],
    plan: CutPlan,
    *,
    min_duration: float = 0.7,
    max_duration: float = 4.0,
    strip_punctuation: bool = True,
) -> List[Cue]:
    """발화를 편집본 타임라인으로 옮겨 한 구간(컷으로 안 끊긴 말 덩어리)에 자막 하나를 채웁니다.

    글자 수로 쪼개지 않습니다 — CapCut 자체가 박스 안에서 줄바꿈을 해 주므로,
    여기서 미리 잘게 쪼개면 오히려 사용자가 직접 다시 이어붙여야 합니다.
    """
    cues: List[Cue] = []

    for utt in utterances:
        # 잘려나가지 않고 살아남은 단어만 모읍니다
        mapped = []
        for w in utt.words:
            span = plan.map_span(w.start, w.end)
            if span is None:
                continue
            # 컷 경계에 반쯤 걸린 단어는 조각으로 남으므로 버립니다
            original = max(1e-6, w.end - w.start)
            if (span[1] - span[0]) / original < 0.55:
                continue
            mapped.append((span[0], span[1], w.text))

        if not mapped:
            # 단어 타임스탬프가 없는 경우 발화 단위로 처리
            span = plan.map_span(utt.start, utt.end)
            if span is None:
                continue
            text = _clean(utt.text, strip_punctuation)
            if text:
                cues.append(Cue(span[0], span[1], text))
            continue

        # 컷으로 끊기거나(시간이 크게 튐) 너무 길어지는 경우에만 자막을 나눕니다.
        line: List[tuple] = []
        for item in mapped:
            jumped = bool(line) and (item[0] - line[-1][1]) > 0.6
            too_long = bool(line) and (item[1] - line[0][0]) > max_duration

            if line and (jumped or too_long):
                cues.append(_make_cue(line, strip_punctuation))
                line = []
            line.append(item)

        if line:
            cues.append(_make_cue(line, strip_punctuation))

    cues = [c for c in cues if c.text]
    cues.sort(key=lambda c: c.start)

    # 길이 보정 + 겹침 제거
    # 다음 자막 시작 전까지가 절대 상한(hard_cap) — 컷이 촘촘해 자막이
    # 빽빽하게 붙어 있을 때 min_duration 을 채우려다 다음 자막을 침범해
    # CapCut에 "세그먼트 겹침" 오류를 내지 않도록, 늘릴 땐 이 상한을 넘지 않는다.
    for i, c in enumerate(cues):
        if c.duration > max_duration:
            c.end = c.start + max_duration
        hard_cap = cues[i + 1].start - 0.04 if i + 1 < len(cues) else float("inf")
        if c.duration < min_duration and hard_cap > c.start:
            c.end = min(c.start + min_duration, hard_cap)
        if c.end > hard_cap:
            c.end = hard_cap
        if c.end < c.start:
            c.end = c.start

    return [c for c in cues if c.duration > 0.1]


def _make_cue(line: List[tuple], strip_punct: bool) -> Cue:
    text = _clean(" ".join(w[2] for w in line), strip_punct)
    return Cue(line[0][0], line[-1][1], text)


def _srt_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    ms = int(round((s - int(s)) * 1000))
    if ms >= 1000:
        ms, s = 0, int(s) + 1
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{ms:03d}"


def write_srt(cues: Sequence[Cue], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for i, c in enumerate(cues, 1):
            fh.write(f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{c.text}\n\n")
    return path
