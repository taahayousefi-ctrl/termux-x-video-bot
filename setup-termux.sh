#!/data/data/com.termux/files/usr/bin/sh
# One-time local configuration for Termux. Never commit the generated config file.
set -eu

CONFIG_DIR="${HOME}/.config"
CONFIG_FILE="${X_VIDEO_BOT_CONFIG:-${CONFIG_DIR}/x-video-bot.env}"

if [ -f "$CONFIG_FILE" ]; then
    printf 'تنظیمات خصوصی از قبل در %s وجود دارد. برای جلوگیری از بازنویسی متوقف شد.\n' "$CONFIG_FILE"
    exit 0
fi

printf 'توکن ربات BotFather را وارد کنید. هنگام نوشتن نمایش داده نمی‌شود: '
stty -echo
IFS= read -r BOT_TOKEN || true
stty echo
printf '\n'

case "$BOT_TOKEN" in
    ''|*[!A-Za-z0-9:_-]*)
        printf 'توکن نامعتبر است. هیچ فایلی ساخته نشد.\n' >&2
        exit 1
        ;;
esac

mkdir -p "$CONFIG_DIR"
umask 077
{
    printf "export BOT_TOKEN='%s'\n" "$BOT_TOKEN"
    printf '# پس از ارسال /id به ربات، شناسه را در خط زیر قرار دهید.\n'
    printf "# export ALLOWED_USER_IDS='123456789'\n"
} > "$CONFIG_FILE"
chmod 600 "$CONFIG_FILE"

printf 'تنظیمات فقط روی گوشی شما در %s ذخیره شد.\n' "$CONFIG_FILE"
printf 'اکنون برای اجرا از دستور زیر استفاده کنید:\n  ./start.sh\n'
printf 'سپس /id را به ربات بفرستید و ALLOWED_USER_IDS را در فایل تنظیمات اضافه کنید.\n'
