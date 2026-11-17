"""Page theming: validation, contrast guarantees, saving, rendering, and parity with theme.js."""
import json
import random
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from app import theme

ROOT = Path(__file__).resolve().parent.parent
BASE = "/c/ash-s-cards"


def test_normalize_is_lenient_for_stored_values():
    assert theme.normalize(None) == theme.DEFAULT
    assert theme.normalize("not json") == theme.DEFAULT
    assert theme.normalize({"a": "#ABCDEF", "m": "dark", "t": 2}) == {"a": "#abcdef", "m": "dark", "t": 2}
    assert theme.normalize({"a": "red", "m": "sepia", "t": 9}) == theme.DEFAULT       # bad parts fall back
    assert theme.normalize(json.dumps({"a": "#112233", "m": "light"})) == {"a": "#112233", "m": "light", "t": 0}
    assert theme.normalize({"a": "", "t": True})["a"] is None and theme.normalize({"t": True})["t"] == 0


@pytest.mark.parametrize("bad", [None, "x", [], {"a": "red"}, {"a": "#12345"}, {"a": 5}, {"m": "sepia"},
                                 {"t": 3}, {"t": -1}, {"t": 1.5}, {"t": True}, {"a": "#12345g"}])
def test_normalize_is_strict_for_user_input(bad):
    with pytest.raises(ValueError):
        theme.normalize(bad, strict=True)


def test_strict_accepts_good_input():
    assert theme.normalize({"a": "#2F6FB5", "m": "auto", "t": 1}, strict=True) == {"a": "#2f6fb5", "m": "auto", "t": 1}
    assert theme.normalize({"a": None}, strict=True)["a"] is None


def test_default_theme_adds_no_inline_style():
    assert theme.style_for(theme.DEFAULT) == ""
    assert theme.style_for({"a": None, "m": "dark", "t": 1}) == "--tint:8%;"
    assert theme.style_for({"a": "#2f6fb5", "m": "auto", "t": 0}).startswith("--accent-l:#2f6fb5;--accent-ink-l:#ffffff;")


def _ratio(hex_a, hex_b):
    return theme.contrast(theme.luminance(theme._rgb(hex_a)), theme.luminance(theme._rgb(hex_b)))


def test_every_preset_and_random_colour_stays_readable():
    colours = [p["accent"] for p in theme.PRESETS if p["accent"]] + ["#ffffff", "#000000", "#ffff00", "#0000ff"]
    rng = random.Random(7)
    colours += ["#%06x" % rng.randrange(0x1000000) for _ in range(300)]
    for c in colours:
        d = theme.derive(c)
        assert _ratio(d["light"], "#ffffff") >= 3.0 or d["light"] == theme._hex(theme._mix(theme._rgb(c), theme.BLACK, 0.6)), c
        assert _ratio(d["dark"], "#1a222b") >= 4.5 or d["dark"] == theme._hex(theme._mix(theme._rgb(c), theme.WHITE, 0.8)), c
        assert _ratio(d["light_ink"], d["light"]) >= 4.5 and _ratio(d["dark_ink"], d["dark"]) >= 4.5, c


def test_colours_that_already_read_well_are_left_alone():
    d = theme.derive("#2d6a6a")
    assert d["light"] == "#2d6a6a" and d["light_ink"] == "#ffffff"


@pytest.mark.skipif(shutil.which("node") is None, reason="node isn't installed")
def test_javascript_and_python_compute_identical_colours():
    rng = random.Random(11)
    colours = [p["accent"] for p in theme.PRESETS if p["accent"]] + ["#000000", "#ffffff", "#808080", "#7f7f7f"]
    colours += ["#%06x" % rng.randrange(0x1000000) for _ in range(500)]
    script = ("const t = require(process.argv[1]); const out = {};"
              "for (const c of JSON.parse(process.argv[2])) out[c] = t.derive(c);"
              "console.log(JSON.stringify(out));")
    run = subprocess.run(["node", "-e", script, str(ROOT / "app/static/js/theme.js"), json.dumps(colours)],
                         capture_output=True, text=True, check=True)
    from_js = json.loads(run.stdout)
    for c in colours:
        assert from_js[c] == theme.derive(c), c


@pytest.mark.skipif(shutil.which("node") is None, reason="node isn't installed")
def test_javascript_normalize_matches_python():
    cases = [None, {}, {"a": "#ABCDEF", "m": "dark", "t": 2}, {"a": "red", "m": "x", "t": 9}, {"a": "#12345"}, {"t": 1.5}]
    script = ("const t = require(process.argv[1]);"
              "console.log(JSON.stringify(JSON.parse(process.argv[2]).map(c => t.normalize(c))));")
    run = subprocess.run(["node", "-e", script, str(ROOT / "app/static/js/theme.js"), json.dumps(cases)],
                         capture_output=True, text=True, check=True)
    assert json.loads(run.stdout) == [theme.normalize(c) for c in cases]


# ---- saving and rendering ------------------------------------------------------------------

def test_saving_requires_login_and_valid_input(client, login):
    assert client.post("/theme", json={"a": None, "m": "dark", "t": 0}).status_code == 302   # to the login page
    login()
    with patch("app.user_repository.set_theme") as save:
        assert client.post("/theme", json={"a": "javascript:alert(1)"}).status_code == 400
        assert client.post("/theme", data="junk", content_type="text/plain").status_code == 400
        assert client.post("/theme", json={"m": "sepia"}).status_code == 400
        assert not save.called
        ok = client.post("/theme", json={"a": "#2F6FB5", "m": "dark", "t": 2, "extra": "ignored"})
    assert ok.status_code == 204
    user_id, stored = save.call_args.args
    assert user_id == 1 and json.loads(stored) == {"a": "#2f6fb5", "m": "dark", "t": 2}


def test_saved_theme_is_rendered_into_the_page(client, login, stub):
    login()
    stub["user"]["theme"] = json.dumps({"a": "#2f6fb5", "m": "dark", "t": 1})
    html = client.get(BASE + "/").data.decode()
    root = html.split(">", 2)[1] if html.startswith("<!doctype") else html
    tag = html[html.index("<html"): html.index(">", html.index("<html")) + 1]
    assert 'data-mode="dark"' in tag and "data-themed" in tag
    assert "--accent-l:#2f6fb5;" in tag and "--tint:8%;" in tag
    assert 'data-save-url="/theme"' in html and 'name="csrf-token"' in html
    assert "&#34;a&#34;" in html or '"a"' in html          # state handed to the menu


def test_default_theme_renders_without_inline_colours(client, login):
    login()
    html = client.get(BASE + "/").data.decode()
    tag = html[html.index("<html"): html.index(">", html.index("<html")) + 1]
    assert 'data-mode="auto"' in tag and "style=" not in tag


def test_hostile_stored_theme_cannot_break_out_of_the_style_attribute(client, login, stub):
    login()
    stub["user"]["theme"] = json.dumps({"a": '#123456;}</style><script>alert(1)</script>', "m": '"><x>', "t": 0})
    html = client.get(BASE + "/").data.decode()
    assert "<script>alert(1)" not in html and '"><x>' not in html
    assert 'data-mode="auto"' in html


def test_signed_out_pages_have_the_menu_but_no_account_theme(client):
    html = client.get("/login").data.decode()
    tag = html[html.index("<html"): html.index(">", html.index("<html")) + 1]
    assert "data-themed" not in tag                         # theme.js applies this browser's saved copy
    assert "data-theme-menu" in html and "data-save-url" not in html
    assert 'src="/static/js/theme.js"' in html


def test_menu_offers_every_preset_a_custom_colour_mode_and_tint(client):
    html = client.get("/login").data.decode()
    for p in theme.PRESETS:
        assert f'aria-label="{p["name"]}"' in html
    assert "data-custom-accent" in html and html.count("data-mode=") >= 3 + 1 and "data-tint=" in html
    assert "data-theme-reset" in html
