#!/usr/bin/env bash
# Rebuilds static/tailwind.css from the classes used in templates/ and static/.
#
# RUN THIS AFTER EDITING ANY TEMPLATE. Tailwind only emits the utility classes
# it can actually see, so a class added to a template without a rebuild will
# simply have no effect — the page renders unstyled in that spot.
#
#   ./build-css.sh          rebuild once
#   ./build-css.sh --watch  rebuild continuously while editing
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d node_modules ]; then
  echo "Installing build dependencies (first run only)…"
  npm install --no-audit --no-fund
fi

if [ "${1:-}" = "--watch" ]; then
  exec npx tailwindcss -c tailwind.config.js -i ./static/src/tailwind.css -o ./static/tailwind.css --watch
fi

npx tailwindcss -c tailwind.config.js -i ./static/src/tailwind.css -o ./static/tailwind.css --minify

# Tailwind skips the write when the output is byte-identical, which leaves the
# mtime stale. The app's startup freshness check compares that mtime against
# the templates, so without this touch a no-op build warns forever.
touch static/tailwind.css

printf 'Built static/tailwind.css (%s bytes)\n' "$(stat -c%s static/tailwind.css)"
