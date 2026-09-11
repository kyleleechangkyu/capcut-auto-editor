# CapCut 자동 편집기 — 작업 안내

이 저장소에서 작업할 때 먼저 읽어주세요.

> **git 잠금 파일 주의**: `.git/HEAD.lock` 이나 `index.lock` 이 남아 있으면 커밋이 실패합니다.
> (샌드박스에서 작업하다 생긴 것. 맥에서는 `rm -f .git/*.lock` 으로 지우면 됩니다)
> 마지막 CLAUDE.md 수정분은 이 문제로 커밋되지 않은 채 작업트리에 있습니다 — 그냥 커밋하면 됩니다.

## 첫 작업: 설치부터 (아직 안 돼 있음)

`.venv` 가 없으면 아직 한 번도 설치되지 않은 상태입니다. 다른 걸 하기 전에 먼저:

```bash
cd "$(dirname "$0")"          # 이 저장소 폴더
bash "CapCut 자동편집기.command"
```

- ffmpeg(brew), python venv, faster-whisper, pycapcut 을 순서대로 깝니다. 5~10분.
- 모든 출력이 `install.log` 에 남습니다. 실패하면 그 파일부터 보세요.
- 끝나면 브라우저가 `http://127.0.0.1:8756/` 로 열립니다.

설치 후 확인할 것:
1. 화면 우상단 상태 칩이 "준비됨" 인지 (ffmpeg / whisper / pycapcut / CapCut 초안 폴더 4개 점검)
2. CapCut 초안 폴더를 못 찾으면 `agent/config.py` 의 `MAC_DRAFT_CANDIDATES` 에
   실제 경로를 추가하세요. 맥에서 `ls ~/Movies | grep -i capcut` 으로 찾을 수 있습니다.
3. 실제 영상 하나로 돌려보고 `work/<이름>/report.md` 로 컷 판정 검토

---

## 이게 뭔가

영상 하나를 넣으면 컷 편집(무음·말더듬·재촬영 제거)과 자막을 얹은
**CapCut 초안(draft)** 을 만들어주는 로컬 앱입니다. 완성 영상이 아니라 초안을
만드는 게 핵심 — 사용자가 CapCut에서 열어 손보고 직접 내보냅니다.

## 실행

```bash
# 설치 + 실행 (맥)
open "CapCut 자동편집기.command"

# 개발 중에는 서버만
source .venv/bin/activate && python3 app/server.py
```

브라우저가 `http://127.0.0.1:8756/` 로 열립니다.

## 구조

```
CapCut 자동편집기.command   더블클릭 진입점. 첫 실행 시 venv + 패키지 설치
app/server.py              stdlib HTTP 서버. 127.0.0.1 전용. API + 정적 HTML
app/ui.html                단일 화면 앱. 외부 의존성 없음 (Inter 웹폰트만)
agent/audio.py             ffmpeg/ffprobe 래퍼 — 메타데이터, wav 추출
agent/transcribe.py        faster-whisper 단어 단위 전사 + JSON 캐시
agent/detect.py            컷 판정 엔진 + 원본↔편집본 타임라인 매핑
agent/subtitles.py         자막 줄 나누기 + SRT
agent/draft.py             CapCut draft_content.json 생성 + 견본 스타일 복제
agent/defaults.py          기본값 + 프리셋 (컷 세기 / 정확도 / 비율)
agent/config.py            settings.json 병합, CapCut 초안 폴더 자동 탐색
agent/pipeline.py          전체 흐름 + 단계별 진행 콜백
DESIGN-framer.md           UI 디자인 시스템 명세 (토큰의 출처)
```

## 반드시 알아야 할 설계 제약

**1. 자막 스타일은 코드로 만들지 않는다**
CapCut의 텍스트 템플릿·말풍선·花字·애니메이션은 CapCut 서버의 리소스 ID를 참조하므로
코드로 생성할 수 없습니다. 대신 사용자가 CapCut에서 만든 **견본 초안**의 텍스트 조각을
`agent/draft.py: scan_seed()` 로 통째로 읽어와 `_clone_text_segment()` 로 복제합니다.
새 UUID를 부여하고 글자와 시간만 교체합니다. 이 구조를 우회하려 하지 마세요.

기본값(화면에서 견본을 안 골랐을 때)은 `agent/assets/default_text_style.json` 에
미리 뽑아 저장해 둔 스타일 — `draft.default_text_style()` 가 읽어서 씁니다. 사용자가
CapCut의 "텍스트 사전 설정"으로 만들어 실제 프로젝트에 적용해 둔 스타일을 한 번 뽑아
고정해 둔 것(원본 CapCut 프로젝트가 지워져도 남아있음). 기본 스타일을 바꾸려면 그
프로젝트에서 `scan_seed()` 를 다시 돌려 이 파일을 덮어쓰면 됩니다 — 방법은
`git log`에서 이 자산을 처음 만든 커밋 참고.

**1-1. scan_seed() 는 draft_info.json 을 draft_content.json 보다 먼저 본다**
이 CapCut은 draft_info.json 만 실제로 계속 갱신합니다. draft_content.json 은 (우리
도구가 그 초안을 처음 만들 때 썼다면) 그 시점의 낡은 스냅샷으로 남아있습니다.
사용자가 CapCut 안에서 자막 스타일을 직접 고쳐서 견본으로 쓰려는 게 일반적인
시나리오이므로, 스타일을 놓치지 않으려면 draft_info.json 을 우선해야 합니다
(반대로 뒀다가 그림자·커스텀 폰트가 통째로 빠진 채 복제된 적 있음). 이 순서를
다시 뒤집지 마세요 — `_list_drafts()` (app/server.py)도 마찬가지.

**2. 견본에서 CapCut 버전 정보를 물려받는다**
`_finalize()` 가 견본의 `platform` / `version` / `new_version` 을 그대로 씁니다.
CapCut이 업데이트돼 스키마가 바뀌어도 견본만 새로 만들면 대응됩니다.

**3. 사유별로 컷 여백이 다르다**
무음 컷에만 `lead_in`/`lead_out` 여백을 줍니다. 말더듬·재촬영은 이미 단어·문장
경계라서 여백을 주면 잘린 조각("두" 같은 한 글자)이 남습니다. `detect.build_plan()` 참고.

**3-1. "무음"은 발화 인식(주) + dB 감지(보완) 둘 다로 판단한다**
`detect.find_nonspeech_cuts()` 가 기본: whisper가 단어를 인식한 구간(가능하면
단어 타임스탬프, 없으면 발화 구간)만 "말"로 보고 그 사이를 전부 잘라냅니다 —
조용하지 않아도 말이 아니면(마이크 부시럭거림, 숨소리, 배경 잡음) 잘려나갑니다.

**이것만으론 부족합니다.** whisper의 단어 타임스탬프가 항상 정확한 건 아니라서,
실제로 조용한 구간(0.3~0.9초 안팎)도 앞뒤 단어에 넉넉하게 걸쳐 보고하는 경우가
있습니다 — 그러면 실제 침묵인데 "말이 이어지는 중"으로 잘못 판단돼 안 잘립니다
(실측: 어떤 영상은 dB로는 무음 175곳인데 단어 기준으로는 13곳만 잡혔음). 그래서
`agent/audio.py: detect_silence()`(dB 기준)를 보완으로 같이 돌려 `find_silence_cuts()`
로 컷을 만들고, `find_nonspeech_cuts()` 결과와 **합쳐서** 씁니다(pipeline.py). 둘 중
하나라도 "여기는 말이 아니다"라고 하면 자릅니다 — 어느 한쪽만으로 되돌리지 마세요
(단어 기준만 쓰면 실제 침묵을 놓치고, dB 기준만 쓰면 시끄러운 비발화 소음을 놓칩니다).

`cut.silence.min_duration` 은 "이보다 짧은 비발화 틈은 안 자름" 기준(두 감지기
공통), `cut.silence.threshold_db` 는 dB 감지기 임계값(기본 -34dB). 여백은 여기서
안 주고 `build_plan()`의 `lead_in`/`lead_out`(제약 3)이 담당하므로 이중으로
패딩하지 마세요.

**4. 자막 시각은 반드시 재매핑한다**
컷 후 자막 시간이 어긋나므로 `CutPlan.map_span()` 으로 원본→편집본 변환을 거칩니다.
컷 경계에 55% 미만만 걸친 단어는 조각이므로 버립니다 (`subtitles.build_cues`).

**5. pymediainfo 를 쓰지 않는다**
pycapcut의 `VideoMaterial` 이 libmediainfo를 요구하지만, 설치 부담을 줄이려고
`draft._ffprobe_material()` 에서 ffprobe 결과로 객체를 직접 구성합니다.

**6. 초안은 영상 파일의 절대 경로를 기억한다**
`media_dir` 안의 영상을 옮기거나 지우면 CapCut에서 링크가 끊깁니다.
원본을 이동시키는 코드를 추가할 때는 초안 생성 **전에** 옮겨야 합니다.

**7. macOS의 CapCut은 App 샌드박스로 실행된다 — 영상은 반드시 `~/Movies` 아래에**
`~/Desktop`, `~/Documents`, `~/Downloads` 아래 파일은 CapCut이 읽지 못합니다
(초안은 열리지만 클립마다 "액세스할 수 없습니다"가 뜸). 커널 로그에
`Sandbox: CapCut deny(1) file-read-data <path>` 로 남으니, 의심되면
`log show --last 15m --predicate 'eventMessage CONTAINS "<파일명>"'` 로 확인하세요
(zsh에서 `log`는 내장 명령과 충돌하니 `/usr/bin/log` 로 직접 불러야 합니다).
그래서 `config.py: Config.media_dir` 가 macOS에서 `~/Movies` 아래를 씁니다 —
이 경로를 다시 앱 폴더 밑으로 되돌리지 마세요.

**8. 새 초안을 만들 땐 실제 CapCut 초안을 스캐폴드로 통째로 복제한다**
pycapcut의 `create_draft()` 는 `draft_content.json`/`draft_meta_info.json` 만
만드는데, 이 CapCut 버전은 그 외에도 `draft_info.json`(주 콘텐츠 파일 — 이게
없으면 목록엔 뜨지만 열 때 "프로젝트를 사용할 수 없음"), `Resources/`,
`draft_virtual_store.json` 등 많은 부속 파일을 요구합니다. `draft.build()` 가
`scaffold_dir`(자막 견본 또는 `find_scaffold()`가 고른 기존 초안)를
`shutil.copytree` 로 통째로 복제한 뒤 콘텐츠 파일만 덮어쓰는 이유입니다.
단, 복제된 `Timelines/project.json` 은 견본 자신의 `main_timeline_id` 를
그대로 담고 있어서 재생기가 우리 콘텐츠 대신 견본의 옛 타임라인을 가리키게
됩니다 (편집 화면은 `draft_content.json`을 직접 읽어 정상으로 보이니 착각하기
쉽습니다) — 그래서 복제 직후 `Timelines/` 를 통째로 지웁니다. CapCut이 열 때
`draft_content.json` 기준으로 새로 만들어 줍니다.

## UI 작업 규칙

`DESIGN-framer.md` 가 디자인 시스템의 단일 출처입니다. `app/ui.html` 의 `:root` 에
토큰이 그대로 들어가 있습니다. 새 요소를 만들 때:

- 색은 반드시 토큰(`var(--surface-1)` 등)으로. 하드코딩 금지.
- 위계는 `canvas → surface-1 → surface-2` 표면 단계로. 흰 글자의 투명도로 만들지 말 것.
- 텍스트 색은 `--ink` 아니면 `--ink-muted` 둘 중 하나. 중간 회색 추가 금지.
- CTA는 알약(`--r-pill`). 테두리만 있는 고스트 버튼 쓰지 말 것.
- `--accent-blue` 는 링크·포커스·선택 표시 전용. 배경이나 버튼 채우기로 쓰지 말 것.
- 그라디언트는 **카드**에만. 섹션 배경으로 깔지 말 것. 한 화면에 하나까지.
- 한글 디스플레이 트래킹은 -3%(`-0.03em`)까지만. 원 명세의 -5%는 한글 자소가 뭉칩니다.

## 테스트

whisper 없이 파이프라인을 검증하려면 전사 캐시를 직접 넣으면 됩니다:

```python
from agent.transcribe import Utterance, Word, save_cache
save_cache(Path('work/<영상이름>/transcript.json'), utterances)
```

`work/<영상이름>/transcript.json` 이 있으면 전사를 건너뜁니다.
가짜 견본 초안은 `testdrafts/` 아래에 draft_content.json 을 만들어 쓰면 됩니다.

## 하지 말 것

- CapCut UI 자동화(클릭 조작)로 방향을 틀지 마세요. 앱 업데이트마다 깨집니다.
- 사용자에게 터미널 명령이나 설정 파일 편집을 요구하는 기능을 추가하지 마세요.
  이 앱은 그걸 없애려고 만든 것입니다. 설정은 화면에서.
- `settings.json`, `media/`, `work/` 를 커밋하지 마세요 (.gitignore에 있음).
