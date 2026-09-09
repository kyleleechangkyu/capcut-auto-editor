#!/bin/bash
# ─────────────────────────────────────────────
#  이 폴더를 GitHub 저장소로 올립니다.
#  한 번만 더블클릭하면 됩니다. 이후에는 필요 없습니다.
# ─────────────────────────────────────────────
cd "$(dirname "$0")" || exit 1

BOLD=$'\033[1m'; DIM=$'\033[2m'; RST=$'\033[0m'; GRN=$'\033[32m'; RED=$'\033[31m'
REPO_NAME="capcut-auto-editor"

pause_exit() { echo; read -r -p "엔터를 누르면 창이 닫힙니다. "; exit "${1:-0}"; }

echo "${BOLD}GitHub에 올리기${RST}"
echo

# ── gh 확인 ──────────────────────────────────
for p in /opt/homebrew/bin /usr/local/bin; do
  [ -x "$p/gh" ] && export PATH="$p:$PATH"
done

if ! command -v gh >/dev/null 2>&1; then
  echo "${RED}GitHub CLI(gh)가 없습니다.${RST}"
  echo "터미널에 아래를 붙여넣어 설치한 뒤 이 파일을 다시 더블클릭해 주세요."
  echo
  echo "  ${BOLD}brew install gh && gh auth login${RST}"
  pause_exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub 로그인이 필요합니다. 브라우저가 열립니다."
  gh auth login || pause_exit 1
fi

ACCOUNT=$(gh api user --jq .login 2>/dev/null)
echo "  ${GRN}✓${RST} GitHub 계정: ${BOLD}${ACCOUNT}${RST}"

# ── 저장소 정리 ──────────────────────────────
# (Claude가 샌드박스에서 만든 잠금 파일이 남아 있을 수 있어 먼저 치웁니다)
[ -d .git ] || git init -q
rm -f .git/*.lock .git/refs/heads/*.lock 2>/dev/null
find .git/objects -name 'tmp_obj_*' -delete 2>/dev/null
rm -rf .tmp_extract 2>/dev/null   # 설치 중 생긴 임시 폴더
git config user.name  >/dev/null 2>&1 || git config user.name  "$(gh api user --jq .name 2>/dev/null || echo "$ACCOUNT")"
git config user.email >/dev/null 2>&1 || git config user.email "$(gh api user --jq '.email // empty' 2>/dev/null || echo "${ACCOUNT}@users.noreply.github.com")"

git add -A
if ! git diff --cached --quiet 2>/dev/null; then
  git commit -q -m "변경분 반영 $(date '+%Y-%m-%d %H:%M')" && echo "  ${GRN}✓${RST} 커밋했습니다."
fi

# ── 이미 연결돼 있으면 그냥 밀어넣기 ──────────
if git remote get-url origin >/dev/null 2>&1; then
  echo "  이미 연결돼 있습니다: $(git remote get-url origin)"
  echo "  변경분을 올립니다…"
  git add -A
  git diff --cached --quiet || git commit -q -m "업데이트 $(date '+%Y-%m-%d %H:%M')"
  git push -u origin "$(git branch --show-current)" && echo "  ${GRN}✓${RST} 올렸습니다."
  pause_exit 0
fi

# ── 공개 / 비공개 고르기 ─────────────────────
echo
echo "저장소를 어떻게 만들까요?"
echo "  ${BOLD}1${RST}) 비공개 (private) ${DIM}— 나만 봅니다. 추천${RST}"
echo "  ${BOLD}2${RST}) 공개 (public)   ${DIM}— 누구나 볼 수 있습니다${RST}"
read -r -p "번호 [1]: " choice
VIS="--private"
[ "$choice" = "2" ] && VIS="--public"

echo
echo "저장소 이름 [${REPO_NAME}]:"
read -r -p "> " name
[ -n "$name" ] && REPO_NAME="$name"

# ── 만들고 올리기 ────────────────────────────
echo
echo "만드는 중…"
if gh repo create "$REPO_NAME" $VIS --source=. --remote=origin --push; then
  echo
  echo "  ${GRN}✓${RST} 완료  ${BOLD}https://github.com/${ACCOUNT}/${REPO_NAME}${RST}"
  echo
  echo "  다른 컴퓨터나 Claude Code에서 쓰려면:"
  echo "    ${BOLD}gh repo clone ${ACCOUNT}/${REPO_NAME}${RST}"
else
  echo
  echo "${RED}만들지 못했습니다.${RST} 같은 이름의 저장소가 이미 있을 수 있습니다."
  echo "다시 실행해 다른 이름을 넣어보세요."
  pause_exit 1
fi

pause_exit 0
