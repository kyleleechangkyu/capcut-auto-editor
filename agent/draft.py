"""CapCut 초안(draft) 빌더.

전략
----
1. 영상 트랙은 pycapcut 으로 만듭니다 (스키마를 직접 손대지 않아도 됨).
2. 자막은 사용자가 CapCut 안에서 직접 만든 '견본 초안(seed)'의 텍스트 조각을
   통째로 복제해서 씁니다. 그래야 CapCut의 텍스트 템플릿/花字/애니메이션이
   눈으로 고른 그대로 재현됩니다.
3. 마지막에 draft_meta_info.json 과 platform 정보를 이 컴퓨터에 맞게 손봅니다.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .audio import MediaInfo
from .detect import CutPlan
from .subtitles import Cue

US = 1_000_000  # 마이크로초


# ---------------------------------------------------------------- 견본(seed)

# 텍스트 조각이 참조할 수 있는 재료 목록들
_MATERIAL_BUCKETS = [
    "texts", "effects", "material_animations", "text_templates",
    "canvases", "sound_channel_mappings", "speeds", "placeholder_infos",
    "vocal_separations", "loudnesses",
]


@dataclass
class SeedStyle:
    """견본 초안에서 뽑아낸 자막 스타일."""
    segment: Dict[str, Any]
    materials: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    render_index: int = 14000
    canvas: Dict[str, Any] = field(default_factory=dict)
    platform: Dict[str, Any] = field(default_factory=dict)
    version: Dict[str, Any] = field(default_factory=dict)
    description: str = ""

    @property
    def has_template(self) -> bool:
        return bool(self.materials.get("text_templates"))


def _load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def scan_seed(draft_dir: Path) -> SeedStyle:
    """CapCut에서 직접 만든 견본 초안에서 자막 스타일을 추출합니다.

    견본에는 텍스트가 최소 한 개 있어야 합니다.
    """
    content_path = draft_dir / "draft_content.json"
    if not content_path.exists():
        raise FileNotFoundError(
            f"견본 초안에 draft_content.json 이 없습니다: {draft_dir}\n"
            "CapCut에서 초안을 열고 아무 편집이나 한 뒤 저장해 주세요."
        )

    content = _load_json(content_path)

    text_tracks = [t for t in content.get("tracks", []) if t.get("type") == "text"]
    seg = None
    track = None
    for t in text_tracks:
        segs = t.get("segments") or []
        if segs:
            seg, track = segs[0], t
            break

    if seg is None:
        raise ValueError(
            "견본 초안에서 텍스트 조각을 찾지 못했습니다.\n"
            "CapCut에서 자막 한 줄을 원하는 스타일(텍스트 템플릿 포함)로 만들고 저장해 주세요."
        )

    # 이 텍스트 조각이 참조하는 재료를 전부 긁어옵니다
    wanted = {seg.get("material_id")} | set(seg.get("extra_material_refs") or [])
    wanted.discard(None)

    grabbed: Dict[str, List[Dict[str, Any]]] = {}
    all_materials = content.get("materials", {})
    for bucket in _MATERIAL_BUCKETS:
        items = [m for m in (all_materials.get(bucket) or []) if m.get("id") in wanted]
        if items:
            grabbed[bucket] = copy.deepcopy(items)

    # 설명용 요약
    bits = []
    for e in grabbed.get("effects", []):
        if e.get("type") == "text_shape":
            bits.append(f"말풍선 {e.get('name') or e.get('resource_id')}")
        elif e.get("type") == "text_effect":
            bits.append(f"텍스트 효과 {e.get('name') or e.get('resource_id')}")
    if grabbed.get("text_templates"):
        bits.append("텍스트 템플릿")
    for a in grabbed.get("material_animations", []):
        names = [x.get("name") for x in (a.get("animations") or []) if x.get("name")]
        if names:
            bits.append("애니메이션 " + "/".join(names))

    return SeedStyle(
        segment=copy.deepcopy(seg),
        materials=grabbed,
        render_index=int(track.get("render_index") or 14000),
        canvas=copy.deepcopy(content.get("canvas_config") or {}),
        platform=copy.deepcopy(content.get("platform") or {}),
        version={
            "version": content.get("version"),
            "new_version": content.get("new_version"),
        },
        description=", ".join(bits) or "기본 텍스트 스타일",
    )


def _replace_text_content(raw: str, new_text: str) -> str:
    """텍스트 재료의 content(JSON 문자열) 안의 글자를 바꾸고 스타일 범위를 맞춥니다."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return raw

    data["text"] = new_text
    n = len(new_text)
    for style in data.get("styles") or []:
        style["range"] = [0, n]
    return json.dumps(data, ensure_ascii=False)


def _clone_text_segment(
    seed: SeedStyle, text: str, start_us: int, duration_us: int
) -> tuple[Dict[str, Any], Dict[str, List[Dict[str, Any]]]]:
    """견본 텍스트 조각을 새 id / 새 시간 / 새 글자로 복제합니다."""
    seg = copy.deepcopy(seed.segment)
    id_map: Dict[str, str] = {}

    def fresh(old: Optional[str]) -> Optional[str]:
        if not old:
            return old
        if old not in id_map:
            id_map[old] = uuid.uuid4().hex
        return id_map[old]

    materials: Dict[str, List[Dict[str, Any]]] = {}
    for bucket, items in seed.materials.items():
        cloned = []
        for item in items:
            new_item = copy.deepcopy(item)
            new_item["id"] = fresh(item["id"])
            if bucket == "texts":
                new_item["content"] = _replace_text_content(item.get("content", ""), text)
                # 일부 버전은 별도 필드로도 글자를 들고 있습니다
                if "words" in new_item:
                    new_item.pop("words", None)
            if bucket == "material_animations":
                new_item["id"] = new_item["id"]
            cloned.append(new_item)
        materials[bucket] = cloned

    seg["id"] = uuid.uuid4().hex
    seg["material_id"] = fresh(seed.segment.get("material_id"))
    seg["extra_material_refs"] = [
        fresh(r) for r in (seed.segment.get("extra_material_refs") or [])
    ]
    seg["target_timerange"] = {"start": start_us, "duration": duration_us}
    if seg.get("source_timerange"):
        seg["source_timerange"] = {"start": 0, "duration": duration_us}
    seg["common_keyframes"] = []
    seg["keyframe_refs"] = []

    return seg, materials


# ---------------------------------------------------------------- 초안 빌드

def _ffprobe_material(info: MediaInfo):
    """pymediainfo(libmediainfo) 없이 VideoMaterial 을 만듭니다."""
    from pycapcut import VideoMaterial, CropSettings

    mat = VideoMaterial.__new__(VideoMaterial)
    mat.material_name = info.path.name
    mat.material_id = uuid.uuid4().hex
    mat.path = str(info.path.resolve())
    mat.crop_settings = CropSettings()
    mat.local_material_id = ""
    mat.material_type = "video"
    mat.duration = int(round(info.duration * US))
    mat.width = int(info.width)
    mat.height = int(info.height)
    return mat


def _cover_scale(src_w: int, src_h: int, dst_w: int, dst_h: int) -> float:
    """CapCut 기본 배치(화면 안에 맞춤)에서 화면을 꽉 채우려면 몇 배로 키워야 하는지."""
    if not all((src_w, src_h, dst_w, dst_h)):
        return 1.0
    fit = min(dst_w / src_w, dst_h / src_h)
    cover = max(dst_w / src_w, dst_h / src_h)
    return round(cover / fit, 6) if fit else 1.0


@dataclass
class BuildResult:
    draft_dir: Path
    draft_name: str
    clip_count: int
    cue_count: int
    style_note: str


def build(
    *,
    info: MediaInfo,
    plan: CutPlan,
    cues: Sequence[Cue],
    drafts_dir: Path,
    draft_name: str,
    width: int,
    height: int,
    fps: int,
    fit: str = "cover",
    seed: Optional[SeedStyle] = None,
    subtitle_cfg: Optional[Dict[str, Any]] = None,
) -> BuildResult:
    from pycapcut import DraftFolder, VideoSegment, Timerange, TrackType

    subtitle_cfg = subtitle_cfg or {}
    drafts_dir.mkdir(parents=True, exist_ok=True)

    folder = DraftFolder(str(drafts_dir))
    script = folder.create_draft(draft_name, width, height, fps=fps, allow_replace=True)

    material = _ffprobe_material(info)
    script.add_material(material)
    script.add_track(TrackType.video, "main")

    clip_settings = None
    scale = 1.0
    if fit in ("cover", "auto"):
        src_ar = info.width / info.height if info.height else 1.0
        dst_ar = width / height if height else 1.0
        # auto: 화면비가 거의 같으면 건드리지 않고, 다를 때만 꽉 채웁니다
        if fit == "cover" or abs(src_ar - dst_ar) / max(dst_ar, 1e-6) > 0.05:
            scale = _cover_scale(info.width, info.height, width, height)
    if abs(scale - 1.0) > 1e-4:
        from pycapcut import ClipSettings
        clip_settings = ClipSettings(scale_x=scale, scale_y=scale)

    # ---- 영상 조각들 ----
    timeline = 0
    for keep in plan.keeps:
        src_start = int(round(keep.start * US))
        dur = int(round(keep.duration * US))
        if dur <= 0:
            continue
        seg = VideoSegment(
            material,
            Timerange(timeline, dur),
            source_timerange=Timerange(src_start, dur),
            clip_settings=copy.deepcopy(clip_settings) if clip_settings else None,
        )
        script.add_segment(seg, track_name="main")
        timeline += dur

    # ---- 자막: 견본이 없으면 pycapcut 기본 텍스트로 ----
    cue_count = len(cues)
    style_note = "자막 없음" if not cues else ""
    if cues and seed is None:
        _add_plain_subtitles(script, cues, subtitle_cfg)
        style_note = "기본 스타일 (견본 초안 없음)"

    # ---- 초안 파일로 내보내기 ----
    content = json.loads(script.dumps())

    # ---- 자막: 견본이 있으면 그 조각을 통째로 복제 ----
    if cues and seed is not None:
        content = _inject_seed_subtitles(content, seed, cues)
        style_note = f"견본 스타일 사용 ({seed.description})"

    # ---- 이 컴퓨터에 맞게 마무리 ----
    draft_dir = drafts_dir / draft_name
    _finalize(content, draft_dir, draft_name, timeline, seed)

    with open(draft_dir / "draft_content.json", "w", encoding="utf-8") as fh:
        json.dump(content, fh, ensure_ascii=False, indent=2)

    return BuildResult(
        draft_dir=draft_dir,
        draft_name=draft_name,
        clip_count=len(plan.keeps),
        cue_count=cue_count,
        style_note=style_note,
    )


def _inject_seed_subtitles(
    content: Dict[str, Any], seed: SeedStyle, cues: Sequence[Cue]
) -> Dict[str, Any]:
    segments: List[Dict[str, Any]] = []
    materials = content.setdefault("materials", {})

    for cue in cues:
        start_us = int(round(cue.start * US))
        dur_us = max(1, int(round(cue.duration * US)))
        seg, mats = _clone_text_segment(seed, cue.text, start_us, dur_us)
        segments.append(seg)
        for bucket, items in mats.items():
            materials.setdefault(bucket, []).extend(items)

    content.setdefault("tracks", []).append({
        "attribute": 0,
        "flag": 0,
        "id": uuid.uuid4().hex,
        "is_default_name": True,
        "name": "자동 자막",
        "segments": segments,
        "type": "text",
        "render_index": seed.render_index,
    })
    return content


def _add_plain_subtitles(script, cues: Sequence[Cue], cfg: Dict[str, Any]) -> None:
    """견본이 없을 때 쓰는 최소 스타일 자막 (pycapcut 기본 텍스트)."""
    from pycapcut import TextSegment, TextStyle, ClipSettings, Timerange, TextBorder, TrackType

    pos_y = float(cfg.get("position_y", 0.78))
    transform_y = 1.0 - 2.0 * pos_y  # 위 +1 / 아래 -1
    size = float(cfg.get("font_size", 8.0))

    script.add_track(TrackType.text, "자동 자막")
    for cue in cues:
        seg = TextSegment(
            cue.text,
            Timerange(int(round(cue.start * US)), max(1, int(round(cue.duration * US)))),
            style=TextStyle(size=size, align=1, bold=True, auto_wrapping=True),
            border=TextBorder(width=40.0),
            clip_settings=ClipSettings(transform_y=transform_y),
        )
        script.add_segment(seg, track_name="자동 자막")


def _finalize(
    content: Dict[str, Any],
    draft_dir: Path,
    draft_name: str,
    duration_us: int,
    seed: Optional[SeedStyle],
) -> None:
    """플랫폼 정보와 draft_meta_info.json 을 이 컴퓨터에 맞게 손봅니다."""
    os_name = "mac" if sys.platform == "darwin" else ("windows" if sys.platform == "win32" else "mac")

    plat = dict(content.get("platform") or {})
    plat["os"] = os_name
    if seed and seed.platform:
        # 견본에서 읽은 앱 버전을 따라가면 호환성이 가장 좋습니다
        for key in ("app_version", "app_id", "app_source", "device_id", "hard_disk_id", "mac_address"):
            if seed.platform.get(key):
                plat[key] = seed.platform[key]
        plat["os"] = seed.platform.get("os", os_name)
    content["platform"] = plat
    content["last_modified_platform"] = copy.deepcopy(plat)

    if seed and seed.version.get("version"):
        content["version"] = seed.version["version"]
    if seed and seed.version.get("new_version"):
        content["new_version"] = seed.version["new_version"]

    content["duration"] = duration_us
    content["id"] = str(uuid.uuid4()).upper()
    content["name"] = ""

    now_us = int(time.time() * US)
    meta_path = draft_dir / "draft_meta_info.json"
    meta = _load_json(meta_path) if meta_path.exists() else {}
    meta.update({
        "draft_id": str(uuid.uuid4()).upper(),
        "draft_name": draft_name,
        "draft_fold_path": str(draft_dir),
        "draft_root_path": str(draft_dir.parent),
        "draft_cover": "",
        "tm_duration": duration_us,
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "draft_timeline_materials_size_": 0,
    })
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 유틸

def resolve_drafts_dir(configured: str) -> Path:
    from .config import find_capcut_drafts

    if configured:
        p = Path(os.path.expanduser(configured))
        if p.is_dir():
            return p
        raise FileNotFoundError(
            f"설정한 CapCut 초안 폴더가 없습니다: {p}\n"
            "config.yaml 의 paths.capcut_drafts 를 확인해 주세요."
        )

    found = find_capcut_drafts()
    if found:
        return found
    raise FileNotFoundError(
        "CapCut 초안 폴더를 찾지 못했습니다.\n"
        "CapCut을 한 번 실행해 초안을 하나 만든 뒤, config.yaml 의 "
        "paths.capcut_drafts 에 경로를 적어 주세요."
    )
