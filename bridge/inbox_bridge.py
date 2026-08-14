#!/usr/bin/env python3
"""Local-first Weibo inbox with incremental ENEX export for Evernote."""

from __future__ import annotations

import argparse
import base64
from contextlib import closing
import datetime as dt
import hashlib
from html import escape, unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import plistlib
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


VERSION = "1.3.0"
LABEL = "com.local.weibo-evernote-inbox.bridge"
ENEX_TAG = "weibo"
DEFAULT_PORT = 38419
FOLDER_PICKER_TIMEOUT_SECONDS = 60
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 80 * 1024 * 1024
MAX_VIDEO_BYTES = 80 * 1024 * 1024
MAX_TOTAL_VIDEO_BYTES = 120 * 1024 * 1024
MAX_VIDEO_PAGE_BYTES = 5 * 1024 * 1024
USER_AGENT = f"WeiboEvernoteInbox/{VERSION}"


class InboxError(RuntimeError):
    pass


def app_support_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "WeiboEvernoteInbox"
    return Path.home() / "Library" / "Application Support" / "WeiboEvernoteInbox"


def default_archive_dir() -> Path:
    return Path.home() / "Documents" / "Weibo Evernote Inbox"


def config_path() -> Path:
    return app_support_dir() / "config.json"


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def windows_startup_path() -> Path:
    base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "WeiboEvernoteInbox.vbs"


def windows_picker_source_path() -> Path:
    return Path(__file__).resolve().parent.parent / "platforms" / "windows" / "windows_folder_picker.cs"


def windows_picker_executable() -> Path:
    return Path(__file__).resolve().with_name("windows_folder_picker.exe")


def windows_csharp_compiler() -> Path:
    windows_dir = Path(os.environ.get("WINDIR") or r"C:\Windows")
    candidates = [
        windows_dir / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
        windows_dir / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe",
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    raise InboxError("找不到 Windows .NET Framework C# 編譯器，無法安裝原生資料夾選擇器")


def build_windows_picker(support: Path) -> tuple[Path, Path]:
    source = windows_picker_source_path()
    if not source.is_file():
        raise InboxError(f"找不到 Windows 資料夾選擇器原始碼：{source}")
    output = support / "windows_folder_picker.new.exe"
    compiler = windows_csharp_compiler()
    result = subprocess.run(
        [
            str(compiler),
            "/nologo",
            "/target:winexe",
            "/optimize+",
            f"/out:{output}",
            "/reference:System.Windows.Forms.dll",
            "/reference:System.Drawing.dll",
            str(source),
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=30,
        check=False,
    )
    if result.returncode != 0 or not output.is_file():
        raise InboxError(f"無法編譯 Windows 資料夾選擇器：{clean_line(result.stderr or result.stdout, 1000)}")
    return source, output


def evernote_path() -> Path | None:
    if sys.platform == "darwin":
        candidate = Path("/Applications/Evernote.app")
        try:
            return candidate if candidate.exists() else None
        except OSError:
            return None
    if sys.platform == "win32":
        candidates = []
        if os.environ.get("LOCALAPPDATA"):
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Evernote" / "Evernote.exe")
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(variable):
                candidates.append(Path(os.environ[variable]) / "Evernote" / "Evernote.exe")
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    return None


def sanitize_unicode(value: Any) -> Any:
    """Replace lone UTF-16 surrogates while preserving valid Unicode text."""
    if isinstance(value, str):
        return value.encode("utf-16-le", errors="surrogatepass").decode("utf-16-le", errors="replace")
    if isinstance(value, list):
        return [sanitize_unicode(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_unicode(item) for item in value)
    if isinstance(value, dict):
        return {sanitize_unicode(key): sanitize_unicode(item) for key, item in value.items()}
    return value


def json_text(value: Any) -> str:
    return json.dumps(sanitize_unicode(value), ensure_ascii=False, indent=2) + "\n"


def read_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise InboxError(f"找不到設定檔：{path}") from error
    except json.JSONDecodeError as error:
        raise InboxError(f"設定檔格式錯誤：{error}") from error
    if not isinstance(value, dict):
        raise InboxError("設定檔必須是 JSON 物件")
    return value


def clean_line(value: Any, maximum: int = 300) -> str:
    text = re.sub(r"[\r\n\t]+", " ", sanitize_unicode(str(value or "")))
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text[:maximum]


def safe_name(value: str, maximum: int = 80) -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return (value or "weibo")[:maximum].rstrip(" ._")


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def enex_time(value: str | None = None) -> str:
    try:
        parsed = dt.datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        parsed = dt.datetime.now(dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def host_matches(url: str, suffixes: tuple[str, ...]) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme in {"http", "https"} and any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)


def allowed_image_url(url: str) -> bool:
    return host_matches(url, ("sinaimg.cn", "weibocdn.com"))


def allowed_video_url(url: str) -> bool:
    return host_matches(url, ("weibocdn.com", "weibo.com", "sina.com.cn"))


def allowed_video_page(url: str) -> bool:
    return host_matches(url, ("video.weibo.com",))


def validate_clip(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InboxError("clip 必須是物件")
    value = sanitize_unicode(value)
    source_url = str(value.get("sourceUrl") or "").split("#", 1)[0]
    if not source_url.startswith(("https://weibo.com/", "https://www.weibo.com/")):
        raise InboxError("只接受 weibo.com 的微博連結")
    text = str(value.get("text") or "").replace("\r", "").strip()
    if not text:
        raise InboxError("微博文字為空")
    images = value.get("images") or []
    if not isinstance(images, list):
        raise InboxError("images 必須是陣列")
    videos = value.get("videos") or []
    if not isinstance(videos, list):
        raise InboxError("videos 必須是陣列")
    clean_videos: list[dict[str, str]] = []
    seen_videos: set[tuple[str, str]] = set()
    for item in videos[:8]:
        item = {"url": str(item)} if isinstance(item, str) else item
        if not isinstance(item, dict):
            continue
        direct_url = str(item.get("url") or "")
        page_url = str(item.get("pageUrl") or "")
        direct_url = direct_url if allowed_video_url(direct_url) else ""
        page_url = page_url if allowed_video_page(page_url) else ""
        key = (direct_url, page_url)
        if key == ("", "") or key in seen_videos:
            continue
        seen_videos.add(key)
        clean_videos.append({"url": direct_url, "pageUrl": page_url})
    result = dict(value)
    result["sourceUrl"] = source_url
    result["text"] = text
    result["images"] = [str(item) for item in images if allowed_image_url(str(item))][:18]
    result["videos"] = clean_videos
    result["hasVideo"] = bool(value.get("hasVideo") or clean_videos)
    result["title"] = clean_line(value.get("title") or "微博", 160)
    result["author"] = clean_line(value.get("author"), 100)
    result["published"] = clean_line(value.get("published"), 100)
    result["capturedAt"] = clean_line(value.get("capturedAt") or iso_now(), 80)
    result["id"] = clean_line(value.get("id"), 160) or hashlib.sha256(source_url.encode()).hexdigest()[:20]
    return result


def connect_db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS clips (
          id TEXT PRIMARY KEY,
          source_url TEXT NOT NULL UNIQUE,
          title TEXT NOT NULL,
          author TEXT NOT NULL,
          published TEXT NOT NULL,
          text TEXT NOT NULL,
          images_json TEXT NOT NULL,
          media_json TEXT NOT NULL,
          raw_path TEXT NOT NULL,
          markdown_path TEXT NOT NULL,
          captured_at TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'queued',
          batch_id TEXT,
          exported_at TEXT
        );
        CREATE TABLE IF NOT EXISTS batches (
          id TEXT PRIMARY KEY,
          enex_path TEXT NOT NULL,
          note_count INTEGER NOT NULL,
          created_at TEXT NOT NULL,
          opened_at TEXT
        );
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(clips)")}
    if "videos_json" not in columns:
        connection.execute("ALTER TABLE clips ADD COLUMN videos_json TEXT NOT NULL DEFAULT '[]'")
    if "has_video" not in columns:
        connection.execute("ALTER TABLE clips ADD COLUMN has_video INTEGER NOT NULL DEFAULT 0")
    connection.commit()
    return connection


def suffix_for(url: str, content_type: str) -> str:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9]{2,5}", suffix or ""):
        return suffix
    return mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) or ".jpg"


def download_image_media(urls: list[str], target_dir: Path) -> list[dict[str, Any]]:
    target_dir.mkdir(parents=True, exist_ok=True)
    result: list[dict[str, Any]] = []
    total = 0
    for index, url in enumerate(urls[:18], start=1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": "https://weibo.com/"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                content_type = response.headers.get_content_type()
                if not content_type.startswith("image/"):
                    continue
                announced = int(response.headers.get("Content-Length") or 0)
                if announced > MAX_IMAGE_BYTES or total + announced > MAX_TOTAL_IMAGE_BYTES:
                    continue
                data = response.read(MAX_IMAGE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, ValueError):
            continue
        if len(data) > MAX_IMAGE_BYTES or total + len(data) > MAX_TOTAL_IMAGE_BYTES:
            continue
        total += len(data)
        digest = hashlib.md5(data).hexdigest()  # Evernote en-media requires MD5.
        suffix = suffix_for(url, content_type)
        filename = f"{index:02d}-{digest[:12]}{suffix}"
        path = target_dir / filename
        path.write_bytes(data)
        result.append({
            "kind": "image",
            "url": url,
            "path": str(path),
            "filename": filename,
            "mime": content_type,
            "md5": digest,
            "size": len(data),
        })
    return result


def video_urls_from_page(page_url: str) -> list[str]:
    if not allowed_video_page(page_url):
        return []
    request = urllib.request.Request(page_url, headers={"User-Agent": USER_AGENT, "Referer": "https://weibo.com/"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read(MAX_VIDEO_PAGE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return []
    if len(data) > MAX_VIDEO_PAGE_BYTES:
        return []
    text = unescape(data.decode("utf-8", errors="ignore").replace("\\/", "/").replace("\\u0026", "&"))
    candidates = re.findall(r'https?://[^\s"\'<>\\]+?\.mp4(?:\?[^\s"\'<>\\]+)?', text, flags=re.IGNORECASE)
    result: list[str] = []
    for candidate in candidates:
        candidate = candidate.rstrip("),.;")
        if allowed_video_url(candidate) and candidate not in result:
            result.append(candidate)
    return result[:4]


def resolve_video_urls(videos: list[dict[str, str]]) -> list[str]:
    result: list[str] = []
    for item in videos:
        direct = str(item.get("url") or "")
        if allowed_video_url(direct) and direct not in result:
            result.append(direct)
    for item in videos:
        page = str(item.get("pageUrl") or "")
        for resolved in video_urls_from_page(page):
            if resolved not in result:
                result.append(resolved)
    return result[:2]


def download_video_media(videos: list[dict[str, str]], target_dir: Path) -> list[dict[str, Any]]:
    target_dir.mkdir(parents=True, exist_ok=True)
    result: list[dict[str, Any]] = []
    total = 0
    for index, url in enumerate(resolve_video_urls(videos), start=1):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": "https://weibo.com/"})
        temporary = target_dir / f"video-{index:02d}.part"
        digest = hashlib.md5()  # Evernote en-media requires MD5.
        size = 0
        content_type = "video/mp4"
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                content_type = response.headers.get_content_type()
                if not content_type.startswith("video/") and ".mp4" not in urllib.parse.urlparse(url).path.lower():
                    continue
                announced = int(response.headers.get("Content-Length") or 0)
                if announced > MAX_VIDEO_BYTES or total + announced > MAX_TOTAL_VIDEO_BYTES:
                    continue
                with temporary.open("wb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > MAX_VIDEO_BYTES or total + size > MAX_TOTAL_VIDEO_BYTES:
                            raise InboxError("影片超過本機保存上限")
                        digest.update(chunk)
                        handle.write(chunk)
        except (urllib.error.URLError, TimeoutError, ValueError, InboxError, OSError):
            if temporary.exists():
                temporary.unlink()
            continue
        if not size:
            if temporary.exists():
                temporary.unlink()
            continue
        total += size
        md5 = digest.hexdigest()
        filename = f"video-{index:02d}-{md5[:12]}.mp4"
        path = target_dir / filename
        temporary.replace(path)
        result.append({
            "kind": "video",
            "url": url,
            "path": str(path),
            "filename": filename,
            "mime": content_type if content_type.startswith("video/") else "video/mp4",
            "md5": md5,
            "size": size,
        })
    return result


def media_kind(item: dict[str, Any]) -> str:
    explicit = str(item.get("kind") or "")
    if explicit in {"image", "video"}:
        return explicit
    return "video" if str(item.get("mime") or "").startswith("video/") else "image"


def display_time(clip: dict[str, Any]) -> str:
    return clean_line(clip.get("published") or clip.get("capturedAt") or "時間未知", 100)


def video_notice(clip: dict[str, Any], media: list[dict[str, Any]]) -> str:
    downloaded = sum(media_kind(item) == "video" for item in media)
    if downloaded:
        return f"此微博包含影片，已下載並作為附件保存（{downloaded} 個）。"
    if bool(clip.get("hasVideo")):
        return "此微博包含影片，但頁面未提供可直接下載的影片檔，請從原始連結觀看。"
    return ""


def markdown_for(clip: dict[str, Any], media: list[dict[str, Any]]) -> str:
    lines = [
        f'# {clip["title"]}',
        "",
        f'**時間：** {display_time(clip)}',
    ]
    if clip.get("author"):
        lines.extend([f'**作者：** {clip["author"]}'])
    lines.extend(["", clip["text"]])
    for item in media:
        relative = Path(item["path"]).name
        link = f'../assets/{safe_name(clip["id"])}/{relative}'
        if media_kind(item) == "video":
            lines.extend(["", f'[影片附件：{item["filename"]}]({link})'])
        else:
            lines.extend(["", f'![{item["filename"]}]({link})'])
    notice = video_notice(clip, media)
    if notice:
        lines.extend(["", f'> 影片提醒：{notice}'])
    lines.extend(["", "---", "", f'**原始連結：** [{clip["sourceUrl"]}]({clip["sourceUrl"]})'])
    return "\n".join(lines).strip() + "\n"


def enml_text(text: str) -> str:
    blocks: list[str] = []
    for line in text.splitlines():
        blocks.append(f"<div>{escape(line)}</div>" if line else "<div><br/></div>")
    return "".join(blocks)


def note_enml(clip: dict[str, Any], media: list[dict[str, Any]]) -> str:
    header = f"<div><b>時間：{escape(display_time(clip))}</b></div>"
    if clip.get("author"):
        header += f"<div>作者：{escape(str(clip['author']))}</div>"
    source = escape(str(clip["sourceUrl"]), quote=True)
    attachments = "".join(
        f'<div><en-media type="{escape(str(item["mime"]), quote=True)}" hash="{item["md5"]}"/></div>'
        for item in media if Path(str(item.get("path") or "")).is_file()
    )
    notice = video_notice(clip, media)
    reminder = f'<div><br/></div><div><b>影片提醒：</b>{escape(notice)}</div>' if notice else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">'
        '<en-note>'
        + header
        + '<div><br/></div>'
        + enml_text(str(clip["text"]))
        + ('<div><br/></div>' + attachments if attachments else '')
        + reminder
        + '<div><br/></div><hr/><div><br/></div>'
        + f'<div><b>原始連結：</b><a href="{source}">{source}</a></div>'
        + '</en-note>'
    )


def wrap_cdata(value: str) -> str:
    return value.replace("]]>", "]]]]><![CDATA[>")


def resource_xml(item: dict[str, Any]) -> str:
    path = Path(item["path"])
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        "<resource>"
        f'<data encoding="base64">{data}</data>'
        f'<mime>{escape(str(item["mime"]))}</mime>'
        "<resource-attributes>"
        f'<file-name>{escape(str(item["filename"]))}</file-name>'
        f'<source-url>{escape(str(item["url"]))}</source-url>'
        "</resource-attributes>"
        "</resource>"
    )


def note_xml(clip: dict[str, Any], media: list[dict[str, Any]]) -> str:
    created = enex_time(str(clip.get("capturedAt") or ""))
    tag_xml = f"<tag>{ENEX_TAG}</tag>"
    resources = "".join(resource_xml(item) for item in media if Path(item["path"]).is_file())
    source_url = escape(str(clip["sourceUrl"]))
    return (
        "<note>"
        f'<title>{escape(str(clip["title"]))}</title>'
        f'<content><![CDATA[{wrap_cdata(note_enml(clip, media))}]]></content>'
        f'<created>{created}</created><updated>{created}</updated>'
        f'{tag_xml}'
        "<note-attributes>"
        "<source>web.clip</source>"
        f'<source-url>{source_url}</source-url>'
        "<source-application>Weibo Evernote Inbox</source-application>"
        "</note-attributes>"
        f'{resources}'
        "</note>"
    )


def build_enex(rows: list[sqlite3.Row], output: Path) -> None:
    notes: list[str] = []
    for row in rows:
        clip = {
            "id": row["id"],
            "sourceUrl": row["source_url"],
            "title": row["title"],
            "author": row["author"],
            "published": row["published"],
            "text": row["text"],
            "capturedAt": row["captured_at"],
            "hasVideo": bool(row["has_video"]),
            "videos": json.loads(row["videos_json"]),
        }
        media = json.loads(row["media_json"])
        notes.append(note_xml(clip, media))
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE en-export SYSTEM "http://xml.evernote.com/pub/evernote-export4.dtd">\n'
        f'<en-export export-date="{enex_time()}" application="Weibo Evernote Inbox" version="{VERSION}">'
        + "".join(notes)
        + "</en-export>\n"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(output)


def prepare_archive(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for name in ("raw", "posts", "assets", "exports"):
        (path / name).mkdir(exist_ok=True)
    with closing(connect_db(path / "inbox.sqlite")) as connection:
        connection.execute("SELECT 1")


def validate_archive_target(path: Path) -> Path:
    target = path.expanduser().resolve()
    if target.parent == target or target == Path.home().resolve():
        raise InboxError("請選擇專用資料夾，不要直接選擇磁碟根目錄或個人主目錄")
    if target.exists():
        allowed = {"inbox.sqlite", "raw", "posts", "assets", "exports", ".DS_Store"}
        unexpected = {item.name for item in target.iterdir()} - allowed
        if unexpected:
            raise InboxError("所選資料夾含有其它檔案；請選擇空資料夾或既有微博收件匣")
    return target


def choose_archive_folder(current: Path) -> Path | None:
    if sys.platform == "win32":
        picker = windows_picker_executable()
        if not picker.is_file():
            raise InboxError("找不到 Windows 原生資料夾選擇器，請重新執行安裝程式")
        initial = current if current.exists() else current.parent
        try:
            result = subprocess.run(
                [str(picker), "--initial", str(initial)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=FOLDER_PICKER_TIMEOUT_SECONDS,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise InboxError("資料夾選擇逾時；選擇器已關閉，請重新操作") from error
        if result.returncode == 2:
            return None
        if result.returncode != 0:
            raise InboxError(f"無法打開資料夾選擇器：{clean_line(result.stderr or result.stdout, 500)}")
        selected = result.stdout.strip()
        if not selected:
            raise InboxError("Windows 原生資料夾選擇器沒有回傳路徑")
        return Path(selected)
    if sys.platform != "darwin":
        raise InboxError("此作業系統不支援圖形化資料夾選擇器")
    escaped_current = str(current.parent).replace("\\", "\\\\").replace('"', '\\"')
    script = (
        'set chosenFolder to choose folder with prompt "選擇微博本機收件匣（請使用空資料夾或既有收件匣）" '
        f'default location POSIX file "{escaped_current}"\n'
        'POSIX path of chosenFolder'
    )
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=FOLDER_PICKER_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise InboxError("資料夾選擇逾時；選擇器已關閉，請重新操作") from error
    if result.returncode != 0:
        if "-128" in result.stderr or "User canceled" in result.stderr:
            return None
        raise InboxError(f"無法打開資料夾選擇器：{clean_line(result.stderr or result.stdout, 500)}")
    return Path(result.stdout.strip())


def open_file(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
            return
        if sys.platform == "darwin":
            result = subprocess.run(["/usr/bin/open", str(path)], capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise InboxError(clean_line(result.stderr or result.stdout, 500))
            return
    except OSError as error:
        raise InboxError(clean_line(error, 500)) from error
    raise InboxError("此作業系統不支援打開檔案")


def open_enex(path: Path) -> None:
    if sys.platform == "darwin":
        result = subprocess.run(["/usr/bin/open", "-a", "Evernote", str(path)], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise InboxError(f"無法交給 Evernote：{clean_line(result.stderr or result.stdout, 500)}")
        return
    open_file(path)


class InboxService:
    def __init__(self, config: dict[str, Any], config_file: Path | None = None) -> None:
        self.config = config
        self.config_file = config_file
        self.archive = Path(str(config["archiveDir"])).expanduser().resolve()
        prepare_archive(self.archive)
        self.db_path = self.archive / "inbox.sqlite"
        self.lock = threading.Lock()
        self.picker_lock = threading.Lock()

    @property
    def token(self) -> str:
        return str(self.config.get("token") or "")

    def status(self) -> dict[str, Any]:
        with closing(connect_db(self.db_path)) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total, SUM(state='queued') AS queued, SUM(state='exported') AS exported FROM clips"
            ).fetchone()
            latest = connection.execute("SELECT * FROM batches ORDER BY created_at DESC LIMIT 1").fetchone()
        return {
            "ok": True,
            "version": VERSION,
            "total": int(row["total"] or 0),
            "queued": int(row["queued"] or 0),
            "exported": int(row["exported"] or 0),
            "latestBatch": dict(latest) if latest else None,
            "archiveDir": str(self.archive),
            "evernoteAvailable": evernote_path() is not None,
            "pickerBusy": self.picker_lock.locked(),
        }

    def capture(self, raw_clip: Any) -> dict[str, Any]:
        clip = validate_clip(raw_clip)
        with self.lock, closing(connect_db(self.db_path)) as connection:
            existing = connection.execute("SELECT state FROM clips WHERE source_url = ?", (clip["sourceUrl"],)).fetchone()
            if existing:
                return {"ok": True, "duplicate": True, "state": existing["state"], "message": "這條微博已在本機收件匣"}

            clip_id = safe_name(str(clip["id"]), 100)
            asset_dir = self.archive / "assets" / clip_id
            if bool(self.config.get("dryRun")):
                media: list[dict[str, Any]] = []
            else:
                media = download_image_media(clip["images"], asset_dir)
                if bool(self.config.get("downloadVideos", True)) and clip["hasVideo"]:
                    media.extend(download_video_media(clip["videos"], asset_dir))
            raw_path = self.archive / "raw" / f"{clip_id}.json"
            markdown_path = self.archive / "posts" / f"{clip_id}.md"
            payload = {**clip, "media": media}
            raw_path.write_text(json_text(payload), encoding="utf-8")
            markdown_path.write_text(markdown_for(clip, media), encoding="utf-8")
            connection.execute(
                """
                INSERT INTO clips (
                  id, source_url, title, author, published, text, images_json, videos_json,
                  has_video, media_json, raw_path, markdown_path, captured_at, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued')
                """,
                (
                    clip_id, clip["sourceUrl"], clip["title"], clip["author"], clip["published"], clip["text"],
                    json.dumps(clip["images"], ensure_ascii=False), json.dumps(clip["videos"], ensure_ascii=False),
                    int(clip["hasVideo"]), json.dumps(media, ensure_ascii=False),
                    str(raw_path), str(markdown_path), clip["capturedAt"],
                ),
            )
            connection.commit()
        image_count = sum(media_kind(item) == "image" for item in media)
        video_count = sum(media_kind(item) == "video" for item in media)
        return {
            "ok": True,
            "duplicate": False,
            "message": "已暫存到本機收件匣",
            "imageCount": image_count,
            "videoCount": video_count,
            "hasVideo": bool(clip["hasVideo"]),
        }

    def sync(self) -> dict[str, Any]:
        with self.lock, closing(connect_db(self.db_path)) as connection:
            rows = connection.execute("SELECT * FROM clips WHERE state = 'queued' ORDER BY captured_at, id").fetchall()
            if not rows:
                return {"ok": True, "empty": True, "message": "沒有待同步微博"}
            batch_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            output = self.archive / "exports" / f"weibo-inbox-{batch_id}.enex"
            build_enex(rows, output)
            if not bool(self.config.get("dryRun")):
                open_enex(output)
            now = iso_now()
            connection.execute(
                "INSERT INTO batches (id, enex_path, note_count, created_at, opened_at) VALUES (?, ?, ?, ?, ?)",
                (batch_id, str(output), len(rows), now, now),
            )
            connection.executemany(
                "UPDATE clips SET state='exported', batch_id=?, exported_at=? WHERE id=?",
                [(batch_id, now, row["id"]) for row in rows],
            )
            connection.commit()
        return {"ok": True, "empty": False, "message": "已交給 Evernote 匯入", "count": len(rows), "path": str(output)}

    def reopen_latest(self) -> dict[str, Any]:
        with closing(connect_db(self.db_path)) as connection:
            row = connection.execute("SELECT * FROM batches ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            raise InboxError("尚未生成任何 ENEX")
        path = Path(row["enex_path"])
        if not path.is_file():
            raise InboxError("最近一批 ENEX 已不存在")
        if not bool(self.config.get("dryRun")):
            open_enex(path)
        return {"ok": True, "message": "已重新交給 Evernote", "path": str(path), "count": int(row["note_count"])}

    def open_folder(self) -> dict[str, Any]:
        if not bool(self.config.get("dryRun")):
            open_file(self.archive)
        return {"ok": True, "message": "已打開本機收件匣"}

    def switch_archive(self, selected: Path) -> dict[str, Any]:
        target = validate_archive_target(selected)
        with self.lock:
            previous = self.archive
            if target == previous:
                return {"ok": True, "cancelled": False, "message": "目前已使用這個資料夾", "archiveDir": str(target)}
            prepare_archive(target)
            updated = dict(self.config)
            updated["archiveDir"] = str(target)
            if self.config_file:
                temporary = self.config_file.with_suffix(".json.tmp")
                temporary.write_text(json_text(updated), encoding="utf-8")
                temporary.chmod(0o600)
                temporary.replace(self.config_file)
            self.config = updated
            self.archive = target
            self.db_path = target / "inbox.sqlite"
        return {
            "ok": True,
            "cancelled": False,
            "message": "已切換暫存資料夾；舊資料仍保留在原位置",
            "archiveDir": str(target),
            "previousArchiveDir": str(previous),
        }

    def choose_archive(self) -> dict[str, Any]:
        if bool(self.config.get("dryRun")):
            raise InboxError("乾跑模式不會打開資料夾選擇器")
        if not self.picker_lock.acquire(blocking=False):
            raise InboxError("資料夾選擇視窗已開啟；請先完成或取消目前的選擇")
        try:
            selected = choose_archive_folder(self.archive)
            if selected is None:
                return {"ok": True, "cancelled": True, "message": "已取消選擇資料夾", "archiveDir": str(self.archive)}
            return self.switch_archive(selected)
        finally:
            self.picker_lock.release()


class Handler(BaseHTTPRequestHandler):
    server_version = f"WeiboEvernoteInbox/{VERSION}"

    @property
    def service(self) -> InboxService:
        return self.server.service  # type: ignore[attr-defined]

    def log_message(self, format_string: str, *args: Any) -> None:
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), format_string % args))

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(sanitize_unicode(payload), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin", "")
        if origin.startswith("chrome-extension://"):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Inbox-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def authorized(self) -> bool:
        supplied = self.headers.get("X-Inbox-Token", "")
        return bool(self.service.token) and secrets.compare_digest(supplied, self.service.token)

    def do_OPTIONS(self) -> None:
        self.send_json(200, {"ok": True})

    def do_GET(self) -> None:
        if not self.authorized():
            self.send_json(401, {"ok": False, "message": "橋接器驗證失敗，請重新執行對應平台的安裝程式"})
            return
        if self.path == "/status":
            self.send_json(200, self.service.status())
        else:
            self.send_json(404, {"ok": False, "message": "找不到路徑"})

    def read_payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise InboxError("請求大小不正確")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise InboxError("請求必須是 JSON 物件")
        return payload

    def do_POST(self) -> None:
        if not self.authorized():
            self.send_json(401, {"ok": False, "message": "橋接器驗證失敗，請重新執行對應平台的安裝程式"})
            return
        try:
            payload = self.read_payload()
            if self.path == "/capture":
                result = self.service.capture(payload.get("clip"))
            elif self.path == "/sync":
                result = self.service.sync()
            elif self.path == "/reopen-latest":
                result = self.service.reopen_latest()
            elif self.path == "/open-folder":
                result = self.service.open_folder()
            elif self.path == "/choose-archive":
                result = self.service.choose_archive()
            elif self.path == "/shutdown":
                result = {"ok": True, "message": "橋接器正在停止"}
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.send_json(404, {"ok": False, "message": "找不到路徑"})
                return
        except (InboxError, json.JSONDecodeError, sqlite3.Error) as error:
            self.send_json(400, {"ok": False, "message": str(error)})
            return
        except Exception as error:  # noqa: BLE001
            self.send_json(500, {"ok": False, "message": f"本機收件匣錯誤：{clean_line(error, 500)}"})
            return
        self.send_json(200, result)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: InboxService) -> None:
        super().__init__(address, Handler)
        self.service = service


def serve(path: Path) -> None:
    config = read_config(path)
    if not str(config.get("token") or ""):
        raise InboxError("Token 為空，請重新執行對應平台的安裝程式")
    service = InboxService(config, path)
    port = int(config.get("port") or DEFAULT_PORT)
    server = Server(("127.0.0.1", port), service)
    print(f"Weibo Evernote Inbox {VERSION} listening on 127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def windows_launcher_text(python_path: Path, bridge_path: Path, local_config: Path) -> str:
    command = f'"{python_path}" "{bridge_path}" serve --config "{local_config}"'
    escaped = command.replace('"', '""')
    return f'Set shell = CreateObject("WScript.Shell")\r\nshell.Run "{escaped}", 0, False\r\n'


def start_windows_bridge(python_path: Path, bridge_path: Path, local_config: Path, log_path: Path) -> None:
    log_handle = log_path.open("a", encoding="utf-8")
    try:
        subprocess.Popen(
            [str(python_path), str(bridge_path), "serve", "--config", str(local_config)],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
    finally:
        log_handle.close()


def runtime_python_path() -> Path:
    if sys.platform == "win32":
        return Path(sys.executable).resolve()
    return Path(shutil.which("python3") or sys.executable).resolve()


def request_bridge_shutdown(path: Path) -> None:
    try:
        config = read_config(path)
        token = str(config.get("token") or "")
        port = int(config.get("port") or DEFAULT_PORT)
        if not token:
            return
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/shutdown",
            data=b"{}",
            headers={"Content-Type": "application/json", "X-Inbox-Token": token},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=2):
            pass
        for _ in range(20):
            time.sleep(0.1)
            try:
                probe = urllib.request.Request(
                    f"http://127.0.0.1:{port}/status",
                    headers={"X-Inbox-Token": token},
                )
                with urllib.request.urlopen(probe, timeout=0.2):
                    pass
            except (OSError, urllib.error.URLError):
                break
    except (InboxError, OSError, ValueError, urllib.error.URLError):
        pass


def wait_for_bridge(token: str, port: int = DEFAULT_PORT) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/status",
        headers={"X-Inbox-Token": token},
    )
    last_error = ""
    for _ in range(50):
        try:
            with urllib.request.urlopen(request, timeout=0.5) as response:
                payload = json.load(response)
            if payload.get("ok") and payload.get("version") == VERSION:
                return
        except (OSError, ValueError, urllib.error.URLError) as error:
            last_error = clean_line(error, 300)
        time.sleep(0.1)
    raise InboxError(f"橋接器啟動後未能通過狀態檢查：{last_error or '沒有回應'}")


def install(extension_dir: Path, archive_dir: Path) -> None:
    if sys.platform not in {"darwin", "win32"}:
        raise InboxError("目前只支援 macOS 與 Windows")
    if not (extension_dir / "manifest.json").is_file():
        raise InboxError(f"找不到 Chrome 擴充功能：{extension_dir}")
    support = app_support_dir()
    support.mkdir(parents=True, exist_ok=True)
    picker_build: tuple[Path, Path] | None = None
    if sys.platform == "win32":
        picker_build = build_windows_picker(support)
        request_bridge_shutdown(config_path())
    archive_dir.mkdir(parents=True, exist_ok=True)
    installed_bridge = support / "inbox_bridge.py"
    shutil.copy2(Path(__file__).resolve(), installed_bridge)
    installed_bridge.chmod(0o700)
    if picker_build:
        picker_source, picker_output = picker_build
        shutil.copy2(picker_source, support / picker_source.name)
        picker_output.replace(support / "windows_folder_picker.exe")
    token = secrets.token_urlsafe(32)
    config = {
        "version": VERSION,
        "token": token,
        "archiveDir": str(archive_dir),
        "port": DEFAULT_PORT,
        "tags": [ENEX_TAG],
        "downloadVideos": True,
        "dryRun": False,
    }
    local_config = config_path()
    local_config.write_text(json_text(config), encoding="utf-8")
    if sys.platform == "darwin":
        local_config.chmod(0o600)
    extension_config = {
        "bridgeUrl": f"http://127.0.0.1:{DEFAULT_PORT}",
        "bridgeToken": token,
        "archiveDir": str(archive_dir),
    }
    (extension_dir / "config.json").write_text(json_text(extension_config), encoding="utf-8")

    log_path = support / "bridge.log"
    python_path = runtime_python_path()
    if sys.platform == "darwin":
        agent = {
            "Label": LABEL,
            "ProgramArguments": [str(python_path), str(installed_bridge), "serve", "--config", str(local_config)],
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(log_path),
            "StandardErrorPath": str(log_path),
            "ProcessType": "Background",
        }
        plist = launch_agent_path()
        plist.parent.mkdir(parents=True, exist_ok=True)
        with plist.open("wb") as handle:
            plistlib.dump(agent, handle)
        domain = f"gui/{os.getuid()}"
        subprocess.run(["/bin/launchctl", "bootout", domain, str(plist)], capture_output=True, check=False)
        result = subprocess.run(["/bin/launchctl", "bootstrap", domain, str(plist)], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise InboxError(f"無法啟動橋接器：{clean_line(result.stderr or result.stdout, 500)}")
    else:
        log_path.write_text("", encoding="utf-8")
        startup = windows_startup_path()
        startup.parent.mkdir(parents=True, exist_ok=True)
        startup.write_text(windows_launcher_text(python_path, installed_bridge, local_config), encoding="utf-16")
        start_windows_bridge(python_path, installed_bridge, local_config, log_path)
    wait_for_bridge(token)
    print("本機收件匣已安裝並啟動。")
    print(f"本機資料：{archive_dir}")
    print(f"Chrome 擴充功能：{extension_dir}")


def uninstall() -> None:
    stored_archive = default_archive_dir()
    local_config = config_path()
    try:
        stored_archive = Path(str(read_config(local_config).get("archiveDir") or stored_archive))
    except InboxError:
        pass
    if sys.platform == "darwin":
        plist = launch_agent_path()
        subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True, check=False)
        if plist.exists():
            plist.unlink()
    elif sys.platform == "win32":
        request_bridge_shutdown(local_config)
        startup = windows_startup_path()
        if startup.exists():
            startup.unlink()
    else:
        raise InboxError("目前只支援 macOS 與 Windows")
    support = app_support_dir()
    if support.exists():
        shutil.rmtree(support)
    print(f"已移除橋接器。本機微博資料沒有刪除：{stored_archive}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="微博 Evernote 本機收件匣")
    commands = root.add_subparsers(dest="command", required=True)
    serve_parser = commands.add_parser("serve")
    serve_parser.add_argument("--config", type=Path, default=config_path())
    install_parser = commands.add_parser("install")
    install_parser.add_argument("--extension-dir", type=Path, required=True)
    install_parser.add_argument("--archive-dir", type=Path, default=default_archive_dir())
    commands.add_parser("uninstall")
    return root


def main() -> int:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parser().parse_args()
    try:
        if args.command == "serve":
            serve(args.config.expanduser().resolve())
        elif args.command == "install":
            install(args.extension_dir.expanduser().resolve(), args.archive_dir.expanduser().resolve())
        elif args.command == "uninstall":
            uninstall()
    except InboxError as error:
        print(f"錯誤：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
