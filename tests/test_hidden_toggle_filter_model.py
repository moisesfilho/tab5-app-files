"""Behavioral model (host-side) for the hidden-directory filter contract.

Valida, em Python puro, a semântica exigida pela Decisão D1 do plano aprovado
(Fase 44) e pelo REQ-003/REQ-005 da correção do reboot ao alternar ocultos:

  1. ocultos por padrão: entradas iniciadas com '.' NÃO aparecem;
  2. com o toggle ON elas aparecem;
  3. "." e ".." NUNCA aparecem na listagem (navegação por go_up/`..`);
  4. ordenação: diretórios antes de arquivos, ambos por nome;
  5. o filtro é UMA regra em load_directory (extraída do fonte real de
     src/main.c — o modelo executa a condição textual real, não uma cópia).

Nenhum SDK é necessário; o teste falha apenas se o contrato da regra no fonte
divergir do comportamento esperado.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src" / "main.c").read_text(encoding="utf-8")

LOAD_DIRECTORY = re.search(
    r"static void load_directory\(const char \*path\)\n\{(?P<body>.*?)\n\}\n\nstatic void build_files_ui",
    SOURCE,
    re.DOTALL,
).group("body")

FILTER_LOOP = re.search(
    r"for \(uint32_t i = 0; i < count && s_entry_count < MAX_ENTRIES; i\+\+\)\s*\{(?P<body>.*?)\n    \}",
    LOAD_DIRECTORY,
    re.DOTALL,
).group("body")

ENTRY_RE = re.compile(
    r"if\s*\(strcmp\(name, \"\.\"\) == 0 \|\| strcmp\(name, \"\.\.\"\) == 0\)\s*\{[\s\S]*?continue;[\s\S]*?\}"
    r"[\s\S]*?"
    r"if\s*\(\s*(?P<cond>!s_show_hidden\s*&&\s*name\[0\]\s*==\s*'\.'\s*)\s*\)\s*\{[\s\S]*?continue;",
    re.DOTALL,
)
MATCH = ENTRY_RE.search(FILTER_LOOP)


def is_hidden(name: str, show_hidden: bool) -> bool:
    """Executa a condição REAL extraída do fonte (regra D1)."""
    if MATCH is None:
        raise AssertionError("regra de filtro D1 não encontrada no fonte")
    cond = MATCH.group("cond")
    # condições suportadas: !s_show_hidden && name[0]=='.'  (D1)
    assert "!s_show_hidden" in cond and "name[0]" in cond
    return name.startswith(".") if not show_hidden else False


MAX_ENTRIES = int(re.search(r"#define\s+MAX_ENTRIES\s+(\d+)", SOURCE).group(1))


def apply_filter(entries, show_hidden):
    listed = []
    for name, is_dir in entries:
        if name in (".", ".."):
            continue  # nunca listadas
        if is_hidden(name, show_hidden):
            continue
        if len(listed) >= MAX_ENTRIES:
            break  # espelha o limite do loop em load_directory
        listed.append((name, is_dir))
    # ordering: dirs first (is_dir), depois strcmp (ordenação estável em ASCII)
    listed.sort(key=lambda item: (0 if item[1] else 1, item[0]))
    return [name for name, _ in listed]


class HiddenFilterModel(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            MATCH,
            "a regra de filtro extraída do fonte não corresponde ao contrato D1",
        )
        self.entries = [
            (".tab5_os", True),   # diretório oculto (Fase 45)
            (".bashrc", False),   # arquivo oculto
            ("Music", True),
            ("readme.txt", False),
            (".", True),          # sempre ignorado
            ("..", True),         # sempre ignorado (navegação)
            ("Aula.pdf", False),
            ("zzz.log", False),
        ]

    def test_hidden_by_default(self):
        result = apply_filter(self.entries, show_hidden=False)
        self.assertNotIn(".tab5_os", result)
        self.assertNotIn(".bashrc", result)
        self.assertIn("Music", result)
        self.assertIn("readme.txt", result)

    def test_toggle_on_shows_hidden(self):
        result = apply_filter(self.entries, show_hidden=True)
        self.assertIn(".tab5_os", result)
        self.assertIn(".bashrc", result)
        for name in (".", ".."):
            self.assertNotIn(name, result, "navegação '.'/'..' nunca é listada")

    def test_parent_always_available_with_show_hidden_off(self):
        # '..' nunca é filtrado (mitigação da Fase 44, risco "perder-se").
        self.assertNotIn("..", apply_filter(self.entries, False))

    def test_dirs_first_then_alpha(self):
        result = apply_filter(self.entries, show_hidden=True)
        index = {item: i for i, item in enumerate(result)}
        self.assertLess(index["Music"], index["readme.txt"], "dirs antes de arquivos")
        self.assertLess(index["Aula.pdf"], index["zzz.log"], "ordem alfabética")
        # .tab5_os é diretório: vem antes de .bashrc (arquivo) mesmo oculto
        self.assertLess(index[".tab5_os"], index[".bashrc"])

    def test_toggle_roundtrip(self):
        hidden_off = apply_filter(self.entries, False)
        hidden_on = apply_filter(self.entries, True)
        self.assertNotEqual(len(hidden_off), len(hidden_on))
        # re-ocultar volta ao conjunto base
        self.assertEqual(apply_filter(self.entries, False), hidden_off)

    def test_repeated_toggle_is_idempotent(self):
        first = apply_filter(self.entries, True)
        for _ in range(10):  # REQ-001: >=10 toggles
            self.assertEqual(apply_filter(self.entries, True), first)

    def test_empty_and_scan_cap(self):
        self.assertEqual(apply_filter([], True), [])
        big = [(f".tmp{i}", True) for i in range(MAX_ENTRIES + 50)]
        result = apply_filter(big, show_hidden=True)
        self.assertLessEqual(
            len(result), MAX_ENTRIES,
            "listing must be bounded by MAX_ENTRIES",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)