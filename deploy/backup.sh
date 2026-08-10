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

  # `rsync -a` preserves the SOURCE directory's mtime, so every mirror was
  # stamped with the upload directory's date — the day the first recording
  # landed — regardless of when the mirror was actually taken. The retention
  # sweep below matches on mtime, so once that date passed KEEP_DAYS, every
  # nightly run would delete every mirror including the one it had just
  # written, and still print "ok", because verification only inspected the
  # database dump.
  #
  # Stamping the mirror with the time it was made is what makes the retention
  # line below mean what it says.
  touch "$DEST/audio-$STAMP"
fi

find "$DEST" -maxdepth 1 -name 'db-*.dump' -mtime "+$KEEP_DAYS" -delete
find "$DEST" -maxdepth 1 -name 'audio-*' -type d -mtime "+$KEEP_DAYS" -exec rm -rf {} +

# A backup nobody has restored is a hope, not a backup. Two checks, because a
# dump can be perfectly readable and still contain nothing — the classic silent
# backup failure is a file that grows every night and restores an empty schema.
pg_restore --list "$DEST/db-$STAMP.dump" > /dev/null

# The audio mirror is verified too, and for the same reason the retention bug
# above went unnoticed: the "ok" line only ever described the database dump, so
# a mirror that was emptied — or never written — looked exactly like success.
audio_files=0
if [ -d /srv/qaboom/uploads ]; then
  src_files="$(find /srv/qaboom/uploads -type f | wc -l)"
  audio_files="$(find "$DEST/audio-$STAMP" -type f 2>/dev/null | wc -l)"
  if [ "$audio_files" -lt "$src_files" ]; then
    echo "$(date -Is) FAIL audio-$STAMP holds $audio_files of $src_files files" >&2
    exit 1
  fi
fi

rows="$(pg_restore --data-only --table=calls -f - "$DEST/db-$STAMP.dump" 2>/dev/null \
        | awk '/^COPY /{f=1;next} /^\\\.$/{f=0} f{c++} END{print c+0}')"
if [ "$rows" -lt 1 ]; then
  echo "$(date -Is) FAIL db-$STAMP.dump restores zero calls — investigate" >&2
  exit 1
fi

echo "$(date -Is) ok  db-$STAMP.dump  ${rows} calls  ${audio_files} audio files  $(du -sh "$DEST" | cut -f1) total"
