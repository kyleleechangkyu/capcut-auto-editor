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
_SENTENCE_END = re.compile(r"[.!?…]$")


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
    max_chars: int = 20,
    max_duration: float = 4.0,
    strip_punctuation: bool = True,
) -> List[Cue]:
    """발화를 편집본 타임라인으로 옮겨 어절 단위로 자막을 채웁니다.

    whisper의 발화(utterance) 경계로 쪼개지 않습니다. 컷 편집이 촘촘하면 원래
    서로 다른 발화였던 것들이 사이의 무음/비발화 구간이 통째로 잘려나가 편집본
    에서는 바로 붙어버립니다 — 그런데도 발화 단위로 나누면 뻔히 이어지는 말인데
    자막만 뚝뚝 끊깁니다. 그래서 모든 발화의 단어를 시간순으로 한 줄로 모읍니다.

    whisper가 주는 "단어"는 이미 한국어 어절 단위(예: '디지몬이', '한다고')라서
    어절 중간에서 잘리는 일은 없습니다. 그 위에서 (1) 문장이 끝나는 지점(마침표
    류)이면 그 자리에서 끊고, (2) 띄어쓰기 포함 max_chars 를 넘기 직전에 끊어,
    줄바꿈 없이 한 줄에 깔끔하게 들어가도록 합니다. 실제로 컷으로 끊기거나
    (편집본에서 시간이 크게 튐) 너무 길어질 때도 끊습니다.
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

    # 컷으로 끊기거나, 문장이 끝나거나, 글자 수(어절 경계에서)나 시간이 넘칠
    # 때 자막을 나눕니다. 절대 어절 중간에서는 안 끊습니다(단어 단위로만 붙임).
    cues: List[Cue] = []
    line: List[tuple] = []
    line_len = 0
    for item in mapped:
        word = item[2]
        jumped = bool(line) and (item[0] - line[-1][1]) > 0.6
        candidate_len = (line_len + 1 + len(word)) if line else len(word)
        too_long_chars = bool(line) and candidate_len > max_chars
        too_long_time = bool(line) and (item[1] - line[0][0]) > max_duration

        if line and (jumped or too_long_chars or too_long_time):
            cues.append(_make_cue(line, strip_punctuation))
            line = []
            line_len = 0

        line.append(item)
        line_len = (line_len + 1 + len(word)) if line_len else len(word)

        # 문장이 끝나는 지점(마침표류)이면 글자 수가 남아도 여기서 끊어,
        # 다음 문장과 한 자막에 섞이지 않게 합니다.
        if _SENTENCE_END.search(word):
            cues.append(_make_cue(line, strip_punctuation))
            line = []
            line_len = 0

    if line:
        cues.append(_make_cue(line, strip_punctuation))

    cues = [c for c in cues if c.text]
    cues.sort(key=lambda c: c.start)

    # 다음 자막 시작 지점까지 완전히 붙여 이어줍니다(공백 0초, 부동소수점
    # 반올림 오차만 피하려고 1ms 만 남김). max_duration 은 자막 내용(담기는
    # 말)의 길이만 제한할 뿐, 채워 늘이는 건 제한하지 않습니다 — 여기서까지
    # 막으면 내용이 짧은 자막 뒤에 다시 공백이 남기 때문입니다. 영상은 컷
    # 편집으로 끊김 없이 이어 붙으므로, 화면이 비는 것보다 이전 자막이 좀 더
    # 오래 떠 있는 쪽이 낫다고 봅니다.
    for i, c in enumerate(cues):
        # 마지막 자막은 다음 자막이 없으니 편집본 전체 길이가 상한입니다
        # (없으면 영상이 끝난 뒤까지 자막이 늘어날 수 있음).
        next_start = cues[i + 1].start if i + 1 < len(cues) else plan.kept_duration
        c.end = max(next_start - 0.001, c.start)

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
