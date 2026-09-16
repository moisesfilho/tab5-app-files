"""Static contracts for the hidden-directory toggle reboot fix (app Arquivos).

Cobre os REQ-001..REQ-005 do plano aprovado ("reboot ao alternar diretórios
ocultos no app Arquivos").  Este módulo valida o lado *do app* (src/main.c e
manifest.json):

  REQ-001/REQ-002 (baseline/crash)  -> evidência vem do device test
                                       (device/device_test_hidden_toggle_reboot.py).
  REQ-003 (stack WASM buf[512])     -> o buffer de scan de load_directory NÃO
                                       pode ter storage automático (pilha
                                       WASM).  `static` no corpo da função ou
                                       um global de escopo de arquivo têm
                                       duração estática e satisfazem o
                                       requisito: baseline (buffer local) ->
                                       red; correção (static/global) -> green.
  REQ-004 (LVGL na thread principal)-> invariantes de render/bounds que evitam
                                       use-after-free de handles ao re-render.
  REQ-005 (persistência NVS)        -> read em app_init ANTES de build_files_ui
                                       e write 0/1 no toggle, antes do reload.

Nenhuma asserção depende de SDK ou de link: apenas fonte + manifesto.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src" / "main.c").read_text(encoding="utf-8")
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))


def function_body(source: str, signature: str) -> str:
    """Corpo da função C/C++, respeitando chaves aninhadas.

    Localiza a DEFINIÇÃO (não a declaração forward): a partir de uma
    ocorrência da assinatura, avança até o primeiro delimitador `{` ou `;`
    (uma declaração forward termina em ';' antes de qualquer '{').  Se for
    `;`, procura a próxima ocorrência; se for `{`, é a abertura do corpo.
    """
    search_from = 0
    while True:
        start = source.find(signature, search_from)
        if start < 0:
            raise AssertionError(f"definição não encontrada: {signature}")
        pos = start + len(signature)
        while pos < len(source) and source[pos] not in "{;":
            pos += 1
        if pos < len(source) and source[pos] == "{":
            opening = pos
            break
        # era declaração (`;`) ou fim de arquivo: tenta a próxima ocorrência
        search_from = start + 1
    depth = 0
    for pos in range(opening, len(source)):
        if source[pos] == "{":
            depth += 1
        elif source[pos] == "}":
            depth -= 1
            if depth == 0:
                return source[opening + 1 : pos]
    raise AssertionError(f"bloco sem fechamento: {signature}")


LOAD_DIRECTORY = function_body(SOURCE, "static void load_directory(const char *path)")
RENDER_CONTENT = function_body(SOURCE, "static void render_content(void)")
ON_ITEM_CLICK = function_body(SOURCE, "static void on_item_click(int32_t idx)")
ON_HIDDEN_TOGGLE = function_body(SOURCE, "static void on_hidden_toggle(void *user_data)")
APP_INIT = function_body(SOURCE, "static void app_init(void)")
ENTRY_CMP = function_body(SOURCE, "static int entry_cmp(const void *a, const void *b)")
GO_UP = function_body(SOURCE, "static void go_up(void)")

FILTER_LOOP = re.search(
    r"for \(uint32_t i = 0; i < count && s_entry_count < MAX_ENTRIES; i\+\+\)\s*\{(?P<body>.*?)\n    \}",
    LOAD_DIRECTORY,
    re.DOTALL,
)
assert FILTER_LOOP is not None, "loop de filtro de load_directory não encontrado"
FILTER_BODY = FILTER_LOOP.group("body")

# sizeof(tab5_dir_entry_t) = name[64] + size(4) + mtime(4) + is_dir(1) =
# 73 bytes, alinhado a 4 -> 76 bytes (sdk/tab5-app-sdk/include/tab5_sdk.h).
ENTRY_SIZE = 76


def scan_buffer_usage_bytes():
    match = re.search(r"#define\s+MAX_SCAN_ENTRIES\s+(\d+)", SOURCE)
    count = int(match.group(1)) if match else 0
    return count * ENTRY_SIZE


def scan_buffer_name():
    """Nome do buffer de scan: 2º argumento de tab5_storage_scandir(...)."""
    match = re.search(
        r"tab5_storage_scandir\(\s*[A-Za-z_]\w*\s*,\s*(?P<name>[A-Za-z_]\w*)\s*,",
        LOAD_DIRECTORY,
    )
    assert match is not None, \
        "call de tab5_storage_scandir com buffer de scan não encontrada"
    return match.group("name")


def scan_buffer_decls(body):
    """Ocorrências da declaração do buffer de scan em `body`.

    Âncora semântica: o buffer usado na chamada tab5_storage_scandir
    (rename-proof, em vez de casar um nome fixo `buf`).  O match começa no
    token do tipo `tab5_dir_entry_t`; um eventual `static` fica no prefixo,
    antes do match — é isso que permite distinguir storage estática de
    automática pelo texto que antecede a declaração.
    """
    name = scan_buffer_name()
    return list(re.finditer(
        r"tab5_dir_entry_t\s+" + name + r"\[", body))


def automatic_scan_buffer_decls(body):
    """Declarações do buffer de scan COM storage automático (pilha WASM).

    Storage automático = objeto local sem `static` -> pilha WASM (proibido
    pelo REQ-003).  `static <nome>[]` no corpo de uma função (storage
    estática, fora da pilha) e globais de escopo de arquivo NÃO são listados
    aqui.
    """
    automatic = []
    for m in scan_buffer_decls(body):
        prefix = body[: m.start()]
        if not re.search(r"\bstatic\s+$", prefix):
            automatic.append(m.group(0))
    return automatic


def is_static_or_file_scope(source, pos):
    """True se a declaração em `pos` tem duração estática.

    Satisfazem o REQ-003: (a) declaração precedida de `static`/`extern`
    (bloco ou escopo de arquivo) ou (b) declaração em escopo de arquivo
    (profundidade de chaves == 0 na posição — objeto global, nunca na pilha).
    """
    if re.search(r"\b(?:static|extern)\s+$", source[:pos]):
        return True
    depth = 0
    for ch in source[:pos]:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
    return depth == 0


class ScanBufferStackContract(unittest.TestCase):
    """REQ-003: o buffer de scan não pode morar na pilha WASM do app."""

    def test_load_directory_has_no_local_scan_buffer(self):
        # REQ-003: o buffer de scan NÃO pode ter storage automático (pilha
        # WASM, ~38,9 KiB de uma stack de 64 KiB do manifesto).  A correção
        # aceita `static tab5_dir_entry_t buf[MAX_SCAN_ENTRIES];` no corpo da
        # função (storage estática, reusada entre toggles) ou um global de
        # escopo de arquivo; apenas a declaração local SEM `static` é o
        # defeito que estoura a stack na call de persistência.
        automatic = automatic_scan_buffer_decls(LOAD_DIRECTORY)
        self.assertFalse(
            automatic,
            "REQ-003: load_directory declara o buffer de scan com storage "
            "automático (pilha WASM): "
            f"{automatic}. Use `static tab5_dir_entry_t buf[...]` no corpo "
            "(ou um global de escopo de arquivo); um local desse tamanho na "
            "call de persistência é candidato a estouro de stack/reboot.",
        )
        # O buffer precisa existir em algum lugar e ter duração estática
        # (static em qualquer escopo, ou global de arquivo).  O nome vem do
        # 2º argumento de tab5_storage_scandir: renomear `buf` (ex.: um
        # global `g_scan_buf`) não enfraquece o contrato.
        name = scan_buffer_name()
        all_decls = list(re.finditer(
            r"tab5_dir_entry_t\s+" + name + r"\[", SOURCE))
        self.assertTrue(
            all_decls,
            f"REQ-003: declaração do buffer de scan `{name}[]` não encontrada "
            "em src/main.c.",
        )
        for m in all_decls:
            self.assertTrue(
                is_static_or_file_scope(SOURCE, m.start()),
                "REQ-003: o buffer de scan precisa ter duração estática "
                "(declarado com `static`, em corpo de função ou escopo de "
                "arquivo) — nunca automático na pilha WASM. Ocorrência "
                f"ofensora: {m.group(0)!r}@pos {m.start()}.",
            )

    def test_scan_buffer_is_reused_not_reallocated_per_toggle(self):
        # Alternância rápida (REQ-001: >=10 toggles) não pode reaprovisionar o
        # buffer a cada load_directory.
        self.assertNotIn(
            "malloc(", LOAD_DIRECTORY,
            "REQ-003: load_directory não deve alocar por chamada; prefira "
            "buffer static reusado.",
        )

    def test_scan_buffer_stack_budget_is_bounded(self):
        # Documentação do orçamento: MAX_SCAN_ENTRIES*76 bytes (~38,9 KiB).
        # Com REQ-003 corrigido (static/global) o buffer NÃO consome a pilha
        # WASM; o limite rígido abaixo só se aplica enquanto existir
        # declaração automática (regressão ao baseline).
        stack = int(MANIFEST.get("stack_size") or 0)
        used = scan_buffer_usage_bytes()
        self.assertGreater(stack, 0, "manifest.json sem stack_size")
        self.assertGreater(used, 0, "MAX_SCAN_ENTRIES inválido no fonte")
        automatic = automatic_scan_buffer_decls(LOAD_DIRECTORY)
        if automatic:
            ratio = used / stack
            self.assertLess(
                ratio, 3 / 4,
                f"REQ-003: declaração automática de buf[] ({used} bytes) "
                f"consome {ratio:.0%} da stack WASM declarada ({stack} "
                "bytes); margem abaixo de 25% é risco para a profundidade de "
                "call com persistência (qsort+render+nvs_commit).",
            )

    def test_max_constants_are_present_and_bounded(self):
        self.assertRegex(SOURCE, r"#define\s+MAX_ENTRIES\s+\d+")
        self.assertRegex(SOURCE, r"#define\s+MAX_SCAN_ENTRIES\s+\d+")
        scan = int(re.search(r"#define\s+MAX_SCAN_ENTRIES\s+(\d+)", SOURCE).group(1))
        self.assertLessEqual(scan, 512, "MAX_SCAN_ENTRIES acima do combinado (512)")


class HiddenFilterContract(unittest.TestCase):
    """Decisão D1 do plano (Fase 44): filtro único em load_directory."""

    def test_dot_and_dotdot_are_excluded_before_hidden_check(self):
        self.assertRegex(FILTER_BODY, r'strcmp\(name, "\."\)\s*==\s*0')
        self.assertRegex(FILTER_BODY, r'strcmp\(name, "\.\."\)\s*==\s*0')
        self.assertLess(
            FILTER_BODY.index(".."),
            FILTER_BODY.index("s_show_hidden"),
            "REQ: '.' e '..' devem ser excluídos ANTES do filtro de ocultos "
            "(.. nunca pode ser filtrado).",
        )

    def test_hidden_filter_is_the_d1_rule(self):
        # D1: `name[0]=='.' && name!=".." && !show_hidden` -> skip.
        self.assertRegex(
            FILTER_BODY,
            r"if\s*\(\s*!s_show_hidden\s*&&\s*name\[0\]\s*==\s*'\.'\s*\)",
            "REQ-002/D1: o filtro de ocultos precisa ser "
            "`if (!s_show_hidden && name[0] == '.') continue;` (regra única).",
        )

    def test_filters_are_centralized_in_load_directory(self):
        # A regra de oculteza não pode ser duplicada na renderização.
        self.assertNotIn("name[0]", RENDER_CONTENT,
                         "REQ-001/D1: render_content não pode re-filtrar "
                         "ocultos; regra única em load_directory.")

    def test_parent_navigation_never_filtered(self):
        # A navegação '..' nunca passa pelo filtro de ocultos: ela é excluída
        # ANTES do check `name[0] == '.'` (coberto acima) e o app oferece go_up
        # com guarda de raiz.
        self.assertIn("strcmp(name, \"..\")", FILTER_BODY)
        self.assertRegex(GO_UP, r'strcmp\(s_current_path, "/sdcard"\)\s*==\s*0')
        self.assertIn("load_directory(\"/sdcard\")", GO_UP)
        self.assertIn("load_directory(parent)", GO_UP)

    def test_dirs_first_ordering_contract(self):
        # entry_cmp: diretórios primeiro, depois strcmp por nome.
        self.assertIn("is_dir", ENTRY_CMP)
        self.assertIn("strcmp(ea->name, eb->name)", ENTRY_CMP)
        self.assertLess(ENTRY_CMP.index("is_dir"), ENTRY_CMP.index("strcmp"),
                        "diretórios precisam vir antes dos arquivos")

    def test_toggle_reloads_current_path(self):
        self.assertIn("load_directory(s_current_path)", ON_HIDDEN_TOGGLE)


class RenderBoundsContract(unittest.TestCase):
    """REQ-004/REQ-001: invariantes de render que evitam use-after-free."""

    def test_item_count_reset_before_creates(self):
        first_assign = RENDER_CONTENT.index("s_item_count = 0")
        first_handle = RENDER_CONTENT.index("s_item_handles[")
        self.assertLess(first_assign, first_handle,
                        "s_item_count precisa zerar antes de novos handles "
                        "(REQ-004: sem handles residuais de re-renders)")

    def test_handle_arrays_written_only_inside_bounds(self):
        self.assertRegex(RENDER_CONTENT, r"s_item_handles\[s_item_count\]")
        self.assertRegex(RENDER_CONTENT, r"s_item_entry_idx\[s_item_count\]")
        self.assertRegex(RENDER_CONTENT, r"s_item_count\s*<\s*MAX_ENTRIES")
        self.assertRegex(SOURCE, r"s_item_handles\[MAX_ENTRIES\]")
        self.assertRegex(SOURCE, r"s_item_entry_idx\[MAX_ENTRIES\]")

    def test_item_click_bounds_guards(self):
        self.assertRegex(ON_ITEM_CLICK, r"if\s*\(idx\s*<\s*0\)")
        self.assertRegex(
            ON_ITEM_CLICK,
            r"if\s*\(\(uint32_t\)idx\s*>=\s*s_entry_count\)",
        )


class NvsPersistenceContract(unittest.TestCase):
    """REQ-005 (lado app): persistência lida antes da UI e gravada no toggle."""

    def test_nvs_namespace_and_key(self):
        self.assertRegex(SOURCE, r'#define\s+NVS_NS\s+"tab5"')
        self.assertRegex(SOURCE, r'#define\s+NVS_KEY_HIDDEN\s+"files_hidden"')

    def test_nvs_read_precedes_ui_build(self):
        # app_init lê files_hidden ANTES de build_files_ui().
        init_before_ui = APP_INIT[: APP_INIT.index("build_files_ui()")]
        self.assertIn("tab5_nvs_get_u8(NVS_NS, NVS_KEY_HIDDEN, &hidden)", init_before_ui)
        self.assertIn("s_show_hidden = (hidden != 0);", init_before_ui)

    def test_toggle_persists_before_reload(self):
        # on_hidden_toggle grava 0/1 e só depois recarrega o diretório atual.
        toggle_until_reload = ON_HIDDEN_TOGGLE[: ON_HIDDEN_TOGGLE.index("load_directory(s_current_path)")]
        self.assertIn(
            "tab5_nvs_set_u8(NVS_NS, NVS_KEY_HIDDEN, s_show_hidden ? 1 : 0)",
            toggle_until_reload,
            "REQ-005: o estado precisa ser persistido antes do reload do "
            "diretório (s_show_hidden ? 1 : 0, nunca outro valor).",
        )

    def test_hidden_state_read_error_defaults_to_hidden(self):
        # Valor NVS inválido não pode vazar para a UI: usado somente 0/1.
        self.assertNotIn("s_show_hidden = hidden;", APP_INIT)


class ManifestContract(unittest.TestCase):
    def test_manifest_declares_stack_and_heap(self):
        self.assertGreater(int(MANIFEST.get("stack_size") or 0), 0)
        self.assertGreater(int(MANIFEST.get("heap_size") or 0), 0)
        self.assertEqual(MANIFEST.get("id"), "com.tab5.files")


if __name__ == "__main__":
    unittest.main(verbosity=2)
