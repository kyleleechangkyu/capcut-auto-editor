"""ffmpeg / ffprobe 래퍼: 메타데이터, 오디오 추출, 무음 구간 감지."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List


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
