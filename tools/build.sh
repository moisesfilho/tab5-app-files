#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDK_DIR="${TAB5_SDK_PATH:-${SCRIPT_DIR}/../../tab5-os/sdk/tab5-app-sdk}"
exec "${SDK_DIR}/tools/build_wasm_app.sh" --app-dir "${SCRIPT_DIR}/.." --exports main app_main \
    tab5_app_on_ui_event on_ui_event tab5_app_on_theme_changed on_theme_changed \
    tab5_app_on_open_file on_open_file
