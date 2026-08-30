#!/usr/bin/env bash
# build.sh - Compila e empacota o app Arquivos para Tab5 OS
set(set -euo pipefail)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${APP_DIR}/dist"
SDK_DIR="${TAB5_SDK_PATH:-${APP_DIR}/../tab5-os/sdk/tab5-app-sdk}"
PACK_TOOL="${SDK_DIR}/tools/pack.py"
WASI_CLANG="${WASI_SDK_PATH:-/home/moises/.wasi-sdk}/bin/clang"

mkdir -p "${DIST_DIR}"

if [ -x "${WASI_CLANG}" ] && [ -f "${APP_DIR}/src/main.c" ]; then
    echo "[INFO] Compilando WebAssembly com wasi-sdk clang..."
    "${WASI_CLANG}" -O2 -I"${SDK_DIR}/include" \
        -Wl,--export=main -Wl,--export=app_main -Wl,--allow-undefined \
        -o "${APP_DIR}/app.wasm" "${APP_DIR}/src/main.c"
elif [ ! -f "${APP_DIR}/app.wasm" ]; then
    echo "[WARN] wasi-sdk nao encontrado, gerando dummy wasm..."
    printf '\x00\x61\x73\x6d\x01\x00\x00\x00' > "${APP_DIR}/app.wasm"
fi

echo "[INFO] Empacotando com Tab5 Pack Tool..."
python3 "${PACK_TOOL}" "${APP_DIR}" -o "${DIST_DIR}"

echo "[OK] Build e empacotamento concluidos com sucesso em ${DIST_DIR}/com.tab5.files.tab5pkg"
