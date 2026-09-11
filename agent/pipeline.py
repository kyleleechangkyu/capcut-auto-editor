"""전체 흐름: 영상 하나 → CapCut 초안 하나."""

from __future__ import annotations

import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from . import audio, detect, draft, report, subtitles, transcribe
from .config import Config

# 단계 정의 — 화면의 진행 표시와 1:1로 대응합니다
STAGES = [
    ("probe", "영상 확인"),
    ("audio", "오디오 추출"),
    ("transcribe", "말 알아듣는 중"),
    ("detect", "컷 지점 찾는 중"),
    ("subtitle", "자막 만드는 중"),
    ("draft", "CapCut 초안 쓰는 중"),
]


@dataclass
class Progress:
    """진행 상황을 화면으로 흘려보내는 통로."""
    on_stage: Optional[Callable[[str, str, float], None]] = None
    on_log: Optional[Callable[[str], None]] = None

    def stage(self, key: str, note: str = "", ratio: float = 0.0) -> None:
        if self.on_stage:
            self.on_stage(key, note, ratio)

    def log(self, msg: str) -> None:
        if self.on_log:
            self.on_log(msg)
        else:
            print(f"  {msg}", flush=True)


@dataclass
class RunResult:
    source: Path
    draft_name: str
    draft_dir: Path
    srt: Optional[Path]
    report_md: Path
    plan: detect.CutPlan
    cues: List[subtitles.Cue] = field(default_factory=list)
    elapsed: float = 0.0
    style_note: str = ""
    info: Optional[audio.MediaInfo] = None


def process(
    video: Path,
    cfg: Config,
    *,
    reuse_transcript: bool = True,
    progress: Optional[Progress] = None,
) -> RunResult:
    p = progress or Progress()
    t0 = time.time()
    video = Path(video).resolve()

    # '파일 선택'으로 media_dir 바깥의 영상을 골랐다면 CapCut이 읽지 못하는
    # 위치일 수 있으므로 (macOS 샌드박스 — media_dir 참고) media_dir 로 복사해
    # 초안이 접근 가능한 경로를 기억하게 한다. 드래그로 넣은 영상은 서버가
    # 이미 media_dir 에 저장해 두므로 다시 복사하지 않는다.
    if sys.platform == "darwin" and video.parent != cfg.media_dir:
        dest = cfg.media_dir / video.name
        if dest.exists() and dest.resolve() != video.resolve():
            dest = cfg.media_dir / f"{video.stem}-{int(time.time())}{video.suffix}"
        if not (dest.exists() and dest.resolve() == video.resolve()):
            shutil.copy2(video, dest)
        video = dest

    job = cfg.work_dir / video.stem
    job.mkdir(parents=True, exist_ok=True)

    # 1) 영상 확인 -------------------------------------------------
    p.stage("probe")
    info = audio.probe(video)
    p.log(f"{info.width}×{info.height} · {info.fps:g}fps · {info.duration:.1f}초")
    if not info.has_audio:
        raise RuntimeError("이 영상에는 소리가 없어서 컷 편집과 자막을 만들 수 없습니다.")

    # 2) 오디오 추출 -----------------------------------------------
    p.stage("audio")
    wav = job / "audio.wav"
    if not wav.exists():
        audio.extract_wav(video, wav)

    # 3) 전사 ------------------------------------------------------
    tr = cfg.get_path("transcribe", {}) or {}
    cache = job / "transcript.json" if reuse_transcript else None
    if cache and cache.exists():
        p.stage("transcribe", "이전 결과 재사용", 1.0)
    else:
        p.stage("transcribe", "처음 한 번은 모델을 내려받느라 오래 걸립니다")

    def _tick(seconds_done: float) -> None:
        if info.duration:
            p.stage("transcribe", "", min(0.99, seconds_done / info.duration))

    utterances = transcribe.transcribe(
        wav,
        model=tr.get("model", "large-v3-turbo"),
        language=tr.get("language", "ko"),
        device=tr.get("device", "cpu"),
        compute_type=tr.get("compute_type", "int8"),
        initial_prompt=tr.get("initial_prompt", "") or "",
        cache_path=cache,
        progress=_tick,
    )
    p.stage("transcribe", f"발화 {len(utterances)}개", 1.0)

    # 4) 컷 판정 ---------------------------------------------------
    p.stage("detect")
    cut_cfg = cfg.get_path("cut", {}) or {}
    cuts: List[detect.Cut] = []

    sil = cut_cfg.get("silence", {}) or {}
    if sil.get("enabled", True):
        found = detect.find_nonspeech_cuts(
            utterances,
            info.duration,
            min_duration=float(sil.get("min_duration", 0.12)),
        )
        cuts += found
        p.log(f"무음/비발화 {len(found)}곳")

    st = cut_cfg.get("stutter", {}) or {}
    if st.get("enabled", True):
        found = detect.find_stutter_cuts(
            utterances,
            threshold=float(st.get("similarity", 0.85)),
            max_gap=float(st.get("max_gap", 1.2)),
        )
        cuts += found
        p.log(f"말 더듬음 {len(found)}곳")

    rt = cut_cfg.get("retake", {}) or {}
    if rt.get("enabled", True):
        found = detect.find_retake_cuts(
            utterances,
            threshold=float(rt.get("similarity", 0.72)),
            lookahead=int(rt.get("lookahead", 3)),
            max_gap=float(rt.get("max_gap", 25.0)),
            keep=str(rt.get("keep", "last")),
        )
        cuts += found
        p.log(f"재촬영(NG) {len(found)}곳")

    plan = detect.build_plan(
        info.duration,
        cuts,
        lead_in=float(cut_cfg.get("lead_in", 0.08)),
        lead_out=float(cut_cfg.get("lead_out", 0.14)),
        min_clip=float(cut_cfg.get("min_clip", 0.30)),
        merge_gap=float(cut_cfg.get("merge_gap", 0.18)),
        fps=info.fps,
    )
    p.stage("detect", f"클립 {len(plan.keeps)}개", 1.0)

    # 5) 자막 ------------------------------------------------------
    p.stage("subtitle")
    sub = cfg.get_path("subtitle", {}) or {}
    cues: List[subtitles.Cue] = []
    srt_path: Optional[Path] = None
    if sub.get("enabled", True):
        cues = subtitles.build_cues(
            utterances,
            plan,
            min_duration=float(sub.get("min_duration", 0.7)),
            max_duration=float(sub.get("max_duration", 4.0)),
            strip_punctuation=bool(sub.get("strip_punctuation", True)),
        )
        srt_path = subtitles.write_srt(cues, job / f"{video.stem}.srt")
    p.stage("subtitle", f"{len(cues)}줄", 1.0)

    # 6) 초안 ------------------------------------------------------
    p.stage("draft")
    drafts_dir = draft.resolve_drafts_dir(str(cfg.get("capcut_drafts") or ""))

    seed = None
    scaffold_dir = None
    seed_name = str(cfg.get("seed_draft") or "")
    if seed_name and (drafts_dir / seed_name).is_dir():
        scaffold_dir = drafts_dir / seed_name
        try:
            seed = draft.scan_seed(drafts_dir / seed_name)
            p.log(f"자막 스타일: {seed.description}")
        except (ValueError, FileNotFoundError) as exc:
            p.log(f"견본을 못 읽어 기본 스타일로 갑니다 — {exc}")
    if seed is None:
        # 화면에서 견본을 따로 고르지 않았으면 앱에 저장된 기본 자막 스타일을 씁니다.
        seed = draft.default_text_style()
        if seed:
            p.log(f"자막 스타일: {seed.description} (기본값)")
    if scaffold_dir is None:
        # 자막 견본을 고르지 않았어도 CapCut이 요구하는 부속 파일은 필요하므로
        # 기존 초안 아무거나 하나를 구조 복제용 스캐폴드로 쓴다.
        scaffold_dir = draft.find_scaffold(drafts_dir)

    d = cfg.get_path("draft", {}) or {}
    width = int(d.get("width", 1080)) or info.width
    height = int(d.get("height", 1920)) or info.height

    now = datetime.now()
    draft_name = _unique_name(
        drafts_dir,
        str(d.get("name_pattern", "{name}_auto_{time}")).format(
            name=video.stem, date=now.strftime("%Y%m%d"), time=now.strftime("%H%M")
        ),
    )

    built = draft.build(
        info=info,
        plan=plan,
        cues=cues,
        drafts_dir=drafts_dir,
        draft_name=draft_name,
        width=width,
        height=height,
        fps=int(d.get("fps", 30)),
        fit=str(d.get("fit", "auto")),
        seed=seed,
        subtitle_cfg=sub,
        scaffold_dir=scaffold_dir,
    )
    p.stage("draft", built.draft_name, 1.0)

    report.write_json(plan, cues, job / "cuts.json")
    md = report.write_markdown(
        info, plan, cues, job / "report.md",
        draft_name=built.draft_name, style_note=built.style_note,
    )

    return RunResult(
        source=video,
        draft_name=built.draft_name,
        draft_dir=built.draft_dir,
        srt=srt_path,
        report_md=md,
        plan=plan,
        cues=cues,
        elapsed=time.time() - t0,
        style_note=built.style_note,
        info=info,
    )


def _unique_name(drafts_dir: Path, base: str) -> str:
    """같은 이름의 초안을 덮어쓰지 않도록 뒤에 번호를 붙입니다."""
    name = base
    n = 2
    while (drafts_dir / name).exists():
        name = f"{base}-{n}"
        n += 1
    return name
