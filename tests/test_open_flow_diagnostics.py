"""Static diagnostics for the file-opening flow.

This repository does not contain the Tab5 SDK or a runtime test harness, so
these checks validate the source/manifest contract without linking or mocking
SDK APIs that are not present here.
"""

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src" / "main.c").read_text(encoding="utf-8")
ON_ITEM_CLICK = re.search(
    r"static void on_item_click\(int32_t idx\)\n\{(?P<body>.*?)\n\}\n\nstatic void render_content",
    SOURCE,
    re.DOTALL,
).group("body")


class OpenFlowDiagnostics(unittest.TestCase):
    def test_directory_entry_does_not_call_file_association(self):
        branch = re.search(
            r"if\s*\(entry->is_dir\)\s*\{(?P<directory>.*?)\}\s*else\s*\{(?P<file>.*?)\}",
            ON_ITEM_CLICK,
            re.DOTALL,
        )
        self.assertIsNotNone(branch, "on_item_click branch was not found")
        self.assertNotIn("tab5_file_assoc_open", branch.group("directory"))

    def test_file_entry_calls_file_association_open(self):
        branch = re.search(
            r"if\s*\(entry->is_dir\).*?else\s*\{(?P<file>.*?)\}",
            ON_ITEM_CLICK,
            re.DOTALL,
        )
        self.assertIsNotNone(branch, "on_item_click branch was not found")
        self.assertIn("tab5_file_assoc_open(full_path)", branch.group("file"))

    def test_manifest_has_no_file_associations(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([], manifest.get("file_associations"))

    def test_file_open_has_before_and_after_diagnostic_logs(self):
        """The open attempt records its path, result, and failure status."""
        branch = re.search(
            r"else\s*\{(?P<file>.*?)tab5_file_assoc_open\(full_path\)(?P<after>.*?)\}",
            ON_ITEM_CLICK,
            re.DOTALL,
        )
        self.assertIsNotNone(branch, "file-open branch was not found")
        self.assertIn("tab5_system_log", branch.group("file"), "missing pre-open log")
        self.assertIn("tab5_system_log", branch.group("after"), "missing post-open log")
        self.assertRegex(ON_ITEM_CLICK, r"tab5_err_t\s+ret\s*=\s*tab5_file_assoc_open\(full_path\)")
        self.assertIn("(int)ret", ON_ITEM_CLICK, "missing numeric return in diagnostic log")
        self.assertIn("ret == TAB5_OK", ON_ITEM_CLICK, "missing return interpretation")
        self.assertIn("ret != TAB5_OK", ON_ITEM_CLICK, "missing failure condition")


if __name__ == "__main__":
    unittest.main()
