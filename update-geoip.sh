#!/bin/bash
# =================================================================
#  GeoIP Database Atomic Updater
#  Ensures zero-downtime and failsafe database updates.
# =================================================================

set -Eeuo pipefail
umask 077

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CONFIG_FILE=${GEOIP_CONFIG_FILE:-/etc/geoipblock.conf}
GEOIP_SOURCE=dbip
MAXMIND_ACCOUNT_ID=""
MAXMIND_LICENSE_KEY=""
if [ -f "$CONFIG_FILE" ]; then
    # Root-managed shell configuration; never install a user-writable file here.
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
fi

DB_DIR=${GEOIP_DB_DIR:-/usr/share/xt_geoip}
TMP_BUILD_DIR="${DB_DIR}_new"
TEMP_DL_DIR=""
LOG_TAG="geoip-updater"

# 失敗時のハンドラ
error_handler() {
    local exit_code=$?
    local line_number=$1
    logger -t "$LOG_TAG" -p user.err "ERROR: Database update failed at line $line_number with exit code $exit_code. Existing database preserved."
    exit "$exit_code"
}

cleanup() {
    [ ! -d "$TMP_BUILD_DIR" ] || rm -rf "$TMP_BUILD_DIR"
    [ -z "$TEMP_DL_DIR" ] || rm -rf "$TEMP_DL_DIR"
}

fail() {
    logger -t "$LOG_TAG" -p user.err "ERROR: $*"
    exit 1
}

find_helper() {
    local name=$1 candidate
    if command -v "$name"; then return; fi
    for candidate in "/usr/libexec/xtables-addons/$name" "/usr/lib/xtables-addons/$name"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return
        fi
    done
    return 1
}

trap 'error_handler $LINENO' ERR

# 0. 排他制御 (Exclusive Lock)
LOCK_FILE=${GEOIP_LOCK_FILE:-/var/run/geoipblock_update.lock}
exec 9> "$LOCK_FILE"
if ! flock -n 9; then
    logger -t "$LOG_TAG" -p user.err "ERROR: Another update process is already running. Exiting."
    exit 1
fi

# Only the lock owner may clean up a build directory.
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

XT_GEOIP_BUILD=$(find_helper xt_geoip_build) || fail "xt_geoip_build not found. Install xtables-addons-common."
case "$GEOIP_SOURCE" in
    dbip)
        XT_GEOIP_DL=$(find_helper xt_geoip_dl) || fail "xt_geoip_dl not found. Install xtables-addons-common."
        ;;
    maxmind)
        [[ "$MAXMIND_ACCOUNT_ID" =~ ^[0-9]+$ ]] || fail "Set MAXMIND_ACCOUNT_ID in $CONFIG_FILE."
        [[ "$MAXMIND_LICENSE_KEY" =~ ^[a-zA-Z0-9_]+$ ]] || fail "Set a valid MAXMIND_LICENSE_KEY in $CONFIG_FILE."
        command -v curl >/dev/null || fail "curl is required for MaxMind."
        command -v python3 >/dev/null || fail "python3 is required for MaxMind."
        ;;
    *) fail "Unknown GEOIP_SOURCE: $GEOIP_SOURCE (use dbip or maxmind)." ;;
esac

# 1. 作業用ディレクトリの準備
rm -rf "$TMP_BUILD_DIR"
mkdir -p "$TMP_BUILD_DIR"

# 2. 一時ディレクトリの作成と移動
TEMP_DL_DIR=$(mktemp -d)
cd "$TEMP_DL_DIR"

# 3. データのダウンロードと一時ディレクトリへのビルド
logger -t "$LOG_TAG" "Starting GeoIP database download and build..."

download_database() {
    if [ "$GEOIP_SOURCE" = dbip ]; then
        "$XT_GEOIP_DL"
    else
        # Keep credentials out of process arguments, URLs and logs.
        printf 'user = "%s:%s"\n' "$MAXMIND_ACCOUNT_ID" "$MAXMIND_LICENSE_KEY" |
            curl --disable --config - --fail --silent --show-error --location \
                --proto '=https' --proto-redir '=https' \
                --connect-timeout 30 --max-time 600 \
                --output maxmind.zip \
                'https://download.maxmind.com/geoip/databases/GeoLite2-Country-CSV/download?suffix=zip'
    fi
}

# Retry logic for download (resilience against remote API drops)
MAX_RETRIES=3
RETRY_COUNT=0
DOWNLOAD_SUCCESS=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if download_database; then
        DOWNLOAD_SUCCESS=1
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT+1))
    logger -t "$LOG_TAG" -p user.warn "$GEOIP_SOURCE download failed (attempt $RETRY_COUNT/$MAX_RETRIES)."
    if [ "$RETRY_COUNT" -lt "$MAX_RETRIES" ]; then sleep 15; fi
done

if [ "$DOWNLOAD_SUCCESS" -ne 1 ]; then
    logger -t "$LOG_TAG" -p user.err "ERROR: GeoIP database download failed after $MAX_RETRIES attempts. Aborting update. Your existing firewall rules and DB are completely untouched."
    exit 1
fi

if [ "$GEOIP_SOURCE" = maxmind ]; then
    python3 "$SCRIPT_DIR/maxmind-to-dbip.py" maxmind.zip dbip-country-lite.csv
fi

"$XT_GEOIP_BUILD" -D "$TMP_BUILD_DIR"

# Require nonempty output for both address families before replacing the DB.
for family in 4 6; do
    if [ -z "$(find "$TMP_BUILD_DIR" -type f -name "*.iv$family" -size +0c -print -quit)" ]; then
        fail "Build produced no IPv$family ranges; existing database preserved."
    fi
done
chmod 755 "$TMP_BUILD_DIR"
find "$TMP_BUILD_DIR" -type d -exec chmod 755 {} +
find "$TMP_BUILD_DIR" -type f -exec chmod 644 {} +

# 4. アトミック・スワップ (ディレクトリの入れ替え)
# ビルドが成功した（ここまで到達した）場合のみ、本番環境を更新する
mkdir -p "$DB_DIR"
# 古いバックアップを消して、現在の本番をバックアップにする (もしもの時のため)
rm -rf "${DB_DIR}.old"
[ -d "$DB_DIR" ] && mv "$DB_DIR" "${DB_DIR}.old"

# 新しいビルドを本番にする
if mv "$TMP_BUILD_DIR" "$DB_DIR"; then
    # 5. 後始末
    cd /
    rm -rf "$TEMP_DL_DIR"
    # Note: We keep ${DB_DIR}.old for emergency manual rollbacks.
    
    # 6. 成功ログ
    logger -t "$LOG_TAG" "GeoIP Database updated successfully and atomically swapped. Backup preserved at ${DB_DIR}.old."
else
    # ロールバック
    logger -t "$LOG_TAG" -p user.err "ERROR: Failed to swap new database. Rolling back to previous version."
    [ -d "${DB_DIR}.old" ] && mv "${DB_DIR}.old" "$DB_DIR"
    rm -rf "$TMP_BUILD_DIR"
    rm -rf "$TEMP_DL_DIR"
    exit 1
fi
