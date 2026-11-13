"""Protect the public README's navigational contract without network access."""
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
README=ROOT/"README.md"

def test_readme_local_links_resolve():
    text=README.read_text(encoding="utf-8")
    links=re.findall(r"\]\(([^)]+)\)",text)
    missing=[]
    for href in links:
        if href.startswith(("https://","http://","mailto:","#")):
            continue
        path=href.split("#",1)[0].split("?",1)[0]
        if path and not (ROOT / path).is_file():
            missing.append(path)
    assert not missing, f"README local files missing: {missing}"

def test_hero_and_public_demo_are_local_and_credential_free():
    hero=(ROOT/"docs/assets/hero-belief-lifecycle.svg").read_text(encoding="utf-8")
    demo=(ROOT/"docs/demo/index.html").read_text(encoding="utf-8")
    assert "<svg" in hero and "Memory should know how." in hero
    assert "<script" in demo and "data-step" in demo
    assert "MEMORY_API_KEY" not in demo
    assert "YOUR_LOCAL_API_KEY" in README.read_text(encoding="utf-8")

def test_architecture_diagrams_are_valid_svg_and_keep_truth_boundaries_visible():
    import xml.etree.ElementTree as ET
    for path in ("docs/assets/architecture.svg", "docs/assets/memory-lifecycle.svg"):
        root = ET.parse(ROOT / path).getroot()
        assert root.tag.endswith("svg")
        assert root.attrib.get("viewBox")
    architecture=(ROOT / "docs/assets/architecture.svg").read_text(encoding="utf-8")
    assert "ONE POSTGRESQL SERVER / DATABASE" in architecture
    assert "CANONICAL RECORD" in architecture
    assert "DERIVED QUERY VIEWS" in architecture
    assert "Not a general authorization" in architecture
    lifecycle=(ROOT / "docs/assets/memory-lifecycle.svg").read_text(encoding="utf-8")
    assert "CANONICAL" in lifecycle.upper()
    assert "Canonical recheck" in lifecycle

def test_readme_mentions_both_diagrams_and_limitations():
    readme=README.read_text(encoding="utf-8")
    assert "docs/assets/architecture.svg" in readme
    assert "docs/assets/memory-lifecycle.svg" in readme
    assert "not yet guarantee" in readme
    assert "docs/ARCHITECTURE-REVIEW.md" in readme
