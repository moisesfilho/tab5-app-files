#!/usr/bin/env bash
# run_all_tests.sh - Orchestrator for the Tab5 Files App test suites
#
# Suites estáticas (sempre executadas):
#   1. Open-flow diagnostics (abertura de arquivos por associação)
#   2. Hidden-toggle contract (REQ-001..REQ-005, lado app: stack WASM, filtro
#      D1, bounds de render, persistência NVS no fonte)
#   3. Hidden-toggle filter model (comportamento real da regra D1 extraída)
#
# Suíte de dispositivo (opcional, requer /dev/ttyACM0 + bridge):
#   --device  -> device_test_hidden_toggle_reboot.py (validação serial)
#   --reboot  -> adiciona TEST-005 (power-cycle físico; operador pressiona RST
#                ou define TAB5_REBOOT_CMD). Implica --device.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DEVICE=0
RUN_REBOOT=0
for arg in "$@"; do
    case "$arg" in
        --device) RUN_DEVICE=1 ;;
        --reboot) RUN_DEVICE=1; RUN_REBOOT=1 ;;
        *) echo "opção desconhecida: $arg" >&2; exit 2 ;;
    esac
done

echo "========================================="
echo "  Tab5 Files App - Test Suite Runner"
echo "========================================="

TOTAL_PASS=0
TOTAL_FAIL=0
TOTAL_SKIP=0

run_py() {
    local suite_name="$1"
    local script="$2"
    shift 2
    echo ""
    echo ">>> Running: ${suite_name} ($(basename "${script}"))"
    if python3 "${script}" "$@"; then
        TOTAL_PASS=$((TOTAL_PASS + 1))
    else
        TOTAL_FAIL=$((TOTAL_FAIL + 1))
    fi
}

run_py "Open-flow diagnostics (associação de arquivos)" \
    "${SCRIPT_DIR}/test_open_flow_diagnostics.py"

run_py "Hidden-toggle contract (REQ-001..REQ-005, estático)" \
    "${SCRIPT_DIR}/test_hidden_toggle_contract.py"

run_py "Hidden-toggle filter model (regra D1 real)" \
    "${SCRIPT_DIR}/test_hidden_toggle_filter_model.py"

run_py "Hidden-toggle runtime contract (NVS, worker e ABI)" \
    "${SCRIPT_DIR}/test_files_hidden_reboot_runtime_contracts.py"

if [ "${RUN_DEVICE}" -eq 1 ]; then
    echo ""
    echo ">>> Running: Device-in-the-loop (serial /dev/ttyACM0)"
    EXTRA_ARGS=()
    if [ "${RUN_REBOOT}" -eq 1 ]; then
        EXTRA_ARGS+=(--reboot)
    fi
    if python3 "${SCRIPT_DIR}/device_test_hidden_toggle_reboot.py" "${EXTRA_ARGS[@]}"; then
        TOTAL_PASS=$((TOTAL_PASS + 1))
    else
        TOTAL_FAIL=$((TOTAL_FAIL + 1))
    fi
else
    echo ""
    echo "[SKIP] Device-in-the-loop: passe --device (dispositivo no /dev/ttyACM0)"
    TOTAL_SKIP=$((TOTAL_SKIP + 1))
fi

echo ""
echo "========================================="
echo "  Test Suites Summary"
echo "========================================="
echo "  Suites passed: ${TOTAL_PASS}"
echo "  Suites failed: ${TOTAL_FAIL}"
echo "  Suites skipped: ${TOTAL_SKIP}"
echo "========================================="

if [ "${TOTAL_FAIL}" -gt 0 ]; then
    echo ""
    echo "[OVERALL] SOME TESTS FAILED"
    exit 1
fi
echo ""
echo "[OVERALL] ALL EXECUTED SUITES PASSED"
exit 0
