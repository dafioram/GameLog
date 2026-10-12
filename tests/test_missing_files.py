"""What happens when the database is moved (or restored) without the files
it points at: cover art, save files, and library game files.

Each test builds its own app and data folder, so nothing here depends on
what another test left behind. Ids are 1 because every test starts from an
empty database."""
import base64
import hashlib
import os
import re
import shutil
from io import BytesIO

import db
from app import create_app
from conftest import add_copy, create_game, page

LIBRARY_PATH = "/library/SNES/Chrono Trigger (USA).sfc"


def _png():
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )


def _upload_cover(client, game_id):
    return client.post(f"/games/{game_id}/cover",
                       data={"cover": (BytesIO(_png()), "box.png")},
                       content_type="multipart/form-data", follow_redirects=True)


def _upload_save(client, copy_id, data=b"SAVE-DATA-1234", name="slot.srm", **form):
    form = {"save": (BytesIO(data), name), **form}
    return client.post(f"/copies/{copy_id}/saves/new", data=form,
                       content_type="multipart/form-data", follow_redirects=True)


def _cover_name(body):
    return next(iter(_covers(body)), None)


def _covers(body):
    return re.findall(r"/covers/([a-f0-9]+\.\w+)", body)


def _saved_name(data, name):
    return hashlib.sha256(data).hexdigest()[:16] + os.path.splitext(name)[1]


# ================================================ whole database moved alone

def test_database_moved_without_any_files_still_works(tmp_path):
    old = tmp_path / "old"
    rom = tmp_path / "library" / "Chrono Trigger (USA).sfc"
    rom.parent.mkdir()
    rom.write_bytes(b"ROM" * 100)
    app1 = create_app(data_dir=old)
    app1.config["TESTING"] = True
    c1 = app1.test_client()

    game_id = create_game(c1, title="Chrono Trigger", platform="SNES")
    add_copy(c1, 1, format="Digital ROM", path=str(rom), compute_hash_now="1")
    _upload_cover(c1, game_id)
    _upload_save(c1, 1, label="Before Lavos")
    assert _cover_name(page(c1, f"/games/{game_id}"))

    # Only the database file comes across to the new machine.
    new = tmp_path / "new"
    new.mkdir()
    shutil.copy(old / db.DB_FILENAME, new / db.DB_FILENAME)
    rom.unlink()                                  # and the game folder didn't come across

    app2 = create_app(data_dir=new)
    app2.config["TESTING"] = True
    c2 = app2.test_client()

    for path in ["/", "/games", "/backlog", "/stats", f"/games/{game_id}", "/games/new"]:
        assert c2.get(path).status_code == 200, path

    body = page(c2, f"/games/{game_id}")
    assert "No cover art" in body
    assert "file missing" in body                 # save file
    assert "Before Lavos" in body                 # the save record itself survives
    assert "Chrono Trigger" in body
    assert b"No file found" in c2.get("/copies/1/download", follow_redirects=True).data
    assert b"is missing" in c2.get("/saves/1/download", follow_redirects=True).data
    # The library folder didn't come across either, so the copy's recorded
    # "verified" status is stale until someone runs Verify.
    assert "status-tag verified" in body
    assert b"File is missing" in c2.post("/copies/1/verify", follow_redirects=True).data
    assert "status-tag missing" in page(c2, f"/games/{game_id}")


# ================================================================ cover art

def test_missing_cover_is_placeholder_on_every_page(client, app):
    game_id = create_game(client)
    _upload_cover(client, game_id)
    name = _cover_name(page(client, f"/games/{game_id}"))
    os.unlink(os.path.join(app.config["COVERS_DIR"], name))

    for path in ["/", "/games", "/stats", f"/games/{game_id}"]:
        assert name not in page(client, path), path


def test_clear_cover_works_on_a_dangling_pointer(client, app):
    game_id = create_game(client)
    _upload_cover(client, game_id)
    name = _cover_name(page(client, f"/games/{game_id}"))
    os.unlink(os.path.join(app.config["COVERS_DIR"], name))

    client.post(f"/games/{game_id}/cover/clear")
    body = page(client, f"/games/{game_id}")
    assert "No cover art" in body
    assert "Clear cover" not in body


def test_new_cover_upload_recreates_a_deleted_covers_folder(client, app):
    game_id = create_game(client)
    shutil.rmtree(app.config["COVERS_DIR"], ignore_errors=True)
    assert b"Cover image updated" in _upload_cover(client, game_id).data
    assert _cover_name(page(client, f"/games/{game_id}"))


# ================================================================= saves

def test_only_the_missing_save_is_flagged(client, app):
    game_id = create_game(client)
    add_copy(client, 1, format="Physical")
    _upload_save(client, 1, data=b"AAAA", name="a.srm", label="Keep")
    _upload_save(client, 1, data=b"BBBB", name="b.srm", label="Lost")
    os.unlink(os.path.join(app.config["SAVES_DIR"], _saved_name(b"BBBB", "b.srm")))

    body = page(client, f"/games/{game_id}")
    assert body.count("file missing") == 1
    assert "Keep" in body and "Lost" in body


def test_a_save_with_missing_file_can_still_be_deleted(client, app):
    game_id = create_game(client)
    add_copy(client, 1, format="Physical")
    _upload_save(client, 1, label="Gone")
    shutil.rmtree(app.config["SAVES_DIR"])

    resp = client.post("/saves/1/delete", follow_redirects=True)
    assert resp.status_code == 200 and b"Save deleted" in resp.data
    assert "Gone" not in page(client, f"/games/{game_id}")


def test_a_save_with_missing_file_can_be_relabelled(client, app):
    game_id = create_game(client)
    add_copy(client, 1, format="Physical")
    _upload_save(client, 1, label="Old name")
    shutil.rmtree(app.config["SAVES_DIR"])

    resp = client.post("/saves/1/edit", data={"label": "New name", "copy_id": "1"},
                       follow_redirects=True)
    assert b"Save updated" in resp.data
    assert "New name" in page(client, f"/games/{game_id}")


def test_uploading_a_save_recreates_a_missing_saves_folder(client, app):
    game_id = create_game(client)
    add_copy(client, 1, format="Physical")
    shutil.rmtree(app.config["SAVES_DIR"], ignore_errors=True)
    assert b"Save uploaded" in _upload_save(client, 1, label="Fresh").data
    assert "Fresh" in page(client, f"/games/{game_id}")


# ============================================================ library files

def test_library_file_actions_report_missing_without_crashing(client):
    game_id = create_game(client)
    resp = add_copy(client, 1, format="Digital ROM", path=LIBRARY_PATH, compute_hash_now="1")
    assert resp.status_code == 302                # copy is still saved
    assert "Chrono Trigger" in page(client, f"/games/{game_id}")

    assert b"No file found" in client.post("/copies/1/compute_hash",
                                           follow_redirects=True).data
    assert b"File is missing" in client.post("/copies/1/verify", follow_redirects=True).data
    assert "status-tag missing" in page(client, f"/games/{game_id}")


def test_library_path_that_is_a_folder_is_treated_as_missing(client, tmp_path):
    game_id = create_game(client)
    add_copy(client, 1, format="Digital ROM", path=str(tmp_path))
    assert b"File is missing" in client.post("/copies/1/verify", follow_redirects=True).data
    assert "status-tag missing" in page(client, f"/games/{game_id}")


def test_unreadable_library_file_is_reported_not_a_server_error(client, tmp_path, monkeypatch):
    # chmod can't make a file unreadable when the tests run as root, so make
    # hashing raise the error an unreadable file produces.
    import app as app_module
    game_id = create_game(client)
    rom = tmp_path / "rom.nes"
    rom.write_bytes(b"ROM" * 10)
    add_copy(client, 1, format="Digital ROM", path=str(rom), compute_hash_now="1")
    assert "status-tag verified" in page(client, f"/games/{game_id}")

    def unreadable(path):
        raise PermissionError(13, "Permission denied", str(path))
    monkeypatch.setattr(app_module, "hash_file", unreadable)

    resp = client.post("/copies/1/verify", follow_redirects=True)
    assert resp.status_code == 200
    assert b"read the file: Permission denied" in resp.data
    # Unreadable is not the same as missing: the recorded status is kept.
    assert "status-tag verified" in page(client, f"/games/{game_id}")

    resp = client.post("/copies/1/compute_hash", follow_redirects=True)
    assert resp.status_code == 200 and b"read the file: Permission denied" in resp.data


def test_unreadable_file_when_adding_a_copy_still_saves_the_copy(client, tmp_path, monkeypatch):
    import app as app_module
    game_id = create_game(client)
    rom = tmp_path / "rom.nes"
    rom.write_bytes(b"ROM" * 10)

    def unreadable(path):
        raise PermissionError(13, "Permission denied", str(path))
    monkeypatch.setattr(app_module, "hash_file", unreadable)

    resp = add_copy(client, 1, format="Digital ROM", path=str(rom), compute_hash_now="1")
    assert resp.status_code == 302
    body = page(client, f"/games/{game_id}")
    assert str(rom) in body                       # the copy itself was saved
    assert "status-tag unchecked" in body
