#!/usr/bin/env bash
# Nightly backup: database dump + the uploaded audio that reports cite as evidence.
#
# Both halves matter. A report's evidence quotes a recording; restoring the
# database without the audio leaves a compliance record whose evidence cannot
# be played back, which for this product is only half a restore.
#
# Install:  crontab -l | { cat; echo "15 3 * * * /srv/qaboom/deploy/backup.sh"; } | crontab -
set -euo pipefail

DEST="${BACKUP_DIR:-/home/claude/backups/qaboom}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"

# Credentials come from the app's own .env — one place to rotate, and the file
# is already root/claude-readable only.
set -a; . /srv/qaboom/.env; set +a
DB_URL="${DATABASE_URL:?DATABASE_URL missing from /srv/qaboom/.env}"

# pg_dump reads the URL directly, so the password never lands in the process
# list of a separate psql invocation.
pg_dump --no-owner --format=custom --dbname="$DB_URL" --file="$DEST/db-$STAMP.dump"

# Audio: incremental hard-link mirror. A nightly copy of an append-mostly
# directory would otherwise duplicate every historical recording every night.
if [ -d /srv/qaboom/uploads ]; then
  LATEST="$(ls -1d "$DEST"/audio-* 2>/dev/null | tail -1 || true)"
  rsync -a --delete \
    ${LATEST:+--link-dest="$LATEST"} \
    /srv/qaboom/uploads/ "$DEST/audio-$STAMP/"
fi

find "$DEST" -maxdepth 1 -name 'db-*.dump' -mtime "+$KEEP_DAYS" -delete
find "$DEST" -maxdepth 1 -name 'audio-*' -type d -mtime "+$KEEP_DAYS" -exec rm -rf {} +

# A backup nobody has restored is a hope, not a backup. Two checks, because a
# dump can be perfectly readable and still contain nothing — the classic silent
# backup failure is a file that grows every night and restores an empty schema.
pg_restore --list "$DEST/db-$STAMP.dump" > /dev/null

rows="$(pg_restore --data-only --table=calls -f - "$DEST/db-$STAMP.dump" 2>/dev/null \
        | awk '/^COPY /{f=1;next} /^\\\.$/{f=0} f{c++} END{print c+0}')"
if [ "$rows" -lt 1 ]; then
  echo "$(date -Is) FAIL db-$STAMP.dump restores zero calls — investigate" >&2
  exit 1
fi

echo "$(date -Is) ok  db-$STAMP.dump  ${rows} calls  $(du -sh "$DEST" | cut -f1) total"
