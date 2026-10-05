"""How a platform is shown as a compact badge: a short name ("PS4", "SNES"),
a family used for the badge's tint, and a device kind for its small icon.

Badges are an original design (text + tint + a generic device glyph), not
manufacturer logos. Unknown platforms still get a neutral badge.
"""

SHORT_NAMES = {
    "nes": "NES", "famicom": "FC", "snes": "SNES", "super nintendo": "SNES",
    "super famicom": "SFC", "nintendo 64": "N64", "gamecube": "GCN", "wii": "Wii",
    "wii u": "Wii U", "switch": "Switch", "switch 2": "Switch 2", "game boy": "GB",
    "game boy color": "GBC", "game boy advance": "GBA", "nintendo ds": "DS",
    "nintendo 3ds": "3DS", "virtual boy": "VB",
    "sega master system": "SMS", "sega genesis": "Genesis", "genesis": "Genesis",
    "mega drive": "MD", "sega mega drive": "MD", "sega cd": "Sega CD", "sega 32x": "32X",
    "sega saturn": "Saturn", "sega dreamcast": "DC", "dreamcast": "DC", "sega game gear": "GG",
    "playstation": "PS1", "playstation 2": "PS2", "playstation 3": "PS3",
    "playstation 4": "PS4", "playstation 5": "PS5", "playstation portable": "PSP",
    "playstation vita": "Vita", "ps1": "PS1", "ps2": "PS2", "ps3": "PS3", "ps4": "PS4", "ps5": "PS5",
    "xbox": "Xbox", "xbox 360": "X360", "xbox one": "XB1", "xbox series x": "XSX",
    "xbox series x|s": "XS", "xbox series s": "XSS",
    "atari 2600": "2600", "atari 7800": "7800", "atari lynx": "Lynx", "atari jaguar": "Jaguar",
    "pc": "PC", "steam deck": "Deck", "windows": "Win", "mac": "Mac", "macos": "Mac",
    "linux": "Linux", "dos": "DOS", "ms-dos": "DOS", "mister": "MiSTer",
    "neo geo": "NeoGeo", "neo geo pocket color": "NGPC", "turbografx-16": "TG-16",
    "pc engine": "PCE", "3do": "3DO",
}

# Matched as whole words/phrases ("nes" must not match "genesis").
_FAMILY_KEYWORDS = [
    ("nintendo", ("nintendo", "nes", "snes", "famicom", "game boy", "gamecube", "wii", "switch",
                  "virtual boy", "ds", "3ds")),
    ("sony", ("playstation", "ps1", "ps2", "ps3", "ps4", "ps5", "psp", "vita")),
    ("sega", ("sega", "genesis", "mega drive", "saturn", "dreamcast", "game gear", "master system", "32x")),
    ("microsoft", ("xbox",)),
    ("atari", ("atari", "lynx", "jaguar")),
    ("pc", ("pc", "steam deck", "windows", "mac", "macos", "linux", "dos", "ms-dos")),
]
_FAMILY_EXCEPTIONS = {"pc engine": "other", "turbografx-16": "other"}

_HANDHELD = ("game boy", "nintendo ds", "3ds", "psp", "vita", "game gear", "steam deck",
             "lynx", "neo geo pocket", "virtual boy")
_COMPUTER = ("pc", "windows", "mac", "macos", "linux", "dos", "ms-dos")


def platform_info(name):
    """{'name', 'short', 'family', 'device'} for a platform name."""
    name = (name or "").strip()
    key = name.lower()
    short = SHORT_NAMES.get(key) or (name if len(name) <= 10 else name[:9] + "…")
    padded = f" {key.replace('|', ' ').replace('-', ' ')} "
    family = _FAMILY_EXCEPTIONS.get(key, "other")
    if key not in _FAMILY_EXCEPTIONS:
        for fam, words in _FAMILY_KEYWORDS:
            if any(f" {w.replace('-', ' ')} " in padded for w in words):
                family = fam
                break
    if key in _COMPUTER:
        device = "computer"
    elif any(h in key for h in _HANDHELD):
        device = "handheld"
    else:
        device = "console"
    return {"name": name, "short": short, "family": family, "device": device}
