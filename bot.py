#!/usr/bin/env python3
"""Lightweight X/Twitter video downloader bot for Telegram and Termux.

This program intentionally uses only Python's standard library. It downloads
native MP4 variants exposed by the public FxEmbed API and uploads them to the
chat through the Telegram Bot API. It does not transcode, merge streams, or
use FFmpeg.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
from http.client import HTTPSConnection
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
FX_API_BASE = "https://api.fxtwitter.com/2/status/"
TELEGRAM_API_BASE = "https://api.telegram.org/bot"
DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "downloads"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(49 * 1024 * 1024)))
POLL_TIMEOUT = 50
HTTP_TIMEOUT = 90
CHUNK_SIZE = 256 * 1024
USER_AGENT = "TermuxXVideoBot/1.0 (+https://github.com)"

# Recognizes x.com, twitter.com, www and mobile links, including x.com/i/status/ID.
STATUS_URL_RE = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:x\.com|twitter\.com)/[^/\s?#]+/status(?:es)?/(\d+)(?:[^\s]*)?",
    re.IGNORECASE,
)


class BotError(Exception):
    """A user-facing error that can be reported safely in Telegram."""


class DownloadTooLarge(BotError):
    """Raised when a video variant crosses the upload budget while downloading."""


def log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def parse_allowed_users(raw_value: str) -> set[int]:
    users: set[int] = set()
    for value in raw_value.split(","):
        value = value.strip()
        if value:
            try:
                users.add(int(value))
            except ValueError:
                raise SystemExit("ALLOWED_USER_IDS باید شامل شناسه‌های عددیِ جداشده با کاما باشد.")
    return users


ALLOWED_USER_IDS = parse_allowed_users(os.environ.get("ALLOWED_USER_IDS", ""))


def telegram_url(method: str) -> str:
    return f"{TELEGRAM_API_BASE}{BOT_TOKEN}/{method}"


def telegram_json(method: str, fields: dict[str, Any] | None = None, timeout: int = HTTP_TIMEOUT) -> Any:
    """Call a JSON Telegram Bot API method using form-encoded standard-library HTTP."""
    encoded = urlencode(fields or {}, doseq=True).encode("utf-8")
    request = Request(
        telegram_url(method),
        data=encoded,
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise BotError(f"خطای تلگرام ({exc.code}): {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise BotError("ارتباط با تلگرام برقرار نشد؛ اینترنت را بررسی کنید.") from exc

    if not payload.get("ok"):
        raise BotError(payload.get("description", "پاسخ ناموفق از تلگرام."))
    return payload.get("result")


def send_message(chat_id: int, text: str) -> None:
    telegram_json("sendMessage", {"chat_id": chat_id, "text": text[:4096]})


def send_action(chat_id: int, action: str) -> None:
    try:
        telegram_json("sendChatAction", {"chat_id": chat_id, "action": action}, timeout=20)
    except BotError as exc:
        log(f"ارسال وضعیت چت ناموفق بود: {exc}")


def get_updates(offset: int | None) -> list[dict[str, Any]]:
    fields: dict[str, Any] = {
        "timeout": POLL_TIMEOUT,
        "allowed_updates": json.dumps(["message"]),
    }
    if offset is not None:
        fields["offset"] = offset
    result = telegram_json("getUpdates", fields, timeout=POLL_TIMEOUT + 20)
    return result if isinstance(result, list) else []


def extract_status_id(text: str) -> str | None:
    match = STATUS_URL_RE.search(text)
    return match.group(1) if match else None


def fetch_status(status_id: str) -> dict[str, Any]:
    request = Request(
        f"{FX_API_BASE}{status_id}",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            raise BotError("این پست پیدا نشد، خصوصی است یا دیگر در دسترس نیست.") from exc
        raise BotError(f"دریافت اطلاعات پست از X ناموفق بود (HTTP {exc.code}).") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise BotError("اطلاعات پست X قابل دریافت نیست؛ چند لحظه بعد دوباره تلاش کنید.") from exc

    if payload.get("code") != 200 or not isinstance(payload.get("status"), dict):
        raise BotError("پست X یا رسانهٔ آن قابل دسترسی نیست.")
    return payload["status"]


def is_mp4(url: str, container: str = "") -> bool:
    path_without_query = url.split("?", 1)[0].lower()
    return container.lower() == "mp4" or path_without_query.endswith(".mp4")


def video_candidates(video: dict[str, Any]) -> list[tuple[int, str]]:
    """Return unique MP4 URLs from best to lowest advertised bitrate."""
    candidates: list[tuple[int, str]] = []
    for variant in video.get("formats", []):
        if not isinstance(variant, dict):
            continue
        url = variant.get("url")
        if isinstance(url, str) and is_mp4(url, str(variant.get("container", ""))):
            candidates.append((int(variant.get("bitrate") or 0), url))

    primary_url = video.get("url")
    if isinstance(primary_url, str) and is_mp4(primary_url, str(video.get("format", ""))):
        candidates.append((int(video.get("bitrate") or 0), primary_url))

    unique: dict[str, int] = {}
    for bitrate, url in candidates:
        unique[url] = max(unique.get(url, 0), bitrate)
    return sorted(((bitrate, url) for url, bitrate in unique.items()), reverse=True)


def extract_videos(status: dict[str, Any]) -> list[dict[str, Any]]:
    media = status.get("media")
    if not isinstance(media, dict):
        return []
    videos = media.get("videos")
    if isinstance(videos, list):
        return [item for item in videos if isinstance(item, dict)]
    all_media = media.get("all", [])
    if isinstance(all_media, list):
        return [item for item in all_media if isinstance(item, dict) and item.get("type") == "video"]
    return []


def remote_size(url: str) -> int | None:
    """Return Content-Length when the CDN provides it; otherwise return None."""
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}, method="HEAD")
    try:
        with urlopen(request, timeout=30) as response:
            value = response.headers.get("Content-Length")
            return int(value) if value and value.isdigit() else None
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None


def download_with_limit(url: str, destination: Path) -> int:
    """Download an MP4 while enforcing the upload budget without loading it into memory."""
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    written = 0
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT) as response, destination.open("wb") as file:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise DownloadTooLarge
                file.write(chunk)
    except DownloadTooLarge:
        destination.unlink(missing_ok=True)
        raise
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        destination.unlink(missing_ok=True)
        raise BotError("دانلود فایل ویدیو ناموفق بود.") from exc
    return written


def download_best_fitting_video(video: dict[str, Any], destination: Path) -> tuple[Path, int]:
    candidates = video_candidates(video)
    if not candidates:
        raise BotError("برای این ویدیو نسخهٔ MP4 سازگار پیدا نشد.")

    skipped_for_size = 0
    for _bitrate, url in candidates:
        size = remote_size(url)
        if size is not None and size > MAX_UPLOAD_BYTES:
            skipped_for_size += 1
            continue
        try:
            downloaded = download_with_limit(url, destination)
            return destination, downloaded
        except DownloadTooLarge:
            skipped_for_size += 1
            continue

    if skipped_for_size:
        raise BotError("هیچ نسخهٔ MP4 این ویدیو در سقف حجم تعیین‌شده برای ارسال در تلگرام جا نشد.")
    raise BotError("دانلود هیچ نسخهٔ MP4 این ویدیو ممکن نشد.")


def multipart_upload(chat_id: int, path: Path, method: str, file_field: str, caption: str) -> None:
    """Stream a file to Telegram using multipart/form-data and no third-party client."""
    boundary = f"----TermuxXVideoBot{secrets.token_hex(12)}"
    file_name = "x-video.mp4"
    streaming_field = ""
    if method == "sendVideo":
        streaming_field = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="supports_streaming"\r\n\r\ntrue\r\n'
        )
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'
        f"{streaming_field}"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
        "Content-Type: video/mp4\r\n\r\n"
    ).encode("utf-8")
    suffix = f"\r\n--{boundary}--\r\n".encode("utf-8")
    content_length = len(prefix) + path.stat().st_size + len(suffix)

    connection = HTTPSConnection("api.telegram.org", timeout=180)
    try:
        connection.putrequest("POST", f"/bot{BOT_TOKEN}/{method}")
        connection.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
        connection.putheader("Content-Length", str(content_length))
        connection.putheader("User-Agent", USER_AGENT)
        connection.endheaders()
        connection.send(prefix)
        with path.open("rb") as file:
            while True:
                chunk = file.read(CHUNK_SIZE)
                if not chunk:
                    break
                connection.send(chunk)
        connection.send(suffix)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8", "replace"))
    except (OSError, TimeoutError, json.JSONDecodeError) as exc:
        raise BotError("بارگذاری ویدیو در تلگرام ناموفق بود.") from exc
    finally:
        connection.close()

    if not payload.get("ok"):
        raise BotError(payload.get("description", "تلگرام فایل را نپذیرفت."))


def send_downloaded_video(chat_id: int, path: Path, caption: str) -> None:
    try:
        multipart_upload(chat_id, path, "sendVideo", "video", caption)
    except BotError as video_error:
        log(f"sendVideo ناموفق بود؛ ارسال به‌صورت فایل امتحان می‌شود: {video_error}")
        multipart_upload(chat_id, path, "sendDocument", "document", caption)


def status_caption(status: dict[str, Any], index: int, total: int) -> str:
    author = status.get("author") if isinstance(status.get("author"), dict) else {}
    handle = author.get("screen_name") or "x"
    source_url = status.get("url") or ""
    caption = f"@{handle} · ویدیو {index}/{total}\n{source_url}"
    return caption[:1000]


def process_x_link(chat_id: int, status_id: str) -> None:
    send_action(chat_id, "typing")
    status = fetch_status(status_id)
    videos = extract_videos(status)
    if not videos:
        raise BotError("در این پست ویدیوی بومی X پیدا نشد. پیوندهای YouTube، عکس‌ها و پست‌های خصوصی پشتیبانی نمی‌شوند.")

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    successful = 0
    failures: list[str] = []
    for index, video in enumerate(videos, start=1):
        destination = DOWNLOAD_DIR / f"x_{status_id}_{index}.mp4"
        try:
            send_message(chat_id, f"ویدیو {index} از {len(videos)} در حال دانلود است…")
            path, byte_count = download_best_fitting_video(video, destination)
            send_action(chat_id, "upload_video")
            send_downloaded_video(chat_id, path, status_caption(status, index, len(videos)))
            successful += 1
            log(f"ویدیوی {index}/{len(videos)} از پست {status_id} ارسال شد ({byte_count} بایت).")
        except BotError as exc:
            failures.append(f"ویدیو {index}: {exc}")
            log(f"خطا در ویدیوی {index} پست {status_id}: {exc}")
        finally:
            destination.unlink(missing_ok=True)

    if successful == 0:
        raise BotError("هیچ ویدیویی ارسال نشد. " + (failures[0] if failures else ""))
    if failures:
        send_message(chat_id, "برخی ویدیوها ارسال نشدند:\n" + "\n".join(failures))


def is_allowed(user_id: int) -> bool:
    return not ALLOWED_USER_IDS or user_id in ALLOWED_USER_IDS


def handle_message(message: dict[str, Any]) -> None:
    chat = message.get("chat")
    sender = message.get("from")
    text = message.get("text")
    if not isinstance(chat, dict) or not isinstance(sender, dict) or not isinstance(text, str):
        return

    chat_id = chat.get("id")
    user_id = sender.get("id")
    if not isinstance(chat_id, int) or not isinstance(user_id, int):
        return
    if not is_allowed(user_id):
        send_message(chat_id, "این ربات فقط برای کاربران مجاز فعال است.")
        return

    command = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
    if command in {"/start", "/help"}:
        send_message(
            chat_id,
            "لینک یک پست عمومی X/Twitter دارای ویدیو را بفرستید. ربات نسخهٔ MP4 را بدون تبدیل و بدون FFmpeg دانلود و ارسال می‌کند.\n\n"
            "برای امنیت، پس از دریافت شناسه‌تان با /id، متغیر ALLOWED_USER_IDS را تنظیم کنید.",
        )
        return
    if command == "/id":
        send_message(chat_id, f"شناسهٔ عددی شما: {user_id}")
        return

    status_id = extract_status_id(text)
    if not status_id:
        send_message(chat_id, "یک لینک معتبر از x.com یا twitter.com که شامل /status/ باشد بفرستید.")
        return

    try:
        process_x_link(chat_id, status_id)
    except BotError as exc:
        send_message(chat_id, f"خطا: {exc}")
    except Exception as exc:  # Keep the long-polling process alive after unexpected errors.
        log(f"خطای پیش‌بینی‌نشده: {type(exc).__name__}: {exc}")
        send_message(chat_id, "یک خطای غیرمنتظره رخ داد. چند لحظه بعد دوباره تلاش کنید.")


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN تنظیم نشده است. ابتدا آن را در متغیر محیطی قرار دهید.")
    if MAX_UPLOAD_BYTES <= 0 or MAX_UPLOAD_BYTES > 50 * 1024 * 1024:
        raise SystemExit("MAX_UPLOAD_BYTES باید بزرگ‌تر از صفر و حداکثر 52428800 باشد.")

    try:
        telegram_json("deleteWebhook", {"drop_pending_updates": "false"}, timeout=30)
    except BotError as exc:
        log(f"حذف webhook پیشین ناموفق بود: {exc}")

    access_description = "همهٔ کاربران" if not ALLOWED_USER_IDS else f"{len(ALLOWED_USER_IDS)} کاربر مجاز"
    log(f"ربات فعال است. دسترسی: {access_description}. برای توقف Ctrl+C را بزنید.")
    offset: int | None = None
    while True:
        try:
            for update in get_updates(offset):
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                message = update.get("message")
                if isinstance(message, dict):
                    handle_message(message)
        except KeyboardInterrupt:
            log("ربات متوقف شد.")
            return
        except BotError as exc:
            log(f"خطای polling: {exc}")
            time.sleep(3)
        except Exception as exc:
            log(f"خطای غیرمنتظره در polling: {type(exc).__name__}: {exc}")
            time.sleep(3)


if __name__ == "__main__":
    main()
