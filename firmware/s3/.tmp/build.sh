#!/bin/sh
# ESP-IDF v5.5.5 构建通道（WorkBuddy 沙箱 Git Bash）。
# 参数透传给 idf.py：sh .tmp/build.sh build | -p COM5 flash | fullclean
set -u
export PROCESSOR_ARCHITECTURE=AMD64
export IDF_PATH='C:\esp\v5.5.5\esp-idf'
export IDF_TOOLS_PATH='C:\Espressif\tools'
export IDF_PYTHON_ENV_PATH='C:\Espressif\tools\python\v5.5.5\venv'
export ESP_ROM_ELF_DIR='C:\Espressif\tools\esp-rom-elfs'
export IDF_CCACHE_ENABLE=1

TOOLS=/c/Espressif/tools
export PATH="$TOOLS/xtensa-esp-elf/esp-14.2.0_20260121/xtensa-esp-elf/bin:$TOOLS/python/v5.5.5/venv/Scripts:$TOOLS/ninja/1.12.1:$TOOLS/cmake/3.30.2/bin:$TOOLS/ccache/4.12.1/ccache-4.12.1-windows-x86_64:$TOOLS/git/cmd:$TOOLS/idf-exe/1.0.3:$PATH"

cd "$(dirname "$0")/.." || exit 1
PY="$IDF_PYTHON_ENV_PATH/Scripts/python.exe"
exec "$PY" .tmp/idf_runner.py "$@"
