import math
import os
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, send_file, send_from_directory, abort)
from werkzeug.exceptions import RequestEntityTooLarge

import config
import db
import queries as q
import storage
import vocab
from platforms import platform_info
from hashing import hash_file, hash_bytes

# How many hex characters of the sha256 to use as a stored cover filename.
# 16 hex chars = 64 bits: collision risk is negligible for a personal
# library, and it keeps filenames short.
COVER_HASH_LEN = 16


class FormError(ValueError):
    """Bad user input. Routes catch it, flash the message, and redirect."""


def create_app(database_path=None, data_dir=None):
    app = Flask(__name__)
    data_dir = Path(data_dir) if data_dir else config.data_dir()
    app.config.update(
        SECRET_KEY=config.secret_key(),
        DATA_DIR=str(data_dir),
        DATABASE=str(database_path or data_dir / db.DB_FILENAME),
        COVERS_DIR=str(data_dir / "assets" / "covers"),
        SAVES_DIR=str(data_dir / "assets" / "saves"),
        MAX_UPLOAD_MB=config.max_upload_mb(),
        MAX_CONTENT_LENGTH=config.max_upload_mb() * 1024 * 1024,
        # Extra origins allowed to submit forms (only needed behind a reverse
        # proxy that rewrites the Host header). Comma-separated host[:port].
        TRUSTED_ORIGINS={h.strip() for h in
                         os.environ.get("RETRO_TRUSTED_ORIGINS", "").split(",") if h.strip()},
    )

    db.init_app(app)
    with app.app_context():
        db.init_db()

    register_security(app)
    register_template_helpers(app)
    register_routes(app)
    return app


# ===================================================================== security

def safe_next(target, fallback):
    """Only follow same-site relative redirects ('/backlog'), never an
    absolute or protocol-relative URL ('//evil.example')."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return fallback


def register_security(app):
    @app.before_request
    def same_origin_writes_only():
        # Any web page you visit could otherwise auto-submit a form to this
        # app (e.g. "delete game"). Browsers send Origin (or at least Referer)
        # on form posts, so reject writes that came from a different site.
        # Requests with neither header (curl, scripts, the test client) pass.
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return
        source = request.headers.get("Origin") or request.headers.get("Referer")
        if not source:
            return
        netloc = urlsplit(source).netloc
        if netloc != request.host and netloc not in app.config["TRUSTED_ORIGINS"]:
            abort(403, description="Cross-site form submission blocked.")

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(e):
        flash(f"That file is too large (limit {app.config['MAX_UPLOAD_MB']} MB; "
              "set RETRO_MAX_UPLOAD_MB to change it). Zip large save folders.", "error")
        return redirect(safe_next(_same_site_referrer(), url_for("home")))


def _same_site_referrer():
    ref = request.referrer
    if not ref:
        return None
    parts = urlsplit(ref)
    if parts.netloc != request.host:
        return None
    return parts.path + (f"?{parts.query}" if parts.query else "")


# ============================================================ input parsing

def _text(name):
    return request.form.get(name, "").strip() or None


def _multiline(name):
    value = request.form.get(name, "").replace("\r\n", "\n").strip()
    return value or None


def _number(name, label, cast=float, minimum=0, maximum=None):
    raw = request.form.get(name, "").strip()
    if not raw:
        return None
    try:
        value = cast(raw)
    except ValueError:
        raise FormError(f"{label} must be a number.")
    if isinstance(value, float) and not math.isfinite(value):
        raise FormError(f"{label} must be a number.")
    if minimum is not None and value < minimum:
        raise FormError(f"{label} can't be less than {minimum}.")
    if maximum is not None and value > maximum:
        raise FormError(f"{label} can't be more than {maximum}.")
    return value


def _rating():
    """1 to 10 in steps of 0.1 (8.5 is fine, 8.25 is not)."""
    raw = request.form.get("rating", "").strip()
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise FormError("Rating must be between 1 and 10.")
    if not value.is_finite() or not 1 <= value <= 10:
        raise FormError("Rating must be between 1 and 10.")
    if (value * 10) % 1 != 0:
        raise FormError("Rating can have at most one decimal place, like 8.5.")
    return float(value)


def _date(name, default=None):
    raw = request.form.get(name, "").strip()
    if not raw:
        return default
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        raise FormError("Dates must look like YYYY-MM-DD.")


def _url(name="url"):
    raw = _text(name)
    if not raw:
        return None
    parts = urlsplit(raw)
    # Only real web links: a "javascript:" or "data:" URL in an href would
    # run script when clicked, and Jinja's HTML escaping doesn't stop that.
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise FormError("Links must start with http:// or https://")
    return raw


def _is_owned():
    # The form sends a hidden "0" followed by the checkbox's "1" when ticked,
    # so the last value wins. A client that omits the field entirely gets
    # the default (owned).
    values = request.form.getlist("is_owned")
    return values[-1] == "1" if values else True


def _copy_fields():
    """Validate + collect copy fields. Call inside a transaction (it may
    create a platform for 'via')."""
    fields = dict(
        format=_text("format"),
        location=_text("location"),
        path=_text("path"),
        url=_url(),
        notes=_multiline("notes"),
        is_owned=_is_owned(),
    )
    if not (fields["format"] or fields["location"] or fields["path"] or fields["url"]):
        raise FormError("Give at least a format, a location, a file path, or a link "
                        "so the entry means something.")
    via = _text("via_platform")
    fields["via_platform_id"] = q.get_or_create_platform(via) if via else None
    return fields


def _release_fields():
    return dict(
        region=_text("region"),
        release_year=_number("release_year", "Release year", cast=int, minimum=1950, maximum=2100),
        publisher=_text("publisher"),
        developer=_text("developer"),
        serial=_text("serial"),
        notes=_multiline("notes"),
    )


def _method(field="method"):
    value = request.form.get(field, "").strip()
    if not value:
        return None
    if value not in vocab.METHODS:
        raise FormError("Pick a method from the list.")
    return value


def _relation():
    """(related_game_id, relation_kind) from the 'link to another game' fields."""
    related_game_id = request.form.get("related_game_id", type=int)
    if not related_game_id:
        return None, None
    if not q.get_game(related_game_id):
        raise FormError("The linked game no longer exists.")
    kind = request.form.get("relation_kind", "").strip() or "related"
    if kind not in vocab.RELATION_KINDS:
        raise FormError("Pick what kind of link this is from the list.")
    return related_game_id, kind


def _completion_fields(game_id):
    copy_id = request.form.get("copy_id", type=int)
    if copy_id:
        c = q.get_copy(copy_id)
        if not c or c["game_id"] != game_id:
            raise FormError("That copy doesn't belong to this game.")
    played_on = _text("played_on")
    return dict(
        completed_at=_date("completed_at", default=date.today().isoformat()),
        hours_played=_number("hours_played", "Hours played"),
        rating=_rating(),
        notes=_multiline("notes"),
        copy_id=copy_id or None,
        # Blank = "the copy's own system" (worked out when displayed).
        played_on_platform_id=q.get_or_create_platform(played_on) if played_on else None,
        method=_method(),
    )


def _completion_choice(game_id, field="completion_id"):
    completion_id = request.form.get(field, type=int)
    if completion_id:
        comp = q.get_completion(completion_id)
        if not comp or comp["game_id"] != game_id:
            raise FormError("That completion entry doesn't belong to this game.")
    return completion_id or None


def sniff_image_ext(data):
    """Decide the image type from the bytes themselves, not the filename."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


# =========================================================== template helpers

def copy_label(format=None, platform=None, region=None, via=None, location=None):
    """'Subscription — SNES (USA) through Switch · Closet'"""
    label = f"{format or 'Copy'} — {platform or '?'}"
    if region:
        label += f" ({region})"
    if via:
        label += f" through {via}"
    if location:
        label += f" · {location}"
    return label


def register_template_helpers(app):
    @app.template_filter("filesize")
    def filesize_filter(n):
        if n is None:
            return "—"
        n = float(n)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if n < 1024:
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} PB"

    @app.template_filter("hours")
    def hours_filter(n):
        """6.0 -> '6h', 6.5 -> '6.5h', unset -> '-'."""
        if n is None:
            return "-"
        return f"{float(n):g}h"

    @app.template_filter("copy_label")
    def copy_label_filter(c):
        return copy_label(c["format"], c["platform_name"], c["region"],
                          c["via_platform_name"], c["location"])

    @app.template_filter("platform_info")
    def platform_info_filter(name):
        return platform_info(name)

    @app.template_filter("month_day")
    def month_day_filter(iso):
        """'2026-10-04' -> 'Oct 4'."""
        try:
            d = date.fromisoformat(iso)
        except (TypeError, ValueError):
            return iso or ""
        return f"{d:%b} {d.day}"

    @app.template_filter("method_label")
    def method_label_filter(key):
        return vocab.method_label(key) or ""

    @app.template_filter("relation_phrase")
    def relation_phrase_filter(kind):
        return vocab.relation_phrase(kind)

    @app.template_filter("variants_summary")
    def variants_summary_filter(variants):
        return vocab.variants_summary([v["relation_kind"] for v in variants])

    @app.template_global("has_cover")
    def has_cover(filename):
        # True only when a cover is set AND its file is on disk. A game whose
        # file is missing (e.g. the database was moved to a new machine
        # without data/assets/covers) renders the "no cover" placeholder
        # instead of a broken image.
        if not filename:
            return False
        return (Path(app.config["COVERS_DIR"]) / Path(filename).name).is_file()

    @app.context_processor
    def inject_globals():
        return {
            "current_year": date.today().year,
            "METHODS": vocab.METHODS,
            "METHOD_HINTS": vocab.METHOD_HINTS,
            "RELATION_KINDS": vocab.RELATION_KINDS,
            "FOLDED_KINDS": vocab.FOLDED_KINDS,
        }


# =================================================================== routes

def register_routes(app):

    def covers_dir():
        return Path(app.config["COVERS_DIR"])

    def saves_dir():
        return Path(app.config["SAVES_DIR"])

    def store_save_upload(upload):
        try:
            return storage.store_upload(upload, saves_dir())
        except ValueError as e:
            raise FormError(str(e))

    def cleanup_save_files(stored_names):
        for name in set(stored_names):
            storage.remove_if_unreferenced(saves_dir(), name, q.save_file_in_use(name))

    def to_game(game_id):
        return redirect(url_for("game_detail", game_id=game_id))

    def game_or_404(game_id):
        game = q.get_game(game_id)
        if not game:
            abort(404)
        return game

    # ---------------------------------------------------------------- health

    @app.route("/healthz")
    def healthz():
        # Used by the Docker HEALTHCHECK: proves the app answers AND the
        # database is reachable.
        db.get_db().execute("SELECT 1").fetchone()
        return {"ok": True}

    # ------------------------------------------------------------------- home

    @app.route("/")
    def home():
        return render_template(
            "home.html",
            platform_counts=q.get_platform_counts(),
            recent=q.get_recent_games(limit=8),
            recently_beaten=q.get_recent_completions(limit=8),
            totals=q.get_totals(),
            health=q.archive_health_summary(),
            saves=q.saves_summary(),
        )

    # ------------------------------------------------------------------ games

    @app.route("/games")
    def games_list():
        filters = dict(
            q=request.args.get("q", "").strip() or None,
            platform_id=request.args.get("platform_id", type=int),
            location=request.args.get("location", "").strip() or None,
            format=request.args.get("format", "").strip() or None,
            tag_id=request.args.get("tag_id", type=int),
            ownership=request.args.get("ownership", "").strip() or None,
            sort=request.args.get("sort", "title"),
            view=request.args.get("view", "").strip() or None,
        )
        if filters["view"] not in (None, "flat", "originals", "variants"):
            filters["view"] = None
        games, grouped = q.list_games(**filters)
        return render_template(
            "games_list.html",
            games=games,
            grouped=grouped,
            folded_count=sum(len(g["variants"]) for g in games),
            filters=filters,
            platforms=q.get_platforms(),
            locations=q.get_distinct_locations(),
            formats=q.get_distinct_formats(),
            tags=q.get_tags_in_use(),
        )

    @app.route("/games/new", methods=["GET", "POST"])
    def game_new():
        def show_form(form):
            return render_template("game_form.html", platforms=q.get_platforms(),
                                   all_games=q.list_all_games(), tags=q.get_all_tags(), form=form)

        if request.method == "GET":
            return show_form({})

        title = _text("title")
        platform_name = _text("platform")
        if not title or not platform_name:
            flash("A title and platform are required.", "error")
            return show_form(request.form)
        try:
            with db.transaction():
                related_game_id, relation_kind = _relation()
                game_id = q.create_game(
                    title=title,
                    edition_note=_text("edition_note"),
                    related_game_id=related_game_id,
                    relation_kind=relation_kind,
                    length_hours=_number("length_hours", "Length"),
                )
                q.create_release(game_id=game_id,
                                 platform_id=q.get_or_create_platform(platform_name),
                                 **_release_fields())
                q.add_tags_from_text(game_id, request.form.get("tags", ""))
        except FormError as e:
            flash(str(e), "error")
            return show_form(request.form)
        flash(f'Added "{title}".', "success")
        return to_game(game_id)

    @app.route("/games/<int:game_id>")
    def game_detail(game_id):
        game = game_or_404(game_id)
        releases_data = [
            {"release": r, "copies": q.get_copies_for_release(r["id"])}
            for r in q.get_releases_for_game(game_id)
        ]
        completions = []
        for c in q.get_completions_for_game(game_id):
            row = dict(c)
            row["via_label"] = (copy_label(c["copy_format"], c["copy_platform_name"], c["copy_region"],
                                           c["copy_via_platform_name"], c["copy_location"])
                                if c["copy_id"] else None)
            completions.append(row)
        saves_by_copy, saves_by_completion = {}, {}
        for s in q.get_saves_for_game(game_id):
            row = dict(s)
            row["missing"] = not (saves_dir() / s["stored_name"]).is_file()
            saves_by_copy.setdefault(s["copy_id"], []).append(row)
            if s["completion_id"]:
                saves_by_completion.setdefault(s["completion_id"], []).append(row)
        return render_template(
            "game_detail.html",
            game=game,
            status=q.get_game_status(game_id),
            saves_by_copy=saves_by_copy,
            saves_by_completion=saves_by_completion,
            save_count=sum(len(v) for v in saves_by_copy.values()),
            max_upload_mb=app.config["MAX_UPLOAD_MB"],
            releases_data=releases_data,
            game_copies=q.get_copies_for_game(game_id),
            game_tags=q.get_tags_for_game(game_id),
            links=q.get_game_links(game_id),
            backlog_entry=q.get_backlog_entry_for_game(game_id),
            completions=completions,
            platforms=q.get_platforms(),
            all_games=q.list_all_games(exclude_game_id=game_id),
            locations=q.get_distinct_locations(),
            formats=q.get_distinct_formats(),
            tags=q.get_all_tags(),
            today=date.today().isoformat(),
        )

    @app.route("/games/<int:game_id>/edit", methods=["POST"])
    def game_edit(game_id):
        game_or_404(game_id)
        title = _text("title")
        if not title:
            flash("Title cannot be empty.", "error")
            return to_game(game_id)
        try:
            related_game_id, relation_kind = _relation()
            if related_game_id and (related_game_id == game_id
                                    or q.link_chain_reaches(related_game_id, game_id)):
                raise FormError("That link would loop back to this game. "
                                "Link it to the game it was made from instead.")
            with db.transaction():
                q.update_game(game_id, title=title, edition_note=_text("edition_note"),
                              related_game_id=related_game_id, relation_kind=relation_kind,
                              length_hours=_number("length_hours", "Length"))
        except FormError as e:
            flash(str(e), "error")
            return to_game(game_id)
        flash("Game updated.", "success")
        return to_game(game_id)

    @app.route("/games/<int:game_id>/delete", methods=["POST"])
    def game_delete(game_id):
        game = game_or_404(game_id)
        save_files = q.stored_save_names_for_game(game_id)
        with db.transaction():
            q.delete_game(game_id)
        cleanup_save_files(save_files)
        flash(f'Deleted "{game["title"]}".', "success")
        return redirect(url_for("games_list"))

    # ----------------------------------------------------------------- covers

    @app.route("/games/<int:game_id>/cover", methods=["POST"])
    def game_cover(game_id):
        game_or_404(game_id)
        file = request.files.get("cover")
        if not file or not file.filename:
            flash("No image selected.", "error")
            return to_game(game_id)
        data = file.read()
        if not data:
            flash("That file appears to be empty.", "error")
            return to_game(game_id)
        ext = sniff_image_ext(data)
        if not ext:
            flash("Unsupported image type. Use PNG, JPEG, GIF, or WebP.", "error")
            return to_game(game_id)

        # Content-addressed: the filename IS the hash of the bytes, so the
        # same image used for two games is stored once, and a stored file is
        # never overwritten with different content.
        filename = f"{hash_bytes(data)[:COVER_HASH_LEN]}.{ext}"
        covers_dir().mkdir(parents=True, exist_ok=True)
        dest = covers_dir() / filename
        if not dest.exists():
            dest.write_bytes(data)
        with db.transaction():
            q.set_game_cover(game_id, filename)
        flash("Cover image updated.", "success")
        return to_game(game_id)

    @app.route("/games/<int:game_id>/cover/clear", methods=["POST"])
    def game_cover_clear(game_id):
        game_or_404(game_id)
        # Only the pointer is cleared: another game may share the same
        # hash-named file. `flask prune-covers` removes truly unused files.
        with db.transaction():
            q.set_game_cover(game_id, None)
        flash("Cover image cleared.", "success")
        return to_game(game_id)

    @app.route("/covers/<path:filename>")
    def cover_file(filename):
        return send_from_directory(covers_dir(), filename, max_age=31536000)

    # ------------------------------------------------------------------- tags

    @app.route("/games/<int:game_id>/tags/add", methods=["POST"])
    def tag_add(game_id):
        game_or_404(game_id)
        names = request.form.get("name", "")
        with db.transaction():
            q.add_tags_from_text(game_id, names)
        return to_game(game_id)

    @app.route("/games/<int:game_id>/tags/<int:tag_id>/remove", methods=["POST"])
    def tag_remove(game_id, tag_id):
        game_or_404(game_id)
        with db.transaction():
            q.remove_tag_from_game(game_id, tag_id)
        return to_game(game_id)

    # --------------------------------------------------------------- releases

    @app.route("/games/<int:game_id>/releases/new", methods=["POST"])
    def release_new(game_id):
        game_or_404(game_id)
        platform_name = _text("platform")
        if not platform_name:
            flash("A platform is required for a new release.", "error")
            return to_game(game_id)
        try:
            with db.transaction():
                q.create_release(game_id=game_id,
                                 platform_id=q.get_or_create_platform(platform_name),
                                 **_release_fields())
        except FormError as e:
            flash(str(e), "error")
            return to_game(game_id)
        flash("Release added.", "success")
        return to_game(game_id)

    @app.route("/releases/<int:release_id>/edit", methods=["POST"])
    def release_edit(release_id):
        release = q.get_release(release_id)
        if not release:
            abort(404)
        try:
            with db.transaction():
                platform_name = _text("platform")
                platform_id = (q.get_or_create_platform(platform_name)
                               if platform_name else release["platform_id"])
                q.update_release(release_id, platform_id=platform_id, **_release_fields())
        except FormError as e:
            flash(str(e), "error")
            return to_game(release["game_id"])
        flash("Release updated.", "success")
        return to_game(release["game_id"])

    @app.route("/releases/<int:release_id>/delete", methods=["POST"])
    def release_delete(release_id):
        release = q.get_release(release_id)
        if not release:
            abort(404)
        if len(q.get_releases_for_game(release["game_id"])) <= 1:
            flash("A game needs at least one release. Delete the game instead.", "error")
            return to_game(release["game_id"])
        n_saves = q.count_saves_for_release(release_id)
        if n_saves:
            flash(f"This release's copies have {n_saves} saved game(s). Move them to another "
                  "copy or delete them first — save files can't be recovered.", "error")
            return to_game(release["game_id"])
        with db.transaction():
            q.delete_release(release_id)
        flash("Release deleted.", "success")
        return to_game(release["game_id"])

    # ----------------------------------------------------------------- copies

    @app.route("/releases/<int:release_id>/copies/new", methods=["POST"])
    def copy_new(release_id):
        release = q.get_release(release_id)
        if not release:
            abort(404)
        try:
            with db.transaction():
                fields = _copy_fields()
                copy_id = q.create_copy(release_id=release_id, **fields)
        except FormError as e:
            flash(str(e), "error")
            return to_game(release["game_id"])

        if fields["path"] and request.form.get("compute_hash_now"):
            try:
                sha256, crc32, size = hash_file(fields["path"])
                with db.transaction():
                    q.update_copy_hash(copy_id, sha256, crc32, size)
                    q.set_copy_verify_status(copy_id, "verified")
            except FileNotFoundError:
                flash("Copy saved, but no file was found at that path to hash.", "error")
            except OSError as e:
                flash(f"Copy saved, but couldn't read the file: {e.strerror or e}.", "error")
        flash("Copy added.", "success")
        return to_game(release["game_id"])

    @app.route("/copies/<int:copy_id>/edit", methods=["POST"])
    def copy_edit(copy_id):
        copy = q.get_copy(copy_id)
        if not copy:
            abort(404)
        try:
            with db.transaction():
                path_changed = q.update_copy(copy_id, **_copy_fields())
        except FormError as e:
            flash(str(e), "error")
            return to_game(copy["game_id"])
        msg = "Copy updated."
        if path_changed and copy["sha256"]:
            msg += " The file path changed, so the old hash was cleared."
        flash(msg, "success")
        return to_game(copy["game_id"])

    @app.route("/copies/<int:copy_id>/delete", methods=["POST"])
    def copy_delete(copy_id):
        copy = q.get_copy(copy_id)
        if not copy:
            abort(404)
        n_saves = q.count_saves_for_copy(copy_id)
        if n_saves:
            flash(f"This copy has {n_saves} saved game(s). Move them to another copy or "
                  "delete them first — save files can't be recovered.", "error")
            return to_game(copy["game_id"])
        with db.transaction():
            q.delete_copy(copy_id)
        flash("Copy removed.", "success")
        return to_game(copy["game_id"])

    @app.route("/copies/<int:copy_id>/download")
    def copy_download(copy_id):
        copy = q.get_copy(copy_id)
        if not copy:
            abort(404)
        if not copy["path"]:
            flash("This copy has no file path on record.", "error")
            return to_game(copy["game_id"])
        p = Path(copy["path"])
        if not p.is_file():
            flash("No file found at the recorded path.", "error")
            return to_game(copy["game_id"])
        return send_file(p, as_attachment=True)

    @app.route("/copies/<int:copy_id>/compute_hash", methods=["POST"])
    def copy_compute_hash(copy_id):
        copy = q.get_copy(copy_id)
        if not copy:
            abort(404)
        if not copy["path"]:
            flash("This copy has no file path on record.", "error")
            return to_game(copy["game_id"])
        try:
            sha256, crc32, size = hash_file(copy["path"])
        except FileNotFoundError:
            flash("No file found at the recorded path.", "error")
            return to_game(copy["game_id"])
        except OSError as e:
            flash(f"Couldn't read the file: {e.strerror or e}.", "error")
            return to_game(copy["game_id"])
        with db.transaction():
            q.update_copy_hash(copy_id, sha256, crc32, size)
            q.set_copy_verify_status(copy_id, "verified")
        flash("Hash computed and saved.", "success")
        return to_game(copy["game_id"])

    @app.route("/copies/<int:copy_id>/verify", methods=["POST"])
    def copy_verify(copy_id):
        copy = q.get_copy(copy_id)
        if not copy:
            abort(404)
        if not copy["path"]:
            flash("This copy has no file path on record.", "error")
            return to_game(copy["game_id"])
        try:
            sha256, crc32, size = hash_file(copy["path"])
        except FileNotFoundError:
            with db.transaction():
                q.set_copy_verify_status(copy_id, "missing")
            flash("File is missing at the recorded path.", "error")
            return to_game(copy["game_id"])
        except OSError as e:
            # Unreadable is not missing: keep the recorded status as it was.
            flash(f"Couldn't read the file: {e.strerror or e}.", "error")
            return to_game(copy["game_id"])

        with db.transaction():
            if copy["sha256"] and copy["sha256"] != sha256:
                q.set_copy_verify_status(copy_id, "modified")
                modified = True
            else:
                q.update_copy_hash(copy_id, sha256, crc32, size)
                q.set_copy_verify_status(copy_id, "verified")
                modified = False
        if modified:
            flash("Warning: file contents no longer match the recorded hash.", "error")
        else:
            flash("Verified — file matches.", "success")
        return to_game(copy["game_id"])

    # ---------------------------------------------------------------- backlog

    @app.route("/backlog")
    def backlog_list():
        filters = {
            "platform_id": request.args.get("platform_id", type=int),
            "location": request.args.get("location", "").strip() or None,
            "tag_id": request.args.get("tag_id", type=int),
            "sort": request.args.get("sort", "rank") or "rank",
        }
        is_filtered = bool(filters["platform_id"] or filters["location"] or filters["tag_id"])
        is_rank_sort = filters["sort"] == "rank"
        entries = q.list_active_backlog(**filters)
        return render_template(
            "backlog.html",
            entries=entries,
            total_hours=sum(e["length_hours"] or 0 for e in entries),
            unknown_length=sum(1 for e in entries if e["length_hours"] is None),
            platforms=q.get_platforms(),
            locations=q.get_distinct_locations(),
            tags=q.get_tags_in_use(),
            filters=filters,
            is_filtered=is_filtered,
            is_rank_sort=is_rank_sort,
            # Manual reordering only where on-screen neighbours are the real
            # neighbours: the full, unfiltered list in rank order.
            can_reorder=is_rank_sort and not is_filtered,
        )

    @app.route("/games/<int:game_id>/backlog/add", methods=["POST"])
    def backlog_add(game_id):
        game_or_404(game_id)
        with db.transaction():
            q.add_to_backlog(game_id)
        flash("Added to backlog.", "success")
        return redirect(safe_next(request.form.get("next"), url_for("game_detail", game_id=game_id)))

    @app.route("/games/<int:game_id>/backlog/remove", methods=["POST"])
    def backlog_remove(game_id):
        game_or_404(game_id)
        with db.transaction():
            q.remove_from_backlog(game_id, reason="manual")
        flash("Removed from backlog.", "success")
        return redirect(safe_next(request.form.get("next"), url_for("game_detail", game_id=game_id)))

    @app.route("/backlog/<int:backlog_id>/move", methods=["POST"])
    def backlog_move(backlog_id):
        with db.transaction():
            q.move_backlog_item(backlog_id, request.form.get("direction"))
        return redirect(url_for("backlog_list"))

    @app.route("/backlog/reorder", methods=["POST"])
    def backlog_reorder():
        data = request.get_json(silent=True) or {}
        with db.transaction():
            ok = q.reorder_backlog(data.get("order", []))
        if not ok:
            return jsonify({"ok": False, "error": "Order must list every active backlog entry exactly once."}), 400
        return jsonify({"ok": True})

    # ------------------------------------------------------------ completions

    @app.route("/games/<int:game_id>/complete", methods=["POST"])
    def complete_add(game_id):
        game_or_404(game_id)
        upload = request.files.get("save")
        stored = None
        try:
            fields = _completion_fields(game_id)
            if upload and upload.filename:
                if not fields["copy_id"]:
                    raise FormError("To keep a save with this completion, pick which copy "
                                    "you used (Copy used).")
                stored = store_save_upload(upload)
            with db.transaction():
                completion_id = q.add_completion(game_id, **fields)
                if stored:
                    stored_name, sha, size = stored
                    filename = storage.safe_display_name(upload.filename)
                    q.create_save(copy_id=fields["copy_id"], completion_id=completion_id,
                                  label=_text("save_label") or filename,
                                  filename=filename, stored_name=stored_name, sha256=sha, size=size)
        except FormError as e:
            flash(str(e), "error")
            return to_game(game_id)
        finally:
            if stored:  # no-op if the save row was written
                cleanup_save_files([stored[0]])
        flash("Marked as beaten." + (" Save file kept." if stored else ""), "success")
        return to_game(game_id)

    @app.route("/completions/<int:completion_id>/edit", methods=["POST"])
    def complete_edit(completion_id):
        comp = q.get_completion(completion_id)
        if not comp:
            abort(404)
        try:
            with db.transaction():
                q.update_completion(completion_id, **_completion_fields(comp["game_id"]))
        except FormError as e:
            flash(str(e), "error")
            return to_game(comp["game_id"])
        flash("Completion updated.", "success")
        return to_game(comp["game_id"])

    @app.route("/completions/<int:completion_id>/delete", methods=["POST"])
    def complete_delete(completion_id):
        comp = q.get_completion(completion_id)
        if not comp:
            abort(404)
        with db.transaction():
            q.delete_completion(completion_id)
        flash("Completion entry removed.", "success")
        return to_game(comp["game_id"])

    # ------------------------------------------------------------------ saves

    @app.route("/copies/<int:copy_id>/saves/new", methods=["POST"])
    def save_new(copy_id):
        copy = q.get_copy(copy_id)
        if not copy:
            abort(404)
        upload = request.files.get("save")
        stored = None
        try:
            if not upload or not upload.filename:
                raise FormError("Choose a save file to upload.")
            completion_id = _completion_choice(copy["game_id"])
            method = _method()
            stored = store_save_upload(upload)
            stored_name, sha, size = stored
            filename = storage.safe_display_name(upload.filename)
            with db.transaction():
                q.create_save(copy_id=copy_id, completion_id=completion_id, method=method,
                              label=_text("label") or filename, notes=_multiline("notes"),
                              filename=filename, stored_name=stored_name, sha256=sha, size=size)
        except FormError as e:
            flash(str(e), "error")
            return to_game(copy["game_id"])
        finally:
            if stored:
                cleanup_save_files([stored[0]])
        flash("Save uploaded.", "success")
        return to_game(copy["game_id"])

    @app.route("/saves/<int:save_id>/download")
    def save_download(save_id):
        save = q.get_save(save_id)
        if not save:
            abort(404)
        if not (saves_dir() / save["stored_name"]).is_file():
            flash("That save's file is missing from data/assets/saves.", "error")
            return to_game(save["game_id"])
        return send_from_directory(saves_dir(), save["stored_name"], as_attachment=True,
                                   download_name=save["filename"],
                                   mimetype="application/octet-stream")

    @app.route("/saves/<int:save_id>/edit", methods=["POST"])
    def save_edit(save_id):
        save = q.get_save(save_id)
        if not save:
            abort(404)
        try:
            copy_id = request.form.get("copy_id", type=int) or save["copy_id"]
            target = q.get_copy(copy_id)
            if not target or target["game_id"] != save["game_id"]:
                raise FormError("A save can only move to another copy of the same game.")
            with db.transaction():
                q.update_save(save_id, copy_id=copy_id,
                              label=_text("label") or save["filename"],
                              notes=_multiline("notes"),
                              completion_id=_completion_choice(save["game_id"]),
                              method=_method())
        except FormError as e:
            flash(str(e), "error")
            return to_game(save["game_id"])
        flash("Save updated.", "success")
        return to_game(save["game_id"])

    @app.route("/saves/<int:save_id>/delete", methods=["POST"])
    def save_delete(save_id):
        save = q.get_save(save_id)
        if not save:
            abort(404)
        with db.transaction():
            q.delete_save(save_id)
        cleanup_save_files([save["stored_name"]])
        flash("Save deleted.", "success")
        return to_game(save["game_id"])

    # ------------------------------------------------------------------ stats

    @app.route("/stats")
    @app.route("/stats/<int:year>")
    def stats(year=None):
        """One year: its numbers, its journal of playthroughs, best of the
        year, and how you played. Opens on this year, or the most recent
        year with completions."""
        years = q.get_completion_years()
        if year is None:
            this_year = date.today().year
            year = this_year if (this_year in years or not years) else years[0]
        journal = q.get_year_journal(year)
        return render_template("stats.html", mode="year", year=year, years=years,
                               journal=journal, summary=q.summarize_journal(journal))

    @app.route("/stats/all")
    def stats_all():
        data = q.get_stats()
        max_hours = max([row["total_hours"] or 0 for row in data["by_year"]], default=0)
        return render_template("stats.html", mode="all", years=q.get_completion_years(),
                               stats=data, max_hours=max_hours)


if __name__ == "__main__":
    # Native run: `python3 app.py`. Reads .env if present. Defaults to
    # localhost-only with the debugger off; see .env.example.
    config.load_dotenv()
    create_app().run(host=config.host(), port=config.port(), debug=config.debug())
