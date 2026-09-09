#!/bin/bash
# ─────────────────────────────────────────────
#  CapCut 자동 편집기
#  이 파일을 더블클릭하면 됩니다.
#  처음 한 번만 설치가 돌고, 그 다음부터는 바로 열립니다.
# ─────────────────────────────────────────────
cd "$(dirname "$0")" || exit 1

# ── 모든 출력을 install.log 에 남깁니다 ──────────
# (문제가 생겼을 때 Claude가 이 파일을 읽고 바로 진단할 수 있게)
LOG="$(pwd)/install.log"
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 시작"
echo "PATH=$PATH"
echo "shell=$BASH_VERSION  arch=$(uname -m)"
echo "----------------------------------------------"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RST=$'\033[0m'
GRN=$'\033[32m'; RED=$'\033[31m'; YLW=$'\033[33m'

fail() {
  echo
  echo "${RED}✗ $1${RST}"
  echo
  read -r -p "엔터를 누르면 창이 닫힙니다. "
  exit 1
}

# Homebrew 는 PATH 에 없을 수 있어 흔한 위치를 먼저 훑습니다
for p in /opt/homebrew/bin /usr/local/bin; do
  [ -d "$p" ] && export PATH="$p:$PATH"
done

# 설치 중 남은 임시 파일 정리
rm -rf .tmp_extract capcut-agent 2>/dev/null
rm -f capcut-app*.tar.gz capcut-agent*.tar.gz 2>/dev/null

# ══════════════════════════════════════════════
#  설치 (처음 한 번)
# ══════════════════════════════════════════════
if [ ! -f ".venv/.ready" ]; then
  echo "${BOLD}처음 실행이라 설치를 먼저 합니다.${RST}"
  echo "${DIM}전체 5~10분쯤 걸립니다. 이 창을 닫지 마세요.${RST}"
  echo

  # ── 1/3 ffmpeg ──────────────────────────────
  echo "${BOLD}[1/3]${RST} ffmpeg"
  if command -v ffmpeg >/dev/null 2>&1; then
    echo "      ${GRN}✓${RST} 이미 있습니다"
  else
    if ! command -v brew >/dev/null 2>&1; then
      echo
      echo "  ${YLW}Homebrew 가 필요합니다.${RST}"
      echo "  아래 한 줄을 복사해 ${BOLD}터미널${RST}에 붙여넣고 실행한 뒤,"
      echo "  이 파일을 다시 더블클릭해 주세요."
      echo
      echo "  ${BOLD}/bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"${RST}"
      echo
      read -r -p "엔터를 누르면 창이 닫힙니다. "
      exit 1
    fi
    echo "      설치 중… (몇 분 걸립니다)"
    brew install ffmpeg || fail "ffmpeg 설치에 실패했습니다."
    echo "      ${GRN}✓${RST} 완료"
  fi

  # ── 2/3 파이썬 환경 ─────────────────────────
  echo
  echo "${BOLD}[2/3]${RST} 파이썬 환경"
  command -v python3 >/dev/null 2>&1 || \
    fail "python3 가 없습니다. 터미널에서  xcode-select --install  을 먼저 실행해 주세요."
  if [ ! -d ".venv" ]; then
    python3 -m venv .venv || fail "파이썬 환경을 만들지 못했습니다."
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate || fail "파이썬 환경을 열지 못했습니다."
  echo "      ${GRN}✓${RST} python $(python3 -V 2>&1 | cut -d' ' -f2)"

  # ── 3/3 패키지 ──────────────────────────────
  echo
  echo "${BOLD}[3/3]${RST} 음성 인식 엔진 내려받기"
  echo "${DIM}      가장 오래 걸리는 단계입니다. 아래에 진행 상황이 나옵니다.${RST}"
  echo
  pip install --upgrade pip 2>&1 | grep -v "already satisfied" | sed 's/^/      /'
  if ! pip install -r requirements.txt 2>&1 | grep -v "already satisfied" | sed 's/^/      /'; then
    fail "설치에 실패했습니다. 인터넷 연결을 확인하고 다시 시도해 주세요."
  fi

  # 실제로 들어갔는지 확인 — 여기서 걸러야 나중에 조용히 멈추지 않습니다
  python3 -c "import faster_whisper, pycapcut" 2>/dev/null \
    || fail "설치가 끝나지 않았습니다. 다시 더블클릭해 주세요."

  touch .venv/.ready
  echo
  echo "      ${GRN}✓${RST} 설치 완료"
  echo
fi

# ══════════════════════════════════════════════
#  실행
# ══════════════════════════════════════════════
# shellcheck disable=SC1091
source .venv/bin/activate || fail "파이썬 환경을 열지 못했습니다. .venv 폴더를 지우고 다시 실행해 주세요."

command -v ffmpeg >/dev/null 2>&1 || fail "ffmpeg 를 찾지 못했습니다. 터미널에서  brew install ffmpeg  를 실행해 주세요."
python3 -c "import faster_whisper, pycapcut" 2>/dev/null \
  || fail "설치가 덜 됐습니다. .venv 폴더를 지우고 이 파일을 다시 더블클릭해 주세요."

python3 app/server.py

echo
read -r -p "엔터를 누르면 창이 닫힙니다. "
