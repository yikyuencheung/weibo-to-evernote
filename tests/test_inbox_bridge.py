from __future__ import annotations

import importlib.util
import base64
from email.message import Message
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock
import urllib.request
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("inbox_bridge", ROOT / "bridge" / "inbox_bridge.py")
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


def sample_clip(identifier: str = "AbC123") -> dict[str, object]:
    return {
        "id": identifier,
        "title": "測試作者：一條測試微博",
        "author": "測試作者",
        "published": "2026-08-04 09:00",
        "sourceUrl": f"https://weibo.com/123456/{identifier}",
        "text": "第一行 & 特殊字元\n\n第二行 <內容>",
        "images": [],
        "videos": [],
        "hasVideo": False,
        "capturedAt": "2026-08-04T01:00:00Z",
    }


class InboxBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.archive = Path(self.temporary.name) / "archive"
        self.service = bridge.InboxService({
            "archiveDir": str(self.archive),
            "token": "test-token",
            "tags": ["微博", "測試"],
            "dryRun": True,
        })

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_capture_writes_local_formats_and_deduplicates(self) -> None:
        first = self.service.capture(sample_clip())
        duplicate = self.service.capture(sample_clip())
        self.assertFalse(first["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(self.service.status()["queued"], 1)
        raw = json.loads((self.archive / "raw" / "AbC123.json").read_text(encoding="utf-8"))
        markdown = (self.archive / "posts" / "AbC123.md").read_text(encoding="utf-8")
        self.assertEqual(raw["sourceUrl"], "https://weibo.com/123456/AbC123")
        self.assertIn("**時間：** 2026-08-04 09:00", markdown)
        self.assertTrue(markdown.rstrip().endswith("[https://weibo.com/123456/AbC123](https://weibo.com/123456/AbC123)"))

    def test_capture_replaces_lone_surrogates_and_preserves_unicode(self) -> None:
        clip = sample_clip("Unicode777")
        clip["text"] = "損壞字元：\ud835；正常字元：𝟘；Emoji：🐘"
        clip["metadata"] = {"nested": "\ud835"}

        captured = self.service.capture(clip)

        self.assertFalse(captured["duplicate"])
        raw = json.loads((self.archive / "raw" / "Unicode777.json").read_text(encoding="utf-8"))
        markdown = (self.archive / "posts" / "Unicode777.md").read_text(encoding="utf-8")
        self.assertEqual(raw["text"], "損壞字元：�；正常字元：𝟘；Emoji：🐘")
        self.assertEqual(raw["metadata"]["nested"], "�")
        self.assertIn("正常字元：𝟘；Emoji：🐘", markdown)
        result = self.service.sync()
        ET.parse(result["path"])

    def test_http_capture_accepts_json_with_lone_surrogate(self) -> None:
        server = bridge.Server(("127.0.0.1", 0), self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            clip = sample_clip("HttpUnicode888")
            clip["text"] = "HTTP 傳入的損壞字元：\ud835"
            body = json.dumps({"clip": clip}).encode("utf-8")
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_address[1]}/capture",
                data=body,
                headers={"Content-Type": "application/json", "X-Inbox-Token": "test-token"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                payload = json.load(response)
            self.assertTrue(payload["ok"])
            saved = json.loads((self.archive / "raw" / "HttpUnicode888.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["text"], "HTTP 傳入的損壞字元：�")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_sync_is_incremental_and_enex_is_well_formed(self) -> None:
        self.service.capture(sample_clip("One111"))
        first = self.service.sync()
        self.assertEqual(first["count"], 1)
        self.assertTrue(Path(first["path"]).is_file())
        tree = ET.parse(first["path"])
        notes = tree.getroot().findall("note")
        self.assertEqual(len(notes), 1)
        self.assertEqual([tag.text for tag in notes[0].findall("tag")], ["weibo"])
        content = notes[0].findtext("content") or ""
        self.assertIn("第一行 &amp; 特殊字元", content)
        self.assertLess(content.index("時間：2026-08-04 09:00"), content.index("第一行"))
        self.assertLess(content.index("第一行"), content.index("原始連結："))
        self.assertEqual(self.service.status()["queued"], 0)
        self.assertTrue(self.service.sync()["empty"])

        self.service.capture(sample_clip("Two222"))
        second = self.service.sync()
        second_tree = ET.parse(second["path"])
        self.assertEqual(len(second_tree.getroot().findall("note")), 1)
        self.assertEqual(self.service.status()["exported"], 2)

    def test_rejects_non_weibo_urls(self) -> None:
        clip = sample_clip()
        clip["sourceUrl"] = "https://example.com/not-weibo"
        with self.assertRaises(bridge.InboxError):
            self.service.capture(clip)

    def test_enex_embeds_image_as_resource(self) -> None:
        self.service.capture(sample_clip("Img333"))
        image = b"\x89PNG\r\n\x1a\nlocal-test-image"
        digest = hashlib.md5(image).hexdigest()
        asset_dir = self.archive / "assets" / "Img333"
        asset_dir.mkdir(parents=True, exist_ok=True)
        image_path = asset_dir / "01-test.png"
        image_path.write_bytes(image)
        media = [{
            "url": "https://wx1.sinaimg.cn/large/test.png",
            "path": str(image_path),
            "filename": image_path.name,
            "mime": "image/png",
            "md5": digest,
            "size": len(image),
        }]
        connection = sqlite3.connect(self.archive / "inbox.sqlite")
        try:
            connection.execute("UPDATE clips SET media_json=? WHERE id='Img333'", (json.dumps(media),))
            connection.commit()
        finally:
            connection.close()

        result = self.service.sync()
        tree = ET.parse(result["path"])
        note = tree.getroot().find("note")
        self.assertIsNotNone(note)
        self.assertIn(f'hash="{digest}"', note.findtext("content") or "")
        resource = note.find("resource")
        self.assertIsNotNone(resource)
        self.assertEqual(base64.b64decode(resource.findtext("data") or ""), image)
        self.assertEqual(resource.findtext("mime"), "image/png")

    def test_video_notice_when_direct_download_is_unavailable(self) -> None:
        clip = sample_clip("Vid444")
        clip["hasVideo"] = True
        clip["videos"] = [{"url": "", "pageUrl": "https://video.weibo.com/show?fid=1034:test"}]
        captured = self.service.capture(clip)
        self.assertTrue(captured["hasVideo"])
        self.assertEqual(captured["videoCount"], 0)
        markdown = (self.archive / "posts" / "Vid444.md").read_text(encoding="utf-8")
        self.assertIn("此微博包含影片", markdown)
        self.assertIn("請從原始連結觀看", markdown)
        result = self.service.sync()
        content = ET.parse(result["path"]).getroot().find("note").findtext("content") or ""
        self.assertIn("影片提醒：", content)
        self.assertLess(content.index("影片提醒："), content.index("原始連結："))

    def test_switch_archive_keeps_previous_data_and_persists_selection(self) -> None:
        config_file = Path(self.temporary.name) / "config.json"
        config = {
            "archiveDir": str(self.archive),
            "token": "test-token",
            "tags": ["微博"],
            "dryRun": True,
        }
        config_file.write_text(json.dumps(config), encoding="utf-8")
        service = bridge.InboxService(config, config_file)
        service.capture(sample_clip("Keep555"))
        target = Path(self.temporary.name) / "selected-archive"
        switched = service.switch_archive(target)
        self.assertEqual(switched["archiveDir"], str(target.resolve()))
        self.assertTrue((self.archive / "posts" / "Keep555.md").is_file())
        self.assertEqual(service.status()["total"], 0)
        saved = json.loads(config_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["archiveDir"], str(target.resolve()))

    def test_downloads_direct_mp4_as_video_resource(self) -> None:
        payload = b"mock-mp4-video-data"

        class FakeResponse(io.BytesIO):
            def __init__(self, value: bytes) -> None:
                super().__init__(value)
                self.headers = Message()
                self.headers["Content-Type"] = "video/mp4"
                self.headers["Content-Length"] = str(len(value))

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.close()

        url = "https://f.video.weibocdn.com/test/video.mp4"
        with mock.patch.object(bridge.urllib.request, "urlopen", return_value=FakeResponse(payload)):
            media = bridge.download_video_media([{"url": url, "pageUrl": ""}], self.archive / "assets" / "Video666")
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0]["kind"], "video")
        self.assertEqual(media[0]["mime"], "video/mp4")
        self.assertEqual(Path(media[0]["path"]).read_bytes(), payload)

    def test_archive_selector_rejects_unrelated_nonempty_folder(self) -> None:
        target = Path(self.temporary.name) / "not-an-inbox"
        target.mkdir()
        (target / "personal.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(bridge.InboxError):
            bridge.validate_archive_target(target)

    def test_archive_selector_rejects_filesystem_root(self) -> None:
        root = Path(self.temporary.name).resolve()
        while root.parent != root:
            root = root.parent
        with self.assertRaises(bridge.InboxError):
            bridge.validate_archive_target(root)

    def test_windows_launcher_quotes_paths(self) -> None:
        text = bridge.windows_launcher_text(
            Path(r"C:\Program Files\Python\python.exe"),
            Path(r"C:\Users\Test User\AppData\Local\WeiboEvernoteInbox\inbox_bridge.py"),
            Path(r"C:\Users\Test User\AppData\Local\WeiboEvernoteInbox\config.json"),
        )
        self.assertIn('""C:\\Program Files\\Python\\python.exe""', text)
        self.assertIn('""C:\\Users\\Test User\\AppData\\Local\\WeiboEvernoteInbox\\config.json""', text)
        self.assertIn(', 0, False', text)

    def test_windows_runtime_uses_current_python_not_store_alias(self) -> None:
        expected = Path(r"C:\Tools\Bundled Python\python.exe").resolve()
        with mock.patch.object(bridge.sys, "platform", "win32"), mock.patch.object(
            bridge.sys,
            "executable",
            str(expected),
        ), mock.patch.object(bridge.shutil, "which", return_value=r"C:\Users\Test\WindowsApps\python3.exe"):
            self.assertEqual(bridge.runtime_python_path(), expected)

    def test_folder_picker_timeout_returns_recoverable_error(self) -> None:
        picker = Path(self.temporary.name) / "windows_folder_picker.exe"
        picker.write_bytes(b"test")
        with mock.patch.object(bridge.sys, "platform", "win32"), mock.patch.object(
            bridge,
            "windows_picker_executable",
            return_value=picker,
        ), mock.patch.object(
            bridge.subprocess,
            "run",
            side_effect=bridge.subprocess.TimeoutExpired(str(picker), bridge.FOLDER_PICKER_TIMEOUT_SECONDS),
        ):
            with self.assertRaisesRegex(bridge.InboxError, "資料夾選擇逾時"):
                bridge.choose_archive_folder(self.archive)

    def test_native_folder_picker_cancel_is_not_an_error(self) -> None:
        picker = Path(self.temporary.name) / "windows_folder_picker.exe"
        picker.write_bytes(b"test")
        completed = bridge.subprocess.CompletedProcess([str(picker)], 2, stdout="", stderr="")
        with mock.patch.object(bridge.sys, "platform", "win32"), mock.patch.object(
            bridge,
            "windows_picker_executable",
            return_value=picker,
        ), mock.patch.object(bridge.subprocess, "run", return_value=completed):
            self.assertIsNone(bridge.choose_archive_folder(self.archive))

    def test_missing_native_folder_picker_requests_reinstall(self) -> None:
        missing = Path(self.temporary.name) / "missing-picker.exe"
        with mock.patch.object(bridge.sys, "platform", "win32"), mock.patch.object(
            bridge,
            "windows_picker_executable",
            return_value=missing,
        ):
            with self.assertRaisesRegex(bridge.InboxError, "請重新執行安裝程式"):
                bridge.choose_archive_folder(self.archive)

    def test_only_one_folder_picker_can_run(self) -> None:
        self.service.config["dryRun"] = False
        self.service.picker_lock.acquire()
        try:
            self.assertTrue(self.service.status()["pickerBusy"])
            with self.assertRaisesRegex(bridge.InboxError, "資料夾選擇視窗已開啟"):
                self.service.choose_archive()
        finally:
            self.service.picker_lock.release()

    def test_windows_evernote_detection(self) -> None:
        local_app_data = Path(self.temporary.name) / "Local"
        executable = local_app_data / "Programs" / "Evernote" / "Evernote.exe"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"")
        with mock.patch.object(bridge.sys, "platform", "win32"), mock.patch.dict(
            bridge.os.environ,
            {"LOCALAPPDATA": str(local_app_data)},
            clear=False,
        ):
            self.assertEqual(bridge.evernote_path(), executable)

    def test_authenticated_shutdown_stops_server(self) -> None:
        server = bridge.Server(("127.0.0.1", 0), self.service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_address[1]}/shutdown",
            data=b"{}",
            headers={"Content-Type": "application/json", "X-Inbox-Token": "test-token"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.load(response)
        self.assertTrue(payload["ok"])
        thread.join(timeout=5)
        server.server_close()
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
