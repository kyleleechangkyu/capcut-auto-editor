"""ffmpeg / ffprobe 래퍼: 메타데이터, 오디오 추출, 무음 구간 감지."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple


class FFmpegMissing(RuntimeError):
    pass


def _run(cmd: List[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise FFmpegMissing(
            "ffmpeg / ffprobe 를 찾을 수 없습니다.  brew install ffmpeg  으로 설치해 주세요."
        ) from exc


@dataclass
class MediaInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def probe(path: Path) -> MediaInfo:
    res = _run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if res.returncode != 0:
        raise RuntimeError(
            f"'{path.name}' 을 열지 못했습니다. 파일이 손상됐거나 지원하지 않는 형식입니다."
        )

    data = json.loads(res.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None:
        raise RuntimeError(f"'{path.name}' 에는 영상이 들어 있지 않습니다.")

    # 회전 메타데이터가 있으면 가로/세로를 뒤집어 실제 보이는 크기를 씁니다
    width = int(video.get("width", 0))
    height = int(video.get("height", 0))
    rotation = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = abs(int(sd["rotation"])) % 180
    if rotation == 90:
        width, height = height, width

    fps = 30.0
    raw_fps = video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1"
    try:
        num, den = raw_fps.split("/")
        if float(den) != 0:
            fps = float(num) / float(den)
    except (ValueError, ZeroDivisionError):
        pass

    duration = float(data.get("format", {}).get("duration", 0.0) or 0.0)

    return MediaInfo(
        path=path,
        duration=duration,
        width=width,
        height=height,
        fps=round(fps, 3),
        has_audio=audio is not None,
    )


def extract_wav(src: Path, dst: Path, sample_rate: int = 16000) -> Path:
    """전사용 16kHz 모노 wav를 뽑습니다."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    res = _run([
        "ffmpeg", "-y", "-v", "error", "-i", str(src),
        "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le",
        str(dst),
    ])
    if res.returncode != 0:
        raise RuntimeError(
            f"'{src.name}' 에서 소리를 꺼내지 못했습니다. 다른 형식으로 내보낸 뒤 다시 시도해 주세요."
        )
    return dst


_SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def detect_silence(
    wav: Path, threshold_db: float = -34.0, min_duration: float = 0.3
) -> List[Tuple[float, float]]:
    """ffmpeg silencedetect 로 (시작, 끝) 무음 구간 목록을 얻습니다.

    detect.find_nonspeech_cuts() 의 보완용입니다: whisper의 단어 타임스탬프는
    실제로 조용한 구간도 이전/다음 단어에 넉넉하게 걸쳐 보고하는 경우가 있어
    (그 결과, 진짜 침묵인데도 "말이 이어지는 중"으로 판단됨), 단어 인식만으로는
    못 잡는 침묵이 남는다. dB 기준으로 한 번 더 훑어 그 틈을 메운다.
    """
    res = _run([
        "ffmpeg", "-v", "info", "-i", str(wav),
        "-af", f"silencedetect=noise={threshold_db}dB:d={min_duration}",
        "-f", "null", "-",
    ])
    # silencedetect 결과는 stderr로 나옵니다
    log = res.stderr or ""

    spans: List[Tuple[float, float]] = []
    pending: float | None = None
    for line in log.splitlines():
        m = _SIL_START.search(line)
        if m:
            pending = max(0.0, float(m.group(1)))
            continue
        m = _SIL_END.search(line)
        if m and pending is not None:
            end = float(m.group(1))
            if end > pending:
                spans.append((pending, end))
            pending = None

    if pending is not None:  # 파일 끝까지 무음인 경우
        total = duration_of(wav)
        if total > pending:
            spans.append((pending, total))

    return spans


def duration_of(path: Path) -> float:
    """영상/오디오 무관하게 길이(초)만 읽습니다."""
    res = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path),
    ])
    try:
        return float(res.stdout.strip())
    except (TypeError, ValueError):
        return 0.0
