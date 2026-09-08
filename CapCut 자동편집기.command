#!/bin/bash
# ─────────────────────────────────────────────
#  CapCut 자동 편집기
#  이 파일을 더블클릭하면 됩니다.
#  처음 한 번만 설치가 돌고, 그 다음부터는 바로 열립니다.
# ─────────────────────────────────────────────
cd "$(dirname "$0")" || exit 1

BOLD=$'\033[1m'; DIM=$'\033[2m'; RST=$'\033[0m'; GRN=$'\033[32m'; RED=$'\033[31m'

fail() {
  echo
  echo "${RED}$1${RST}"
  echo
  read -r -p "엔터를 누르면 창이 닫힙니다. "
  exit 1
}

# ── 1. 처음이면 설치 ───────────────────────────
if [ ! -f ".venv/.ready" ]; then
  echo "${BOLD}처음 실행이라 설치를 먼저 합니다. 몇 분 걸립니다.${RST}"
  echo "${DIM}(다음부터는 바로 열립니다)${RST}"
  echo

  # ffmpeg
  if ! command -v ffmpeg >/dev/null 2>&1; then
    # Homebrew 가 PATH 에 없을 수 있어 흔한 위치를 먼저 훑습니다
    for p in /opt/homebrew/bin /usr/local/bin; do
      [ -x "$p/ffmpeg" ] && export PATH="$p:$PATH"
    done
  fi
  if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "  · ffmpeg 설치 중…"
    if command -v brew >/dev/null 2>&1; then
      brew install ffmpeg || fail "ffmpeg 설치에 실패했습니다."
    else
      echo
      echo "  Homebrew 가 필요합니다. 아래 한 줄을 복사해 터미널에 붙여넣고 실행한 뒤,"
      echo "  이 파일을 다시 더블클릭해 주세요."
      echo
      echo "  ${BOLD}/bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"${RST}"
      echo
      read -r -p "엔터를 누르면 창이 닫힙니다. "
      exit 1
    fi
  fi
  echo "  ${GRN}✓${RST} ffmpeg"

  command -v python3 >/dev/null 2>&1 || fail "python3 가 없습니다. 맥 앱스토어에서 Xcode 명령줄 도구를 설치해 주세요."

  [ -d ".venv" ] || python3 -m venv .venv || fail "파이썬 환경을 만들지 못했습니다."
  # shellcheck disable=SC1091
  source .venv/bin/activate

  echo "  · 필요한 프로그램 받는 중… (가장 오래 걸리는 단계입니다)"
  pip install --quiet --upgrade pip
  pip install --quiet -r requirements.txt || fail "설치에 실패했습니다. 인터넷 연결을 확인해 주세요."
  echo "  ${GRN}✓${RST} 설치 완료"

  touch .venv/.ready
  echo
fi

# ── 2. 실행 ──────────────────────────────────
for p in /opt/homebrew/bin /usr/local/bin; do
  [ -x "$p/ffmpeg" ] && export PATH="$p:$PATH"
done
# shellcheck disable=SC1091
source .venv/bin/activate
python3 app/server.py

echo
read -r -p "엔터를 누르면 창이 닫힙니다. "
