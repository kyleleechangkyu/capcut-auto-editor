"""자막(cue)에서 강조할 키워드 후보를 고릅니다 (규칙 기반 — 사람이 쓰는 문구를 대신하진 못함).

레퍼런스 프로젝트를 세어 보니 사용자가 단 키워드의 상당수는 말한 문장을 그대로 떼어낸 게
아니라 요약·바꿔 쓴 문구("엄청난 속도", "이름빨 아니냐")라서, 자동으로는 그걸 재현할 수
없습니다. 대신 레퍼런스에서 실제로 반복해서 키워드가 되는 종류 — 이름·영문 고유명사
(SHINee, A2O LITE), 숫자가 든 말(14회 취소, 12위), 여러 번 나오는 핵심어 — 를 골라
"여기에 키워드를 달면 좋다"는 후보로 올립니다. 달린 키워드는 CapCut에서 고쳐 쓰면 됩니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from .subtitles import Cue

_JOSA = re.compile(
    r"(이라는|라는|이라고|라고|이었던|에서는|에게는|으로는|에서|에게|으로|부터|까지|처럼|보다|"
    r"이랑|랑|하고|이고|인데|은|는|이|가|을|를|의|에|로|와|과|도|만|랑)$"
)
_STOP = {
    "그", "이", "저", "그게", "이게", "저게", "근데", "그래서", "그리고", "하지만", "그러나", "진짜",
    "너무", "그냥", "뭐", "좀", "이제", "아", "음", "어", "그런", "이런", "저런", "있는", "없는",
    "하는", "되는", "같은", "많이", "다시", "바로", "정말", "완전", "약간", "거의", "그럼", "근데요",
    "우리", "제가", "저는", "나는", "내가", "이거", "그거", "저거", "여기", "거기",
    "지금", "처음", "시기", "하나", "그때", "때문", "정도", "부분", "이후", "이전", "동안", "사람",
    "이야기", "얘기", "생각", "때", "곳", "번", "중", "경우", "느낌", "상황", "시간", "뒤", "앞",
}
_PREDICATE = re.compile(r"(다|요|죠|네|고|데|면|서|야|까|니|지|해|라|해서|했는데|했고|했다)$")
_LATIN = re.compile(r"[A-Za-z]")
_DIGIT = re.compile(r"\d")


@dataclass
class Keyword:
    start: float
    end: float
    text: str
    tier: int  # 0 보통, 1 크게(영문·숫자 등 강한 후보)
    score: float
    cue_index: int


def _strip(token: str) -> str:
    t = token.strip('.,!?…·"\'()[]<>')
    if len(t) >= 4 and not _LATIN.search(t):
        t = _JOSA.sub("", t) or t
    return t


def _guess_kind(tok: str) -> str:
    if _LATIN.search(tok):
        return "foreign"
    if _DIGIT.search(tok):
        return "number"
    return "other" if _PREDICATE.search(tok) else "noun"


def _words(cue: Cue) -> List[Tuple[str, str]]:
    """(알맹이, 종류) 목록 — 형태소 분석 결과가 있으면 그걸, 없으면 규칙으로 짐작."""
    toks = cue.text.split()
    if cue.infos and len(cue.infos) == len(toks):
        return [(stem.strip(".,!?…·\"'()[]<>"), kind) for stem, kind in cue.infos]
    return [(_strip(t), _guess_kind(_strip(t))) for t in toks]


def _word_score(stem: str, kind: str, repeat: Dict[str, int]) -> float:
    if kind == "other" or not stem or stem in _STOP or len(stem) < 2:
        return -9.0
    score = {"foreign": 3.5, "number": 2.5, "noun": 1.5}.get(kind, 0.0)
    if 3 <= len(stem) <= 7:
        score += 1.0
    elif len(stem) == 2:
        score += 0.3
    score += min(2.0, 1.0 * (repeat.get(stem, 1) - 1))
    return score


def pick(
    cues: Sequence[Cue],
    *,
    per_min: float,
    max_chars: int = 10,
    min_gap: float = 2.0,
    min_score: float = 2.5,
    max_duration: float = 3.5,
) -> List[Keyword]:
    if not cues:
        return []
    total = max(c.end for c in cues)
    want = max(1, int(round(per_min * total / 60.0)))

    words = [_words(c) for c in cues]
    repeat: Dict[str, int] = {}
    for ws in words:
        for stem in {w[0] for w in ws}:
            repeat[stem] = repeat.get(stem, 0) + 1

    cands: List[Keyword] = []
    for i, (c, ws) in enumerate(zip(cues, words)):
        toks = [w[0] for w in ws]
        scores = [_word_score(w[0], w[1], repeat) for w in ws]
        if not scores or max(scores) < min_score:
            continue
        j = max(range(len(toks)), key=lambda k: scores[k])
        lo = hi = j
        # 이웃 어절이 쓸 만하면 붙여 한 덩어리 문구로 (최대 글자 수 안에서)
        for side in (-1, 1):
            k = j + side
            if 0 <= k < len(toks) and scores[k] > 0 and \
                    len(" ".join(toks[min(lo, k):max(hi, k) + 1])) <= max_chars:
                lo, hi = min(lo, k), max(hi, k)
        text = " ".join(toks[lo:hi + 1])
        if len(text) > max_chars:
            text = toks[j]
        best = max(scores[lo:hi + 1])
        cands.append(Keyword(c.start, min(c.end, c.start + max_duration), text,
                             1 if best >= 4.5 else 0, best, i))

    cands.sort(key=lambda k: -k.score)
    chosen: List[Keyword] = []
    for k in cands:
        if len(chosen) >= want:
            break
        if any(abs(k.start - o.start) < min_gap for o in chosen):
            continue
        if any(k.text == o.text for o in chosen):
            continue
        chosen.append(k)
    chosen.sort(key=lambda k: k.start)
    return chosen


def broll_candidates(keywords: Sequence[Keyword]) -> List[Keyword]:
    """사진/영상을 얹을 만한 자리 — 영문 고유명사나 숫자가 든 강한 키워드."""
    return [k for k in keywords if k.tier == 1]
