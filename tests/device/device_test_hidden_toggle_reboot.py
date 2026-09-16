#!/usr/bin/env python3
"""Validação no dispositivo (/dev/ttyACM0) da correção do reboot ao alternar
"Mostrar ocultos" no app Arquivos (REQ-001..REQ-005 do plano aprovado).

Automatizado (sem interação física):
  TEST-001 (REQ-001) - baseline: >=10 toggles do botão de ocultos com prova de
                       estabilidade (sys.info) após cada toggle; qualquer
                       queda de resposta + recuperação = REBOOT detectado.
  TEST-002 (REQ-002) - controle negativo: >=10 toggles do botão de Grade/Lista
                       (mesmo fluxo, sem NVS) -> não pode reiniciar; isola o
                       crash no caminho de persistência.
  TEST-003 (REQ-004) - repetir toggles de ocultos com o app em modo Lista
                       (grade->lista) e registrar heap livre/estabilidade.
  TEST-004 (REQ-005) - persistência em reinício do APP: alternar para ON,
                       app.close + app.open (novo app_init), e conferir no
                       ui.dump que o estado NVS foi restaurado (entrada '.').
                       Esperado FALHA hoje: wasm_tab5_nvs_get_u8 re-traduz
                       out_val (uso de wasm_runtime_addr_app_to_native num
                       ponteiro já nativo) -> files_hidden nunca é restaurado.

Opcional (operador/reboot físico), com --reboot:
  TEST-005 (REQ-005) - persistência após power-cycle: salva estado ON, aguarda
                       o operador pressionar o RST (ou executa TAB5_REBOOT_CMD,
                       se definido) e revalida o dump após a volta.

A suíte é SKIP inteira quando a porta não existe / o bridge não responde
sys.info (não é o ambiente certo para rodar).  Transcript NDJSON + raw serial
em /tmp/opencode/tab5_device/hidden_toggle_<ts>/.
"""

import argparse
import json
import os
import shutil
import sys
import time
import unittest
from pathlib import Path

DEVICE_PORT = os.environ.get("TAB5_DEVICE_PORT", "/dev/ttyACM0")
DEVICE_BAUD = int(os.environ.get("TAB5_DEVICE_BAUD", "115200"))
OUT_ROOT = Path("/tmp/opencode/tab5_device")
FILES_APP_ID = "com.tab5.files"
PROBE_TIMEOUT_S = 8
SETTLE_S = 0.6
TOGGLES_REQUIRED = int(os.environ.get("TAB5_TOGGLES", "10"))

PANIC_HINTS = (
    "Guru Meditation", "abort()", "Backtrace", "Stack canary",
    "panic", "rst:", "Store access fault", "Instruction fetch",
    "Load access fault", "assert failed", "wasm", "EXCEPTION",
)

REBOOT_HINTS = (
    "reboot", "restart", "rst:0x", "boot:0x", "ESP32", "ets",
)

# Glifos LVGL exibidos nos labels da app bar (o dump_ui do bridge emite os
# labels; o firmware mapeia "LV_SYMBOL_*" em tab5_host_abi.cpp sym_map /
# tab5_manifest.cpp; defines em sdk/tab5-app-sdk/include/tab5_sdk.h):
GLYPH_CLOSE = "\uf00d"       # LV_SYMBOL_CLOSE  — botão fechar da app bar
GLYPH_EYE_OPEN = "\uf06e"    # LV_SYMBOL_EYE_OPEN
GLYPH_EYE_CLOSE = "\uf070"   # LV_SYMBOL_EYE_CLOSE
EYE_GLYPHS = (GLYPH_EYE_OPEN, GLYPH_EYE_CLOSE)

# Faixa vertical da app bar do firmware (ui_bar.h: UI_BAR_HEIGHT = 52):
# barra de status do sistema ocupa [0,52) e a app bar do app [52,104].
APP_BAR_TOP = 52
APP_BAR_BOTTOM = 104


class SerialTransport:
    """Transporte NDJSON sobre o console USB-Serial-JTAG (mesmo padrão do
             tab5-os/tests/device/test_serial_bridge_device_validation.py)."""

    def __init__(self, port, baud):
        import serial  # pyserial

        self.ser = serial.Serial(port, baud, timeout=0.5)
        self.ser.reset_input_buffer()

    def drain(self, seconds=0.3):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            n = self.ser.in_waiting
            if n:
                self.ser.read(n)
            else:
                time.sleep(0.01)

    def exchange(self, command, timeout_s, raw=None):
        """Escreve comando e devolve (frames, raw_bytes). Determina resposta
        pelo action esperado."""
        request = json.loads(command)
        expected_action = request.get("cmd")
        self._write(command)
        t0 = time.monotonic()
        buf = bytearray()
        frames = []
        deadline = t0 + timeout_s
        got = False
        while time.monotonic() < deadline:
            try:
                data = self.ser.read(65536)
            except Exception:
                data = b""
            if not data:
                time.sleep(0.02)
                continue
            buf.extend(data)
            while True:
                i = buf.find(b"\n")
                if i < 0:
                    break
                line = bytes(buf[:i]).rstrip(b"\r").strip()
                del buf[: i + 1]
                if raw is not None:
                    raw.write(line + b"\n")
                if line.startswith(b"{"):
                    try:
                        obj = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(obj, dict):
                        frames.append(obj)
                        if obj.get("action") in (None, expected_action):
                            got = True
                            break
            if got:
                break
        return frames, bytes(buf)

    def _write(self, command):
        payload = command.encode() if isinstance(command, str) else command
        if not payload.endswith(b"\n"):
            payload += b"\n"
        self.ser.reset_input_buffer()
        self.ser.write(payload)
        self.ser.flush()


class HiddenToggleRebootDeviceValidation(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.out_dir = OUT_ROOT / ("hidden_toggle_" + time.strftime("%Y%m%d_%H%M%S"))
        cls.out_dir.mkdir(parents=True, exist_ok=True)
        cls.transcript = (cls.out_dir / "transcript.ndjson").open("a", encoding="utf-8")
        cls.raw = (cls.out_dir / "serial_raw.log").open("ab")
        cls.findings = []
        cls.reboot_count = 0
        cls.dot_entry_observed = False

        if not Path(DEVICE_PORT).exists():
            raise unittest.SkipTest(
                f"dispositivo ausente: {DEVICE_PORT}; rode na máquina com o "
                "tab5-os conectado (validação de dispositivo).")

        try:
            cls.transport = SerialTransport(DEVICE_PORT, DEVICE_BAUD)
        except Exception as exc:
            raise unittest.SkipTest(f"não foi possível abrir {DEVICE_PORT}: {exc!r}")

        probe, _ = cls.transport.exchange('{"cmd":"sys.info"}', PROBE_TIMEOUT_S, cls.raw)
        if not probe or probe[0].get("status") != "ok":
            raise unittest.SkipTest(
                f"bridge não respondeu sys.info em {DEVICE_PORT}: {probe!r}")
        cls.sysinfo = probe[0]

    @classmethod
    def tearDownClass(cls):
        try:
            cls.transport.exchange('{"cmd":"app.close"}', 8, cls.raw)
        except Exception:
            pass
        cls.transcript.write(json.dumps(
            {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "reboots_detected": cls.reboot_count,
             "dot_entry_observed": cls.dot_entry_observed,
             "findings": cls.findings}, ensure_ascii=False) + "\n")
        cls.transcript.close()
        cls.raw.close()
        print(f"\n[transcript] {cls.out_dir}")

    # ------------------------------------------------------------ helpers
    @classmethod
    def record_finding(cls, level, scope, message):
        cls.findings.append({"level": level, "scope": scope, "message": message})
        print(f"[FINDING {level}] {scope}: {message}", flush=True)

    def record(self, label, command, frames, extra=None):
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "label": label,
                 "cmd": command, "frames": frames}
        if extra:
            entry.update(extra)
        self.transcript.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.transcript.flush()

    def sys_info(self, label, timeout=PROBE_TIMEOUT_S):
        frames, _ = self.transport.exchange('{"cmd":"sys.info"}', timeout, self.raw)
        self.record(label, "sys.info", frames)
        return frames

    def probe_ok(self, label):
        frames = self.sys_info(label)
        return bool(frames and frames[0].get("status") == "ok")

    def dump(self, label):
        frames, _ = self.transport.exchange('{"cmd":"ui.dump"}', 12, self.raw)
        self.record(label, "ui.dump", frames)
        items = ((frames[0].get("data") or {}).get("items")) or [] if frames else []
        return items

    def dot_entries(self, items):
        return sorted({it.get("text", "") for it in items
                       if (it.get("text") or "").startswith(".")
                       and it.get("text") != ".."})

    def click(self, x, y, label):
        cmd = json.dumps({"cmd": "ui.click", "x": int(x), "y": int(y)})
        frames, _ = self.transport.exchange(cmd, 8, self.raw)
        self.record(label, cmd, frames)
        return frames

    def open_files(self):
        cmd = json.dumps({"cmd": "app.open", "id": FILES_APP_ID})
        frames, _ = self.transport.exchange(cmd, 15, self.raw)
        self.record("open files", cmd, frames)
        time.sleep(2.0)  # deixa o launch + render assentar
        return self.dump("dump após open files")

    def wait_recovery(self, timeout_s=30):
        """Após queda de comunicação, espera o bridge voltar (provável reboot)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                frames = self.sys_info("sys.info pós queda", timeout=6)
                if frames and frames[0].get("status") == "ok":
                    self.reboot_count += 1
                    self.record_finding(
                        "reboot", "estabilidade",
                        f"REBOOT detectado: bridge voltou a responder após queda "
                        f"({self.reboot_count}º). device test deve falhar em "
                        "TEST-001/002/003 (baseline red).")
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def click_and_probe(self, cx, cy, label):
        """Um clique + prova de estabilidade. Devolve True se sobreviveu."""
        self.click(cx, cy, label)
        time.sleep(SETTLE_S)
        if self.probe_ok(label + " / probe"):
            return True
        self.record_finding("falha", label, "sys.info não respondeu após o clique")
        recovered = self.wait_recovery()
        if not recovered:
            self.record_finding("falha", label,
                                "bridge não voltou após queda (porta morta?)")
        return not recovered and False

    def top_bar_labels(self, items):
        """Labels da faixa da app bar do app (y em [52, 104]).

        O dump_ui emite apenas labels/textareas (serial_bridge.cpp dump_ui),
        então os botões da app bar aparecem como o label interno.  A barra de
        status do sistema (relógio/bateria, y < 52) fica FORA dessa faixa e
        não pode entrar nas candidatas a toggle.
        """
        return [it for it in items
                if APP_BAR_TOP <= it.get("y", 1e9)
                and it.get("y", 1e9) + it.get("h", 0) <= APP_BAR_BOTTOM
                and it.get("x", -1) >= 0 and it.get("w", 0) > 0
                and it.get("h", 0) > 0]

    def is_close_label(self, item):
        """True para o botão fechar padrão da app bar (LV_SYMBOL_CLOSE).

        O firmware cria o fechar à direita de todas as apps (ui_app_bar.cpp)
        e o clique fecha o app — nunca pode ser alvo dos toggles.  Reconhece
        tanto o glifo (U+F00D) quanto a string crua "LV_SYMBOL_CLOSE" (e
        variantes "close"/"fechar"); "LV_SYMBOL_EYE_CLOSE" NÃO é fechar.
        """
        text = (item.get("text") or "").strip()
        upper = text.upper()
        return (GLYPH_CLOSE in text
                or upper in ("X", "×", "CLOSE", "FECHAR")
                or ("CLOSE" in upper and "EYE" not in upper))

    def find_toggles(self, items):
        """Identifica os botões Grade/Lista e Ocultos da app bar.

        Semântica primeiro, posição como fallback — nunca o botão fechar:

          1. descarta o fechar (LV_SYMBOL_CLOSE, U+F00D), o item mais à
             direita da barra (fecha o app se clicado);
          2. semântica: o toggle de ocultos é o único botão com glifo de olho
             (LV_SYMBOL_EYE_OPEN/CLOSE, U+F06E/U+F070);
          3. o toggle de Grade/Lista é o action button restante mais à direita
             (a app cria view antes de hidden na actions_cont -> ocultos fica
             colado ao fechar);
          4. fallback posicional (glifos indisponíveis): excluído o fechar,
             o mais à direita é o de ocultos e o penúltimo o de grade.

        Devolve (hidden_btn, view_btn, candidates).
        """
        zone = self.top_bar_labels(items)
        candidates = [it for it in zone if not self.is_close_label(it)]

        text = lambda it: (it.get("text") or "")
        hidden_btn = next(
            (it for it in candidates
             if any(g in text(it) for g in EYE_GLYPHS)),
            None)
        rest = [it for it in candidates if it is not hidden_btn]
        view_btn = None
        if hidden_btn is not None and rest:
            # excluídos fechar e ocultos, o action button mais à direita é o
            # toggle de Grade/Lista (título fica à esquerda, com flex grow)
            view_btn = max(rest, key=lambda it: it["x"] + it["w"])

        if hidden_btn is None or view_btn is None:
            ordered = sorted(candidates, key=lambda it: it["x"] + it["w"],
                             reverse=True)
            if len(ordered) >= 2:
                if hidden_btn is None:
                    hidden_btn = ordered[0]
                if view_btn is None:
                    view_btn = ordered[1]
            self.record_finding(
                "info", "find_toggles",
                f"identificação posicional (semântica de glifos indisponível): "
                f"{len(candidates)} candidatos na app bar")
        return hidden_btn, view_btn, candidates

    def ensure_active_app(self):
        active = self.dump("dump estado")
        return active

    # ---------------------------------------------------------------- tests
    def test_01_baseline_ten_hidden_toggles(self):
        """REQ-001/TEST-001: baseline — 10 toggles de ocultos, sem reboot."""
        items = self.open_files()
        hidden_btn, view_btn, candidates = self.find_toggles(items)
        if not (hidden_btn and view_btn):
            self.record_finding("skip", "TEST-001",
                                f"app bar com <2 botões identificáveis: {len(candidates)}; "
                                "não é possível conduzir o baseline.")
            self.skipTest("app bar sem botões: sem alvo para os toggles")

        cx, cy = hidden_btn["x"] + hidden_btn["w"] // 2, hidden_btn["y"] + hidden_btn["h"] // 2
        self.record_finding("info", "TEST-001",
                            f"toggle ocultos em ({cx},{cy}) — {TOGGLES_REQUIRED} toggles")

        survived = True
        heap_before = (self.sysinfo.get("data") or {}).get("heap_free_internal")
        for i in range(TOGGLES_REQUIRED):
            survived = self.click_and_probe(cx, cy, f"TEST-001 toggle {i + 1}")
            if not survived:
                break
        heap_after = (self.sys_info("TEST-001 heap após toggles")[0].get("data") or {}) \
            .get("heap_free_internal") if survived else None

        self.record_finding(
            "info", "TEST-001",
            f"heap_internal antes={heap_before} depois={heap_after} "
            f"reboots={self.reboot_count}")
        # BASELINE: espera-se FALHA hoje (crash ao persistir no worker).
        self.assertTrue(
            survived and self.reboot_count == 0,
            "REQ-001: >=10 toggles de ocultos sem reboot/instabilidade "
            "(FALHA ESPERADA no baseline pré-correção).")

    def test_02_view_toggle_control(self):
        """REQ-002/TEST-002: controle negativo — toggle Grade/Lista não pode
        reiniciar (caminho sem NVS)."""
        items = self.open_files()
        hidden_btn, view_btn, candidates = self.find_toggles(items)
        if not (hidden_btn and view_btn):
            self.skipTest("app bar sem botões")
        cx, cy = view_btn["x"] + view_btn["w"] // 2, view_btn["y"] + view_btn["h"] // 2
        survived = True
        for i in range(TOGGLES_REQUIRED):
            survived = self.click_and_probe(cx, cy, f"TEST-002 view toggle {i + 1}")
            if not survived:
                break
        self.assertTrue(
            survived and self.reboot_count == 0,
            "REQ-002: toggle de Grade/Lista (sem NVS) precisa sobreviver "
            ">=10 cliques; se reiniciar apenas aqui, a causa não é só o NVS.",
        )

    def test_03_hidden_toggles_in_list_mode(self):
        """REQ-004/TEST-003: toggles de ocultos também no modo Lista."""
        items = self.open_files()
        hidden_btn, view_btn, candidates = self.find_toggles(items)
        if not (hidden_btn and view_btn):
            self.skipTest("app bar sem botões")
        vx, vy = view_btn["x"] + view_btn["w"] // 2, view_btn["y"] + view_btn["h"] // 2
        hx, hy = hidden_btn["x"] + hidden_btn["w"] // 2, hidden_btn["y"] + hidden_btn["h"] // 2
        # alterna para o modo Lista (1 clique) e confirma o sucesso
        self.click(vx, vy, "TEST-003 muda p/ lista")
        time.sleep(SETTLE_S)
        items2 = self.dump("dump pós mudança p/ lista")
        self.record_finding("info", "TEST-003",
                            f"itens antes={len(items)} depois={len(items2)}")

        survived = True
        for i in range(TOGGLES_REQUIRED):
            survived = self.click_and_probe(hx, hy, f"TEST-003 lista toggle {i + 1}")
            if not survived:
                break
        self.assertTrue(
            survived and self.reboot_count == 0,
            "REQ-004: toggles de ocultos no modo Lista sem reboot/instabilidade",
        )

    def test_04_persistence_across_app_restart(self):
        """REQ-005/TEST-004: estado ON persiste após reinício do APP.

        Automatizado: alterna para ON (bounded), reinicia o app
        (app.close + app.open -> app_init relê NVS) e confere no dump que as
        entradas ocultas voltaram a aparecer.
        """
        items = self.open_files()
        hidden_btn, view_btn, candidates = self.find_toggles(items)
        if not (hidden_btn and view_btn):
            self.skipTest("app bar sem botões")
        hx, hy = hidden_btn["x"] + hidden_btn["w"] // 2, hidden_btn["y"] + hidden_btn["h"] // 2

        # garante estado ON (até dot entries aparecerem, com limite)
        desired_on = False
        for _ in range(6):
            time.sleep(SETTLE_S)
            current = self.dot_entries(self.dump("dump estado ocultos"))
            if current:
                desired_on = True
                break
            self.click(hx, hy, "TEST-004 rumo a ON")
        if not desired_on:
            self.record_finding(
                "skip", "TEST-004",
                "nenhuma entrada oculta ('.') visível no SD mesmo com toggle ON; "
                "persistência não pode ser observada neste ambiente.")
            self.skipTest("SD sem diretórios ocultos (.tab5_os ausente)")

        # reinício do app (equivalente automático ao reboot físico para o app)
        time.sleep(SETTLE_S)
        self.transport.exchange('{"cmd":"app.close"}', 8, self.raw)
        time.sleep(1.0)
        items2 = self.open_files()
        after = self.dot_entries(items2)
        self.record_finding("info", "TEST-004",
                            f"entradas ocultas após reinício do app: {after}")

        self.assertTrue(
            after,
            "REQ-005: estado 'Mostrar ocultos' ON precisa sobreviver ao "
            "reinício do app (NVS lido em app_init).",
        )

    def test_05_persistence_across_power_cycle(self):
        """REQ-005/TEST-005 (opcional, --reboot): persistência após power-cycle.

        Requer interação do operador (pressionar RST no hardware) ou
        TAB5_REBOOT_CMD definido.  É o TEST-005 do plano (reboot físico).
        """
        mandatory = getattr(self, "_do_reboot", False)
        if not mandatory:
            self.skipTest(
                "TEST-005 roda apenas com --reboot (operador pressiona RST no "
                "hardware, ou define TAB5_REBOOT_CMD).")
        items = self.open_files()
        hidden_btn, view_btn, candidates = self.find_toggles(items)
        if not hidden_btn:
            self.skipTest("app bar sem botões")
        hx, hy = hidden_btn["x"] + hidden_btn["w"] // 2, hidden_btn["y"] + hidden_btn["h"] // 2
        for _ in range(6):
            time.sleep(SETTLE_S)
            current = self.dot_entries(self.dump("dump estado ocultos (reboot)"))
            if current:
                break
            self.click(hx, hy, "TEST-005 rumo a ON")
        self.transport.ser.close() if hasattr(self.transport, "ser") else None

        reboot_cmd = os.environ.get("TAB5_REBOOT_CMD")
        if reboot_cmd:
            self.record_finding("info", "TEST-005",
                                f"executando TAB5_REBOOT_CMD={reboot_cmd}")
            os.system(reboot_cmd)
        else:
            print("\n>>> TEST-005: pressione o botão RST do dispositivo agora. "
                  "Aguardando até 120s...", flush=True)
            self.record_finding("info", "TEST-005",
                                "aguardando reset físico do operador (RST)")

        deadline = time.monotonic() + 120
        alive = False
        while time.monotonic() < deadline:
            time.sleep(1.0)
            if not Path(DEVICE_PORT).exists():
                continue  # porta caiu durante o reboot, aguarda voltar
            try:
                self.transport = SerialTransport(DEVICE_PORT, DEVICE_BAUD)
                frames = self.sys_info("sys.info pós power-cycle", timeout=6)
                if frames and frames[0].get("status") == "ok":
                    alive = True
                    break
            except Exception:
                continue
        if not alive:
            self.fail("dispositivo não voltou a responder após power-cycle")

        items2 = self.open_files()
        after = self.dot_entries(items2)
        self.record_finding("info", "TEST-005",
                            f"entradas ocultas após power-cycle: {after}")
        self.assertTrue(
            after, "REQ-005: estado 'Mostrar ocultos' precisa sobreviver ao "
            "power-cycle real do dispositivo (FALHA ESPERADA no baseline).")


def main(argv=None):
    global TOGGLES_REQUIRED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=None)
    parser.add_argument("--baud", type=int, default=None)
    parser.add_argument("--reboot", action="store_true",
                        help="habilita TEST-005 (power-cycle físico/operador)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args, runner_args = parser.parse_known_args(argv)
    if args.port:
        global DEVICE_PORT
        DEVICE_PORT = args.port
    if args.baud:
        global DEVICE_BAUD
        DEVICE_BAUD = args.baud

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        HiddenToggleRebootDeviceValidation)
    if args.reboot:
        # marca de classe p/ o teste 05 (há um único TestCase)
        HiddenToggleRebootDeviceValidation._do_reboot = True
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1,
                                     stream=sys.stdout)
    return 0 if runner.run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
