#!/data/data/com.termux/files/usr/bin/sh
# Start the bot with local private configuration; the token is never kept in Git.
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CONFIG_FILE="${X_VIDEO_BOT_CONFIG:-${HOME}/.config/x-video-bot.env}"

if [ ! -f "$CONFIG_FILE" ]; then
    printf 'تنظیمات محلی پیدا نشد. ابتدا یک بار ./setup-termux.sh را اجرا کنید.\n' >&2
    exit 1
fi

# shellcheck disable=SC1090
. "$CONFIG_FILE"
export BOT_TOKEN
if [ "${ALLOWED_USER_IDS+x}" = "x" ]; then
    export ALLOWED_USER_IDS
fi

cd "$PROJECT_DIR"
exec python bot.py
