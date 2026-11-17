"""Page theming: an accent colour, a light/dark mode and how strongly the page background is tinted.

A theme is {"a": "#rrggbb" or None (the original teal), "m": "auto"|"light"|"dark", "t": 0|1|2}.
It is saved per user (users.theme) and mirrored in the browser. The page is rendered with the
theme already applied, so there is no flash of the wrong colours.

Contrast: any accent a user picks is adjusted so it stays readable. In light mode it is darkened
until it has 3:1 contrast against white; in dark mode it is lightened until it has 4.5:1 against
the dark surface. Text on top of the accent is white or near-black, whichever reads better, and the
accent is nudged further if neither reaches 4.5:1.
static/js/theme.js implements the same maths (a test keeps the two identical).
"""
import json
import math
import re

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MODES = ("auto", "light", "dark")
TINTS = (0, 8, 16)                 # % of the accent mixed into the page background: Plain / Tinted / Vivid
TINT_NAMES = ("Plain", "Tinted", "Vivid")
DEFAULT = {"a": None, "m": "auto", "t": 0}

# (name, accent). None = the original teal the app shipped with (its hand-tuned colours).
PRESETS = [
    {"name": "Original", "accent": None, "swatch": "#2d6a6a"},
    {"name": "Poké Ball", "accent": "#d6342c", "swatch": "#d6342c"},
    {"name": "Charmander", "accent": "#e0662a", "swatch": "#e0662a"},
    {"name": "Pikachu", "accent": "#f2b705", "swatch": "#f2b705"},
    {"name": "Bulbasaur", "accent": "#3f8f5a", "swatch": "#3f8f5a"},
    {"name": "Squirtle", "accent": "#2f6fb5", "swatch": "#2f6fb5"},
    {"name": "Gengar", "accent": "#6b4fb3", "swatch": "#6b4fb3"},
    {"name": "Jigglypuff", "accent": "#d9558f", "swatch": "#d9558f"},
]

WHITE, BLACK = (255, 255, 255), (0, 0, 0)
INK_DARK = (0x11, 0x14, 0x18)      # text on a light accent
DARK_SURFACE = (0x1a, 0x22, 0x2b)  # the dark theme's card/panel colour


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _channel(c: int) -> float:
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb) -> float:
    return 0.2126 * _channel(rgb[0]) + 0.7152 * _channel(rgb[1]) + 0.0722 * _channel(rgb[2])


def contrast(l1: float, l2: float) -> float:
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _mix(rgb, target, t: float):
    # floor(x + .5) rather than round(): Python rounds halves to even, JavaScript's Math.round doesn't
    return tuple(math.floor(c + (tc - c) * t + 0.5) for c, tc in zip(rgb, target))


def ink_for(rgb) -> tuple[int, int, int]:
    """White or near-black text, whichever has more contrast on `rgb`."""
    lum = luminance(rgb)
    return WHITE if contrast(1.0, lum) >= contrast(lum, luminance(INK_DARK)) else INK_DARK


def ink_contrast(rgb) -> float:
    """Contrast of the best text colour on `rgb`. Mid-tone colours are the worst case."""
    lum = luminance(rgb)
    return max(contrast(1.0, lum), contrast(lum, luminance(INK_DARK)))


def light_variant(rgb):
    """Darken until the accent has 3:1 contrast on white and 4.5:1 for its button text
    (or 60% black has been mixed in)."""
    cand = rgb
    for i in range(13):
        cand = _mix(rgb, BLACK, i * 0.05)
        if contrast(luminance(cand), 1.0) >= 3.0 and ink_contrast(cand) >= 4.5:
            break
    return cand


def dark_variant(rgb):
    """Lighten until the accent has 4.5:1 contrast on the dark surface and for its button text
    (or 80% white has been mixed in)."""
    target, cand = luminance(DARK_SURFACE), rgb
    for i in range(17):
        cand = _mix(rgb, WHITE, i * 0.05)
        if contrast(luminance(cand), target) >= 4.5 and ink_contrast(cand) >= 4.5:
            break
    return cand


def derive(accent: str) -> dict[str, str]:
    """The four colours the stylesheet needs for one accent."""
    base = _rgb(accent)
    light, dark = light_variant(base), dark_variant(base)
    return {"light": _hex(light), "light_ink": _hex(ink_for(light)),
            "dark": _hex(dark), "dark_ink": _hex(ink_for(dark))}


def normalize(raw, strict: bool = False) -> dict:
    """A clean theme dict from stored JSON, a dict, or None.

    Lenient by default (bad parts fall back to the default), strict for user input (ValueError).
    """
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except ValueError:
            if strict:
                raise ValueError("theme isn't valid JSON") from None
            raw = None
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("theme must be an object")
        raw = {}
    out = dict(DEFAULT)

    accent = raw.get("a")
    if accent in (None, ""):
        out["a"] = None
    elif isinstance(accent, str) and HEX_RE.match(accent):
        out["a"] = accent.lower()
    elif strict:
        raise ValueError("accent must be a #rrggbb colour")

    mode = raw.get("m", "auto")
    if mode in MODES:
        out["m"] = mode
    elif strict:
        raise ValueError("unknown mode")

    tint = raw.get("t", 0)
    if isinstance(tint, int) and not isinstance(tint, bool) and 0 <= tint < len(TINTS):
        out["t"] = tint
    elif strict:
        raise ValueError("unknown tint")
    return out


def dumps(theme: dict) -> str:
    return json.dumps(theme, separators=(",", ":"))


def style_for(theme: dict) -> str:
    """Inline CSS custom properties for <html>. Empty for the original look."""
    parts = []
    if theme["a"]:
        c = derive(theme["a"])
        parts.append(f"--accent-l:{c['light']};--accent-ink-l:{c['light_ink']};"
                     f"--accent-d:{c['dark']};--accent-ink-d:{c['dark_ink']};")
    if TINTS[theme["t"]]:
        parts.append(f"--tint:{TINTS[theme['t']]}%;")
    return "".join(parts)


def context(raw) -> dict:
    """Everything the templates need to render the theme."""
    theme = normalize(raw)
    return {"state": theme, "mode": theme["m"], "style": style_for(theme),
            "presets": PRESETS, "modes": MODES, "tint_names": TINT_NAMES}
