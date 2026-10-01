import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app  # noqa: E402


@pytest.fixture
def app(tmp_path):
    # Every test gets its own data folder (DB + covers) under pytest's tmp
    # dir, so the suite never touches the real data/ folder.
    application = create_app(data_dir=tmp_path / "data")
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def create_game(client, title="Chrono Trigger", platform="SNES", **extra):
    data = {"title": title, "platform": platform}
    data.update(extra)
    resp = client.post("/games/new", data=data)
    assert resp.status_code == 302, resp.data[:500]
    return int(resp.headers["Location"].rstrip("/").split("/")[-1])


def add_copy(client, release_id, **fields):
    resp = client.post(f"/releases/{release_id}/copies/new", data=fields)
    assert resp.status_code == 302
    return resp


def page(client, url):
    """GET a page as text, after flushing queued flash messages (several
    POSTs in a row accumulate flashes that could pollute assertions)."""
    client.get("/stats")
    return client.get(url).data.decode()
