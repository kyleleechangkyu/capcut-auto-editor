"""faster-whisper 로 단어 단위 전사."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_MODEL_CACHE: Dict[tuple, Any] = {}


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Utterance:
    """whisper가 끊어준 한 덩어리의 발화."""
    index: int
    start: float
    end: float
    text: str
    words: List[Word] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def _load_model(model: str, device: str, compute_type: str):
    key = (model, device, compute_type)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "faster-whisper가 설치되어 있지 않습니다.\n"
            "  pip install faster-whisper\n"
            "설치 후 다시 실행해 주세요."
        ) from exc

    m = WhisperModel(model, device=device, compute_type=compute_type)
    _MODEL_CACHE[key] = m
    return m


def transcribe(
    wav: Path,
    *,
    model: str = "large-v3-turbo",
    language: str = "ko",
    device: str = "cpu",
    compute_type: str = "int8",
    initial_prompt: str = "",
    cache_path: Optional[Path] = None,
    progress=None,
) -> List[Utterance]:
    """오디오를 전사해 발화 목록을 돌려줍니다.

    cache_path 가 주어지고 이미 파일이 있으면 그대로 재사용합니다.
    (설정만 바꿔가며 컷 기준을 튜닝할 때 전사를 다시 돌리지 않기 위함)
    """
    if cache_path and cache_path.exists():
        return load_cache(cache_path)

    whisper = _load_model(model, device, compute_type)

    segments, _info = whisper.transcribe(
        str(wav),
        language=language or None,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        initial_prompt=initial_prompt or None,
        beam_size=5,
        condition_on_previous_text=False,  # 반복 환각 방지
    )

    utterances: List[Utterance] = []
    for i, seg in enumerate(segments):
        text = (seg.text or "").strip()
        if not text:
            continue
        words = [
            Word(start=float(w.start), end=float(w.end), text=(w.word or "").strip())
            for w in (seg.words or [])
            if w.word and w.word.strip()
        ]
        utterances.append(
            Utterance(
                index=len(utterances),
                start=float(seg.start),
                end=float(seg.end),
                text=text,
                words=words,
            )
        )
        if progress:
            progress(seg.end)

    if cache_path:
        save_cache(cache_path, utterances)

    return utterances


def save_cache(path: Path, utterances: List[Utterance]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([asdict(u) for u in utterances], fh, ensure_ascii=False, indent=1)


def load_cache(path: Path) -> List[Utterance]:
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    out: List[Utterance] = []
    for item in raw:
        out.append(
            Utterance(
                index=item["index"],
                start=item["start"],
                end=item["end"],
                text=item["text"],
                words=[Word(**w) for w in item.get("words", [])],
            )
        )
    return out
