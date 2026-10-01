# Retro Archive

A personal preservation ledger for a retro game collection. It is not a ROM
launcher and not an emulator front-end. It tracks what you have, how you can
play it (a cartridge, a ROM on the NAS, a Steam license, a source port, a
web-playable version), what you've beaten, and what to play next.

You enter everything by hand. There is no directory scanner, and the app
never moves or modifies your game files. It only reads one when you ask it
to hash, verify, or download that file.

## Running it

There are two ways to run it. Both read settings from a `.env` file
(see [Settings](#settings-env)).

### Option A: Docker (recommended for access from other PCs)

```bash
cd retro-archive
cp .env.example .env        # then edit: port, time zone, your game folder, PUID/PGID
mkdir -p data               # create it as *you*; if Docker creates it, it's root-owned
docker compose up -d --build
```

Open **http://\<this-machine's-IP\>:5050** from any PC on your network, or
**http://localhost:5050** on the machine running it. By default the container
is published on all of the host's network interfaces (`RETRO_BIND_ADDRESS=0.0.0.0`).
Set it to `127.0.0.1` to keep the app private to the machine running it.

- **Data:** `./data` on the host is mounted at `/data`. It holds the
  database and cover art. Back up this folder. If you get a "permission
  denied" error at startup, `./data` was created by Docker as root:
  `sudo chown -R $(id -u):$(id -g) data`.
- **Game files:** `RETRO_LIBRARY_DIR` (default `./library`) is mounted
  **read-only** at `/library`. The app sees container paths, so a copy's
  file path must be entered as `/library/SNES/Chrono Trigger (USA).sfc`,
  not the host path. You can point `RETRO_LIBRARY_DIR` at a NAS mount.
- **File ownership:** the container runs as `PUID:PGID` (default 1000:1000)
  so files it writes to `./data` belong to you. Set these to the output of
  `id -u` and `id -g`.
- **Server:** gunicorn, one process with four threads, since SQLite has a
  single writer. A health check calls `/healthz`.

Common commands:

```bash
docker compose logs -f                                   # watch logs
docker compose down                                      # stop
docker compose up -d --build                             # rebuild after updating the code
docker compose exec retro-archive flask --app wsgi prune-covers --dry-run
```

### Option B: Native Python

Requires Python 3.9+.

```bash
cd retro-archive
pip install -r requirements.txt
cp .env.example .env        # optional; the defaults work
python3 app.py
```

Open **http://127.0.0.1:5050**. Running natively, the app listens on
localhost only and the debugger is off. To reach it from other machines,
set `RETRO_HOST=0.0.0.0` in `.env`. Only set `RETRO_DEBUG=1` on a machine
nobody else can reach.

For a native setup that other people use, run the same server the container
does: `pip install gunicorn && gunicorn -c gunicorn.conf.py wsgi:app`.

Either way, the database is created on first run at `data/retro_archive.db`
and seeded with a list of common platforms.

## Settings (`.env`)

`.env.example` documents every setting. Copy it to `.env` and edit it.
`.env` is gitignored; `.env.example` is the file to commit. Real environment
variables always take precedence over `.env`.

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `RETRO_PORT` | both | `5050` | Port you browse to |
| `RETRO_SECRET_KEY` | both | random per start | Signs the flash-message cookie |
| `RETRO_TRUSTED_ORIGINS` | both | — | Extra `host[:port]` allowed to submit forms (reverse proxy only) |
| `RETRO_HOST` | native | `127.0.0.1` | Interface to listen on (`0.0.0.0` = LAN) |
| `RETRO_DEBUG` | native | `0` | Werkzeug debugger and auto-reload |
| `RETRO_DATA_DIR` | native | `./data` | Database and covers location |
| `RETRO_BIND_ADDRESS` | Docker | `0.0.0.0` | Host interface the port is published on |
| `RETRO_DATA_PATH` | Docker | `./data` | Host folder mounted at `/data` |
| `RETRO_LIBRARY_DIR` | Docker | `./library` | Host folder mounted read-only at `/library` |
| `PUID` / `PGID` | Docker | `1000` | User the container runs as |
| `TZ` | Docker | `UTC` | Time zone, so "date completed" defaults to your local date |

## How the data is organized

Games, releases, and copies are three separate layers:

- A **game** is what you play and track: backlog, completions, tags,
  cover art, and length. A remake or rework is a **separate game**,
  cross-linked through "Related game" (the link shows on both games' pages),
  so beating one doesn't mark the other as beaten.
- A **release** is a regional or platform edition (USA SNES cart, Japan
  Super Famicom, DS re-release). Beating any release counts as beating the
  game.
- A **copy** is one concrete way you can play a release. It has a format,
  an optional "played via" platform, a location, an optional file path, an
  optional **link**, free-text **notes**, and an **"I own this"** checkbox.

Some examples of copies:

| You have… | Format | Via | Location / path / link | Owned? |
|---|---|---|---|---|
| The cartridge | Physical | — | Closet box 3 | ✔ |
| A ROM backup on the NAS | Digital ROM | — | NAS | ✔ |
| A ROM the app can read | Digital ROM | — | path `/library/SNES/…` | ✔ |
| A decomp / source port (e.g. SM64 → PC) | Source port | PC | Steam Deck; notes: build, required ROM | ✔ |
| A fan translation | Patched ROM | — | NAS; notes: patch name and version | ✔ |
| A browser-playable version | Web | — | link `https://…` | ✘ |
| A SNES game through Switch Online | Subscription | Switch | — | ✘ |

The rule used to decide between a copy and a new game: **if beating it
counts as beating the original, it's a copy.** Decomps, fan translations,
and ports are copies. A rom hack with new content, or a rework, is its own
game (tag it "Rom hack" and link it to the original).

**Owned vs. playable.** Any copy makes a game *playable*. A copy with
"I own this" ticked makes it *owned*. Untick the box for web links and
subscription access. Owned games are always also playable. Each game
shows one status: **Owned**, **Playable** (link or subscription access
only), or **—** (no copies yet).

**Platforms include "via".** Filtering the games list or backlog by
*Switch* also shows SNES games you play through Switch Online. The home
page counts games per platform the same way.

Only a **title** and **platform** are required to add a game.

## Features

- **Games list:** one row per game, showing every platform it's on, its
  status, its length, and Backlog/Beaten badges. Filter by platform
  (including "via"), location, format, tag, and status (Owned /
  Playable / Not playable). Sort by title, first release year, recently
  added, or length.
- **Cover art:** set per game. Choosing an image uploads and sets it
  immediately; "Clear cover" removes it. Images are checked by their
  content, not their file name (PNG, JPEG, GIF, WebP, up to 16 MB).
- **Tags:** set per game (genre, "Rom hack", "Co-op", …), case-insensitive.
  You can enter several, comma-separated, on the Add-game form. A tag no
  game uses anymore is deleted automatically.
- **Backlog:** your ranked "play this (again) someday" list. Reorder by
  dragging or with the arrow buttons. Reordering is only available in the
  full, unfiltered Rank view; filtered or length-sorted views show each
  game's real rank but can't be reordered. A dashed line marks your top 10,
  and the list shows the total estimated hours. Beating a game removes it
  from the backlog automatically.
- **Length:** an estimated or typical completion time per game, in the
  spirit of HowLongToBeat. It appears on the games list and backlog and can
  be sorted on. Games with no length show "-" and always sort last.
- **Completion history:** a log of every playthrough, so the same game can
  be beaten more than once. Each entry has a date, hours played, a 1–10
  rating, notes, and optionally **which copy you played it on**
  ("via Source port — Nintendo 64 via PC"). Entries can be edited.
- **Integrity checking:** only for copies with a file path. "Compute hash"
  stores SHA-256, CRC32, and the file size. "Verify" re-hashes the file and
  marks it verified, modified, or missing. Changing a copy's path clears its
  old hash.
- **Stats:** games beaten and hours played per year, average rating, and
  your top-rated games.

## Project layout

```
app.py               Flask app factory and routes (python3 app.py runs it natively)
wsgi.py              Production entry point: gunicorn wsgi:app / flask --app wsgi ...
config.py            RETRO_* settings and a small .env reader
db.py                Connection, transactions, schema versioning, CLI commands
queries.py           All the SQL
hashing.py           Chunked SHA-256 / CRC32 for large files
schema.sql           Current schema (version 1)
seed_platforms.py    Default platform list
gunicorn.conf.py     Server settings (used by Docker)
Dockerfile           Container image
docker-compose.yml   Docker Compose setup (ports and volumes come from .env)
.env.example         Documented settings template
templates/           Jinja2 templates (server-rendered, no JS framework)
static/              CSS, plus a little progressive-enhancement JS
data/                Database + data/assets/covers/ (entirely gitignored, auto-created)
library/             Default Docker mount point for game files (gitignored)
tests/               pytest suite
```

## Data, backups, and upgrades

- **Back up `data/`.** It contains the whole database and all cover images.
- Covers are saved as `data/assets/covers/<hash>.<ext>`, named by a hash of
  the image's contents, so an image shared by several games is stored once.
  Because of that, clearing a cover only removes the game's reference to
  it; the file stays. To delete files nothing uses anymore:
  `flask --app wsgi prune-covers` (add `--dry-run` to preview).
- **Schema versioning:** the database stores its schema version
  (`PRAGMA user_version`). Future schema changes will come with numbered
  migrations in `db.py` that upgrade your database automatically.
- **Coming from the earlier prototype:** this version doesn't migrate
  databases created before schema versioning. The app refuses to start and
  explains why, rather than corrupting the file. Move or delete
  `data/retro_archive.db` (and optionally `data/assets/covers/`) to start
  fresh.

## Security notes

This is a single-user app with no login. Anyone who can reach its port can
change or delete anything in it, and can download any file the app can read
by adding a copy that points at it. In Docker, that means only what you
mount: `/library` (read-only) and `/data`. So:

- Keep it on your home network. Don't forward the port to the internet.
- The built-in protections are inexpensive and always on:
  - form submissions from other websites are rejected (an origin check);
  - copy links must be `http(s)`;
  - redirects only go to pages within the app;
  - the server sends `nosniff` and frame-blocking headers.

## Testing

```bash
pip install pytest
pytest tests/ -v
```

Every test uses its own temporary data folder, so the suite never touches
your real `data/`. The tests cover:

- full create/edit/delete flows and input validation;
- owned vs. playable, and copy links, notes, and editing;
- platform filters that include "via";
- covers stored per game and deduplicated by hash;
- backlog ordering, including that reordering a filtered view is refused;
- completions recording which copy was played;
- the remake and regional-release rules;
- the cross-site form block;
- schema versioning and the settings / `.env` handling.

The UI has also been tested in a real Chromium browser against the gunicorn
server, connecting over the machine's network IP the way another PC would.
