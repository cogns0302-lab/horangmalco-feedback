#!/bin/bash
# 가이드·캐러셀에 쓰는 글꼴 설치 (둘 다 SIL OFL). 이미 있으면 건너뛴다.
set -e
DIR="${HOME}/.local/share/fonts/guide"
mkdir -p "$DIR"
for w in Regular Medium SemiBold Bold ExtraBold Black; do
  [ -s "$DIR/Pretendard-$w.otf" ] || curl -sSL -o "$DIR/Pretendard-$w.otf" \
    "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/packages/pretendard/dist/public/static/Pretendard-$w.otf"
done
[ -s "$DIR/BlackHanSans-Regular.ttf" ] || curl -sSL -o "$DIR/BlackHanSans-Regular.ttf" \
  "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/blackhansans/BlackHanSans-Regular.ttf"
fc-cache -f >/dev/null 2>&1 || true
fc-list | grep -c -i -E "pretendard|black han" | xargs echo "설치된 글꼴 파일 수:"
