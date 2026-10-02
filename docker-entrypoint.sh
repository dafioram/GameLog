#!/bin/sh
# Container entrypoint: make the data folder usable, then run the app as
# PUID:PGID (from .env) instead of root.
#
# Why: when ./data doesn't exist on the host, Docker creates it owned by
# root, and a non-root app then can't create its database ("unable to open
# database file"). Starting as root just long enough to fix ownership is the
# standard home-server image pattern.
set -eu

DATA_DIR="${RETRO_DATA_DIR:-/data}"
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    # Hand over anything in the data folder that isn't already owned by
    # PUID:PGID (usually just the folder itself on first run).
    if ! find "$DATA_DIR" \( ! -user "$PUID" -o ! -group "$PGID" \) \
            -exec chown "$PUID:$PGID" {} + ; then
        echo "retro-archive: warning: couldn't change ownership of $DATA_DIR" \
             "(read-only, NFS root_squash, or similar). The app will explain" \
             "if it can't write there." >&2
    fi
    if [ "$PUID" = "0" ]; then
        exec "$@"
    fi
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi

# Already started as a non-root user (e.g. a `user:` override): just run.
exec "$@"
