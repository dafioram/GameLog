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
}

# Kinds folded under their original in the (unfiltered) games list: they're
# their own games for backlog/completions, but would swamp the list.
FOLDED_KINDS = ("rom_hack", "mod", "fan_game")


def method_label(key):
    return METHODS.get(key or "", None)


def relation_phrase(kind):
    """'ROM hack of', 'Remake of', 'Related to'."""
    if kind in (None, "", "related"):
        return "Related to"
    return f"{RELATION_KINDS.get(kind, 'Related')} of"
