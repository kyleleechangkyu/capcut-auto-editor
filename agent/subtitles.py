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
    max_duration: float = 4.0,
    strip_punctuation: bool = True,
) -> List[Cue]:
    """발화를 편집본 타임라인으로 옮겨 한 구간(컷으로 안 끊긴 말 덩어리)에 자막 하나를 채웁니다.

    글자 수로 쪼개지 않습니다 — CapCut 자체가 박스 안에서 줄바꿈을 해 주므로,
    여기서 미리 잘게 쪼개면 오히려 사용자가 직접 다시 이어붙여야 합니다.

    whisper의 발화(utterance) 경계로도 쪼개지 않습니다. 컷 편집이 촘촘하면
    원래 서로 다른 발화였던 것들이 사이의 무음/비발화 구간이 통째로 잘려나가
    편집본에서는 바로 붙어버립니다 — 그런데도 발화 단위로 나누면 뻔히 이어지는
    말인데 자막만 뚝뚝 끊깁니다. 그래서 모든 발화의 단어를 시간순으로 한 줄로
    모은 뒤, 편집본 기준으로 실제로 끊기거나(시간이 크게 튐) 너무 길어질 때만
    나눕니다.
    """
    # 잘려나가지 않고 살아남은 단어만, 발화 구분 없이 시간순으로 모읍니다
    mapped: List[tuple] = []
    for utt in utterances:
        utt_mapped = []
        for w in utt.words:
            span = plan.map_span(w.start, w.end)
            if span is None:
                continue
            # 컷 경계에 반쯤 걸린 단어는 조각으로 남으므로 버립니다
            original = max(1e-6, w.end - w.start)
            if (span[1] - span[0]) / original < 0.55:
                continue
            utt_mapped.append((span[0], span[1], w.text))

        if utt_mapped:
            mapped.extend(utt_mapped)
        else:
            # 단어 타임스탬프가 없는 경우 발화 전체를 한 덩어리로 넣습니다
            span = plan.map_span(utt.start, utt.end)
            if span is None:
                continue
            text = _clean(utt.text, strip_punctuation)
            if text:
                mapped.append((span[0], span[1], text))

    mapped.sort(key=lambda item: item[0])

    # 컷으로 끊기거나(편집본에서 시간이 크게 튐) 너무 길어지는 경우에만 자막을 나눕니다.
    cues: List[Cue] = []
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

    # 다음 자막 시작 직전까지 빈틈없이 채웁니다. 영상은 컷 편집으로 끊김 없이
    # 이어 붙으므로, 말 사이 짧은 틈에도 이전 자막이 계속 떠 있는 게 (화면이
    # 비는 것보다) 낫습니다 — 그렇다고 하나가 너무 오래 떠 있진 않도록
    # max_duration 은 넘기지 않습니다. hard_cap 이 다음 자막을 침범하지 않는
    # 절대 상한이라, CapCut에 "세그먼트 겹침" 오류가 나지 않습니다.
    for i, c in enumerate(cues):
        # 마지막 자막은 다음 자막이 없으니 편집본 전체 길이가 상한입니다
        # (없으면 영상이 끝난 뒤까지 자막이 늘어날 수 있음).
        next_start = cues[i + 1].start if i + 1 < len(cues) else plan.kept_duration
        hard_cap = next_start - 0.04
        cap = min(hard_cap, c.start + max_duration)
        c.end = max(cap, c.start)

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
