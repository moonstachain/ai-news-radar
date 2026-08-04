from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def page(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_both_channels_use_the_static_greenbook_shell():
    for name in ("index.html", "business.html"):
        html = page(name)
        assert "greenbook-cockpit.css" in html
        assert "apple-adaptive.css" not in html
        assert 'class="radar-side"' in html
        assert 'class="radar-mobile-switch"' in html
        assert 'href="./index.html"' in html
        assert 'href="./business.html"' in html


def test_theme_switching_does_not_return():
    combined = page("index.html") + page("business.html") + page("assets/radar-shell.js")
    assert "theme-toggle" not in combined
    assert "yuanli_radar_theme_v1" not in combined
    assert "matchMedia(\"(prefers-color-scheme" not in combined


def test_quick_look_keeps_modal_and_focus_contract():
    shell = page("assets/radar-shell.js")
    for name in ("index.html", "business.html"):
        html = page(name)
        assert 'role="dialog"' in html
        assert 'aria-modal="true"' in html
        assert 'aria-labelledby="radarQuickLookTitle"' in html
        assert " inert>" in html
    assert "drawerFocusable" in shell
    assert 'event.key === "Escape"' in shell
    assert "drawerTrigger?.focus()" in shell


def test_greenbook_palette_uses_semantic_tokens():
    css = page("assets/greenbook-cockpit.css")
    for token in (
        "--radar-bg: #081510",
        "--radar-leather: #0d2018",
        "--radar-text: #f0e6d2",
        "--radar-gold: #c9a961",
        "--radar-jade: #6f9f7b",
        "--radar-seal: #a64a3c",
    ):
        assert token in css
    assert "prefers-reduced-motion: reduce" in css
