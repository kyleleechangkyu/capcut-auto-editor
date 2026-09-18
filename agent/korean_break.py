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

import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

_APP_ROOT = Path(__file__).resolve().parent.parent
_JAR_PATH = _APP_ROOT / "KoreanAnalyzer-master" / "KoreanAnalyzer-0.2.2.3-jar-with-dependencies.jar"
_DRIVER_DIR = Path(__file__).resolve().parent / "assets" / "korean_break"

# 종결어미(EF, EFN, EFQ, EFA, EFI, EFR, EFH, EFO 등 — 문장이 끝나는 곳)
_BREAK_PREFIXES = ("EF",)
# 화제/대조를 나타내는 보조사(JX, JXC 등 — 는/은/도/만…). 주격/목적격(JKS/JKO)
# 등 일반 조사는 포함하지 않습니다 — 그건 문장이 계속 이어지는 자리라서.
_BREAK_EXACT = {"JX", "JXC"}

_JAVA_CANDIDATES = ["java", "/opt/homebrew/opt/openjdk/bin/java", "/usr/local/opt/openjdk/bin/java"]


def _find_java() -> Optional[str]:
    for cand in _JAVA_CANDIDATES:
        found = shutil.which(cand)
        if found:
            return found
        if Path(cand).is_file():
            return cand
    return None


def available() -> bool:
    """형태소 분석기를 쓸 수 있는 환경인지(jar/드라이버/java 모두 있는지)."""
    return (
        _JAR_PATH.exists()
        and (_DRIVER_DIR / "BreakAnalyzer.class").exists()
        and _find_java() is not None
    )


def analyze_lines(lines: List[str]) -> Optional[List[Optional[List[bool]]]]:
    """줄마다 형태소 분석해 어절별 '이 어절 뒤에서 끊기 좋다' 여부를 돌려줍니다.

    lines[i] 는 공백으로 이어붙인 어절들(예: "돼지국밥이라는거는 뭐 그냥").
    반환값 result[i] 는 lines[i]를 공백으로 나눈 어절 수와 길이가 같은
    bool 리스트, 단 분석기가 그 줄을 다른 개수의 어절로 쪼갰다면(정렬이
    어긋나므로) 그 줄은 None. 분석기 자체를 못 쓰면 전체 None.
    """
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
            try:
                subprocess.run(["/bin/bash", "-c", shell_cmd], timeout=90)
            except subprocess.TimeoutExpired:
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
        if raw.count("\t") == 3:
            current.append(raw)
    # 마지막 ###END### 가 없었다면(비정상 종료) 남은 것도 버리지 않고 받아줌
    if current:
        blocks.append(current)

    if len(blocks) != len(lines):
        return None

    results: List[Optional[List[bool]]] = []
    for line, block in zip(lines, blocks):
        expected = line.split()
        if len(block) != len(expected):
            results.append(None)
            continue
        flags = []
        for row in block:
            _word, _morph, tag, raw_tag = row.split("\t")
            t = tag or raw_tag
            flags.append(t in _BREAK_EXACT or any(t.startswith(p) for p in _BREAK_PREFIXES))
        results.append(flags)
    return results
