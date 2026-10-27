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


_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_DEFAULT_TEXT_STYLE_PATH = _ASSETS_DIR / "default_text_style.json"


def default_text_style() -> Optional[SeedStyle]:
    """사용자가 견본 초안을 고르지 않았을 때 쓸 기본 자막 스타일.

    scan_seed() 는 CapCut 프로젝트에서 스타일을 뽑아내지만, 이건 그 결과를
    앱 안에 미리 저장해 둔 것입니다(agent/assets/default_text_style.json) —
    사용자가 CapCut의 "텍스트 사전 설정"으로 만들어 한 번 적용해 둔 스타일을
    scan_seed() 로 뽑아 고정해 둔 것. 원본 CapCut 프로젝트가 지워져도 이 기본값은
    남아있습니다. 화면에서 다른 견본을 고르면 그게 우선합니다.
    """
    if not _DEFAULT_TEXT_STYLE_PATH.exists():
        return None
    try:
        raw = _load_json(_DEFAULT_TEXT_STYLE_PATH)
    except (OSError, json.JSONDecodeError):
        return None
    return SeedStyle(
        segment=raw.get("segment") or {},
        materials=raw.get("materials") or {},
        render_index=int(raw.get("render_index") or 14000),
        canvas=raw.get("canvas") or {},
        platform=raw.get("platform") or {},
        version=raw.get("version") or {},
        description=raw.get("description") or "기본 스타일",
    )


def scan_seed(draft_dir: Path) -> SeedStyle:
    """CapCut에서 직접 만든 견본 초안에서 자막 스타일을 추출합니다.

    견본에는 텍스트가 최소 한 개 있어야 합니다.
    """
    # 이 CapCut은 draft_info.json 을 실제로 계속 갱신하고, draft_content.json 은
    # (우리 도구가 초안을 처음 만들 때 쓴) 그 시점의 낡은 스냅샷으로 남아있습니다.
    # 사용자가 CapCut 안에서 자막을 직접 꾸민 뒤 견본으로 쓰려는 것이므로
    # draft_info.json 을 먼저 봅니다.
    content_path = draft_dir / "draft_info.json"
    if not content_path.exists():
        content_path = draft_dir / "draft_content.json"
    if not content_path.exists():
        raise FileNotFoundError(
            f"견본 초안에 draft_content.json / draft_info.json 이 없습니다: {draft_dir}\n"
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


# 폰트를 강제로 바꿀 때 같이 비워야 하는 필드들. CapCut은 font_id/
# font_resource_id 가 채워져 있으면 경로보다 그 카탈로그 ID를 우선해서
# 예전 폰트를 계속 쓸 수 있어, 로컬 파일 경로만으로 찾도록 전부 비웁니다.
# agent/assets/default_text_style.json 도 원래 이 방식(빈 ID + 경로만)으로
# 저장돼 있고 실제로 정상 동작이 확인됐습니다.
_FONT_ID_RESET = {
    "font_name": "",
    "font_title": "none",
    "font_id": "",
    "font_resource_id": "",
    "font_url": "",
    "font_category_name": "",
    "font_source_platform": 0,
    "font_third_resource_id": "",
    "font_category_id": "",
    "fonts": [],
}


def apply_font_override(seed: SeedStyle, font_path: str) -> None:
    """견본 스타일의 폰트를 지정한 로컬 폰트 파일 하나로 바꿉니다(제자리 수정).

    화면 비율·그림자 등 나머지 스타일은 그대로 두고 폰트만 바꿉니다. 존재
    여부는 호출하는 쪽(pipeline.py)에서 미리 확인합니다 — 여기서는 그냥
    적용만 합니다.
    """
    for item in seed.materials.get("texts", []):
        for key, value in _FONT_ID_RESET.items():
            if key in item:
                item[key] = value
        item["font_path"] = font_path

        try:
            data = json.loads(item.get("content", ""))
        except (TypeError, ValueError):
            continue
        changed = False
        for style in data.get("styles") or []:
            font = style.get("font")
            if isinstance(font, dict):
                font["path"] = font_path
                font["id"] = ""
                changed = True
        if changed:
            item["content"] = json.dumps(data, ensure_ascii=False)


# ---------------------------------------------------------------- 텍스트 사전 설정

def resolve_text_presets_dir(drafts_dir: Path) -> Path:
    """CapCut '텍스트 사전 설정'(.textpreset) 파일이 있는 폴더.

    초안 폴더(.../User Data/Projects/com.lveditor.draft)와 같은
    'User Data' 아래(.../User Data/Cache/onlineMaterial)에 있습니다.
    """
    return drafts_dir.parent.parent / "Cache" / "onlineMaterial"


def list_text_presets(drafts_dir: Path) -> List[Dict[str, str]]:
    """사용자가 CapCut에서 저장해 둔 텍스트 사전 설정 목록."""
    presets_dir = resolve_text_presets_dir(drafts_dir)
    if not presets_dir.is_dir():
        return []
    out = []
    for p in presets_dir.glob("*.textpreset"):
        try:
            data = _load_json(p)
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"name": str(data.get("name") or p.stem), "path": str(p)})
    out.sort(key=lambda f: f["name"])
    return out


def load_text_preset(path: Path) -> Dict[str, Any]:
    """텍스트 사전 설정 파일 하나를 읽어 스타일 값과 리소스(폰트/글로우)를 뽑아냅니다."""
    raw = _load_json(path)
    resources = [r for r in (raw.get("resources") or []) if isinstance(r, dict)]
    font_path = ""
    for res in resources:
        if res.get("panel") == "fonts" and res.get("file_path"):
            font_path = res["file_path"]
            break
    return {
        "name": raw.get("name") or path.stem,
        "style": raw.get("style") or {},
        "font_path": font_path,
        "resources": resources,
        "cover_image_path": raw.get("cover_image_path") or "",
    }


def _hex_to_rgb01(hex_color: str) -> Optional[List[float]]:
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return None
    try:
        return [int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
    except ValueError:
        return None


def _solid(rgb: List[float]) -> Dict[str, Any]:
    return {"solid": {"color": rgb}, "render_type": "solid"}


# 굵게(has_bold_width)일 때 CapCut이 실제로 적어 넣는 값 (사전 설정을 적용한 실제 초안에서 확인)
_BOLD_WIDTH = 0.00800000037997961


def apply_preset_style(seed: SeedStyle, preset: Dict[str, Any]) -> List[str]:
    """견본 스타일 위에 텍스트 사전 설정(.textpreset)의 속성을 전부 입힙니다(제자리 수정).

    .textpreset 은 draft_content.json 의 완전한 텍스트 스키마와 다른 CapCut 자체의
    단순화된 표현입니다. 각 속성이 실제 초안에서 어떤 필드로 바뀌는지는, 사용자가
    CapCut에서 같은 사전 설정을 적용해 저장한 초안과 우리가 만든 초안을 필드 단위로
    대조해 확인한 대응만 씁니다:

    - 글자: 크기/색/정렬/자간/줄간격/투명도/기울임/밑줄
    - 폰트: apply_font_override 재사용
    - 그림자: has_shadow + styles[].shadows[] (diffuse = smoothing/18 — 실제 값 3쌍으로 확인)
    - 테두리(border): styles[].strokes[] + border_* (이전엔 빠져 있던 부분)
    - 굵게: styles[].bold + bold_width
    - 세로 크기/가로 크기·위치·회전: 세그먼트 clip.scale / clip.transform / uniform_scale
    - 글로우(light_*): bloom 이펙트 재료를 새로 만들어 세그먼트에 연결
    - check_flag: 7 | 테두리 8 | 그림자·굵게 32 | 글로우 64

    반환값은 대응 관계를 확인하지 못해 적용하지 못한 속성의 설명 목록입니다
    (말풍선/꽃 효과/곡선 텍스트/배경 프레임 — 해당 사전 설정에선 빠지므로 알려야 함).
    """
    unsupported: List[str] = []
    font_path = preset.get("font_path") or ""
    if font_path:
        apply_font_override(seed, font_path)
    seed.description = str(preset.get("name") or seed.description)

    st = preset.get("style") or {}
    rgb = _hex_to_rgb01(st.get("color", ""))
    has_shadow = bool(st.get("shadow_checked"))
    shadow_rgb = _hex_to_rgb01(st.get("shadow_color", "")) if has_shadow else None
    has_border = bool(st.get("border_item_checked"))
    border_rgb = _hex_to_rgb01(st.get("border_color", "")) if has_border else None
    has_bold = bool(st.get("has_bold_width"))
    has_glow = bool(st.get("light_checked")) and bool(st.get("light_res_id"))

    if st.get("bubbleResId"):
        unsupported.append("말풍선(텍스트 쉐이프)")
    if st.get("flowerResId"):
        unsupported.append("꽃 글자 효과")
    if st.get("curve_enable") or st.get("curve_checked"):
        unsupported.append("곡선 텍스트")
    if st.get("frame_item_checked"):
        unsupported.append("배경 프레임")

    check_flag = 7
    if has_border:
        check_flag |= 8
    if has_shadow or has_bold:
        check_flag |= 32
    if has_glow:
        check_flag |= 64

    for item in seed.materials.get("texts", []):
        for key, src in (("font_size", "font_size"), ("line_spacing", "line_spacing"),
                         ("letter_spacing", "letter_spacing"), ("text_alpha", "text_alpha"),
                         ("global_alpha", "global_alpha")):
            if src in st:
                item[key] = float(st[src])
        if "alignment" in st:
            item["alignment"] = int(st["alignment"])
        if "italic_degree" in st:
            item["italic_degree"] = st["italic_degree"]
        if "has_underline" in st:
            item["underline"] = bool(st["has_underline"])
        if "type_setting" in st:
            item["typesetting"] = int(st["type_setting"])
        if rgb and st.get("color"):
            item["text_color"] = st["color"]
        item["use_effect_default_color"] = not has_glow

        item["has_shadow"] = has_shadow
        if has_shadow:
            if st.get("shadow_color"):
                item["shadow_color"] = st["shadow_color"]
            for key in ("shadow_alpha", "shadow_angle", "shadow_distance", "shadow_smoothing"):
                if key in st:
                    item[key] = float(st[key])

        item["bold_width"] = _BOLD_WIDTH if has_bold else 0.0
        if has_border:
            item["border_alpha"] = 1.0
            item["border_mode"] = 0
            if st.get("border_color"):
                item["border_color"] = st["border_color"]
            if "border_width" in st:
                item["border_width"] = float(st["border_width"])
        item["check_flag"] = check_flag

        try:
            data = json.loads(item.get("content", ""))
        except (TypeError, ValueError):
            continue
        for sty in data.get("styles") or []:
            if "font_size" in st:
                sty["size"] = float(st["font_size"])
            if rgb:
                sty["fill"] = {"content": _solid(rgb)}
            sty["useLetterColor"] = True

            if has_shadow:
                shadows = sty.get("shadows") or [{
                    "thickness_projection_distance": 0, "thickness_projection_enable": False,
                    "thickness_projection_angle": -45, "distance": 0, "diffuse": 0.04,
                    "angle": -45, "alpha": 0.75, "content": _solid([0.0, 0.0, 0.0]),
                }]
                sh = shadows[0]
                if shadow_rgb:
                    sh["content"] = _solid(shadow_rgb)
                for key in ("shadow_alpha", "shadow_angle", "shadow_distance"):
                    if key in st:
                        sh[key.replace("shadow_", "")] = float(st[key])
                if st.get("use_shadow_diffuse") and "shadow_smoothing" in st:
                    sh["diffuse"] = float(st["shadow_smoothing"]) / 18.0
                sty["shadows"] = [sh]
            else:
                sty.pop("shadows", None)

            if has_border:
                sty["strokes"] = [{
                    "width": float(st.get("border_width", 0.0)), "mode": 0,
                    "content": _solid(border_rgb or [0.0, 0.0, 0.0]),
                }]
            else:
                sty.pop("strokes", None)

            if has_bold:
                sty["bold"] = True
            else:
                sty.pop("bold", None)
        item["content"] = json.dumps(data, ensure_ascii=False)

    # --- 세그먼트(위치/크기/회전) ---
    seg = seed.segment
    clip = seg.setdefault("clip", {})
    scale = clip.setdefault("scale", {"x": 1.0, "y": 1.0})
    trans = clip.setdefault("transform", {"x": 0.0, "y": 0.0})
    if "scale_x" in st:
        scale["x"] = float(st["scale_x"])
    if "scale_y" in st:
        scale["y"] = float(st["scale_y"])
    if "transform_x" in st:
        trans["x"] = float(st["transform_x"])
    if "transform_y" in st:
        trans["y"] = float(st["transform_y"])
    if "rotation" in st:
        clip["rotation"] = float(st["rotation"])
    if "global_alpha" in st:
        clip["alpha"] = float(st["global_alpha"])
    if "uniform_scale_enabled" in st:
        seg["uniform_scale"] = {
            "on": bool(st["uniform_scale_enabled"]),
            "value": (seg.get("uniform_scale") or {}).get("value", 1.0),
        }

    # --- 글로우: bloom 이펙트 재료 ---
    effects = seed.materials.setdefault("effects", [])
    old_blooms = {e.get("id") for e in effects if e.get("type") == "bloom"}
    effects[:] = [e for e in effects if e.get("type") != "bloom"]
    refs = [r for r in (seg.get("extra_material_refs") or []) if r not in old_blooms]
    if has_glow:
        res_id = str(st["light_res_id"])
        glow_res = next((r for r in preset.get("resources", [])
                         if r.get("panel") == "text_glow"), {})
        cover = Path(preset.get("cover_image_path") or "")
        effect_path = ""
        if glow_res.get("file_path") and cover.parent.parent.name:
            effect_path = str(cover.parent.parent / "effect" / res_id / glow_res["file_path"])
        glow_id = uuid.uuid4().hex
        effects.append({
            "id": glow_id, "effect_id": res_id, "resource_id": res_id,
            "third_resource_id": "", "name": "", "report_name": "", "type": "bloom",
            "sub_type": "none", "path": effect_path, "value": 0.0, "visible": True,
            "item_effect_type": 0, "category_id": "", "category_name": "",
            "category_key": "", "sub_category_id": "", "sub_category_name": "",
            "platform": "all", "apply_target_type": 0, "source_platform": 1,
            "version": "", "adjust_params": [], "time_range": None, "formula_id": "",
            "enable_skin_tone_correction": False, "algorithm_artifact_path": "",
            "intensity_key": "", "face_adjust_params": [], "exclusion_group": [],
            "panel_id": "text_glow",
            "bloom_params": {
                "color": st.get("light_color") or st.get("color") or "#ffffff",
                "strength": float(st.get("light_strength", 0.5)),
                "range": float(st.get("light_range", 0.2)),
                "dir_x": float(st.get("light_hor_degree", 0.5)),
                "dir_y": float(st.get("light_ver_degree", 0.5)),
            },
            "request_id": "", "color_match_info": None, "multi_language_current": "",
            "lumi_hub_path": "", "covering_relation_change": 0,
            "beauty_face_auto_preset_id": "", "beauty_body_auto_preset_id": "",
            "beauty_face_auto_retouch_info": {"face_id": [], "beauty_face_auto_retouch_id": ""},
            "smart_color_mode": 0, "is_from_intelligent_quality": False,
        })
        refs.append(glow_id)
    seg["extra_material_refs"] = refs
    return unsupported


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
    scaffold_dir: Optional[Path] = None,
    auto_retouch: bool = False,
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

    if not auto_retouch:
        _opt_out_of_retouch(content)

    # ---- 자막: 견본이 있으면 그 조각을 통째로 복제 ----
    if cues and seed is not None:
        content = _inject_seed_subtitles(content, seed, cues)
        style_note = f"견본 스타일 사용 ({seed.description})"

    # ---- CapCut이 실제로 요구하는 부속 파일을 진짜 초안에서 통째로 가져옵니다 ----
    # pycapcut의 create_draft() 는 draft_content.json / draft_meta_info.json 만 만드는데,
    # 최신 CapCut은 draft_info.json 을 비롯해 Resources/, Timelines/, draft_virtual_store.json 등
    # 훨씬 많은 파일을 요구한다. 그게 없으면 목록엔 뜨지만 열 때 "프로젝트를 사용할 수 없음"
    # 오류가 난다. 실제 초안(seed 또는 다른 기존 초안) 폴더를 스캐폴드로 통째로 복제한 뒤
    # 콘텐츠 파일만 우리가 만든 것으로 덮어써서 이 문제를 피한다.
    draft_dir = drafts_dir / draft_name
    if scaffold_dir and scaffold_dir.is_dir() and scaffold_dir.resolve() != draft_dir.resolve():
        shutil.copytree(scaffold_dir, draft_dir, dirs_exist_ok=True)
        for stale in ("draft_cover.jpg", "template-2.tmp"):
            stale_path = draft_dir / stale
            if stale_path.exists():
                stale_path.unlink()
        # Timelines/project.json 은 견본의 main_timeline_id 를 그대로 담고 있어서
        # 재생기(플레이어)가 우리가 쓴 새 콘텐츠 대신 견본의 (이제는 없는) 타임라인을
        # 가리키게 된다 — 편집 화면은 draft_content.json 을 직접 읽어 정상으로 보이지만
        # 재생은 안 되는 원인이었다. CapCut이 열 때 draft_content.json 기준으로 새로
        # 만들도록 통째로 지운다.
        stale_timelines = draft_dir / "Timelines"
        if stale_timelines.exists():
            shutil.rmtree(stale_timelines)

        # 스캐폴드는 구조(어떤 파일이 있어야 CapCut이 여는지)만 빌려오는 것 —
        # 원래 프로젝트의 미디어 목록/작업 흔적이 그대로 딸려오지 않게 비운다.
        _sanitize_scaffold(draft_dir, material)

    # ---- 이 컴퓨터에 맞게 마무리 ----
    _finalize(content, draft_dir, draft_name, timeline, seed)

    raw = json.dumps(content, ensure_ascii=False, indent=2)
    for fname in ("draft_content.json", "draft_info.json", "draft_info.json.bak"):
        with open(draft_dir / fname, "w", encoding="utf-8") as fh:
            fh.write(raw)

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


def _opt_out_of_retouch(content: Dict[str, Any]) -> None:
    """영상 재료에 "얼굴 보정은 이미 정해졌다(없음)"고 적어 CapCut이 자동 보정을 얹지 못하게 한다.

    이 필드가 없으면 CapCut이 초안을 열 때 사용자의 기본 얼굴 보정 프리셋(figure 이펙트
    13개 + makeup_root)을 클립마다 붙인다. 컷이 많은(80조각 안팎) 초안에선 그 이펙트
    수백~천 개가 재생을 못 따라가 영상은 검고 소리만 나오는 구간이 생긴다.
    """
    for mat in content.get("materials", {}).get("videos", []):
        mat["is_set_beauty_mode"] = True
        mat["is_unified_beauty_mode"] = False
        mat["beauty_face_preset_infos"] = []
        mat["beauty_face_auto_preset_infos"] = []
        mat["beauty_face_auto_preset"] = {"preset_id": "", "name": "", "rate_map": "", "scene": ""}
        mat["beauty_body_preset_id"] = ""
        mat["beauty_body_auto_preset"] = None


def _sanitize_scaffold(draft_dir: Path, material) -> None:
    """복제해 온 스캐폴드에서 원래 프로젝트의 내용물을 비우고 이 영상 하나만 남긴다.

    스캐폴드(가장 최근 기존 초안)를 통째로 복사하면 CapCut의 '미디어' 목록
    (draft_meta_info.json 의 draft_materials)과 작업 흔적(Resources/, video/,
    key_value.json 등)까지 같이 와서, 영상 하나만 넣었는데 지금까지 만든 모든
    리소스가 프로젝트에 들어가 있게 된다.
    """
    # 프로젝트가 만든 부산물(알고리즘 결과 wav 등) — 폴더 구조는 두고 파일만 지운다
    for sub in ("Resources", "video", "matting", "smart_crop", "subdraft",
                "adjust_mask", "qr_upload"):
        root = draft_dir / sub
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
    if (draft_dir / "Resources").is_dir():
        for sub in ("audioAlg", "combination", "digitalHuman", "videoAlg"):
            (draft_dir / "Resources" / sub).mkdir(exist_ok=True)

    def _rewrite(name: str, fn) -> None:
        path = draft_dir / name
        if not path.is_file():
            return
        try:
            data = _load_json(path)
        except (OSError, ValueError):
            return
        fn(data)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)

    now_s = int(time.time())
    entry_id = str(material.material_id)
    video_path = str(material.path)

    def _meta(d: Dict[str, Any]) -> None:
        entry = {
            "ai_group_type": "", "create_time": -1,
            "duration": int(material.duration), "enter_from": 0,
            "extra_info": material.material_name, "file_Path": video_path,
            "height": int(material.height), "id": entry_id,
            "import_time": now_s, "import_time_ms": -1, "item_source": 1,
            "material_color_tag": "", "md5": "", "metetype": "video",
            "roughcut_time_range": {"duration": int(material.duration), "start": 0},
            "sub_time_range": {"duration": -1, "start": -1},
            "type": 0, "width": int(material.width),
        }
        buckets = d.get("draft_materials") or []
        kept = {b.get("type"): b for b in buckets if isinstance(b, dict)}
        d["draft_materials"] = [
            {"type": t, "value": [entry] if t == 0 else []}
            for t in sorted(set(kept) | {0, 1, 2, 3, 6, 7, 8})
        ]
        d["draft_materials_copied_info"] = []
        d["draft_segment_extra_info"] = []
        d["draft_removable_storage_device"] = ""

    _rewrite("draft_meta_info.json", _meta)

    def _vstore(d: Dict[str, Any]) -> None:
        d["draft_materials"] = []
        for bucket in d.get("draft_virtual_store") or []:
            if bucket.get("type") == 1:
                bucket["value"] = [{"child_id": entry_id, "parent_id": ""}]

    _rewrite("draft_virtual_store.json", _vstore)

    _rewrite("draft_agency_config.json", lambda d: d.__setitem__("marterials", []))
    _rewrite("key_value.json", lambda d: d.clear())
    _rewrite("common_attachment/attachment_id_mapping.json",
             lambda d: d.__setitem__("id_mapping", {"mapping": []}))

    def _editing(d: Dict[str, Any]) -> None:
        ed = d.get("editing_draft")
        if isinstance(ed, dict):
            ed["paste_segment_list"] = []

    _rewrite("attachment_editing.json", _editing)

    settings = draft_dir / "draft_settings"
    if settings.is_file():
        settings.write_text(
            "[General]\ncloud_last_modify_platform=mac\n"
            f"draft_create_time={now_s}\ndraft_last_edit_time={now_s}\n"
            "real_edit_keys=0\nreal_edit_seconds=0\ntimeline_use_split_scene=true\n",
            encoding="utf-8",
        )


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

def find_scaffold(drafts_dir: Path, *, exclude: str = "") -> Optional[Path]:
    """구조 복제용으로 쓸 실제 CapCut 초안을 하나 고릅니다 (자막 견본과 무관).

    자막 스타일 견본을 고르지 않았을 때도 CapCut이 요구하는 부속 파일이
    필요하므로, 가장 최근에 쓰인 실제 초안을 스캐폴드로 씁니다.
    """
    if not drafts_dir.is_dir():
        return None
    candidates = []
    for p in drafts_dir.iterdir():
        if not p.is_dir() or p.name == exclude:
            continue
        if not ((p / "draft_info.json").exists() or (p / "draft_content.json").exists()):
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0
        candidates.append((mtime, p))
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


def find_referenced_media(drafts_dir: Path, names: Sequence[str]) -> Optional[set]:
    """CapCut 프로젝트들이 아직 쓰고 있는 미디어 파일 이름을 가려냅니다.

    names 중에서 휴지통(.recycle_bin)이 아닌 살아있는 프로젝트의 초안 파일에
    이름이 등장하는 것만 돌려줍니다 — 우리 도구가 만든 초안이든 사용자가 CapCut에서
    직접 만든 프로젝트든 구분하지 않습니다. 하나라도 읽지 못하면 None(판단 불가 —
    호출하는 쪽은 아무것도 지우면 안 됨).

    한글 파일명은 macOS가 NFD로 돌려주고 JSON엔 NFC로 적혀 있을 수 있어 두 형태를
    모두 찾습니다.
    """
    if not drafts_dir.is_dir():
        return None

    files: List[Path] = []
    try:
        for proj in drafts_dir.iterdir():
            # .recycle_bin(삭제한 프로젝트), .cloud_cache_* 등 숨김 폴더는 제외
            if proj.name.startswith(".") or not proj.is_dir():
                continue
            for fname in ("draft_info.json", "draft_content.json", "draft_meta_info.json"):
                if (proj / fname).is_file():
                    files.append(proj / fname)
            timelines = proj / "Timelines"
            if timelines.is_dir():
                files.extend(timelines.glob("*/draft_info.json"))
                files.extend(timelines.glob("*/draft_content.json"))
    except OSError:
        return None

    import unicodedata

    forms = {
        n: {unicodedata.normalize("NFC", n), unicodedata.normalize("NFD", n)} for n in names
    }
    used: set = set()
    for f in files:
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        text = unicodedata.normalize("NFC", raw)
        for n, variants in forms.items():
            if n not in used and any(v in raw or v in text for v in variants):
                used.add(n)
    return used


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
