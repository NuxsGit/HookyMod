"""Keep the README navigation and local documentation links usable."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent


def readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


def test_readme_local_links_exist():
    text = readme()
    # Ignore fenced code: examples are not Markdown links.
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    markdown_links = re.findall(r"\]\(([^)]+)\)", text)
    html_links = re.findall(r'(?:href|src)="([^"]+)"', text)
    for link in markdown_links + html_links:
        if link.startswith(("https://", "http://", "mailto:", "#")):
            continue
        path = unquote(link.split("#", 1)[0])
        assert (ROOT / path).is_file(), link


def test_navigation_targets_existing_headings():
    text = readme()
    headings = re.findall(r"^#{1,6}\s+(.+?)\s*$", text, flags=re.MULTILINE)
    slugs = {
        re.sub(r"\s+", "-", re.sub(r"[^\w\s-]", "", heading.lower())).strip("-")
        for heading in headings
    }
    anchors = re.findall(r'href="#([^"]+)"', text)
    assert anchors
    assert set(anchors) <= slugs


def test_readme_distinguishes_longshot_and_documents_license():
    text = readme()
    assert 'src="assets/HookyMod-logo.png"' in text
    assert "https://git.magicbean.dev/MagicBeanDev/Longshot" in text
    assert "separate fork of Hookshot" in text
    assert "have **not** been verified" in text
    assert "HookyMod Noncommercial License" in text
    assert "including for modified versions" in text
