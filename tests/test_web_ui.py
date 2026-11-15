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
    "btn-blender", "btn-blend", "btn-export-png", "btn-import", "btn-uv",
    "btn-translate-model", "btn-turntable", "copy-command", "file-import",
    "model-budget", "model-budget-note", "model-quality", "model-shot",
    "uv-back", "uv-flip", "uv-normal", "uv-shot", "sound-player",
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
            r'^\s+(home|edit|create|models|ensembles|textures|sounds|mobs|blender|settings):',
            self.js, re.M))
        self.assertEqual(sections, rail)
        self.assertTrue(sections <= labels | {"home"})
        self.assertIn("home", sections)

    def test_editing_and_mod_creation_have_separate_workspaces(self) -> None:
        self.assertIn('data-goto="edit"', self.html)
        self.assertIn('data-goto="create"', self.html)
        home = self.html.split('id="view-home"', 1)[1].split('id="view-edit"', 1)[0]
        creation = self.html.split('id="view-create"', 1)[1].split('id="view-models"', 1)[0]
        self.assertNotIn('id="mod-form"', home)
        self.assertIn('id="mod-form"', creation)
        self.assertIn('id="creation-status"', creation)
        self.assertIn('sera entièrement remplacé', creation)
        self.assertIn("pas la création automatique d'un acteur jouable", creation)
        self.assertIn('showView("create"); $("#mod-name")?.focus()', self.js)
        self.assertNotIn('$("#mod-form")?.requestSubmit()', self.js)

    def test_unsupported_operations_are_described_without_promising_a_mod(self) -> None:
        self.assertIn("BFRES auxiliaires (rapport)", self.html)
        self.assertIn('kinds.push("animation", "auxiliary")', self.js)
        self.assertIn("leurs sous-maillages ne sont pas réécrits", self.html)
        self.assertIn("aucun acteur jouable", self.html)

    def test_small_screens_keep_the_inspector_visible(self) -> None:
        self.assertIn('.inspector:not([hidden])', self.css)
        self.assertNotIn('.inspector { display: none; }', self.css)
        app_grid = self.css.split('.app {', 1)[1].split('}', 1)[0]
        self.assertIn('grid-template-columns: minmax(0, 1fr)', app_grid)

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

    def test_uv_checker_is_wired_to_the_api(self) -> None:
        self.assertIn("/api/model/uv", self.js)
        self.assertIn("/api/model/uv/info", self.js)
        self.assertIn("showUv", self.js)

    def test_ensemble_view_is_wired_to_the_api(self) -> None:
        self.assertIn("/api/ensembles", self.js)
        self.assertIn("renderEnsembles", self.js)
        self.assertIn("tex=1", self.js)

    def test_native_engine_status_is_visible_in_settings(self) -> None:
        self.assertIn('id="engine-list"', self.html)
        self.assertIn('id="engine-hint"', self.html)
        self.assertIn('payload.engine', self.js)
        self.assertIn('renderEngine()', self.js)
        self.assertIn('texture.deswizzle', self.js)
        self.assertIn('Tables de formes BFRES 10', self.js)
        self.assertIn('zstd.label', self.js)
        self.assertIn('Décompression Zstandard', self.js)
        self.assertIn('sarc.label', self.js)
        self.assertIn('Lecture et reconstruction SARC', self.js)
        self.assertIn('restbl.label', self.js)
        self.assertIn('Lecture et reconstruction RESTBL', self.js)

    def test_blend_export_is_wired_to_the_api(self) -> None:
        self.assertIn("/api/model/blend", self.js)
        self.assertIn("/api/blender", self.js)

    def test_blend_view_is_wired_to_the_api(self) -> None:
        for route in ("/api/blend/list", "/api/blend/inspect",
                      "/api/blend/preview", "/api/blend/translate"):
            self.assertIn(route, self.js)
        self.assertIn("/api/blend/upload", self.js)

    def test_recherche_est_differee(self) -> None:
        """Taper ne doit pas reconstruire la grille à chaque touche."""
        self.assertIn("function onInputDebounced", self.js)
        self.assertIn('onInputDebounced("#search"', self.js)
        self.assertIn('onInputDebounced("#search-tex"', self.js)
        self.assertIn('onInputDebounced("#search-ens"', self.js)
        self.assertIn('ensembles: "#search-ens"', self.js)
        # l'ancien câblage direct relançait un rendu par frappe
        self.assertNotIn('$("#search-tex").oninput = (event) => {', self.js)

    def test_vignettes_de_texture_demandent_une_taille_reduite(self) -> None:
        """Une texture de 1024 px ne part pas entière pour une tuile de 160 px."""
        self.assertIn("&max=", self.js)
        self.assertIn("textureThumb(t, 192)", self.js)

    def test_visible_thumbnails_are_queued_and_not_prewarmed_at_boot(self) -> None:
        self.assertIn('id="thumb-toggle"', self.html)
        self.assertIn('/static/thumbnails.js', self.html)
        self.assertIn('data-thumb=', self.js)
        self.assertIn('thumbnailQueue.observe(grid)', self.js)
        self.assertIn('thumbnailQueue.release(grid)', self.js)
        self.assertNotIn('startWarming()', self.js)
        boot = self.js[self.js.index('async function boot()'):]
        self.assertNotIn('prepareThumbnails()', boot)
        self.assertIn('id="warm-start"', self.html)
        self.assertIn('id="warm-stop"', self.html)
        self.assertIn('on("#warm-start", "onclick", prepareThumbnails)', self.js)
        self.assertIn('/api/thumbs/warm', self.js)

    @unittest.skipUnless(shutil.which("node"), "node absent")
    def test_javascript_parses(self) -> None:
        for name in ("app.js", "thumbnails.js"):
            with self.subTest(name=name):
                result = subprocess.run(["node", "--check", str(WEB / name)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "node absent")
    def test_javascript_behaviour(self) -> None:
        for script, message in (("thumbnail_queue_test.js", "ThumbnailQueue: OK"),
                                ("asset_checks_test.js", "Asset validation queue: OK"),
                                ("settings_ui_test.js", "Settings UI: OK"),
                                ("image_revision_ui_test.js", "Image revision UI: OK"),
                                ("interaction_ui_test.js", "Interaction UI: OK"),
                                ("workspaces_ui_test.js", "Workspaces UI: OK")):
            with self.subTest(script=script):
                result = subprocess.run(["node", str(ROOT / "tests" / script)],
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(message, result.stdout)


if __name__ == "__main__":               # pragma: no cover
    unittest.main()
