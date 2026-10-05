"""Fixed vocabularies stored in the database by key and shown by label.

Keys are what's saved (stable, used for stats and filters); labels are what
you see and can be reworded freely without touching stored data.
"""

# How a playthrough was actually played (completions.method, saves.method).
# A fixed list, not free text, so the Stats page can group by it. Details
# such as the emulator or port name go in the completion's notes.
METHODS = {
    "original_hardware": "Original hardware",
    "flashcart": "Flashcart / ODE",
    "fpga": "FPGA",
    "emulator": "Emulator",
    "official_rerelease": "Official re-release",
    "source_port": "Source port",
    "other": "Other",
}

METHOD_HINTS = {
    "original_hardware": "real console or cartridge; a PC game on a PC",
    "flashcart": "Everdrive, optical drive emulators",
    "fpga": "MiSTer, Analogue",
    "emulator": "RetroArch, standalone emulators",
    "official_rerelease": "Switch Online, compilations, official ports",
    "source_port": "decomps and fan-made native ports",
    "other": "anything else",
}

# What a game's link to another game means (games.relation_kind).
# "This game is a <kind> of <other game>".
RELATION_KINDS = {
    "related": "Related to",
    "rom_hack": "ROM hack",
    "mod": "Mod",
    "fan_game": "Fan game",
    "remake": "Remake",
    "remaster": "Remaster",
    "dlc": "DLC / expansion",
}

# How a link reads in a sentence: "<phrase> <other game>".
RELATION_PHRASES = {
    "related": "Related to",
    "rom_hack": "ROM hack of",
    "mod": "Mod of",
    "fan_game": "Fan game of",
    "remake": "Remake of",
    "remaster": "Remaster of",
    "dlc": "DLC for",
}

# Singular / plural nouns for counting folded games ("+2 ROM hacks").
KIND_NOUNS = {
    "rom_hack": ("ROM hack", "ROM hacks"),
    "mod": ("mod", "mods"),
    "fan_game": ("fan game", "fan games"),
    "dlc": ("DLC", "DLC"),
}

# Kinds folded under their original in the (unfiltered) games list: they're
# their own games for backlog/completions, but would swamp the list.
FOLDED_KINDS = ("rom_hack", "mod", "fan_game", "dlc")


def method_label(key):
    return METHODS.get(key or "", None)


def relation_phrase(kind):
    """'ROM hack of', 'DLC for', 'Related to'."""
    return RELATION_PHRASES.get(kind or "related", "Related to")


def count_phrase(kind, n):
    one, many = KIND_NOUNS.get(kind, ("linked game", "linked games"))
    return f"{n} {one if n == 1 else many}"


def variants_summary(kinds):
    """'+2 ROM hacks', or for a mix '+3 · 2 ROM hacks, 1 DLC'."""
    counts = {}
    for k in kinds:
        counts[k] = counts.get(k, 0) + 1
    total = len(kinds)
    if len(counts) == 1:
        return "+" + count_phrase(next(iter(counts)), total)
    order = [k for k in FOLDED_KINDS if k in counts]
    return f"+{total} · " + ", ".join(count_phrase(k, counts[k]) for k in order)
