#!/bin/bash
# Validata's start, in the order Striim's entrypoint uses (services/striim/images/striim/files/entrypoint.sh): Derby (the
# historian) first, the license and names into startUp.properties, sksConfig.sh once, then the server.
# conf/ is a volume, so the one-time setup runs once per volume; the properties are set on every start.
set -eu

log() { echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

: "${VALIDATA_HOME:?VALIDATA_HOME not set}"
: "${VALIDATA_ADMIN_PASSWORD:?not set}" "${VALIDATA_SYS_PASSWORD:?not set}" "${VALIDATA_KEYSTORE_PASSWORD:?not set}"
PROPS="${VALIDATA_HOME%/}/conf/startUp.properties"
MARKER="${VALIDATA_HOME%/}/conf/.sks_configured"
DBMS=/var/validata/sbin/validata-dbms
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}"

# Set KEY=value: drop any line (commented or not) for the key, then append it. No sed, so a key with / & | is safe.
set_prop() {
  grep -vE "^[#[:space:]]*$1=" "$PROPS" > "$PROPS.new" || true
  printf '%s=%s\n' "$1" "$2" >> "$PROPS.new"
  mv "$PROPS.new" "$PROPS"
}

[ -f "$PROPS" ] || cp "${PROPS}.default" "$PROPS"
# The server refuses to start, with no error, when the cluster or company name is blank.
[ -n "${VALIDATA_CLUSTER_NAME:-}" ] && [ -n "${VALIDATA_COMPANY_NAME:-}" ] || die "VALIDATA_CLUSTER_NAME and VALIDATA_COMPANY_NAME are required"
set_prop ValidataClusterName "$VALIDATA_CLUSTER_NAME"
set_prop CompanyName "$VALIDATA_COMPANY_NAME"
[ -z "${VALIDATA_PRODUCT_KEY:-}" ] || set_prop ProductKey "$VALIDATA_PRODUCT_KEY"
[ -z "${VALIDATA_LICENCE_KEY:-}" ] || set_prop LicenceKey "$VALIDATA_LICENCE_KEY"
[ -z "${VALIDATA_MEM_MIN:-}" ] || set_prop MEM_MIN "$VALIDATA_MEM_MIN"
[ -z "${VALIDATA_MEM_MAX:-}" ] || set_prop MEM_MAX "$VALIDATA_MEM_MAX"
set_prop MetaDataRepositoryLocation "localhost:1527"  # the Derby started below

log "Starting Derby (validata-dbms)..."
mkdir -p /var/validata/logs
nohup "$DBMS" start > /var/validata/logs/validata-dbms-start.log 2>&1 &
for _ in $(seq 1 60); do nc -z localhost 1527 && break; sleep 2; done
nc -z localhost 1527 || { cat /var/validata/logs/validata-dbms-start.log >&2; die "Derby didn't open 1527"; }

if [ -f "$MARKER" ]; then
  log "sksConfig.sh already run for this conf volume: skipping"
else
  log "Running sksConfig.sh (Derby historian)..."
  # -a/-s/-k are PASSWORDS (the admin user is always named "admin"); -v true activates the admin user.
  "$VALIDATA_HOME/bin/sksConfig.sh" -a "$VALIDATA_ADMIN_PASSWORD" -s "$VALIDATA_SYS_PASSWORD" -t Derby \
    -k "$VALIDATA_KEYSTORE_PASSWORD" -v true
  touch "$MARKER"
fi

# A stop (SIGTERM) stops the server, then Derby, so Derby closes its files cleanly.
stop() { log "Stopping..."; kill -TERM "$SERVER" 2>/dev/null || true; wait "$SERVER" 2>/dev/null || true; "$DBMS" stop >/dev/null 2>&1 || true; exit 0; }
trap stop TERM INT
log "Starting Validata server..."
bash -l "$VALIDATA_HOME/bin/validata-server.sh" &
SERVER=$!
wait "$SERVER"
