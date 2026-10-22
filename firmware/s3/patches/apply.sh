#!/bin/sh
# 把已修改的第三方组件文件覆盖回 managed_components/。
# managed_components/ 在 .gitignore 里，idf.py 重新拉取组件后本地补丁会丢失，
# 执行本脚本即可恢复（每次改完 managed_components 里的文件也请重新 cp 一份到 patches/）。
set -eu
cd "$(dirname "$0")"
for src in $(find . -type f \( -name '*.cc' -o -name '*.h' \)); do
  dst="../managed_components/${src#./}"
  [ -f "$dst" ] || { echo "skip (missing): $dst"; continue; }
  cp "$src" "$dst"
  echo "patched: $dst"
done
