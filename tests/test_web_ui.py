"""Contrat entre l'interface (HTML/CSS) et son script — sans navigateur.

Une faute de frappe dans un identifiant casse une vue entière et ne se voit
qu'en cliquant. Ces tests relisent les trois fichiers et vérifient qu'ils se
parlent bien : identifiants, vues, menus, raccourcis, classes stylées.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "hookycreator" / "web"

#: éléments fabriqués à la volée par le script (panneau de détail), donc absents
#: du HTML livré : ils sont créés par showInspector() et ses appelants.
DYNAMIC_IDS = {
    "btn-blender", "btn-blend", "btn-export-png", "btn-import",
    "btn-translate-model", "btn-turntable", "copy-command", "file-import",
    "model-budget", "model-budget-note", "model-quality", "model-shot",
}


def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


class WebContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = _read("index.html")
        self.js = _read("app.js")
        self.css = _read("style.css")

    def test_every_used_id_exists(self) -> None:
        used = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"', self.js))
        present = set(re.findall(r'id="([A-Za-z0-9_-]+)"', self.html))
        missing = sorted(used - present - DYNAMIC_IDS)
        self.assertEqual(missing, [], f"identifiants absents du HTML : {missing}")

    def test_every_view_has_a_section_and_a_rail_button(self) -> None:
        sections = set(re.findall(r'id="view-([a-z]+)"', self.html))
        rail = set(re.findall(r'data-view="([a-z]+)"', self.html))
        labels = set(re.findall(
            r'^\s+(home|models|ensembles|textures|mobs|blender|settings):',
            self.js, re.M))
        self.assertEqual(sections, rail)
        self.assertTrue(sections <= labels | {"home"})
        self.assertIn("home", sections)

    def test_menus_have_entries(self) -> None:
        start = self.js.index("const MENUS = {")
        end = self.js.index("\n};", start)
        block = self.js[start:end]
        buttons = set(re.findall(r'data-menu="([a-z]+)"', self.html))
        entries = set(re.findall(r'^\s{2}([a-z]+): \[', block, re.M))
        self.assertEqual(buttons, entries, "un menu sans entrée ne s'ouvrirait pas")

    def test_shortcuts_are_documented_in_the_menus(self) -> None:
        for shortcut in ("Ctrl+K", "Ctrl+J", "Ctrl+F"):
            self.assertIn(shortcut, self.js)

    def test_key_classes_are_styled(self) -> None:
        for selector in (".titlebar", ".rail-btn", ".tab", ".statusbar",
                         ".palette-box", ".inspector", ".panel", ".tile",
                         ".btn.primary", ".log", ".menu-panel"):
            self.assertIn(selector, self.css, f"classe non stylée : {selector}")

    def test_accent_and_density_are_driven_by_settings(self) -> None:
        self.assertIn('dataset.accent', self.js)
        self.assertIn('dataset.density', self.js)
        self.assertIn('html[data-accent="azur"]', self.css)
        self.assertIn('html[data-density="compact"]', self.css)

    def test_ensemble_view_is_wired_to_the_api(self) -> None:
        self.assertIn("/api/ensembles", self.js)
        self.assertIn("renderEnsembles", self.js)
        self.assertIn("tex=1", self.js)

    def test_blend_export_is_wired_to_the_api(self) -> None:
        self.assertIn("/api/model/blend", self.js)
        self.assertIn("/api/blender", self.js)

    def test_blend_view_is_wired_to_the_api(self) -> None:
        for route in ("/api/blend/list", "/api/blend/inspect",
                      "/api/blend/preview", "/api/blend/translate"):
            self.assertIn(route, self.js)
        self.assertIn("/api/blend/upload", self.js)

    @unittest.skipUnless(shutil.which("node"), "node absent")
    def test_javascript_parses(self) -> None:
        result = subprocess.run(["node", "--check", str(WEB / "app.js")],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":               # pragma: no cover
    unittest.main()
