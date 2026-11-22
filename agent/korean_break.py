"""형태소 분석(KoreanAnalyzer)으로 자막이 자연스럽게 끊기는 지점을 찾습니다.

whisper가 주는 어절만으로는 "말이 끊기는 지점"을 모릅니다 — 그래서
`subtitles.build_cues()` 는 글자 수·시간으로만 잘랐고, 그 결과 문장/화제가
한 자막 안에서 뒤섞이는 문제가 있었습니다. 이 모듈은 각 어절의 마지막
형태소 태그를 얻어와, 종결어미(문장이 끝나는 곳: EF*) 나 화제/대조를 뜻하는
보조사(JX, JXC — 는/은/도/만 등) 뒤를 "끊기 좋은 지점"으로 표시합니다.

KoreanAnalyzer(https://github.com/likejazz/korean-position-tagger 계열,
KAIST 한나눔+꼬꼬마 통합)의 jar-with-dependencies(약 90MB, 사전이 안에
들어있음)를 서브프로세스로 돌립니다. jar는 용량 때문에 저장소에 넣지 않고
로컬 경로(`KoreanAnalyzer-master/`)를 그대로 씁니다 — 이 저장소를 새
컴퓨터에 클론하면 이 폴더와 java 런타임을 따로 준비해야 이 기능이 켜집니다.
**없어도 앱은 정상 동작합니다** — 이 모듈의 모든 함수는 실패 시 조용히
`None`/빈 결과를 돌려주고, `subtitles.py` 는 그러면 글자 수 기준 컷으로
돌아갑니다. 이 무음 실패(silent fallback)를 없애지 마세요 — 다른 컴퓨터나
java/jar가 없는 환경에서 파이프라인이 죽으면 안 됩니다.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Tuple

_APP_ROOT = Path(__file__).resolve().parent.parent
_JAR_PATH = _APP_ROOT / "KoreanAnalyzer-master" / "KoreanAnalyzer-0.2.2.3-jar-with-dependencies.jar"
_DRIVER_DIR = Path(__file__).resolve().parent / "assets" / "korean_break"

# 종결어미(EF, EFN, EFQ, EFA, EFI, EFR, EFH, EFO 등 — 문장이 끝나는 곳)
_BREAK_PREFIXES = ("EF",)
# 화제/대조를 나타내는 보조사(JX, JXC 등 — 는/은/도/만…). 주격/목적격(JKS/JKO)
# 등 일반 조사는 포함하지 않습니다 — 그건 문장이 계속 이어지는 자리라서.
_BREAK_EXACT = {"JX", "JXC"}

_JAVA_CANDIDATES = ["/opt/homebrew/opt/openjdk/bin/java", "/usr/local/opt/openjdk/bin/java", "java"]
_JAVA_CACHE: dict = {}


def _runs(path: str) -> bool:
    """macOS 의 /usr/bin/java 는 JDK 가 없으면 곧바로 오류로 끝나는 껍데기라, 실제로 도는지 본다."""
    try:
        return subprocess.run([path, "-version"], capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _find_java() -> Optional[str]:
    if "path" in _JAVA_CACHE:
        return _JAVA_CACHE["path"]
    found = None
    for cand in _JAVA_CANDIDATES:
        path = cand if Path(cand).is_file() else shutil.which(cand)
        if path and _runs(path):
            found = path
            break
    _JAVA_CACHE["path"] = found
    return found


def available() -> bool:
    """형태소 분석기를 쓸 수 있는 환경인지(jar/드라이버/java 모두 있는지)."""
    return (
        _JAR_PATH.exists()
        and (_DRIVER_DIR / "BreakAnalyzer.class").exists()
        and _find_java() is not None
    )


@dataclass
class WordInfo:
    """어절 하나의 분석 결과."""
    flag: bool   # 이 어절 뒤에서 끊기 좋은가
    stem: str    # 조사·어미를 뗀 알맹이(명사류). 명사류가 아니면 원래 어절
    kind: str    # "noun" | "foreign"(영문) | "number" | "other"


def _word_info(word: str, tag: str, morphs: str) -> WordInfo:
    flag = tag in _BREAK_EXACT or any(tag.startswith(p) for p in _BREAK_PREFIXES)
    pairs: List[Tuple[str, str]] = []
    for item in morphs.split():
        m, _, t = item.rpartition("/")
        pairs.append((m, t))
    if not pairs:
        return WordInfo(flag, word, "other")

    first = pairs[0][1]
    if first in ("OL", "OH"):
        kind = "foreign"
    elif first in ("NR", "SN"):
        kind = "number"
    elif first.startswith("NN") or first == "XR":
        kind = "noun"
    else:
        return WordInfo(flag, word, "other")
    # 명사 뒤에 서술 요소(이더라/하다/되다 같은 용언·어미)가 붙은 어절은 서술어다 — 키워드 후보에서 뺀다
    if any(t.startswith("E") or t in ("VCP", "VCN", "XSV", "XSA", "VX", "VV", "VA") for _m, t in pairs[1:]):
        return WordInfo(flag, word, "other")
    # 단어 끝에 붙은 조사(J*)를 원래 어절에서 떼어 알맹이를 얻는다 — 분석기가 알맹이를
    # 이상하게 쪼개도(동방/신기) 어절 자체는 그대로 보존된다.
    tail = ""
    for m, t in reversed(pairs):
        if t.startswith("J"):
            tail = m + tail
        else:
            break
    stem = word[: -len(tail)] if tail and word.endswith(tail) and len(word) > len(tail) else word
    if tail == "만" and len(stem) <= 2:   # '이수만'처럼 이름의 일부일 수 있는 한 글자 조사
        stem = word
    return WordInfo(flag, stem, kind)


def analyze_lines_full(lines: List[str]) -> Optional[List[Optional[List[WordInfo]]]]:
    """analyze_lines 와 같지만 어절별 알맹이(stem)와 종류(kind)까지 돌려줍니다."""
    return _analyze(lines)


def analyze_lines(lines: List[str]) -> Optional[List[Optional[List[bool]]]]:
    """줄마다 형태소 분석해 어절별 '이 어절 뒤에서 끊기 좋다' 여부를 돌려줍니다.

    lines[i] 는 공백으로 이어붙인 어절들(예: "돼지국밥이라는거는 뭐 그냥").
    반환값 result[i] 는 lines[i]를 공백으로 나눈 어절 수와 길이가 같은
    bool 리스트, 단 분석기가 그 줄을 다른 개수의 어절로 쪼갰다면(정렬이
    어긋나므로) 그 줄은 None. 분석기 자체를 못 쓰면 전체 None.
    """
    full = _analyze(lines)
    if full is None:
        return None
    return [None if r is None else [w.flag for w in r] for r in full]


def _analyze(lines: List[str]) -> Optional[List[Optional[List[WordInfo]]]]:
    java = _find_java()
    if not java or not _JAR_PATH.exists() or not (_DRIVER_DIR / "BreakAnalyzer.class").exists():
        return None
    if not lines:
        return []

    stdin_text = "\n".join(l.replace("\n", " ") for l in lines) + "\n"

    # 실제 셸(bash -c)에 stdin/stdout 리다이렉트를 맡깁니다 — Python의
    # subprocess.run() 이 파이프로 넘기든(input=) 연 파일 핸들로 넘기든
    # (stdin=/stdout=) 똑같이 겪는 문제라서 방식을 바꿔도 소용없었습니다.
    #
    # 이 자바 프로그램은 분석을 다 마치고 결과를 출력 파일에 전부 쓴 뒤에도
    # 프로세스 자체가 스스로 끝나지 않는 경우가 있습니다(원인 불명 — 아마
    # 라이브러리가 띄운 non-daemon 스레드가 안 죽는 듯). 그래서 타임아웃이
    # 나도(subprocess.run이 알아서 죽임) 바로 포기하지 않고, 이미 다 쓰여
    # 있을 출력 파일을 그대로 읽습니다 — 아래 줄 수 검증이 정말로 불완전한
    # 경우는 걸러냅니다.
    import shlex
    import tempfile

    try:
        with tempfile.TemporaryDirectory(prefix="korean_break_") as tmp:
            in_path = Path(tmp) / "in.txt"
            out_path = Path(tmp) / "out.txt"
            in_path.write_text(stdin_text, encoding="utf-8")
            shell_cmd = "{java} -cp {cp} BreakAnalyzer < {inp} > {outp} 2>/dev/null".format(
                java=shlex.quote(java),
                cp=shlex.quote(f"{_JAR_PATH}:{_DRIVER_DIR}"),
                inp=shlex.quote(str(in_path)),
                outp=shlex.quote(str(out_path)),
            )
            # 끝났는지는 출력 파일의 ###END### 개수로 직접 봅니다(줄마다 정확히 하나).
            # 다 쓰였으면 안 죽는 JVM을 바로 죽여서, 항상 최대 시간(90초)을 기다리던
            # 것을 분석이 끝나는 즉시(보통 10~30초)로 줄입니다.
            proc = subprocess.Popen(["/bin/bash", "-c", shell_cmd], start_new_session=True)
            deadline = time.time() + 90
            try:
                while time.time() < deadline:
                    if proc.poll() is not None:
                        break
                    try:
                        done = out_path.read_text(encoding="utf-8", errors="replace").count("###END###")
                    except OSError:
                        done = 0
                    if done >= len(lines):
                        break
                    time.sleep(0.25)
            finally:
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except OSError:
                        pass
                try:
                    proc.wait(timeout=5)
                except subprocess.SubprocessError:
                    pass
            stdout_text = out_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None

    # 표준출력엔 사전 로딩 로그도 섞여 나오므로, "단어\t형태소\t태그\t원태그"
    # 형태(탭 3개)만 데이터로 인정하고 나머지는 무시합니다.
    blocks: List[List[str]] = []
    current: List[str] = []
    for raw in stdout_text.splitlines():
        if raw == "###END###":
            blocks.append(current)
            current = []
            continue
        if raw == "###SENT###" or raw.startswith("###ERR###"):
            continue
        if raw.count("\t") >= 3:
            current.append(raw)
    # 마지막 ###END### 가 없었다면(비정상 종료) 남은 것도 버리지 않고 받아줌
    if current:
        blocks.append(current)

    if len(blocks) != len(lines):
        return None

    results: List[Optional[List[WordInfo]]] = []
    for line, block in zip(lines, blocks):
        expected = line.split()
        if len(block) != len(expected):
            results.append(None)
            continue
        infos: List[WordInfo] = []
        for row in block:
            cols = row.split("\t")
            word, tag = cols[0], (cols[2] or cols[3])
            infos.append(_word_info(word, tag, cols[4] if len(cols) > 4 else ""))
        results.append(infos)
    return results
