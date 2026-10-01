"""All SQL lives here, one function per operation.

Write functions do NOT commit. Routes wrap their writes in
`with db.transaction():` so a multi-step change (create a game AND its
first release; log a completion AND drop it from the backlog) lands
atomically or not at all.
"""

from db import get_db

# Reusable "does this game have ..." predicates (expect the game aliased g).
OWNED_SQL = """EXISTS (SELECT 1 FROM copies c JOIN releases r ON r.id = c.release_id
                       WHERE r.game_id = g.id AND c.is_owned = 1)"""
PLAYABLE_SQL = """EXISTS (SELECT 1 FROM copies c JOIN releases r ON r.id = c.release_id
                          WHERE r.game_id = g.id)"""
IN_BACKLOG_SQL = "EXISTS (SELECT 1 FROM backlog b WHERE b.game_id = g.id AND b.removed_at IS NULL)"
COMPLETED_SQL = "EXISTS (SELECT 1 FROM completions comp WHERE comp.game_id = g.id)"

# A game is "on" a platform if it has a release for it OR a copy played
# through it (e.g. a SNES game via Switch Online counts for Switch).
ON_PLATFORM_SQL = """(
    EXISTS (SELECT 1 FROM releases r WHERE r.game_id = g.id AND r.platform_id = ?)
    OR EXISTS (SELECT 1 FROM copies c JOIN releases r ON r.id = c.release_id
               WHERE r.game_id = g.id AND c.via_platform_id = ?)
)"""
HAS_LOCATION_SQL = """EXISTS (SELECT 1 FROM copies c JOIN releases r ON r.id = c.release_id
                              WHERE r.game_id = g.id AND c.location = ?)"""
HAS_FORMAT_SQL = """EXISTS (SELECT 1 FROM copies c JOIN releases r ON r.id = c.release_id
                            WHERE r.game_id = g.id AND c.format = ?)"""
HAS_TAG_SQL = "EXISTS (SELECT 1 FROM game_tags gt WHERE gt.game_id = g.id AND gt.tag_id = ?)"


# ---------------------------------------------------------------- platforms

def get_platforms():
    return get_db().execute("SELECT * FROM platforms ORDER BY name").fetchall()


def get_or_create_platform(name):
    """Case-insensitive (platforms.name is COLLATE NOCASE): 'snes' finds 'SNES'."""
    name = (name or "").strip()
    if not name:
        return None
    db = get_db()
    row = db.execute("SELECT id FROM platforms WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    return db.execute("INSERT INTO platforms (name) VALUES (?)", (name,)).lastrowid


# --------------------------------------------------------------------- tags

def get_all_tags():
    return get_db().execute("SELECT * FROM tags ORDER BY name").fetchall()


def get_tags_in_use():
    return get_db().execute(
        """SELECT t.* FROM tags t
           WHERE EXISTS (SELECT 1 FROM game_tags gt WHERE gt.tag_id = t.id)
           ORDER BY t.name"""
    ).fetchall()


def get_or_create_tag(name):
    name = (name or "").strip()
    if not name:
        return None
    db = get_db()
    row = db.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    return db.execute("INSERT INTO tags (name) VALUES (?)", (name,)).lastrowid


def add_tag_to_game(game_id, tag_name):
    tag_id = get_or_create_tag(tag_name)
    if tag_id is not None:
        get_db().execute(
            "INSERT OR IGNORE INTO game_tags (game_id, tag_id) VALUES (?, ?)",
            (game_id, tag_id),
        )


def add_tags_from_text(game_id, text):
    """'JRPG, Co-op' -> two tags. Used by the add-game form."""
    for name in (text or "").split(","):
        if name.strip():
            add_tag_to_game(game_id, name)


def remove_tag_from_game(game_id, tag_id):
    db = get_db()
    db.execute("DELETE FROM game_tags WHERE game_id = ? AND tag_id = ?", (game_id, tag_id))
    # Drop the tag entirely once nothing uses it, so filter lists don't
    # accumulate dead entries.
    db.execute(
        "DELETE FROM tags WHERE id = ? AND NOT EXISTS (SELECT 1 FROM game_tags WHERE tag_id = ?)",
        (tag_id, tag_id),
    )


def get_tags_for_game(game_id):
    return get_db().execute(
        """SELECT t.* FROM tags t JOIN game_tags gt ON gt.tag_id = t.id
           WHERE gt.game_id = ? ORDER BY t.name""",
        (game_id,),
    ).fetchall()


# ---------------------------------------------------------- distinct values

def get_distinct_locations():
    # location is COLLATE NOCASE, so GROUP BY folds "NAS"/"nas" together.
    rows = get_db().execute(
        """SELECT location FROM copies
           WHERE location IS NOT NULL AND TRIM(location) != ''
           GROUP BY location ORDER BY location"""
    ).fetchall()
    return [r["location"] for r in rows]


def get_distinct_formats():
    rows = get_db().execute(
        """SELECT format FROM copies
           WHERE format IS NOT NULL AND TRIM(format) != ''
           GROUP BY format ORDER BY format"""
    ).fetchall()
    return [r["format"] for r in rows]


# -------------------------------------------------------------------- games

def create_game(title, edition_note=None, related_game_id=None, length_hours=None):
    return get_db().execute(
        """INSERT INTO games (title, edition_note, related_game_id, length_hours)
           VALUES (?, ?, ?, ?)""",
        (title, edition_note or None, related_game_id or None, length_hours),
    ).lastrowid


def update_game(game_id, title, edition_note=None, related_game_id=None, length_hours=None):
    get_db().execute(
        """UPDATE games SET title = ?, edition_note = ?, related_game_id = ?, length_hours = ?
           WHERE id = ?""",
        (title, edition_note or None, related_game_id or None, length_hours, game_id),
    )


def set_game_cover(game_id, filename):
    get_db().execute("UPDATE games SET cover_image_path = ? WHERE id = ?", (filename, game_id))


def delete_game(game_id):
    get_db().execute("DELETE FROM games WHERE id = ?", (game_id,))


def get_game(game_id):
    return get_db().execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()


def get_game_status(game_id):
    row = get_db().execute(
        f"SELECT {OWNED_SQL} AS owned, {PLAYABLE_SQL} AS playable FROM games g WHERE g.id = ?",
        (game_id,),
    ).fetchone()
    return {"owned": bool(row["owned"]), "playable": bool(row["playable"])}


def list_all_games(exclude_game_id=None):
    """Lightweight (id, title, edition_note) list for the 'related game' picker."""
    return get_db().execute(
        "SELECT id, title, edition_note FROM games WHERE id != ? ORDER BY title, edition_note",
        (exclude_game_id or 0,),
    ).fetchall()


def get_related_games(game_id):
    """Both directions: the game this one points at (e.g. the original a
    remake was linked to) AND every game that points at this one."""
    return get_db().execute(
        """SELECT g.id, g.title, g.edition_note FROM games g
           WHERE g.id = (SELECT related_game_id FROM games WHERE id = ?)
           UNION
           SELECT g.id, g.title, g.edition_note FROM games g
           WHERE g.related_game_id = ?
           ORDER BY title, edition_note""",
        (game_id, game_id),
    ).fetchall()


# ----------------------------------------------------------------- releases

def create_release(game_id, platform_id, region=None, release_year=None,
                   publisher=None, developer=None, serial=None, notes=None):
    return get_db().execute(
        """INSERT INTO releases
           (game_id, platform_id, region, release_year, publisher, developer, serial, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (game_id, platform_id, region or None, release_year or None,
         publisher or None, developer or None, serial or None, notes or None),
    ).lastrowid


def update_release(release_id, platform_id, region=None, release_year=None,
                   publisher=None, developer=None, serial=None, notes=None):
    get_db().execute(
        """UPDATE releases SET platform_id = ?, region = ?, release_year = ?, publisher = ?,
                  developer = ?, serial = ?, notes = ?
           WHERE id = ?""",
        (platform_id, region or None, release_year or None, publisher or None,
         developer or None, serial or None, notes or None, release_id),
    )


def delete_release(release_id):
    get_db().execute("DELETE FROM releases WHERE id = ?", (release_id,))


def get_release(release_id):
    return get_db().execute(
        """SELECT r.*, p.name AS platform_name
           FROM releases r JOIN platforms p ON p.id = r.platform_id
           WHERE r.id = ?""",
        (release_id,),
    ).fetchone()


def get_releases_for_game(game_id):
    return get_db().execute(
        """SELECT r.*, p.name AS platform_name
           FROM releases r JOIN platforms p ON p.id = r.platform_id
           WHERE r.game_id = ?
           ORDER BY r.release_year IS NULL, r.release_year, p.name, r.id""",
        (game_id,),
    ).fetchall()


# ------------------------------------------------------------------- copies

_COPY_SELECT = """
    SELECT c.*, r.game_id, r.region, rp.name AS platform_name, vp.name AS via_platform_name
    FROM copies c
    JOIN releases r ON r.id = c.release_id
    JOIN platforms rp ON rp.id = r.platform_id
    LEFT JOIN platforms vp ON vp.id = c.via_platform_id
"""


def create_copy(release_id, format=None, via_platform_id=None, location=None, path=None,
                url=None, notes=None, is_owned=True):
    return get_db().execute(
        """INSERT INTO copies (release_id, format, via_platform_id, location, path, url, notes, is_owned)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (release_id, format or None, via_platform_id or None, location or None,
         path or None, url or None, notes or None, 1 if is_owned else 0),
    ).lastrowid


def update_copy(copy_id, format=None, via_platform_id=None, location=None, path=None,
                url=None, notes=None, is_owned=True):
    """Full replace of the user-editable fields. If the file path changed,
    the stored hash/verify state described a *different* file, so it is
    cleared rather than left to produce a false 'modified' on next verify."""
    db = get_db()
    current = db.execute("SELECT path FROM copies WHERE id = ?", (copy_id,)).fetchone()
    path_changed = current is not None and (current["path"] or None) != (path or None)
    db.execute(
        """UPDATE copies SET format = ?, via_platform_id = ?, location = ?, path = ?,
                  url = ?, notes = ?, is_owned = ?
           WHERE id = ?""",
        (format or None, via_platform_id or None, location or None, path or None,
         url or None, notes or None, 1 if is_owned else 0, copy_id),
    )
    if path_changed:
        db.execute(
            """UPDATE copies SET sha256 = NULL, crc32 = NULL, size = NULL,
                      verify_status = NULL, last_verified_at = NULL
               WHERE id = ?""",
            (copy_id,),
        )
    return path_changed


def update_copy_hash(copy_id, sha256, crc32, size):
    get_db().execute(
        "UPDATE copies SET sha256 = ?, crc32 = ?, size = ? WHERE id = ?",
        (sha256, crc32, size, copy_id),
    )


def set_copy_verify_status(copy_id, status):
    get_db().execute(
        "UPDATE copies SET verify_status = ?, last_verified_at = datetime('now') WHERE id = ?",
        (status, copy_id),
    )


def delete_copy(copy_id):
    get_db().execute("DELETE FROM copies WHERE id = ?", (copy_id,))


def get_copy(copy_id):
    return get_db().execute(_COPY_SELECT + " WHERE c.id = ?", (copy_id,)).fetchone()


def get_copies_for_release(release_id):
    return get_db().execute(
        _COPY_SELECT + " WHERE c.release_id = ? ORDER BY c.is_owned DESC, c.added_at, c.id",
        (release_id,),
    ).fetchall()


def get_copies_for_game(game_id):
    """Every copy across all of a game's releases — the 'played via' picker."""
    return get_db().execute(
        _COPY_SELECT + """ WHERE r.game_id = ?
           ORDER BY r.release_year IS NULL, r.release_year, rp.name, c.added_at, c.id""",
        (game_id,),
    ).fetchall()


def archive_health_summary():
    rows = get_db().execute(
        """SELECT COALESCE(verify_status, 'unchecked') AS status, COUNT(*) AS n
           FROM copies WHERE path IS NOT NULL AND TRIM(path) != ''
           GROUP BY status"""
    ).fetchall()
    summary = {"verified": 0, "modified": 0, "missing": 0, "unchecked": 0}
    for r in rows:
        summary[r["status"]] = r["n"]
    summary["total_with_path"] = sum(summary.values())
    return summary


# -------------------------------------------------------------------- saves

_SAVE_SELECT = """
    SELECT s.*, c.release_id, c.format AS copy_format, r.game_id,
           comp.completed_at AS completion_date
    FROM saves s
    JOIN copies c ON c.id = s.copy_id
    JOIN releases r ON r.id = c.release_id
    LEFT JOIN completions comp ON comp.id = s.completion_id
"""


def create_save(copy_id, label, filename, stored_name, sha256, size,
                notes=None, completion_id=None):
    return get_db().execute(
        """INSERT INTO saves (copy_id, completion_id, label, notes, filename, stored_name, sha256, size)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (copy_id, completion_id or None, label, notes or None, filename, stored_name, sha256, size),
    ).lastrowid


def update_save(save_id, copy_id, label, notes=None, completion_id=None):
    get_db().execute(
        "UPDATE saves SET copy_id = ?, label = ?, notes = ?, completion_id = ? WHERE id = ?",
        (copy_id, label, notes or None, completion_id or None, save_id),
    )


def delete_save(save_id):
    get_db().execute("DELETE FROM saves WHERE id = ?", (save_id,))


def get_save(save_id):
    return get_db().execute(_SAVE_SELECT + " WHERE s.id = ?", (save_id,)).fetchone()


def get_saves_for_game(game_id):
    """Newest first. Grouped by copy (and by completion) in the route."""
    return get_db().execute(
        _SAVE_SELECT + " WHERE r.game_id = ? ORDER BY s.uploaded_at DESC, s.id DESC",
        (game_id,),
    ).fetchall()


def stored_save_names_for_game(game_id):
    rows = get_db().execute(
        """SELECT DISTINCT s.stored_name FROM saves s
           JOIN copies c ON c.id = s.copy_id JOIN releases r ON r.id = c.release_id
           WHERE r.game_id = ?""",
        (game_id,),
    ).fetchall()
    return [r["stored_name"] for r in rows]


def save_file_in_use(stored_name):
    return get_db().execute(
        "SELECT 1 FROM saves WHERE stored_name = ? LIMIT 1", (stored_name,)
    ).fetchone() is not None


def count_saves_for_copy(copy_id):
    return get_db().execute("SELECT COUNT(*) FROM saves WHERE copy_id = ?", (copy_id,)).fetchone()[0]


def count_saves_for_release(release_id):
    return get_db().execute(
        "SELECT COUNT(*) FROM saves s JOIN copies c ON c.id = s.copy_id WHERE c.release_id = ?",
        (release_id,),
    ).fetchone()[0]


def count_saves_for_game(game_id):
    return get_db().execute(
        """SELECT COUNT(*) FROM saves s JOIN copies c ON c.id = s.copy_id
           JOIN releases r ON r.id = c.release_id WHERE r.game_id = ?""",
        (game_id,),
    ).fetchone()[0]


def saves_summary():
    row = get_db().execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(size), 0) AS bytes, COUNT(DISTINCT stored_name) AS files FROM saves"
    ).fetchone()
    return {"count": row["n"], "bytes": row["bytes"], "files": row["files"]}


# ------------------------------------------------------------------ backlog

def get_backlog_entry_for_game(game_id):
    return get_db().execute(
        "SELECT * FROM backlog WHERE game_id = ? AND removed_at IS NULL", (game_id,)
    ).fetchone()


def add_to_backlog(game_id):
    if get_backlog_entry_for_game(game_id):
        return
    db = get_db()
    max_order = db.execute(
        "SELECT MAX(sort_order) AS m FROM backlog WHERE removed_at IS NULL"
    ).fetchone()["m"]
    db.execute(
        "INSERT INTO backlog (game_id, sort_order) VALUES (?, ?)",
        (game_id, (max_order or 0) + 10),
    )


def remove_from_backlog(game_id, reason="manual"):
    get_db().execute(
        """UPDATE backlog SET removed_at = datetime('now'), removed_reason = ?
           WHERE game_id = ? AND removed_at IS NULL""",
        (reason, game_id),
    )


def list_active_backlog(platform_id=None, location=None, tag_id=None, sort="rank"):
    """Each row carries its *true* rank in the whole backlog (window
    function computed before filtering), so a filtered or length-sorted
    view still shows where a game sits in your real priority order."""
    sql = """
        WITH ranked AS (
            SELECT b.*, ROW_NUMBER() OVER (ORDER BY b.sort_order, b.id) AS rank
            FROM backlog b WHERE b.removed_at IS NULL
        )
        SELECT ranked.id AS backlog_id, ranked.rank, ranked.sort_order, ranked.added_at,
               g.id AS game_id, g.title, g.edition_note, g.length_hours
        FROM ranked JOIN games g ON g.id = ranked.game_id
        WHERE 1 = 1
    """
    params = []
    if platform_id:
        sql += " AND " + ON_PLATFORM_SQL
        params += [platform_id, platform_id]
    if location:
        sql += " AND " + HAS_LOCATION_SQL
        params.append(location)
    if tag_id:
        sql += " AND " + HAS_TAG_SQL
        params.append(tag_id)

    # Unset lengths sort last in both directions: (x IS NULL) is 0 for a set
    # value and 1 for NULL, so ordering by it first pushes NULLs to the end.
    sort_map = {
        "rank": "ranked.rank ASC",
        "length_asc": "(g.length_hours IS NULL) ASC, g.length_hours ASC, ranked.rank ASC",
        "length_desc": "(g.length_hours IS NULL) ASC, g.length_hours DESC, ranked.rank ASC",
    }
    sql += " ORDER BY " + sort_map.get(sort, sort_map["rank"])
    return get_db().execute(sql, params).fetchall()


def move_backlog_item(backlog_id, direction):
    """Swap sort_order with the adjacent *active* row. Only exposed in the
    unfiltered rank view, where adjacent-in-the-database == adjacent-on-screen."""
    db = get_db()
    current = db.execute(
        "SELECT * FROM backlog WHERE id = ? AND removed_at IS NULL", (backlog_id,)
    ).fetchone()
    if not current or direction not in ("up", "down"):
        return
    op, order_dir = ("<", "DESC") if direction == "up" else (">", "ASC")
    neighbor = db.execute(
        f"""SELECT * FROM backlog WHERE removed_at IS NULL AND sort_order {op} ?
            ORDER BY sort_order {order_dir} LIMIT 1""",
        (current["sort_order"],),
    ).fetchone()
    if not neighbor:
        return
    db.execute("UPDATE backlog SET sort_order = ? WHERE id = ?", (neighbor["sort_order"], current["id"]))
    db.execute("UPDATE backlog SET sort_order = ? WHERE id = ?", (current["sort_order"], neighbor["id"]))


def reorder_backlog(ordered_backlog_ids):
    """Renumber the whole active backlog. Refuses (returns False) unless the
    ids are exactly the full active set — a partial list (e.g. from a
    filtered view) would interleave with hidden rows and scramble them."""
    db = get_db()
    try:
        ids = [int(x) for x in ordered_backlog_ids]
    except (TypeError, ValueError):
        return False
    active = {r["id"] for r in db.execute("SELECT id FROM backlog WHERE removed_at IS NULL")}
    if len(ids) != len(set(ids)) or set(ids) != active:
        return False
    for i, backlog_id in enumerate(ids):
        db.execute("UPDATE backlog SET sort_order = ? WHERE id = ?", ((i + 1) * 10, backlog_id))
    return True


# --------------------------------------------------------------- completions

def add_completion(game_id, completed_at, hours_played=None, rating=None, notes=None, copy_id=None):
    completion_id = get_db().execute(
        """INSERT INTO completions (game_id, copy_id, completed_at, hours_played, rating, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (game_id, copy_id or None, completed_at, hours_played, rating, notes or None),
    ).lastrowid
    # Beating a game takes it off the active backlog (same transaction).
    remove_from_backlog(game_id, reason="completed")
    return completion_id


def update_completion(completion_id, completed_at, hours_played=None, rating=None, notes=None, copy_id=None):
    get_db().execute(
        """UPDATE completions SET completed_at = ?, hours_played = ?, rating = ?, notes = ?, copy_id = ?
           WHERE id = ?""",
        (completed_at, hours_played, rating, notes or None, copy_id or None, completion_id),
    )


def delete_completion(completion_id):
    get_db().execute("DELETE FROM completions WHERE id = ?", (completion_id,))


def get_completion(completion_id):
    return get_db().execute("SELECT * FROM completions WHERE id = ?", (completion_id,)).fetchone()


def get_completions_for_game(game_id):
    return get_db().execute(
        """SELECT comp.*, c.format AS copy_format, c.location AS copy_location,
                  r.region AS copy_region, rp.name AS copy_platform_name,
                  vp.name AS copy_via_platform_name
           FROM completions comp
           LEFT JOIN copies c ON c.id = comp.copy_id
           LEFT JOIN releases r ON r.id = c.release_id
           LEFT JOIN platforms rp ON rp.id = r.platform_id
           LEFT JOIN platforms vp ON vp.id = c.via_platform_id
           WHERE comp.game_id = ?
           ORDER BY comp.completed_at DESC, comp.id DESC""",
        (game_id,),
    ).fetchall()


def get_stats():
    db = get_db()
    by_year = db.execute(
        """SELECT strftime('%Y', completed_at) AS year,
                  COUNT(*) AS games_beaten,
                  SUM(COALESCE(hours_played, 0)) AS total_hours,
                  AVG(rating) AS avg_rating
           FROM completions GROUP BY year ORDER BY year DESC"""
    ).fetchall()
    totals = db.execute(
        """SELECT COUNT(*) AS games_beaten,
                  SUM(COALESCE(hours_played, 0)) AS total_hours,
                  AVG(rating) AS avg_rating
           FROM completions"""
    ).fetchone()
    top_rated = db.execute(
        """SELECT g.id AS game_id, g.title, g.edition_note, c.rating, c.completed_at
           FROM completions c JOIN games g ON g.id = c.game_id
           WHERE c.rating IS NOT NULL
           ORDER BY c.rating DESC, c.completed_at DESC LIMIT 10"""
    ).fetchall()
    return {"by_year": by_year, "totals": totals, "top_rated": top_rated}


# ------------------------------------------------------------------ home

def get_platform_counts():
    """Games per platform, counted the same way the platform filter matches
    (a release on it, or a copy played via it) so the numbers agree."""
    return get_db().execute(
        """SELECT p.id, p.name, COUNT(DISTINCT x.game_id) AS n
           FROM platforms p
           JOIN (
               SELECT r.platform_id AS platform_id, r.game_id FROM releases r
               UNION
               SELECT c.via_platform_id, r.game_id FROM copies c
               JOIN releases r ON r.id = c.release_id
               WHERE c.via_platform_id IS NOT NULL
           ) x ON x.platform_id = p.id
           GROUP BY p.id ORDER BY n DESC, p.name"""
    ).fetchall()


def get_recent_games(limit=8):
    return get_db().execute(
        "SELECT id AS game_id, title, cover_image_path, created_at FROM games ORDER BY created_at DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def get_totals():
    db = get_db()
    row = db.execute(
        f"""SELECT COUNT(*) AS games,
                   SUM({OWNED_SQL}) AS owned,
                   SUM({PLAYABLE_SQL}) AS playable
            FROM games g"""
    ).fetchone()
    backlog_n = db.execute("SELECT COUNT(*) AS n FROM backlog WHERE removed_at IS NULL").fetchone()["n"]
    this_year = db.execute(
        "SELECT COUNT(*) AS n FROM completions WHERE strftime('%Y', completed_at) = strftime('%Y', 'now')"
    ).fetchone()["n"]
    return {
        "games": row["games"] or 0,
        "owned": row["owned"] or 0,
        "playable": row["playable"] or 0,
        "backlog": backlog_n,
        "completed_this_year": this_year,
    }


# -------------------------------------------------------------- games list

def _platform_names_by_game(game_ids):
    """{game_id: {"platforms": [...], "via": [...]}} — release platforms,
    plus platforms a copy is played *through* that aren't already listed."""
    out = {gid: {"platforms": [], "via": []} for gid in game_ids}
    if not game_ids:
        return out
    db = get_db()
    marks = ",".join("?" * len(game_ids))
    for r in db.execute(
        f"""SELECT DISTINCT r.game_id, p.name FROM releases r
            JOIN platforms p ON p.id = r.platform_id
            WHERE r.game_id IN ({marks}) ORDER BY p.name""",
        game_ids,
    ):
        out[r["game_id"]]["platforms"].append(r["name"])
    for r in db.execute(
        f"""SELECT DISTINCT r.game_id, p.name FROM copies c
            JOIN releases r ON r.id = c.release_id
            JOIN platforms p ON p.id = c.via_platform_id
            WHERE r.game_id IN ({marks}) ORDER BY p.name""",
        game_ids,
    ):
        entry = out[r["game_id"]]
        if r["name"] not in entry["platforms"]:
            entry["via"].append(r["name"])
    return out


def list_games(q=None, platform_id=None, location=None, format=None,
               tag_id=None, ownership=None, sort="title"):
    """One row per game (not per release)."""
    sql = f"""
        SELECT g.id AS game_id, g.title, g.edition_note, g.cover_image_path,
               g.length_hours, g.created_at,
               (SELECT MIN(r.release_year) FROM releases r WHERE r.game_id = g.id) AS first_year,
               {OWNED_SQL} AS owned,
               {PLAYABLE_SQL} AS playable,
               {IN_BACKLOG_SQL} AS in_backlog,
               {COMPLETED_SQL} AS completed
        FROM games g
        WHERE 1 = 1
    """
    params = []
    if q:
        like = f"%{q}%"
        sql += """ AND (g.title LIKE ? OR g.edition_note LIKE ?
                        OR EXISTS (SELECT 1 FROM releases r WHERE r.game_id = g.id
                                   AND (r.publisher LIKE ? OR r.developer LIKE ? OR r.serial LIKE ?))
                        OR EXISTS (SELECT 1 FROM game_tags gt JOIN tags t ON t.id = gt.tag_id
                                   WHERE gt.game_id = g.id AND t.name LIKE ?))"""
        params += [like] * 6
    if platform_id:
        sql += " AND " + ON_PLATFORM_SQL
        params += [platform_id, platform_id]
    if location:
        sql += " AND " + HAS_LOCATION_SQL
        params.append(location)
    if format:
        sql += " AND " + HAS_FORMAT_SQL
        params.append(format)
    if tag_id:
        sql += " AND " + HAS_TAG_SQL
        params.append(tag_id)
    if ownership == "owned":
        sql += " AND " + OWNED_SQL
    elif ownership == "playable":
        sql += " AND " + PLAYABLE_SQL
    elif ownership == "not_playable":
        sql += " AND NOT " + PLAYABLE_SQL

    sort_map = {
        "title": "g.title COLLATE NOCASE ASC, g.edition_note ASC",
        "year": "(first_year IS NULL) ASC, first_year ASC, g.title COLLATE NOCASE ASC",
        "recent": "g.created_at DESC, g.id DESC",
        "length_asc": "(g.length_hours IS NULL) ASC, g.length_hours ASC, g.title COLLATE NOCASE ASC",
        "length_desc": "(g.length_hours IS NULL) ASC, g.length_hours DESC, g.title COLLATE NOCASE ASC",
    }
    sql += " ORDER BY " + sort_map.get(sort, sort_map["title"])

    rows = [dict(r) for r in get_db().execute(sql, params).fetchall()]
    names = _platform_names_by_game([r["game_id"] for r in rows])
    for r in rows:
        r["platforms"] = names[r["game_id"]]["platforms"]
        r["via_platforms"] = names[r["game_id"]]["via"]
    return rows
