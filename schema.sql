-- Retro Archive schema, version 2 (tracked in PRAGMA user_version; see db.py).
-- This file always describes the LATEST schema and is only run for brand-new
-- databases; existing ones are upgraded step by step by db.MIGRATIONS.
--
-- Hierarchy: game -> release -> copy.
--   game    = the thing you play and track (backlog, completions, tags, cover)
--   release = a regional/platform edition of that game
--   copy    = one concrete way you can play a release (cart, ROM, Steam, a
--             source port, a web link, a fan-translated ROM, ...)
--
-- Text that users type freely and then filter on (platform, tag, format,
-- location) is COLLATE NOCASE so "SNES"/"Snes" or "NAS"/"nas" never split
-- into two entries.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS platforms (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE COLLATE NOCASE
);

-- A remake/rework is a *different* game row, cross-linked via
-- related_game_id (shown in both directions on the detail page).
CREATE TABLE IF NOT EXISTS games (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    title             TEXT NOT NULL,
    edition_note      TEXT,               -- "1996 Original", "2026 Remake", ...
    related_game_id   INTEGER,
    length_hours      REAL,               -- typical/estimated length (HLTB-style)
    cover_image_path  TEXT,               -- hash-named file in data/assets/covers
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (related_game_id) REFERENCES games(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS releases (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id            INTEGER NOT NULL,
    platform_id        INTEGER NOT NULL,
    region             TEXT,
    release_year       INTEGER,
    publisher          TEXT,
    developer          TEXT,
    serial             TEXT,
    notes              TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
    FOREIGN KEY (platform_id) REFERENCES platforms(id)
);

-- is_owned: 1 = something you own (cart, ROM backup, Steam/GOG license).
--           0 = access only (web link, subscription like Switch Online).
-- Any copy makes a game "playable"; an is_owned copy makes it "owned".
CREATE TABLE IF NOT EXISTS copies (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    release_id         INTEGER NOT NULL,
    format             TEXT COLLATE NOCASE,  -- "Physical", "Steam", "Source port", "Web", ...
    via_platform_id    INTEGER,              -- played through a different platform
    location           TEXT COLLATE NOCASE,  -- "NAS", "Closet box 3", ...
    path               TEXT,                 -- only when the app can read the file
    url                TEXT,                 -- web-playable link, store page, project page
    notes              TEXT,
    is_owned           INTEGER NOT NULL DEFAULT 1 CHECK (is_owned IN (0, 1)),
    sha256             TEXT,
    crc32              TEXT,
    size               INTEGER,
    last_verified_at   TEXT,
    verify_status      TEXT,                 -- 'verified' | 'modified' | 'missing' | NULL
    added_at           TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (release_id) REFERENCES releases(id) ON DELETE CASCADE,
    FOREIGN KEY (via_platform_id) REFERENCES platforms(id)
);

CREATE TABLE IF NOT EXISTS tags (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE COLLATE NOCASE
);

-- Tags describe the game (genre, "Rom hack", "Co-op"), not a release.
CREATE TABLE IF NOT EXISTS game_tags (
    game_id  INTEGER NOT NULL,
    tag_id   INTEGER NOT NULL,
    PRIMARY KEY (game_id, tag_id),
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

-- Backlog membership is a soft-deleted log, so removal history
-- (completed vs. manually dropped) is preserved.
CREATE TABLE IF NOT EXISTS backlog (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id          INTEGER NOT NULL,
    sort_order       REAL NOT NULL,
    added_at         TEXT NOT NULL DEFAULT (datetime('now')),
    removed_at       TEXT,
    removed_reason   TEXT,               -- 'completed' | 'manual'
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
);

-- copy_id records which copy the run was actually played on ("via").
-- Optional; cleared (not cascaded) if that copy record is later removed.
CREATE TABLE IF NOT EXISTS completions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id          INTEGER NOT NULL,
    copy_id          INTEGER,
    completed_at     TEXT NOT NULL,       -- 'YYYY-MM-DD'
    hours_played     REAL,
    rating           INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 10),
    notes            TEXT,
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
    FOREIGN KEY (copy_id) REFERENCES copies(id) ON DELETE SET NULL
);

-- Save vault: snapshots of save files you want to keep ("100% file",
-- "before the final boss"). A save belongs to the copy it was made with
-- (save formats differ between, say, an N64 cart and a PC source port) and
-- can optionally be tied to the completion it came from. The file itself is
-- stored content-addressed in data/assets/saves/<stored_name>.
CREATE TABLE IF NOT EXISTS saves (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    copy_id         INTEGER NOT NULL,
    completion_id   INTEGER,
    label           TEXT NOT NULL,
    notes           TEXT,
    filename        TEXT NOT NULL,       -- original upload name, used on download
    stored_name     TEXT NOT NULL,       -- hash-named file in data/assets/saves
    sha256          TEXT NOT NULL,
    size            INTEGER NOT NULL,
    uploaded_at     TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (copy_id) REFERENCES copies(id) ON DELETE CASCADE,
    FOREIGN KEY (completion_id) REFERENCES completions(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_releases_game        ON releases(game_id);
CREATE INDEX IF NOT EXISTS idx_releases_platform    ON releases(platform_id);
CREATE INDEX IF NOT EXISTS idx_copies_release       ON copies(release_id);
CREATE INDEX IF NOT EXISTS idx_copies_location      ON copies(location);
CREATE INDEX IF NOT EXISTS idx_copies_format        ON copies(format);
CREATE INDEX IF NOT EXISTS idx_copies_via_platform  ON copies(via_platform_id);
CREATE INDEX IF NOT EXISTS idx_game_tags_tag        ON game_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_backlog_game         ON backlog(game_id);
CREATE INDEX IF NOT EXISTS idx_completions_game     ON completions(game_id);
CREATE INDEX IF NOT EXISTS idx_completions_copy     ON completions(copy_id);
CREATE INDEX IF NOT EXISTS idx_saves_copy           ON saves(copy_id);
CREATE INDEX IF NOT EXISTS idx_saves_completion     ON saves(completion_id);
CREATE INDEX IF NOT EXISTS idx_saves_stored_name    ON saves(stored_name);

-- At most one *active* backlog row per game, enforced by the database.
CREATE UNIQUE INDEX IF NOT EXISTS idx_backlog_one_active
    ON backlog(game_id) WHERE removed_at IS NULL;
