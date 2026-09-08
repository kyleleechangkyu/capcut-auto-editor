"""컷 판정 엔진: 무음 / 말 더듬음 / 재촬영(NG) 구간을 찾아 남길 구간을 계산합니다."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

from .transcribe import Utterance

REASON_LABEL = {
    "silence": "무음",
    "stutter": "말 더듬음",
    "retake": "재촬영(NG)",
    "tail": "끝단 정리",
}

_PUNCT = re.compile(r"[\s.,!?~…·\"'\-—“”‘’()\[\]{}]+")


def normalize(text: str) -> str:
    """비교용 정규화: 공백·문장부호 제거 + 유니코드 정규화."""
    t = unicodedata.normalize("NFKC", text or "").strip().lower()
    return _PUNCT.sub("", t)


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


# ---------------------------------------------------------------- 자료구조

@dataclass
class Cut:
    """잘라낼 구간 (원본 타임라인 기준)."""
    start: float
    end: float
    reason: str
    detail: str = ""

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def label(self) -> str:
        return REASON_LABEL.get(self.reason, self.reason)


@dataclass
class Keep:
    """남길 구간 (원본 기준 start/end + 편집본에서의 시작 위치)."""
    start: float
    end: float
    timeline_start: float = 0.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass
class CutPlan:
    duration: float
    keeps: List[Keep] = field(default_factory=list)
    cuts: List[Cut] = field(default_factory=list)

    @property
    def kept_duration(self) -> float:
        return sum(k.duration for k in self.keeps)

    @property
    def removed_duration(self) -> float:
        return max(0.0, self.duration - self.kept_duration)

    def summary_by_reason(self) -> Dict[str, Tuple[int, float]]:
        out: Dict[str, Tuple[int, float]] = {}
        for c in self.cuts:
            n, d = out.get(c.reason, (0, 0.0))
            out[c.reason] = (n + 1, d + c.duration)
        return out

    # -- 원본 시각 → 편집본 시각 --------------------------------------
    def map_time(self, t: float) -> Optional[float]:
        """원본 시각을 편집본 타임라인 시각으로 옮깁니다.

        잘려나간 구간에 있으면 None.
        """
        for k in self.keeps:
            if k.start - 1e-6 <= t <= k.end + 1e-6:
                return k.timeline_start + max(0.0, t - k.start)
        return None

    def map_span(self, start: float, end: float) -> Optional[Tuple[float, float]]:
        """구간을 편집본으로 옮깁니다. 잘린 부분에 걸치면 살아남은 부분만 반환."""
        pieces: List[Tuple[float, float]] = []
        for k in self.keeps:
            s = max(start, k.start)
            e = min(end, k.end)
            if e - s > 1e-3:
                pieces.append((k.timeline_start + (s - k.start),
                               k.timeline_start + (e - k.start)))
        if not pieces:
            return None
        return pieces[0][0], pieces[-1][1]


# ---------------------------------------------------------------- 감지기

def find_silence_cuts(
    spans: Sequence[Tuple[float, float]], *, keep_padding: float, min_duration: float
) -> List[Cut]:
    """무음 구간에서 여백만 남기고 잘라낼 부분을 만듭니다."""
    cuts: List[Cut] = []
    for start, end in spans:
        if end - start < min_duration:
            continue
        # 앞뒤로 keep_padding 만큼은 숨 쉴 틈으로 남겨둡니다
        s = start + keep_padding
        e = end - keep_padding
        if e - s > 0.05:
            cuts.append(Cut(s, e, "silence", f"{end - start:.2f}초 무음"))
    return cuts


def find_stutter_cuts(
    utterances: Sequence[Utterance], *, threshold: float, max_gap: float
) -> List[Cut]:
    """한 발화 안에서 같은 단어를 연달아 말한 구간을 찾습니다.

    "그 그 그러니까" → 앞의 "그 그" 를 잘라냅니다.
    """
    cuts: List[Cut] = []
    for utt in utterances:
        words = [w for w in utt.words if normalize(w.text)]
        i = 0
        while i < len(words) - 1:
            j = i
            # i번째와 같은 말이 이어지는 동안 j를 밀어냅니다
            while (
                j + 1 < len(words)
                and words[j + 1].start - words[j].end <= max_gap
                and similarity(words[i].text, words[j + 1].text) >= threshold
            ):
                j += 1
            if j > i:
                # 마지막 반복만 남기고 앞의 것들을 제거
                cuts.append(
                    Cut(
                        words[i].start,
                        words[j].start,
                        "stutter",
                        f"'{words[i].text}' {j - i + 1}회 반복",
                    )
                )
                i = j + 1
            else:
                i += 1
    return cuts


def find_retake_cuts(
    utterances: Sequence[Utterance],
    *,
    threshold: float,
    lookahead: int,
    max_gap: float,
    keep: str = "last",
) -> List[Cut]:
    """같은 문장을 다시 말한 경우(재촬영)를 찾아 한 테이크만 남깁니다."""
    cuts: List[Cut] = []
    consumed = set()
    n = len(utterances)

    for i in range(n):
        if i in consumed:
            continue
        base = utterances[i]
        if len(normalize(base.text)) < 4:  # 너무 짧은 발화는 비교 대상에서 제외
            continue

        group = [i]
        last = i
        for j in range(i + 1, min(i + 1 + lookahead, n)):
            if j in consumed:
                continue
            cand = utterances[j]
            if cand.start - utterances[last].end > max_gap:
                break
            if similarity(base.text, cand.text) >= threshold:
                group.append(j)
                last = j

        if len(group) < 2:
            continue

        keep_idx = group[-1] if keep == "last" else group[0]
        for g in group:
            consumed.add(g)
            if g == keep_idx:
                continue
            u = utterances[g]
            cuts.append(
                Cut(
                    u.start,
                    u.end,
                    "retake",
                    f"'{u.text[:20]}…' {len(group)}테이크 중 {'앞' if g < keep_idx else '뒤'} 테이크",
                )
            )
    return cuts


# ---------------------------------------------------------------- 조립

def _merge_intervals(items: List[Cut]) -> List[Cut]:
    """겹치는 컷을 합칩니다. 사유는 먼저 잡힌 쪽을 대표로 씁니다."""
    if not items:
        return []
    items = sorted(items, key=lambda c: (c.start, c.end))
    out = [Cut(items[0].start, items[0].end, items[0].reason, items[0].detail)]
    for c in items[1:]:
        prev = out[-1]
        if c.start <= prev.end + 1e-6:
            if c.end > prev.end:
                prev.end = c.end
                if c.reason != prev.reason and c.reason not in prev.reason:
                    prev.reason = prev.reason if prev.reason != "silence" else c.reason
                    prev.detail = f"{prev.detail} / {c.detail}".strip(" /")
        else:
            out.append(Cut(c.start, c.end, c.reason, c.detail))
    return out


def build_plan(
    duration: float,
    cuts: List[Cut],
    *,
    lead_in: float = 0.08,
    lead_out: float = 0.14,
    min_clip: float = 0.30,
    merge_gap: float = 0.18,
    fps: float = 30.0,
) -> CutPlan:
    """컷 목록으로부터 최종 남길 구간을 계산합니다."""
    merged = _merge_intervals(cuts)

    # 무음 컷만 앞뒤를 줄여 말이 잘려 들리는 것을 막습니다.
    # 말 더듬음/재촬영은 이미 단어·문장 경계라서 여백을 주면 조각이 남습니다.
    trimmed: List[Cut] = []
    for c in merged:
        pad_head, pad_tail = (lead_out, lead_in) if c.reason == "silence" else (0.0, 0.0)
        s = c.start + pad_head   # 이전 클립의 꼬리를 살림
        e = c.end - pad_tail     # 다음 클립의 머리를 살림
        if e - s > 0.05:
            trimmed.append(Cut(s, e, c.reason, c.detail))

    # 남을 구간 = 전체 - 컷
    keeps: List[Keep] = []
    cursor = 0.0
    for c in trimmed:
        if c.start - cursor > 1e-6:
            keeps.append(Keep(cursor, c.start))
        cursor = max(cursor, c.end)
    if duration - cursor > 1e-6:
        keeps.append(Keep(cursor, duration))

    # 간격이 아주 좁은 조각끼리 합치기
    compact: List[Keep] = []
    for k in keeps:
        if compact and k.start - compact[-1].end <= merge_gap:
            compact[-1].end = k.end
        else:
            compact.append(Keep(k.start, k.end))

    # 너무 짧은 조각 버리기
    compact = [k for k in compact if k.duration >= min_clip]

    # 프레임 격자에 맞추기
    frame = 1.0 / fps if fps > 0 else 0.0
    if frame:
        for k in compact:
            k.start = round(k.start / frame) * frame
            k.end = round(k.end / frame) * frame
        compact = [k for k in compact if k.duration >= min_clip]

    # 편집본에서의 시작 위치 기록
    t = 0.0
    for k in compact:
        k.timeline_start = t
        t += k.duration

    # 실제로 잘려나간 구간을 사유와 함께 다시 계산
    final_cuts: List[Cut] = []
    cursor = 0.0
    for k in compact:
        if k.start - cursor > 1e-6:
            reason, detail = _reason_for(cursor, k.start, merged)
            final_cuts.append(Cut(cursor, k.start, reason, detail))
        cursor = k.end
    if duration - cursor > 1e-6:
        reason, detail = _reason_for(cursor, duration, merged)
        final_cuts.append(Cut(cursor, duration, reason, detail))

    return CutPlan(duration=duration, keeps=compact, cuts=final_cuts)


def _reason_for(start: float, end: float, source: List[Cut]) -> Tuple[str, str]:
    """계산된 컷 구간과 가장 많이 겹치는 원래 사유를 찾습니다."""
    best, best_overlap = None, 0.0
    for c in source:
        ov = min(end, c.end) - max(start, c.start)
        if ov > best_overlap:
            best, best_overlap = c, ov
    if best is None:
        return "tail", ""
    return best.reason, best.detail
