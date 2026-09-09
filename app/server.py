"""로컬 앱 서버.

브라우저 화면 하나와 그 화면이 쓰는 API를 제공합니다.
바깥으로 열리지 않고 이 컴퓨터(127.0.0.1)에서만 접속됩니다.
"""

from __future__ import annotations

import json
import mimetypes
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(ROOT))

from agent import config as config_mod  # noqa: E402
from agent.defaults import PRESETS  # noqa: E402

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


# ---------------------------------------------------------------- 작업 관리

@dataclass
class Job:
    id: str
    source: Path
    stages: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    current: str = ""
    logs: List[str] = field(default_factory=list)
    done: bool = False
    error: str = ""
    result: Optional[Dict[str, Any]] = None
    started: float = field(default_factory=time.time)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.source.name,
            "current": self.current,
            "stages": self.stages,
            "logs": self.logs[-40:],
            "done": self.done,
            "error": self.error,
            "result": self.result,
            "elapsed": time.time() - self.started,
        }


JOBS: Dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


def _run_job(job: Job) -> None:
    from agent import pipeline

    def on_stage(key: str, note: str, ratio: float) -> None:
        job.current = key
        st = job.stages.setdefault(key, {})
        st["state"] = "done" if ratio >= 1.0 else "running"
        st["ratio"] = ratio
        if note:
            st["note"] = note
        # 앞 단계들은 모두 끝난 것으로 표시
        for k, _label in pipeline.STAGES:
            if k == key:
                break
            job.stages.setdefault(k, {})["state"] = "done"

    def on_log(msg: str) -> None:
        job.logs.append(msg)

    try:
        cfg = config_mod.load()
        result = pipeline.process(
            job.source, cfg,
            progress=pipeline.Progress(on_stage=on_stage, on_log=on_log),
        )
        job.result = _result_payload(result)
        for k, _l in pipeline.STAGES:
            job.stages.setdefault(k, {})["state"] = "done"
    except Exception as exc:  # noqa: BLE001
        job.error = str(exc)
        job.logs.append(traceback.format_exc().splitlines()[-1])
    finally:
        job.done = True


def _result_payload(r) -> Dict[str, Any]:
    from agent.detect import REASON_LABEL

    total = r.plan.duration or 1.0
    timeline = []
    cursor = 0.0
    for k in r.plan.keeps:
        if k.start - cursor > 1e-6:
            timeline.append({"kind": "cut", "start": cursor, "end": k.start, "reason": "gap"})
        timeline.append({"kind": "keep", "start": k.start, "end": k.end})
        cursor = k.end
    if total - cursor > 1e-6:
        timeline.append({"kind": "cut", "start": cursor, "end": total, "reason": "gap"})

    # 컷 조각에 사유 붙이기
    for seg in timeline:
        if seg["kind"] != "cut":
            continue
        best, best_ov = None, 0.0
        for c in r.plan.cuts:
            ov = min(seg["end"], c.end) - max(seg["start"], c.start)
            if ov > best_ov:
                best, best_ov = c, ov
        seg["reason"] = best.reason if best else "tail"
        seg["label"] = REASON_LABEL.get(seg["reason"], "정리")
        seg["detail"] = best.detail if best else ""

    summary = [
        {"reason": k, "label": REASON_LABEL.get(k, k), "count": v[0], "duration": v[1]}
        for k, v in sorted(r.plan.summary_by_reason().items(), key=lambda kv: -kv[1][1])
    ]

    return {
        "draft_name": r.draft_name,
        "draft_dir": str(r.draft_dir),
        "style_note": r.style_note,
        "duration": r.plan.duration,
        "kept": r.plan.kept_duration,
        "removed": r.plan.removed_duration,
        "clips": len(r.plan.keeps),
        "timeline": timeline,
        "summary": summary,
        "cues": [{"start": c.start, "end": c.end, "text": c.text} for c in r.cues],
        "cuts": [
            {"start": c.start, "end": c.end, "reason": c.reason,
             "label": REASON_LABEL.get(c.reason, c.reason), "detail": c.detail}
            for c in r.plan.cuts
        ],
        "report": str(r.report_md),
        "srt": str(r.srt) if r.srt else "",
        "source": str(r.source),
        "elapsed": r.elapsed,
    }


# ---------------------------------------------------------------- 상태 점검

def _checks(cfg) -> List[Dict[str, Any]]:
    out = []

    ff = shutil.which("ffmpeg")
    out.append({
        "key": "ffmpeg", "label": "ffmpeg", "ok": bool(ff),
        "hint": "" if ff else "터미널에서  brew install ffmpeg",
    })

    try:
        import faster_whisper  # noqa: F401
        ok = True
    except ImportError:
        ok = False
    out.append({
        "key": "whisper", "label": "음성 인식 엔진", "ok": ok,
        "hint": "" if ok else "설치가 덜 끝났습니다. 실행 파일을 다시 더블클릭해 주세요.",
    })

    try:
        import pycapcut  # noqa: F401
        ok = True
    except ImportError:
        ok = False
    out.append({
        "key": "pycapcut", "label": "CapCut 초안 엔진", "ok": ok,
        "hint": "" if ok else "설치가 덜 끝났습니다. 실행 파일을 다시 더블클릭해 주세요.",
    })

    drafts = cfg.get("capcut_drafts") or ""
    ok = bool(drafts and Path(drafts).is_dir())
    out.append({
        "key": "drafts", "label": "CapCut 초안 폴더", "ok": ok,
        "value": drafts,
        "hint": "" if ok else "CapCut을 한 번 실행해 프로젝트를 하나 만든 뒤 새로고침해 주세요.",
    })
    return out


def _list_drafts(cfg) -> List[Dict[str, Any]]:
    drafts = cfg.get("capcut_drafts") or ""
    if not drafts or not Path(drafts).is_dir():
        return []
    items = []
    for p in Path(drafts).iterdir():
        if not p.is_dir() or not (p / "draft_content.json").exists():
            continue
        try:
            mtime = (p / "draft_content.json").stat().st_mtime
        except OSError:
            mtime = 0
        items.append({"name": p.name, "mtime": mtime})
    items.sort(key=lambda x: -x["mtime"])
    return items[:60]


def _seed_info(cfg, name: str) -> Dict[str, Any]:
    from agent import draft as draft_mod

    drafts = cfg.get("capcut_drafts") or ""
    if not name or not drafts:
        return {"ok": False, "message": "선택된 견본이 없습니다."}
    d = Path(drafts) / name
    try:
        style = draft_mod.scan_seed(d)
    except (ValueError, FileNotFoundError) as exc:
        return {"ok": False, "message": str(exc)}
    return {
        "ok": True,
        "description": style.description,
        "has_template": style.has_template,
        "app_version": style.platform.get("app_version", ""),
    }


# ---------------------------------------------------------------- 맥 연동

def _mac_pick_file() -> Optional[str]:
    """맥 기본 파일 선택창을 띄웁니다 (영상을 복사하지 않아도 됨)."""
    if sys.platform != "darwin":
        return None
    script = (
        'set f to choose file with prompt "편집할 영상을 고르세요" '
        'of type {"mp4","mov","m4v","avi","mkv","public.movie"}\n'
        'POSIX path of f'
    )
    try:
        res = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=300
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = (res.stdout or "").strip()
    return path or None


def _reveal(path: str) -> None:
    p = Path(path)
    if not p.exists():
        return
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(p)], check=False)
    elif sys.platform == "win32":
        subprocess.run(["explorer", "/select,", str(p)], check=False)


def _open_capcut() -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", "-a", "CapCut"], check=False)


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "CapCutAgent"

    def log_message(self, *args):  # 콘솔을 조용하게
        pass

    # -- 응답 도우미 --
    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        if not path.exists():
            self._json({"error": "not found"}, 404)
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _json_body(self) -> Dict[str, Any]:
        try:
            return json.loads(self._body() or b"{}")
        except ValueError:
            return {}

    # -- 라우팅 --
    def do_GET(self) -> None:  # noqa: N802
        url = urllib.parse.urlparse(self.path)
        route, qs = url.path, urllib.parse.parse_qs(url.query)

        if route in ("/", "/index.html"):
            self._file(APP_DIR / "ui.html")
            return

        if route == "/api/state":
            cfg = config_mod.load()
            self._json({
                "checks": _checks(cfg),
                "drafts_dir": cfg.get("capcut_drafts") or "",
                "drafts": _list_drafts(cfg),
                "seed_draft": cfg.get("seed_draft") or "",
                "presets": cfg.get("presets"),
                "preset_options": {k: list(v.keys()) for k, v in PRESETS.items()},
                "subtitle": cfg.get_path("subtitle", {}),
                "transcribe_prompt": cfg.get_path("transcribe.initial_prompt", ""),
                "platform": sys.platform,
                "stages": [{"key": k, "label": l} for k, l in _stages()],
            })
            return

        if route == "/api/seed":
            cfg = config_mod.load()
            self._json(_seed_info(cfg, (qs.get("name") or [""])[0]))
            return

        if route == "/api/job":
            jid = (qs.get("id") or [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(jid)
            if not job:
                # 키 이름을 따로 둡니다. job.snapshot() 에도 error 필드가 있어서
                # 같은 이름을 쓰면 '작업 실패'와 '작업 없음'이 구분되지 않습니다.
                self._json({"not_found": True, "error": "no such job"}, 404)
                return
            self._json(job.snapshot())
            return

        self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        route = urllib.parse.urlparse(self.path).path

        if route == "/api/pick":
            path = _mac_pick_file()
            self._json({"path": path or ""})
            return

        if route == "/api/upload":
            name = urllib.parse.unquote(self.headers.get("X-Filename") or "video.mp4")
            name = Path(name).name or "video.mp4"
            cfg = config_mod.load()
            dest = cfg.media_dir / name
            if dest.exists():
                dest = cfg.media_dir / f"{Path(name).stem}-{int(time.time())}{Path(name).suffix}"
            total = int(self.headers.get("Content-Length") or 0)
            written = 0
            with open(dest, "wb") as fh:
                while written < total:
                    chunk = self.rfile.read(min(1 << 20, total - written))
                    if not chunk:
                        break
                    fh.write(chunk)
                    written += len(chunk)
            self._json({"path": str(dest)})
            return

        if route == "/api/run":
            data = self._json_body()
            src = Path(data.get("path", "")).expanduser()
            if not src.exists():
                self._json({"error": f"파일을 찾을 수 없습니다: {src}"}, 400)
                return
            if src.suffix.lower() not in VIDEO_EXT:
                self._json({"error": f"지원하지 않는 형식입니다: {src.suffix}"}, 400)
                return
            job = Job(id=uuid.uuid4().hex[:12], source=src.resolve())
            with JOBS_LOCK:
                JOBS[job.id] = job
            threading.Thread(target=_run_job, args=(job,), daemon=True).start()
            self._json({"id": job.id})
            return

        if route == "/api/settings":
            patch = self._json_body()
            config_mod.save_user_settings(patch)
            self._json({"ok": True})
            return

        if route == "/api/reveal":
            _reveal(self._json_body().get("path", ""))
            self._json({"ok": True})
            return

        if route == "/api/open-capcut":
            _open_capcut()
            self._json({"ok": True})
            return

        if route == "/api/quit":
            self._json({"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.4), self.server.shutdown()), daemon=True).start()
            return

        self._json({"error": "not found"}, 404)


def _stages():
    from agent.pipeline import STAGES
    return STAGES


def _free_port(preferred: int = 8756) -> int:
    for port in range(preferred, preferred + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def main() -> int:
    port = _free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"

    print("─" * 46)
    print("  CapCut 자동 편집기가 열렸습니다")
    print(f"  {url}")
    print("  이 창은 닫지 마세요. 종료하려면 Ctrl+C")
    print("─" * 46)

    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    print("\n종료했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
