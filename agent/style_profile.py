"""레퍼런스(내가 직접 마무리한 CapCut 프로젝트)에서 편집 패턴을 읽어 프로필로 저장합니다.

영상 파일(mp4)만으로는 "어느 구간이 키워드 자막이고 어느 소리가 효과음인지"를 알 수
없어서, 같은 이름의 CapCut 프로젝트(초안)를 찾아 그 안의 트랙 구성을 읽습니다 —
키워드 자막(굵은 큰 글씨) 개수·길이·크기·위치, 효과음 종류와 개수, B-roll(사진/영상
얹기) 개수·길이, 그리고 우리 도구가 처음 만든 컷(draft_content.json) 대비 사용자가
어떤 컷을 지웠는지. 짐작이 아니라 실제 편집 결과에서 센 숫자만 저장합니다.

찾을 수 없는 레퍼런스(프로젝트가 지워졌거나 CapCut 밖에서 만든 영상)는 건너뛰고
`skipped` 에 이유를 남깁니다.
"""

from __future__ import annotations

import copy
import json
import statistics
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

APP_ROOT = Path(__file__).resolve().parent.parent
PROFILE_PATH = APP_ROOT / "style_profile.json"

US = 1_000_000
_SFX_MAX_US = 3_000_000
_AUDIO_BUCKETS = ["speeds", "placeholder_infos", "beats", "sound_channel_mappings", "vocal_separations"]


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _load(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _live_json(draft_dir: Path) -> Optional[Path]:
    # 이 CapCut은 draft_info.json 만 계속 갱신한다(draft.scan_seed 참고)
    for name in ("draft_info.json", "draft_content.json"):
        if (draft_dir / name).is_file():
            return draft_dir / name
    return None


# ---------------------------------------------------------------- 레퍼런스 찾기

def resolve_references(paths: Sequence[str], drafts_dir: Path) -> Tuple[List[Path], List[Dict[str, str]]]:
    """레퍼런스 경로(mp4 또는 초안 폴더)를 CapCut 초안 폴더로 바꿉니다."""
    by_name: Dict[str, Path] = {}
    if drafts_dir.is_dir():
        for d in drafts_dir.iterdir():
            if d.is_dir() and not d.name.startswith("."):
                by_name[_nfc(d.name)] = d

    found: List[Path] = []
    skipped: List[Dict[str, str]] = []
    for raw in paths:
        raw = (raw or "").strip()
        if not raw:
            continue
        p = Path(raw).expanduser()
        stem = _nfc(p.stem if p.suffix else p.name)
        draft = p if (p.is_dir() and _live_json(p)) else by_name.get(stem)
        if draft is None:
            skipped.append({"path": raw, "reason": "같은 이름의 CapCut 프로젝트를 찾지 못함"})
        elif draft not in found:
            found.append(draft)
    return found, skipped


# ---------------------------------------------------------------- 한 프로젝트 분석

def _text_info(tx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        cj = json.loads(tx["content"])
        style = (cj.get("styles") or [{}])[0]
        return {
            "text": str(cj.get("text", "")).replace("\n", " "),
            "size": float(style.get("size") or 0),
            "font": (style.get("font") or {}).get("path", "").split("/")[-1],
            "stroke": bool(style.get("strokes")),
            "bold": bool(style.get("bold")),
            "shadow": bool(style.get("shadows")),
        }
    except (KeyError, ValueError, TypeError):
        return None


def analyze_draft(draft_dir: Path) -> Optional[Dict[str, Any]]:
    live = _live_json(draft_dir)
    if live is None:
        return None
    c = _load(live)
    mats = c.get("materials", {})
    texts = {t["id"]: t for t in mats.get("texts", [])}
    vids = {v["id"]: v for v in mats.get("videos", [])}
    auds = {a["id"]: a for a in mats.get("audios", [])}
    duration = c.get("duration", 0) / US
    if duration <= 0:
        return None

    text_tracks = []
    for t in c.get("tracks", []):
        if t.get("type") != "text":
            continue
        rows = []
        for s in t.get("segments", []):
            info = _text_info(texts.get(s.get("material_id"), {}))
            if not info:
                continue
            tr = s["target_timerange"]
            info.update(start=tr["start"] / US, dur=tr["duration"] / US,
                        y=s["clip"]["transform"]["y"], scale_y=s["clip"]["scale"]["y"])
            rows.append(info)
        if rows:
            text_tracks.append(rows)
    text_tracks.sort(key=len, reverse=True)
    subtitles = text_tracks[0] if text_tracks else []
    keywords = [r for rows in text_tracks[1:] for r in rows if r["y"] < 0.1 and r["size"] >= 10]

    sfx = []
    for t in c.get("tracks", []):
        if t.get("type") != "audio":
            continue
        for s in t.get("segments", []):
            a = auds.get(s.get("material_id"))
            if a and a.get("type") == "sound" and 0 < a.get("duration", 0) < _SFX_MAX_US:
                sfx.append({"start": s["target_timerange"]["start"] / US, "name": a.get("name", ""),
                            "effect_id": a.get("effect_id", ""), "segment_id": s["id"]})

    broll = []
    video_tracks = [t for t in c.get("tracks", []) if t.get("type") == "video"]
    for t in video_tracks[1:]:
        for s in t.get("segments", []):
            v = vids.get(s.get("material_id"))
            if v:
                broll.append({"start": s["target_timerange"]["start"] / US,
                              "dur": s["target_timerange"]["duration"] / US, "type": v.get("type")})

    return {
        "name": _nfc(draft_dir.name), "duration": duration,
        "subtitles": len(subtitles), "keywords": keywords, "sfx": sfx, "broll": broll,
        "main_segments": len(video_tracks[0]["segments"]) if video_tracks else 0,
    }


def cut_edits(draft_dir: Path) -> Optional[Dict[str, Any]]:
    """우리가 만든 컷(draft_content.json)과 사용자가 마무리한 컷(draft_info.json) 비교."""
    gen_p, fin_p = draft_dir / "draft_content.json", draft_dir / "draft_info.json"
    if not (gen_p.is_file() and fin_p.is_file()):
        return None

    def segs(path: Path) -> List[Tuple[float, float]]:
        c = _load(path)
        t = next((t for t in c.get("tracks", []) if t.get("type") == "video"), None)
        if not t:
            return []
        return [(s["source_timerange"]["start"] / US,
                 (s["source_timerange"]["start"] + s["source_timerange"]["duration"]) / US)
                for s in t["segments"]]

    gen, fin = segs(gen_p), segs(fin_p)
    if len(gen) < 15:
        return None
    short_removed = short_total = 0
    heads: List[float] = []
    tails: List[float] = []
    for gs, ge in gen:
        ov = [(es, ee) for es, ee in fin
              if min(ee, ge) - max(es, gs) > 0.3 * (ge - gs)]
        if ge - gs < 0.5:
            short_total += 1
            short_removed += 0 if ov else 1
        if ov:
            es, ee = max(ov, key=lambda x: min(x[1], ge) - max(x[0], gs))
            heads.append(es - gs)
            tails.append(ee - ge)
    return {"short_total": short_total, "short_removed": short_removed,
            "head": heads, "tail": tails}


def scan_sfx_templates(draft_dir: Path, top: int = 2) -> List[Dict[str, Any]]:
    """가장 많이 쓴 효과음의 세그먼트·재료를 통째로 떠서 복제용 견본으로 만듭니다."""
    live = _live_json(draft_dir)
    if live is None:
        return []
    c = _load(live)
    mats = c.get("materials", {})
    auds = {a["id"]: a for a in mats.get("audios", [])}
    by_id: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for bucket in _AUDIO_BUCKETS:
        for it in mats.get(bucket, []):
            by_id[it["id"]] = (bucket, it)

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for t in c.get("tracks", []):
        if t.get("type") != "audio":
            continue
        for s in t.get("segments", []):
            a = auds.get(s.get("material_id"))
            if a and a.get("type") == "sound" and 0 < a.get("duration", 0) < _SFX_MAX_US:
                groups.setdefault(a.get("effect_id") or a.get("name", ""), []).append(s)

    meta_entries: Dict[str, Dict[str, Any]] = {}
    meta_p = draft_dir / "draft_meta_info.json"
    if meta_p.is_file():
        for bucket in _load(meta_p).get("draft_materials", []):
            if bucket.get("type") == 8:
                for e in bucket.get("value", []):
                    meta_entries[e.get("file_Path", "")] = e

    out = []
    for _key, segs in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:top]:
        seg = segs[0]
        audio = copy.deepcopy(auds[seg["material_id"]])
        if not Path(audio.get("path", "")).is_file():
            continue
        refs = {b: [] for b in _AUDIO_BUCKETS}
        for r in seg.get("extra_material_refs") or []:
            if r in by_id:
                refs[by_id[r][0]].append(copy.deepcopy(by_id[r][1]))
        out.append({
            "name": audio.get("name", ""), "uses": len(segs),
            "duration_us": int(audio.get("duration", 0)),
            "segment": {k: v for k, v in copy.deepcopy(seg).items()},
            "audio": audio, "materials": refs,
            "meta_entry": copy.deepcopy(meta_entries.get(audio.get("path", ""))),
        })
    return out


# ---------------------------------------------------------------- 학습(집계)

def _median(xs: Sequence[float], default: float = 0.0) -> float:
    return float(statistics.median(xs)) if xs else default


def learn(
    draft_dirs: Sequence[Path],
    presets: Optional[Iterable[Dict[str, Any]]] = None,
    cut_dirs: Optional[Sequence[Path]] = None,
) -> Dict[str, Any]:
    """draft_dirs: 레퍼런스 프로젝트. cut_dirs: 컷 학습에 쓸 프로젝트(기본: 레퍼런스와 같음) —
    "내가 어떤 컷을 지웠나"는 표본이 많을수록 믿을 수 있어서 우리 도구로 만든 초안 전체를 줄 수 있다."""
    analyses = [a for a in (analyze_draft(d) for d in draft_dirs) if a]
    profile: Dict[str, Any] = {
        "version": 1, "learned_at": int(time.time()),
        "sources": [{"name": a["name"], "duration": round(a["duration"], 1),
                     "keywords": len(a["keywords"]), "sfx": len(a["sfx"]), "broll": len(a["broll"])}
                    for a in analyses],
    }
    if not analyses:
        return profile

    per_min = lambda key: [len(a[key]) / a["duration"] * 60 for a in analyses]  # noqa: E731
    kws = [k for a in analyses for k in a["keywords"]]
    sizes = sorted({round(k["size"]) for k in kws})
    main_size = _median([k["size"] for k in kws]) if kws else 15.0
    font = max({k["font"] for k in kws}, key=lambda f: sum(1 for k in kws if k["font"] == f)) if kws else ""
    chars = sorted(len(k["text"].replace(" ", "")) for k in kws)
    profile["keyword"] = {
        "per_min": round(_median(per_min("keywords")), 1),
        "median_chars": int(_median(chars, 6)),
        "max_chars": int(chars[int(len(chars) * 0.9)]) if chars else 10,
        "avg_duration": round(statistics.mean([k["dur"] for k in kws]), 2) if kws else 2.0,
        "size_main": round(main_size, 1),
        "size_big": round(_median([k["size"] for k in kws if k["size"] > main_size + 0.5], main_size * 4 / 3), 1),
        "y": round(_median([k["y"] for k in kws], -0.25), 3),
        "scale_y": round(_median([k["scale_y"] for k in kws], 1.0), 2),
        "font": font,
        "preset_name": "",
    }
    # 글꼴·크기·세로 배율이 가장 비슷한 사전 설정을 키워드 스타일로 삼는다
    want_scale = profile["keyword"]["scale_y"]
    best, best_gap = None, 1e9
    for p in presets or []:
        st = p.get("style", {})
        if not font or Path(p.get("font_path", "")).name != font:
            continue
        if abs(float(st.get("font_size", 0)) - round(main_size)) > 0.6:
            continue
        gap = abs(float(st.get("scale_y", 1.0)) - want_scale)
        if gap < best_gap:
            best, best_gap = p, gap
    if best is not None:
        profile["keyword"]["preset_name"] = best.get("name", "")

    templates: List[Dict[str, Any]] = []
    for d in draft_dirs:
        templates.extend(scan_sfx_templates(d))
    merged: Dict[str, Dict[str, Any]] = {}
    for t in templates:
        key = t["audio"].get("effect_id") or t["name"]
        if key in merged:
            merged[key]["uses"] += t["uses"]
        else:
            merged[key] = t
    top = sorted(merged.values(), key=lambda t: -t["uses"])[:2]
    profile["sfx"] = {"per_min": round(_median(per_min("sfx")), 1), "templates": top}

    broll = [b for a in analyses for b in a["broll"]]
    profile["broll"] = {
        "per_min": round(_median(per_min("broll")), 1),
        "avg_duration": round(statistics.mean([b["dur"] for b in broll]), 2) if broll else 0.0,
    }

    edits = [e for e in (cut_edits(d) for d in (cut_dirs if cut_dirs is not None else draft_dirs)) if e]
    total = sum(e["short_total"] for e in edits)
    removed = sum(e["short_removed"] for e in edits)
    rate = removed / total if total else 0.0
    profile["cut"] = {
        "short_clip_removed_rate": round(rate, 2), "short_clip_samples": total,
        # 0.5초 미만 조각의 60% 이상을 사용자가 지웠을 때만 기준을 올린다
        "min_clip": 0.5 if total >= 20 and rate >= 0.6 else None,
        "head_trim": round(_median([x for e in edits for x in e["head"]]), 3),
        "tail_trim": round(_median([x for e in edits for x in e["tail"]]), 3),
    }
    return profile


def save_profile(profile: Dict[str, Any], path: Path = PROFILE_PATH) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(profile, fh, ensure_ascii=False, indent=2)


def load_profile(path: Path = PROFILE_PATH) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return _load(path)
    except (OSError, ValueError):
        return None


def summarize(profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """화면에 보여줄 요약(견본 JSON 같은 큰 덩어리는 뺀다)."""
    if not profile:
        return {"ready": False}
    kw, sfx, br, cut = (profile.get(k) or {} for k in ("keyword", "sfx", "broll", "cut"))
    return {
        "ready": True,
        "sources": profile.get("sources", []),
        "keyword": kw,
        "sfx": {"per_min": sfx.get("per_min"),
                "names": [f'{t["name"]} ×{t["uses"]}' for t in sfx.get("templates", [])]},
        "broll": br, "cut": cut,
    }
