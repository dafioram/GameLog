"""
Functional test suite for Retro Archive.

Run from the project root:  pytest -v
"""
import base64
import os
import re
import sqlite3
from io import BytesIO

import pytest

from conftest import add_copy, create_game, page


def _png():
    # Minimal valid 1x1 PNG.
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )


def _game_rows(body):
    """Split the games list into per-game chunks."""
    return body.split('class="game-row"')[1:]


# ===================================================================== smoke

def test_top_level_pages_render(client):
    for path in ["/", "/games", "/games/new", "/backlog", "/stats"]:
        assert client.get(path).status_code == 200, path


def test_unknown_game_is_404(client):
    assert client.get("/games/999").status_code == 404


# ============================================================ game lifecycle

def test_create_game_requires_title_and_platform(client):
    resp = client.post("/games/new", data={"title": "", "platform": ""})
    assert resp.status_code == 200
    assert b"required" in resp.data.lower()


def test_create_game_minimal_fields_only(client):
    game_id = create_game(client, title="Zelda II", platform="NES")
    assert "Zelda II" in page(client, f"/games/{game_id}")


def test_edit_and_delete_game(client):
    game_id = create_game(client)
    client.post(f"/games/{game_id}/edit", data={"title": "Chrono Trigger DS"})
    assert "Chrono Trigger DS" in page(client, f"/games/{game_id}")
    client.post(f"/games/{game_id}/delete")
    assert client.get(f"/games/{game_id}").status_code == 404


def test_invalid_length_is_rejected_and_nothing_is_created(client):
    resp = client.post("/games/new", data={"title": "Bad", "platform": "PC", "length_hours": "-3"})
    assert resp.status_code == 200
    assert b"less than 0" in resp.data
    resp = client.post("/games/new", data={"title": "Bad", "platform": "PC", "length_hours": "nan"})
    assert b"must be a number" in resp.data
    assert "Bad" not in page(client, "/games")


def test_platform_names_are_case_insensitive(client):
    create_game(client, title="A", platform="SNES")
    create_game(client, title="B", platform="snes")
    rows = _game_rows(page(client, "/games"))
    assert len(rows) == 2 and all("SNES" in r and "snes" not in r for r in rows)
    # Only one platform row for SNES on the home shelf, counting both games.
    home = page(client, "/")
    assert re.search(r">SNES</a>\s*<span class=\"shelf-count\">2 games", home)


# ======================================================================= tags

def test_tags_live_on_the_game_and_can_be_added_from_the_new_game_form(client):
    game_id = create_game(client, title="Chrono Trigger", tags="JRPG, Time travel")
    body = page(client, f"/games/{game_id}")
    assert "JRPG" in body and "Time travel" in body
    # filter by tag on the games list
    tag_id = re.search(r'tags/(\d+)/remove', body).group(1)
    create_game(client, title="Untagged Game")
    listing = page(client, f"/games?tag_id={tag_id}")
    assert "Chrono Trigger" in listing and "Untagged Game" not in listing


def test_tag_names_are_case_insensitive_and_orphans_are_cleaned_up(client):
    g1 = create_game(client, title="One", tags="Rom hack")
    g2 = create_game(client, title="Two", tags="rom HACK")
    body = page(client, "/games")
    assert body.count('<option value="1"') >= 1
    assert "rom HACK" not in body  # second spelling reused the first tag

    client.post(f"/games/{g1}/tags/1/remove")
    assert "Rom hack" in page(client, "/games")  # still used by game Two
    client.post(f"/games/{g2}/tags/1/remove")
    assert "Rom hack" not in page(client, "/games")  # gone once unused


# =================================================================== releases

def test_add_second_release_and_cannot_delete_the_last_one(client):
    game_id = create_game(client, platform="SNES")
    client.post(f"/games/{game_id}/releases/new", data={"platform": "Nintendo DS", "region": "USA"})
    body = page(client, f"/games/{game_id}")
    assert "Nintendo DS" in body
    assert body.count("Delete release") == 2

    client.post("/releases/2/delete")
    resp = client.post("/releases/1/delete", follow_redirects=True)
    assert b"needs at least one release" in resp.data


# ===================================================================== copies

def test_copy_requires_at_least_one_meaningful_field(client):
    create_game(client)
    resp = client.post("/releases/1/copies/new", data={"notes": "just a note"}, follow_redirects=True)
    assert b"means something" in resp.data


def test_copy_notes_and_url_are_shown(client):
    game_id = create_game(client, title="Super Mario 64", platform="Nintendo 64", region="USA")
    add_copy(client, 1, format="Source port", via_platform="PC", location="Steam Deck",
             url="https://github.com/example/sm64-port", notes="sm64ex build, 60fps\nneeds USA ROM")
    body = page(client, f"/games/{game_id}")
    assert "Source port" in body and "through PC" in body and "Steam Deck" in body
    assert 'href="https://github.com/example/sm64-port"' in body
    assert "sm64ex build, 60fps" in body


def test_web_link_copy_is_playable_but_not_owned(client):
    game_id = create_game(client, title="Flash Classic", platform="PC")
    add_copy(client, 1, format="Web", url="https://example.org/play", is_owned=["0"])
    detail = page(client, f"/games/{game_id}")
    assert "own-tag playable" in detail and "own-tag owned" not in detail

    assert "Flash Classic" in page(client, "/games?ownership=playable")
    assert "Flash Classic" not in page(client, "/games?ownership=owned")
    assert "Flash Classic" not in page(client, "/games?ownership=not_playable")

    home = page(client, "/")
    assert re.search(r'<span class="n">0</span><span class="label">owned', home)
    assert re.search(r'<span class="n">1</span><span class="label">playable', home)


def test_owned_checkbox_semantics(client):
    """Hidden 0 + checked 1 => owned; hidden 0 only => not owned; field absent => owned."""
    create_game(client, title="A", platform="PC")
    add_copy(client, 1, format="Steam", is_owned=["0", "1"])
    create_game(client, title="B", platform="PC")
    add_copy(client, 2, format="Game Pass", is_owned=["0"])
    create_game(client, title="C", platform="PC")
    add_copy(client, 3, format="GOG")
    owned = page(client, "/games?ownership=owned")
    assert "A</a>" in owned and "C</a>" in owned and "B</a>" not in owned


def test_copy_link_must_be_http_or_https(client):
    game_id = create_game(client)
    resp = client.post("/releases/1/copies/new",
                       data={"format": "Web", "url": "javascript:alert(1)"}, follow_redirects=True)
    assert b"http:// or https://" in resp.data
    assert "javascript:" not in page(client, f"/games/{game_id}")


def test_edit_copy_updates_notes_and_ownership(client):
    game_id = create_game(client)
    add_copy(client, 1, format="Physical", location="Closet")
    client.post("/copies/1/edit", data={"format": "Physical", "location": "Closet box 3",
                                        "notes": "Label is faded", "is_owned": ["0", "1"]})
    body = page(client, f"/games/{game_id}")
    assert "Closet box 3" in body and "Label is faded" in body

    client.post("/copies/1/edit", data={"format": "Switch Online", "is_owned": ["0"]})
    assert "own-tag playable" in page(client, f"/games/{game_id}")


def test_editing_a_copy_path_clears_the_stale_hash(client, tmp_path):
    game_id = create_game(client)
    rom = tmp_path / "game.sfc"
    rom.write_bytes(b"ROM" * 100)
    add_copy(client, 1, format="Digital ROM", path=str(rom), compute_hash_now="1")
    assert "sha256" in page(client, f"/games/{game_id}")

    other = tmp_path / "other.sfc"
    other.write_bytes(b"DIFFERENT" * 100)
    resp = client.post("/copies/1/edit", data={"format": "Digital ROM", "path": str(other)},
                       follow_redirects=True)
    assert b"old hash was cleared" in resp.data
    body = page(client, f"/games/{game_id}")
    assert "sha256" not in body and "unchecked" in body

    client.post("/copies/1/verify")
    assert "verified" in page(client, f"/games/{game_id}")  # not a false "modified"


def test_hash_compute_verify_modified_missing(client, tmp_path):
    game_id = create_game(client, title="Zelda II", platform="NES")
    rom = tmp_path / "zelda2.nes"
    rom.write_bytes(b"ORIGINAL ROM BYTES" * 50)
    add_copy(client, 1, format="Digital ROM", path=str(rom), compute_hash_now="1")
    assert "verified" in page(client, f"/games/{game_id}")

    client.post("/copies/1/verify")
    assert "verified" in page(client, f"/games/{game_id}")

    rom.write_bytes(b"TAMPERED" * 60)
    client.post("/copies/1/verify")
    assert "status-tag modified" in page(client, f"/games/{game_id}")

    rom.unlink()
    client.post("/copies/1/verify")
    assert "status-tag missing" in page(client, f"/games/{game_id}")

    resp = client.get("/copies/1/download", follow_redirects=True)
    assert b"No file found" in resp.data


# ============================================================ games list rows

def test_games_list_is_one_row_per_game_with_all_platforms(client):
    game_id = create_game(client, title="Super Mario World", platform="SNES", region="USA")
    client.post(f"/games/{game_id}/releases/new", data={"platform": "Game Boy Advance"})
    rows = [r for r in _game_rows(page(client, "/games")) if "Super Mario World" in r]
    assert len(rows) == 1
    assert "Game Boy Advance · SNES" in rows[0]


def test_ownership_on_any_release_counts_for_the_game(client):
    game_id = create_game(client, title="Super Mario World", platform="SNES")
    client.post(f"/games/{game_id}/releases/new", data={"platform": "Game Boy Advance"})
    add_copy(client, 2, format="Physical")  # only the GBA release has a copy
    row = _game_rows(page(client, "/games"))[0]
    assert "dot owned" in row


def test_platform_filter_includes_copies_played_via_that_platform(client):
    create_game(client, title="Super Metroid", platform="SNES")
    add_copy(client, 1, format="Switch Online", via_platform="Switch", is_owned=["0"])
    create_game(client, title="Some SNES Cart", platform="SNES")
    add_copy(client, 2, format="Physical")

    home = page(client, "/")
    switch_id = re.search(r'platform_id=(\d+)">Switch<', home).group(1)
    listing = page(client, f"/games?platform_id={switch_id}")
    assert "Super Metroid" in listing and "Some SNES Cart" not in listing
    assert "through Switch" in listing


def test_games_list_filters(client):
    create_game(client, title="Metroid Prime", platform="GameCube")
    add_copy(client, 1, format="Physical", location="Living room shelf")
    create_game(client, title="Sonic the Hedgehog", platform="Sega Genesis")
    add_copy(client, 2, format="Digital ROM", location="NAS")
    create_game(client, title="Half-Life 2", platform="PC")
    add_copy(client, 3, format="Steam")
    create_game(client, title="Wishlist Game", platform="PC")

    body = page(client, "/games?location=nas")  # case-insensitive
    assert "Sonic" in body and "Metroid" not in body and "Half-Life" not in body
    body = page(client, "/games?format=Steam")
    assert "Half-Life" in body and "Sonic" not in body
    body = page(client, "/games?q=sonic")
    assert "Sonic" in body and "Metroid" not in body
    body = page(client, "/games?ownership=not_playable")
    assert "Wishlist Game" in body and "Sonic" not in body


def test_games_list_sorts_by_length_with_unset_last(client):
    create_game(client, title="Mid", length_hours="10")
    create_game(client, title="Unset")
    create_game(client, title="Short", length_hours="2")
    body = page(client, "/games?sort=length_desc")
    assert sorted(["Mid", "Unset", "Short"], key=lambda t: body.index(f">{t}<")) == ["Mid", "Short", "Unset"]


# ===================================================================== covers

def _upload_cover(client, game_id, data=None, name="box.png"):
    return client.post(f"/games/{game_id}/cover",
                       data={"cover": (BytesIO(data if data is not None else _png()), name)},
                       content_type="multipart/form-data", follow_redirects=True)


def test_cover_is_per_game_and_served_from_the_data_folder(client, app):
    game_id = create_game(client)
    _upload_cover(client, game_id)
    body = page(client, f"/games/{game_id}")
    m = re.search(r'/covers/([a-f0-9]{16}\.png)', body)
    assert m and "Clear cover" in body
    assert client.get(f"/covers/{m.group(1)}").data == _png()
    # stored in this app's (temporary) data dir, not the project's data/
    assert os.path.exists(os.path.join(app.config["COVERS_DIR"], m.group(1)))


def test_cover_survives_adding_an_earlier_release(client):
    """Regression: covers used to belong to whichever release sorted first."""
    game_id = create_game(client, platform="SNES", release_year="1995")
    _upload_cover(client, game_id)
    client.post(f"/games/{game_id}/releases/new", data={"platform": "SNES", "region": "Japan"})
    assert "/covers/" in page(client, f"/games/{game_id}")


def test_identical_covers_are_stored_once_and_clear_keeps_shared_file(client):
    g1 = create_game(client, title="Game One")
    g2 = create_game(client, title="Game Two")
    _upload_cover(client, g1, name="a.png")
    _upload_cover(client, g2, name="different-name.png")
    n1 = re.search(r'/covers/([a-f0-9]+\.png)', page(client, f"/games/{g1}")).group(1)
    n2 = re.search(r'/covers/([a-f0-9]+\.png)', page(client, f"/games/{g2}")).group(1)
    assert n1 == n2

    client.post(f"/games/{g1}/cover/clear")
    assert "No cover art" in page(client, f"/games/{g1}")
    assert client.get(f"/covers/{n2}").status_code == 200


def test_cover_type_comes_from_content_not_filename(client):
    game_id = create_game(client)
    resp = _upload_cover(client, game_id, data=b"<html>not an image</html>", name="sneaky.png")
    assert b"Unsupported image type" in resp.data
    resp = _upload_cover(client, game_id, name="actually-a-png.jpg")
    assert b"Cover image updated" in resp.data
    assert re.search(r'/covers/[a-f0-9]+\.png', page(client, f"/games/{game_id}"))


def test_prune_covers_removes_only_unreferenced_files(client, app):
    g1 = create_game(client, title="Keep")
    _upload_cover(client, g1)
    stray = os.path.join(app.config["COVERS_DIR"], "deadbeefdeadbeef.png")
    open(stray, "wb").write(b"x")
    result = app.test_cli_runner().invoke(args=["prune-covers"])
    assert "1 unused cover file(s) removed" in result.output
    assert not os.path.exists(stray)
    kept = re.search(r'/covers/([a-f0-9]+\.png)', page(client, f"/games/{g1}")).group(1)
    assert os.path.exists(os.path.join(app.config["COVERS_DIR"], kept))


# ========================================================== identity semantics

def test_remake_tracks_completion_independently_and_links_both_ways(client):
    original = create_game(client, title="Resident Evil", platform="PlayStation", edition_note="1996 Original")
    remake = create_game(client, title="Resident Evil", platform="GameCube",
                         edition_note="2002 Remake", related_game_id=str(original))
    assert "2002 Remake" in page(client, f"/games/{original}")  # reverse link
    assert "1996 Original" in page(client, f"/games/{remake}")  # forward link

    client.post(f"/games/{remake}/complete", data={"completed_at": "2026-02-01", "rating": "8"})
    rows = _game_rows(page(client, "/games"))
    assert "badge beaten" not in next(r for r in rows if "1996 Original" in r)
    assert "badge beaten" in next(r for r in rows if "2002 Remake" in r)


def test_region_variants_share_one_completion_status(client):
    game_id = create_game(client, title="Super Mario World", platform="SNES", region="USA")
    client.post(f"/games/{game_id}/releases/new", data={"platform": "SNES", "region": "Japan"})
    client.post(f"/games/{game_id}/complete", data={"completed_at": "2026-03-01", "rating": "10"})
    rows = [r for r in _game_rows(page(client, "/games")) if "Super Mario World" in r]
    assert len(rows) == 1 and "badge beaten" in rows[0]


# ==================================================================== backlog

def _backlog_order(body, titles):
    return sorted(titles, key=lambda t: body.index(f">{t}<"))


def test_backlog_add_remove_and_auto_remove_on_completion(client):
    game_id = create_game(client)
    client.post(f"/games/{game_id}/backlog/add")
    assert "badge backlog" in page(client, "/games")
    client.post(f"/games/{game_id}/complete", data={"completed_at": "2026-01-01"})
    body = page(client, "/games")
    assert "badge beaten" in body and "badge backlog" not in body


def test_backlog_add_twice_keeps_one_active_entry(client):
    game_id = create_game(client)
    client.post(f"/games/{game_id}/backlog/add")
    client.post(f"/games/{game_id}/backlog/add")
    assert page(client, "/backlog").count('class="backlog-row') == 1


def test_backlog_reorder_via_move_buttons(client):
    for i in range(3):
        client.post(f"/games/{create_game(client, title=f'Game {i}', platform='PC')}/backlog/add")
    client.post("/backlog/3/move", data={"direction": "up"})
    client.post("/backlog/3/move", data={"direction": "up"})
    assert _backlog_order(page(client, "/backlog"), ["Game 0", "Game 1", "Game 2"])[0] == "Game 2"


def test_backlog_reorder_via_json_endpoint(client):
    for i in range(3):
        client.post(f"/games/{create_game(client, title=f'Game {i}', platform='PC')}/backlog/add")
    assert client.post("/backlog/reorder", json={"order": [3, 1, 2]}).status_code == 200
    assert _backlog_order(page(client, "/backlog"), ["Game 0", "Game 1", "Game 2"]) == ["Game 2", "Game 0", "Game 1"]


def test_backlog_partial_reorder_is_rejected_without_scrambling(client):
    """Regression: a filtered view used to renumber only visible rows."""
    for i in range(4):
        client.post(f"/games/{create_game(client, title=f'Game {i}', platform='PC')}/backlog/add")
    resp = client.post("/backlog/reorder", json={"order": [4, 2]})
    assert resp.status_code == 400
    assert _backlog_order(page(client, "/backlog"), [f"Game {i}" for i in range(4)]) == \
        ["Game 0", "Game 1", "Game 2", "Game 3"]


def test_filtered_backlog_hides_reordering_and_shows_real_rank(client):
    a = create_game(client, title="PC Game A", platform="PC")
    s = create_game(client, title="SNES Game", platform="SNES")
    b = create_game(client, title="PC Game B", platform="PC")
    for gid in (a, s, b):
        client.post(f"/games/{gid}/backlog/add")
    home = page(client, "/")
    snes_id = re.search(r'platform_id=(\d+)">SNES<', home).group(1)

    body = page(client, f"/backlog?platform_id={snes_id}")
    assert "SNES Game" in body and "PC Game A" not in body
    assert 'aria-label="Move up"' not in body and "draggable" not in body
    assert '<span class="rank">2.</span>' in body  # its true rank, not "1."


def test_top_ten_divider_only_in_unfiltered_rank_view(client):
    for i in range(12):
        client.post(f"/games/{create_game(client, title=f'Backlog Game {i}', platform='PC')}/backlog/add")
    assert "Top 10 ends here" in page(client, "/backlog")
    assert "Top 10 ends here" not in page(client, "/backlog?sort=length_asc")


def test_backlog_length_column_sort_and_total(client):
    short = create_game(client, title="Short Game", length_hours="2")
    long_ = create_game(client, title="Long Game", length_hours="60")
    unset = create_game(client, title="Unset Game")
    for gid in (short, long_, unset):
        client.post(f"/games/{gid}/backlog/add")

    body = page(client, "/backlog")
    assert "60h" in body and ">-<" in body
    assert "62h of estimated play time" in body and "1 game with no length set" in body

    titles = ["Short Game", "Long Game", "Unset Game"]
    assert _backlog_order(page(client, "/backlog?sort=length_asc"), titles) == ["Short Game", "Long Game", "Unset Game"]
    assert _backlog_order(page(client, "/backlog?sort=length_desc"), titles) == ["Long Game", "Short Game", "Unset Game"]
    assert 'aria-label="Move up"' not in page(client, "/backlog?sort=length_asc")


def test_backlog_remove_keeps_filters_but_ignores_offsite_next(client):
    game_id = create_game(client)
    client.post(f"/games/{game_id}/backlog/add")
    resp = client.post(f"/games/{game_id}/backlog/remove", data={"next": "/backlog?sort=length_asc"})
    assert resp.headers["Location"].endswith("/backlog?sort=length_asc")
    client.post(f"/games/{game_id}/backlog/add")
    resp = client.post(f"/games/{game_id}/backlog/remove", data={"next": "//evil.example/"})
    assert "evil.example" not in resp.headers["Location"]


# ================================================================ completions

def test_rating_out_of_range_is_rejected(client):
    game_id = create_game(client)
    resp = client.post(f"/games/{game_id}/complete",
                       data={"completed_at": "2026-01-01", "rating": "15"}, follow_redirects=True)
    assert b"between 1 and 10" in resp.data
    assert "Not marked as beaten yet" in page(client, f"/games/{game_id}")


def test_bad_completion_date_is_rejected(client):
    game_id = create_game(client)
    resp = client.post(f"/games/{game_id}/complete", data={"completed_at": "last tuesday"},
                       follow_redirects=True)
    assert b"YYYY-MM-DD" in resp.data


def test_completion_records_which_copy_it_was_played_via(client):
    game_id = create_game(client, title="Super Mario 64", platform="Nintendo 64", region="USA")
    add_copy(client, 1, format="Physical", location="Shelf")
    add_copy(client, 1, format="Source port", via_platform="PC", location="Steam Deck")
    client.post(f"/games/{game_id}/complete", data={"completed_at": "2026-03-01", "copy_id": "2"})
    body = page(client, f"/games/{game_id}")
    assert "Copy: Source port — Nintendo 64 (USA) through PC · Steam Deck" in body


def test_completion_cannot_point_at_another_games_copy(client):
    g1 = create_game(client, title="One")
    add_copy(client, 1, format="Physical")
    g2 = create_game(client, title="Two")
    resp = client.post(f"/games/{g2}/complete", data={"copy_id": "1"}, follow_redirects=True)
    assert b"belong to this game" in resp.data  # apostrophe is HTML-escaped


def test_deleting_the_copy_keeps_the_completion(client):
    game_id = create_game(client)
    add_copy(client, 1, format="Physical")
    client.post(f"/games/{game_id}/complete", data={"completed_at": "2026-01-05", "copy_id": "1"})
    client.post("/copies/1/delete")
    body = page(client, f"/games/{game_id}")
    assert "2026-01-05" in body and "Copy: Physical" not in body


def test_completion_can_be_edited_and_deleted(client):
    game_id = create_game(client)
    add_copy(client, 1, format="Physical")
    client.post(f"/games/{game_id}/complete", data={"completed_at": "2026-01-01", "rating": "7"})
    client.post("/completions/1/edit", data={"completed_at": "2026-01-02", "rating": "9",
                                             "hours_played": "30.5", "copy_id": "1"})
    body = page(client, f"/games/{game_id}")
    assert "2026-01-02" in body and "9/10" in body and "30.5h" in body and "Copy: Physical" in body

    client.post("/completions/1/delete")
    assert "Not marked as beaten yet" in page(client, f"/games/{game_id}")


def test_stats_reflect_completions(client):
    game_id = create_game(client, title="Chrono Trigger")
    client.post(f"/games/{game_id}/complete",
                data={"completed_at": "2026-01-15", "hours_played": "28.5", "rating": "9"})
    body = page(client, "/stats")
    assert "2026" in body and "Chrono Trigger" in body


# =================================================================== security

def test_cross_site_form_posts_are_blocked(client):
    game_id = create_game(client)
    resp = client.post(f"/games/{game_id}/delete", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert client.get(f"/games/{game_id}").status_code == 200

    resp = client.post(f"/games/{game_id}/edit", data={"title": "Renamed"},
                       headers={"Origin": "http://localhost"})  # test client's host
    assert resp.status_code == 302


def test_security_headers_present(client):
    resp = client.get("/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


# ========================================================== data & config

def test_default_paths_live_under_data_folder():
    import db as db_module
    assert db_module.DEFAULT_DB_PATH.parent.name == "data"
    assert db_module.DEFAULT_DB_PATH.name == "retro_archive.db"


def test_new_database_records_schema_version(app):
    import db as db_module
    conn = sqlite3.connect(app.config["DATABASE"])
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_module.SCHEMA_VERSION == 3
    conn.close()


FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _old_database(data_dir, version):
    """Build a database exactly as an older app version left it, from the
    frozen schema file for that version, with representative rows."""
    schema = open(os.path.join(FIXTURES, "schema_v2.sql"), encoding="utf-8").read()
    if version == 1:  # v1 = v2 without the save vault
        schema = re.sub(r"CREATE TABLE IF NOT EXISTS saves \(.*?\);", "", schema, flags=re.S)
        schema = re.sub(r"CREATE INDEX IF NOT EXISTS idx_saves_\w+\s+ON saves\(\w+\);", "", schema)
    os.makedirs(data_dir, exist_ok=True)
    conn = sqlite3.connect(os.path.join(data_dir, "retro_archive.db"))
    conn.executescript(schema)
    conn.executescript("""
        INSERT INTO platforms (id, name) VALUES (1, 'SNES'), (2, 'Switch'), (3, 'Nintendo 64'), (4, 'PC');
        INSERT INTO games (id, title, related_game_id, length_hours) VALUES
            (1, 'Super Metroid', NULL, 8), (2, 'Super Metroid Redesign', 1, 10),
            (3, 'Final Fantasy VI', NULL, NULL), (4, 'Super Mario 64', NULL, 12);
        INSERT INTO releases (id, game_id, platform_id, region) VALUES
            (1, 1, 1, 'USA'), (2, 2, 1, NULL), (3, 3, 1, 'USA'), (4, 4, 3, 'USA');
        INSERT INTO copies (id, release_id, format, via_platform_id, location, is_owned) VALUES
            (1, 1, 'Physical', NULL, 'Closet', 1), (2, 3, 'Subscription', 2, NULL, 0),
            (3, 4, 'Source port', 4, 'Steam Deck', 1);
        INSERT INTO completions (id, game_id, copy_id, completed_at, hours_played, rating, notes) VALUES
            (1, 4, 3, '2026-03-01', 14.5, 9, '120 stars'), (2, 3, 2, '2026-04-01', 40, 10, NULL);
        INSERT INTO backlog (game_id, sort_order) VALUES (1, 10), (2, 20);
    """)
    if version >= 2:
        conn.execute("""INSERT INTO saves (copy_id, completion_id, label, filename, stored_name, sha256, size)
                        VALUES (3, 1, '120 stars', 'sm64ex.sav', 'abc.sav', 'abc', 8)""")
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()


@pytest.mark.parametrize("version", [1, 2])
def test_older_database_upgrades_in_place_keeping_everything(tmp_path, version):
    from app import create_app
    import db as db_module
    data = str(tmp_path / "data")
    _old_database(data, version)
    app = create_app(data_dir=data)

    conn = sqlite3.connect(app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db_module.SCHEMA_VERSION
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # existing values untouched
    games = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM games")}
    assert games[2]["related_game_id"] == 1 and games[1]["length_hours"] == 8
    # existing links become 'related' (so nothing gets folded until you say so)
    assert games[2]["relation_kind"] == "related" and games[1]["relation_kind"] is None
    comp = dict(conn.execute("SELECT * FROM completions WHERE id = 1").fetchone())
    assert (comp["copy_id"], comp["hours_played"], comp["rating"], comp["notes"]) == (3, 14.5, 9, "120 stars")
    assert comp["method"] is None and comp["played_on_platform_id"] is None
    assert conn.execute("SELECT via_platform_id FROM copies WHERE id = 2").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM backlog WHERE removed_at IS NULL").fetchone()[0] == 2
    save_cols = {r["name"] for r in conn.execute("PRAGMA table_info(saves)")}
    assert "method" in save_cols
    if version == 2:
        assert conn.execute("SELECT label FROM saves").fetchone()[0] == "120 stars"
    conn.close()

    # and the app works on it
    client = app.test_client()
    for url in ["/", "/games", "/backlog", "/stats", "/games/1", "/games/2", "/games/4"]:
        assert client.get(url).status_code == 200, url
    listing = client.get("/games").data.decode()
    assert "Super Metroid Redesign" in listing                 # 'related' isn't folded
    assert "through Switch" in listing                          # Switch Online copy kept
    assert "on PC" in client.get("/games/4").data.decode()      # derived from the copy's 'through PC'


def test_a_failed_upgrade_leaves_the_database_untouched(tmp_path, monkeypatch):
    from app import create_app
    import db as db_module
    data = str(tmp_path / "data")
    _old_database(data, 2)

    def broken(conn):
        conn.execute("ALTER TABLE games ADD COLUMN relation_kind TEXT")
        raise RuntimeError("simulated failure halfway through")
    monkeypatch.setitem(db_module.MIGRATIONS, 3, broken)
    with pytest.raises(RuntimeError):
        create_app(data_dir=data)
    conn = sqlite3.connect(os.path.join(data, "retro_archive.db"))
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
    assert "relation_kind" not in cols                          # rolled back
    conn.close()


def test_prototype_database_is_refused_with_a_clear_message(tmp_path):
    from app import create_app
    from db import IncompatibleDatabaseError
    data = tmp_path / "data"
    data.mkdir()
    conn = sqlite3.connect(data / "retro_archive.db")
    conn.execute("CREATE TABLE games (id INTEGER PRIMARY KEY, title TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(IncompatibleDatabaseError, match="early prototype"):
        create_app(data_dir=data)


def test_data_dir_env_var_is_respected(tmp_path, monkeypatch):
    from app import create_app
    monkeypatch.setenv("RETRO_DATA_DIR", str(tmp_path / "elsewhere"))
    app = create_app()
    assert app.config["DATABASE"] == str(tmp_path / "elsewhere" / "retro_archive.db")
    assert os.path.exists(app.config["DATABASE"])


def test_dotenv_loader_never_overrides_real_environment(tmp_path, monkeypatch):
    import config
    env = tmp_path / ".env"
    env.write_text('# comment\nRETRO_PORT=6060\nRETRO_HOST="0.0.0.0"\nRA_TEST_ONLY=from-file\n')
    monkeypatch.setenv("RETRO_PORT", "7070")
    # set-then-delete so monkeypatch restores (removes) whatever the loader adds
    for var in ("RETRO_HOST", "RA_TEST_ONLY"):
        monkeypatch.setenv(var, "x")
        monkeypatch.delenv(var)
    config.load_dotenv(env)
    assert config.port() == 7070            # real env wins
    assert config.host() == "0.0.0.0"       # quotes stripped
    assert os.environ["RA_TEST_ONLY"] == "from-file"


def test_native_defaults_are_localhost_and_no_debugger(monkeypatch):
    import config
    for var in ("RETRO_HOST", "RETRO_DEBUG", "RETRO_PORT"):
        monkeypatch.delenv(var, raising=False)
    assert config.host() == "127.0.0.1"
    assert config.debug() is False
    assert config.port() == 5050


def test_healthz_reports_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200 and resp.get_json() == {"ok": True}


# ================================================================ save vault

SAVE_BYTES = b"SRAM" + bytes(range(256)) * 32


def _upload_save(client, copy_id, data=SAVE_BYTES, name="Chrono Trigger (USA).srm", **form):
    form = {"save": (BytesIO(data), name), **form}
    return client.post(f"/copies/{copy_id}/saves/new", data=form,
                       content_type="multipart/form-data", follow_redirects=True)


def _saves_dir(app):
    return app.config["SAVES_DIR"]


def _stored_files(app):
    d = _saves_dir(app)
    return sorted(f for f in os.listdir(d) if not f.startswith(".")) if os.path.isdir(d) else []


def test_upload_and_download_a_save(client, app):
    game_id = create_game(client)
    add_copy(client, 1, format="Digital ROM", location="NAS")
    resp = _upload_save(client, 1, label="Before Lavos", notes="Slot 2, all techs")
    assert b"Save uploaded" in resp.data
    body = page(client, f"/games/{game_id}")
    assert "Before Lavos" in body and "Slot 2, all techs" in body
    assert "Chrono Trigger (USA).srm" in body

    save_id = int(re.search(r"/saves/(\d+)/download", body).group(1))
    dl = client.get(f"/saves/{save_id}/download")
    assert dl.status_code == 200 and dl.data == SAVE_BYTES
    assert "Chrono Trigger (USA).srm" in dl.headers["Content-Disposition"]
    # stored hash-named inside this app's data dir
    assert _stored_files(app) and _stored_files(app)[0].endswith(".srm")


def test_save_label_defaults_to_file_name_and_empty_files_are_rejected(client, app):
    game_id = create_game(client)
    add_copy(client, 1, format="Physical")
    assert b"That file is empty" in _upload_save(client, 1, data=b"").data
    assert _stored_files(app) == []
    _upload_save(client, 1, name="memcard.mcr")
    assert '<span class="save-label">memcard.mcr</span>' in page(client, f"/games/{game_id}")


def test_upload_name_is_reduced_to_a_safe_basename(client):
    game_id = create_game(client)
    add_copy(client, 1, format="Physical")
    _upload_save(client, 1, name="..\\..\\evil/../../etc/passwd")
    body = page(client, f"/games/{game_id}")
    assert "etc/passwd" not in body and "passwd" in body


def test_identical_saves_share_one_file_until_the_last_is_deleted(client, app):
    game_id = create_game(client)
    add_copy(client, 1, format="Physical")
    _upload_save(client, 1, label="A")
    _upload_save(client, 1, label="B")
    assert len(_stored_files(app)) == 1
    ids = [int(x) for x in re.findall(r"/saves/(\d+)/download", page(client, f"/games/{game_id}"))]
    client.post(f"/saves/{ids[0]}/delete")
    assert len(_stored_files(app)) == 1          # still used by the other save
    client.post(f"/saves/{ids[1]}/delete")
    assert _stored_files(app) == []              # last reference gone -> file removed


def test_copy_and_release_with_saves_cannot_be_deleted(client):
    game_id = create_game(client)
    client.post(f"/games/{game_id}/releases/new", data={"platform": "SNES", "region": "Japan"})
    add_copy(client, 2, format="Physical")      # copy on the 2nd release
    _upload_save(client, 1)
    resp = client.post("/copies/1/delete", follow_redirects=True)
    assert b"has 1 saved game" in resp.data
    resp = client.post("/releases/2/delete", follow_redirects=True)
    assert b"saved game" in resp.data
    assert "Japan" in page(client, f"/games/{game_id}")


def test_save_can_move_to_another_copy_of_the_same_game_only(client):
    g1 = create_game(client, title="One")
    add_copy(client, 1, format="Physical")
    add_copy(client, 1, format="Source port", via_platform="PC")
    g2 = create_game(client, title="Two")
    add_copy(client, 2, format="Physical")      # copy 3 belongs to game Two
    _upload_save(client, 1)
    sid = int(re.search(r"/saves/(\d+)/download", page(client, f"/games/{g1}")).group(1))

    resp = client.post(f"/saves/{sid}/edit", data={"copy_id": "3", "label": "x"}, follow_redirects=True)
    assert b"same game" in resp.data
    client.post(f"/saves/{sid}/edit", data={"copy_id": "2", "label": "Moved"})
    # copy 1 is now free of saves and can be removed
    assert b"Copy removed" in client.post("/copies/1/delete", follow_redirects=True).data
    assert "Moved" in page(client, f"/games/{g1}")


def test_mark_beaten_can_keep_the_save_from_that_run(client, app):
    game_id = create_game(client)
    add_copy(client, 1, format="Source port", via_platform="PC")
    resp = client.post(f"/games/{game_id}/complete",
                       data={"copy_id": "1", "rating": "9", "save_label": "Clear save",
                             "save": (BytesIO(SAVE_BYTES), "clear.sav")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert b"Save file kept" in resp.data
    body = page(client, f"/games/{game_id}")
    assert "from the " in body and "completion" in body          # shown under the copy
    assert 'class="copy-line completion-save"' in body           # and under the completion


def test_mark_beaten_with_a_save_but_no_copy_saves_nothing(client, app):
    game_id = create_game(client)
    add_copy(client, 1, format="Physical")
    resp = client.post(f"/games/{game_id}/complete",
                       data={"save": (BytesIO(SAVE_BYTES), "clear.sav")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert b"pick which copy" in resp.data
    assert "Not marked as beaten yet" in page(client, f"/games/{game_id}")
    assert _stored_files(app) == []


def test_deleting_a_completion_keeps_its_save(client):
    game_id = create_game(client)
    add_copy(client, 1, format="Physical")
    client.post(f"/games/{game_id}/complete",
                data={"copy_id": "1", "save": (BytesIO(SAVE_BYTES), "clear.sav")},
                content_type="multipart/form-data")
    client.post("/completions/1/delete")
    body = page(client, f"/games/{game_id}")
    assert "clear.sav" in body and "from the" not in body


def test_save_cannot_link_another_games_completion(client):
    g1 = create_game(client, title="One")
    add_copy(client, 1, format="Physical")
    g2 = create_game(client, title="Two")
    client.post(f"/games/{g2}/complete", data={})
    resp = _upload_save(client, 1, completion_id="1")
    assert b"belong to this game" in resp.data


def test_deleting_a_game_removes_its_save_files(client, app):
    keep = create_game(client, title="Keep")
    add_copy(client, 1, format="Physical")
    _upload_save(client, 1, data=b"keep me")
    gone = create_game(client, title="Gone")
    add_copy(client, 2, format="Physical")
    _upload_save(client, 2, data=b"delete me")
    assert len(_stored_files(app)) == 2
    client.post(f"/games/{gone}/delete")
    assert len(_stored_files(app)) == 1
    sid = int(re.search(r"/saves/(\d+)/download", page(client, f"/games/{keep}")).group(1))
    assert client.get(f"/saves/{sid}/download").data == b"keep me"


def test_missing_save_file_is_flagged_not_crashing(client, app):
    game_id = create_game(client)
    add_copy(client, 1, format="Physical")
    _upload_save(client, 1)
    for f in _stored_files(app):
        os.unlink(os.path.join(_saves_dir(app), f))
    body = page(client, f"/games/{game_id}")
    assert "file missing" in body
    resp = client.get("/saves/1/download", follow_redirects=True)
    assert b"missing" in resp.data


def test_upload_limit_is_configurable(tmp_path, monkeypatch):
    from app import create_app
    monkeypatch.setenv("RETRO_MAX_UPLOAD_MB", "1")
    app = create_app(data_dir=tmp_path / "data")
    client = app.test_client()
    create_game(client)
    add_copy(client, 1, format="Physical")
    resp = _upload_save(client, 1, data=b"x" * (2 * 1024 * 1024))
    assert b"too large (limit 1 MB" in resp.data


def test_home_shows_save_vault_summary(client):
    create_game(client)
    add_copy(client, 1, format="Physical")
    assert "no saves kept yet" in page(client, "/")
    _upload_save(client, 1)
    assert "1 save kept" in page(client, "/")


# ====================================================== startup diagnostics

def test_unwritable_data_folder_gives_an_actionable_error(tmp_path, monkeypatch):
    """Regression for the Docker crash: a root-owned ./data used to surface as
    SQLite's bare 'unable to open database file'."""
    import db as db_module
    from app import create_app
    data = tmp_path / "data"
    data.mkdir()
    real_access = os.access
    monkeypatch.setattr(db_module.os, "access",
                        lambda p, mode: False if str(p) == str(data) else real_access(p, mode))
    with pytest.raises(db_module.DataDirNotWritableError) as exc:
        create_app(data_dir=data)
    msg = str(exc.value)
    assert str(data) in msg and "PUID/PGID" in msg and "chown" in msg


def test_cover_picker_shows_size_hint(client):
    game_id = create_game(client)
    body = page(client, f"/games/{game_id}")
    assert 'id="cover-hint"' in body and "3:4 portrait, 480 × 640+" in body
    assert 'aria-describedby="cover-hint"' in body   # read out with the file picker


# ================================================= typed links & hack folding

def _link(client, game_id, target_id, kind, title):
    resp = client.post(f"/games/{game_id}/edit", data={"title": title, "related_game_id": str(target_id),
                                                       "relation_kind": kind}, follow_redirects=True)
    assert resp.status_code == 200
    return resp


def _top_level_titles(body):
    """Titles of the games-list rows themselves (not games folded inside them)."""
    return [re.search(r'<div class="title-line">\s*<a href="/games/\d+">([^<]+)</a>', row).group(1)
            for row in _game_rows(body)]


def test_rom_hacks_fold_under_their_original_in_the_games_list(client):
    sm = create_game(client, title="Super Metroid")
    hack = create_game(client, title="Super Metroid Redesign", related_game_id=str(sm), relation_kind="rom_hack")
    remake = create_game(client, title="Metroid: Zero Mission")
    _link(client, remake, sm, "remake", "Metroid: Zero Mission")

    body = page(client, "/games")
    tops = _top_level_titles(body)
    assert "Super Metroid Redesign" not in tops               # folded...
    assert "Super Metroid" in tops and "Metroid: Zero Mission" in tops   # ...remakes are not
    assert "+1 ROM hack" in body and "Super Metroid Redesign" in body    # ...but listed under it
    assert "+ 1 hacks &amp; mods" in body                      # header count

    # original's page lists it; the hack's page says what it's a hack of
    assert "Hacks &amp; mods" in page(client, f"/games/{sm}")
    hack_page = page(client, f"/games/{hack}")
    assert "ROM hack of" in hack_page and "Super Metroid" in hack_page
    assert "Remake of" in page(client, f"/games/{remake}")


def test_filters_and_view_selector_unfold_hacks(client):
    sm = create_game(client, title="Super Metroid")
    create_game(client, title="Super Metroid Redesign", related_game_id=str(sm), relation_kind="rom_hack")
    body = page(client, "/games?q=Redesign")
    assert "Super Metroid Redesign" in _top_level_titles(body)
    assert "ROM hack of" in body and "listed as their own rows" in body

    assert "Super Metroid Redesign" in _top_level_titles(page(client, "/games?view=flat"))
    assert _top_level_titles(page(client, "/games?view=originals")) == ["Super Metroid"]
    assert _top_level_titles(page(client, "/games?view=variants")) == ["Super Metroid Redesign"]


def test_a_hack_of_a_hack_folds_under_the_original(client):
    sm = create_game(client, title="Super Metroid")
    h1 = create_game(client, title="Hack One", related_game_id=str(sm), relation_kind="rom_hack")
    create_game(client, title="Hack Of Hack One", related_game_id=str(h1), relation_kind="rom_hack")
    body = page(client, "/games")
    assert _top_level_titles(body) == ["Super Metroid"]
    assert "+2 ROM hacks" in body


def test_mixed_variant_kinds_are_labelled_hacks_and_mods(client):
    g = create_game(client, title="Doom")
    create_game(client, title="Sigil", related_game_id=str(g), relation_kind="mod")
    create_game(client, title="Freedoom-ish Hack", related_game_id=str(g), relation_kind="rom_hack")
    assert "+2 hacks &amp; mods" in page(client, "/games")


def test_links_cannot_loop(client):
    a = create_game(client, title="A")
    b = create_game(client, title="B", related_game_id=str(a), relation_kind="rom_hack")
    resp = _link(client, a, b, "rom_hack", "A")
    assert b"loop back" in resp.data
    resp = _link(client, a, a, "related", "A")
    assert b"loop back" in resp.data


def test_unknown_link_kind_is_rejected(client):
    a = create_game(client, title="A")
    resp = client.post("/games/new", data={"title": "B", "platform": "SNES", "related_game_id": str(a),
                                           "relation_kind": "nonsense"}, follow_redirects=True)
    assert b"kind of link" in resp.data


def test_hacks_show_in_backlog_as_their_own_entry_with_a_subtitle(client):
    sm = create_game(client, title="Super Metroid")
    hack = create_game(client, title="Redesign", related_game_id=str(sm), relation_kind="rom_hack")
    client.post(f"/games/{hack}/backlog/add")
    body = page(client, "/backlog")
    assert "Redesign" in body and "ROM hack of Super Metroid" in body


def test_home_counts_hacks_separately(client):
    sm = create_game(client, title="Super Metroid")
    create_game(client, title="Redesign", related_game_id=str(sm), relation_kind="rom_hack")
    body = page(client, "/")
    assert re.search(r'<span class="n">1</span><span class="label">games catalogued <br>\+ 1 hack ', body)


# ================================================= how a playthrough happened

def test_completion_records_played_on_and_method(client):
    game_id = create_game(client, title="Super Metroid")
    add_copy(client, 1, format="Digital ROM", location="NAS")
    client.post(f"/games/{game_id}/complete", data={"copy_id": "1", "played_on": "Steam Deck",
                                                    "method": "emulator", "hours_played": "7"})
    body = page(client, f"/games/{game_id}")
    assert "on Steam Deck" in body and ">Emulator<" in body and "Copy: Digital ROM" in body


def test_played_on_defaults_to_the_copys_own_system(client):
    game_id = create_game(client, title="Super Metroid", platform="SNES")
    add_copy(client, 1, format="Physical")
    client.post(f"/games/{game_id}/complete", data={"copy_id": "1", "method": "original_hardware"})
    assert "on SNES" in page(client, f"/games/{game_id}")


def test_played_on_default_uses_accessed_through_system(client):
    game_id = create_game(client, title="Final Fantasy VI", platform="SNES")
    add_copy(client, 1, format="Subscription", via_platform="Switch", is_owned="0")
    client.post(f"/games/{game_id}/complete", data={"copy_id": "1", "method": "official_rerelease"})
    assert "on Switch" in page(client, f"/games/{game_id}")


def test_unknown_method_is_rejected(client):
    game_id = create_game(client)
    resp = client.post(f"/games/{game_id}/complete", data={"method": "magic"}, follow_redirects=True)
    assert b"method from the list" in resp.data
    assert "Not marked as beaten yet" in page(client, f"/games/{game_id}")


def test_editing_a_completion_sets_played_on_and_method(client):
    game_id = create_game(client)
    add_copy(client, 1, format="Digital ROM")
    client.post(f"/games/{game_id}/complete", data={"completed_at": "2026-01-01"})
    client.post("/completions/1/edit", data={"completed_at": "2026-01-01", "copy_id": "1",
                                             "played_on": "MiSTer", "method": "fpga"})
    body = page(client, f"/games/{game_id}")
    assert "on MiSTer" in body and ">FPGA<" in body


def test_save_method_comes_from_its_completion_or_its_own_field(client):
    game_id = create_game(client)
    add_copy(client, 1, format="Digital ROM")
    client.post(f"/games/{game_id}/complete", data={"copy_id": "1", "method": "source_port",
                                                    "save": (BytesIO(b"clear"), "clear.sav")},
                content_type="multipart/form-data")
    _upload_save(client, 1, data=b"loose", name="loose.srm", method="emulator")
    body = page(client, f"/games/{game_id}")
    assert re.search(r"clear\.sav · [^<]*Source port", body)
    assert re.search(r"loose\.srm · [^<]*Emulator", body)


def test_stats_show_how_you_play(client):
    g1 = create_game(client, title="One", platform="SNES")
    add_copy(client, 1, format="Digital ROM")
    g2 = create_game(client, title="Two", platform="SNES")
    add_copy(client, 2, format="Physical")
    client.post(f"/games/{g1}/complete", data={"copy_id": "1", "method": "emulator",
                                               "played_on": "Steam Deck", "hours_played": "10"})
    client.post(f"/games/{g2}/complete", data={"copy_id": "2", "method": "original_hardware", "hours_played": "5"})
    client.post(f"/games/{g2}/complete", data={})      # no method recorded
    body = page(client, "/stats")
    assert "How you play" in body
    assert "Emulator" in body and "Original hardware" in body
    assert "Steam Deck" in body and "SNES" in body
    assert "1 completion without a method" in body


# ===================================================== stats by year & journal

def _beat(client, game_id, **data):
    data.setdefault("completed_at", "2026-05-01")
    resp = client.post(f"/games/{game_id}/complete", data=data)
    assert resp.status_code == 302


def test_year_journal_lines_show_system_method_hours_and_rating_but_not_notes(client):
    game_id = create_game(client, title="Chrono Trigger", platform="SNES")
    add_copy(client, 1, format="Digital ROM")
    _beat(client, game_id, completed_at="2026-03-14", copy_id="1", played_on="Steam Deck",
          method="emulator", hours_played="28", rating="9", notes="SECRET NOTE TEXT")
    body = page(client, "/stats/2026")
    row = body.split('class="journal-row"')[1].split("</li>")[0]
    assert "Mar 14" in row and "Chrono Trigger" in row
    assert 'class="pbadge pb-pc"' in row and ">Deck<" in row and 'title="Steam Deck"' in row
    assert "Emulator" in row and "28h" in row and "9/10" in row
    assert "SECRET NOTE TEXT" not in body


def test_journal_badge_defaults_to_the_copys_system(client):
    game_id = create_game(client, title="Super Metroid", platform="SNES")
    add_copy(client, 1, format="Physical")
    _beat(client, game_id, copy_id="1", method="original_hardware")
    row = page(client, "/stats/2026").split('class="journal-row"')[1]
    assert 'class="pbadge pb-nintendo"' in row and ">SNES<" in row


def test_year_numbers_separate_playthroughs_from_different_games(client):
    g1 = create_game(client, title="One")
    g2 = create_game(client, title="Two")
    _beat(client, g1, completed_at="2026-01-01", hours_played="10", rating="8")
    _beat(client, g1, completed_at="2026-06-01", hours_played="5", rating="10")
    _beat(client, g2, completed_at="2026-07-01")
    body = page(client, "/stats/2026")
    assert "3</span><span class=\"label\">playthroughs · 2 different games" in body
    assert ">15</span><span class=\"label\">hours logged" in body
    assert "Best of 2026" in body


def test_year_switcher_and_default_year(client):
    g = create_game(client)
    _beat(client, g, completed_at="2024-02-02")
    _beat(client, g, completed_at="2025-02-02")
    body = page(client, "/stats")
    # nothing in the current year yet -> opens on the latest year with data
    assert "2025 journal" in body
    assert 'href="/stats/2024"' in body and 'href="/stats/2025"' in body and 'href="/stats/all"' in body
    assert "2024 journal" in page(client, "/stats/2024")


def test_empty_year_page_still_renders(client):
    resp = client.get("/stats/1999")
    assert resp.status_code == 200 and b"Nothing beaten in 1999 yet" in resp.data


def test_all_time_has_top_rated_once_per_game_with_replay_count(client):
    g1 = create_game(client, title="Replayed Favourite")
    g2 = create_game(client, title="Good Game")
    _beat(client, g1, completed_at="2024-01-01", rating="8")
    _beat(client, g1, completed_at="2025-01-01", rating="10")
    _beat(client, g2, completed_at="2025-03-01", rating="9")
    body = page(client, "/stats/all")
    top = body.split('<ol class="top-rated">')[1].split("</ol>")[0]
    assert top.count("Replayed Favourite") == 1 and "beaten 2×" in top
    assert top.index("Replayed Favourite") < top.index("Good Game")
    assert "Beaten by year" in body and 'href="/stats/2024"' in body


def test_home_dashboard_shows_recently_beaten_and_hours_this_year(client):
    from datetime import date
    g = create_game(client, title="Fresh Clear", platform="SNES")
    add_copy(client, 1, format="Physical")
    _beat(client, g, completed_at=date.today().isoformat(), copy_id="1", hours_played="12.5", rating="9")
    body = page(client, "/")
    assert "Recently beaten" in body and "Fresh Clear" in body and ">SNES<" in body
    assert ">12</span><span class=\"label\">hours this year" in body or ">13</span><span class=\"label\">hours this year" in body
    assert 'class="stat stat-link" href="/stats"' in body


def test_platform_badges_classify_by_whole_words():
    from platforms import platform_info
    assert platform_info("Sega Genesis")["family"] == "sega"        # not 'nes' -> Nintendo
    assert platform_info("PlayStation 4") == {"name": "PlayStation 4", "short": "PS4",
                                              "family": "sony", "device": "console"}
    assert platform_info("Steam Deck")["device"] == "handheld"
    assert platform_info("PC")["device"] == "computer"
    assert platform_info("PC Engine")["family"] == "other"
    assert platform_info("Some Homebrew Thing")["family"] == "other"


def test_best_of_year_lists_each_game_once(client):
    g = create_game(client, title="Replayed")
    _beat(client, g, completed_at="2026-01-01", rating="10")
    _beat(client, g, completed_at="2026-02-01", rating="9")
    body = page(client, "/stats/2026")
    best = body.split("Best of 2026")[1].split("</ol>")[0]
    assert best.count("Replayed") == 1 and "10/10" in best
