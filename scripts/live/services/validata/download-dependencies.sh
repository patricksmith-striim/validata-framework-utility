#!/usr/bin/env bash
# Download the Validata packages for the Docker image ONCE, into images/validata/deps/. The Dockerfile bind-mounts that
# folder at build time instead of fetching 0.9 GB on every (emulated) rebuild.
#
#   Run once before `docker compose build`:  ./download-dependencies.sh
#
# Idempotent: a file already present (non-empty) is skipped. A partial download is resumed, and a connection that moves
# under 100 kB/s for 60 s is dropped and retried, so a dead connection cannot hold the download forever.
# VALIDATA_VERSION (default 1.1.0) names the release; it must match compose.yaml's build arg.
set -euo pipefail

VALIDATA_VERSION="${VALIDATA_VERSION:-1.1.0}"
DEPS_DIR="$(cd "$(dirname "$0")" && pwd)/images/validata/deps"
BASE="https://striim-downloads.s3.us-west-1.amazonaws.com/Validata/${VALIDATA_VERSION}"
mkdir -p "$DEPS_DIR"

fetch() {
  local name="$1" dest="$DEPS_DIR/$1"
  if [ -s "$dest" ]; then echo "  present  $name"; return 0; fi
  echo "  fetching $name"
  curl -fL -C - --retry 5 --retry-all-errors --speed-limit 100000 --speed-time 60 --progress-bar -o "$dest.part" "$BASE/$name"
  mv "$dest.part" "$dest"
  echo "  done     $name ($(du -h "$dest" | cut -f1))"
}

echo "Validata ${VALIDATA_VERSION} packages -> $DEPS_DIR"

# Fail fast with a clear message when this release isn't published, rather than a bare curl error.
status="$(curl -fsL -o /dev/null -w '%{http_code}' "$BASE/validata-dbms-${VALIDATA_VERSION}-Linux.deb" 2>/dev/null || true)"
case "$status" in
  200|301|302) ;;
  *) echo "ERROR: Validata ${VALIDATA_VERSION} is not available for download ($BASE/): pick another release." >&2; exit 1 ;;
esac

fetch "validata-dbms-${VALIDATA_VERSION}-Linux.deb"
fetch "validata-node-${VALIDATA_VERSION}-Linux.deb"
echo "All dependencies present in $DEPS_DIR"
