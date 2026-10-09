import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from apps.update_manager.app import UpdateManagerWindow
from desktop.version import APP_VERSION
from desktop.notifications import NotificationCenter, NotificationStore
from update_manager.cli import main as update_cli_main
from update_manager import (
    DEFAULT_REPOSITORY,
    UpdateError,
    UpdateManager,
    UpdateRelease,
    compare_versions,
    infer_channel,
    parse_version,
)


class FakeResponse:
    def __init__(self, body: bytes, url: str) -> None:
        self.body = body
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def geturl(self) -> str:
        return self.url

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.body)
        result, self.body = self.body[:size], self.body[size:]
        return result


class UpdateManagerCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()

    def manager(
        self,
        *,
        opener=None,
        channel: str = "stable",
        current_version: str = "1.0.0",
    ) -> UpdateManager:
        instance = UpdateManager(
            repository=DEFAULT_REPOSITORY,
            current_version=current_version,
            home=self.home,
            system_root=self.root / "system",
            architecture="aarch64",
            is_raspberry_pi4=lambda: True,
            urlopen=opener,
        )
        if channel != "stable":
            instance.save_preferences(channel=channel)
        return instance

    @staticmethod
    def make_release_entry(
        version: str,
        *,
        channel: str | None = None,
        body: bytes = b"bundle",
        published_at: str = "2026-10-08T12:00:00Z",
    ) -> tuple[dict, dict[str, bytes]]:
        release_channel = channel or infer_channel(version)
        tag = f"v{version}"
        owner_repo = "reftuhiuhiefquhiefrvqhiufrehiu/myos"
        asset_name = f"MyOS-{version}-arm64.tar.gz"
        digest = hashlib.sha256(body).hexdigest()
        manifest = {
            "schema": 1,
            "version": version,
            "channel": release_channel,
            "release_date": published_at,
            "changelog": "Neue Funktionen und Fehlerbehebungen.",
            "architecture": "arm64",
            "platform": "raspberry-pi-4",
            "asset": asset_name,
            "download_url": (
                f"https://github.com/{owner_repo}/releases/download/{tag}/{asset_name}"
            ),
            "size": len(body),
            "sha256": digest,
        }
        manifest_url = (
            f"https://github.com/{owner_repo}/releases/download/{tag}/myos-update.json"
        )
        asset_url = (
            f"https://github.com/{owner_repo}/releases/download/{tag}/{asset_name}"
        )
        entry = {
            "tag_name": tag,
            "prerelease": release_channel != "stable",
            "draft": False,
            "published_at": published_at,
            "body": "Neue Funktionen und Fehlerbehebungen.",
            "assets": [
                {
                    "name": "myos-update.json",
                    "size": len(json.dumps(manifest).encode()),
                    "browser_download_url": manifest_url,
                },
                {
                    "name": asset_name,
                    "size": len(body),
                    "digest": f"sha256:{digest}",
                    "browser_download_url": asset_url,
                },
            ],
        }
        return entry, {
            manifest_url: json.dumps(manifest).encode(),
            asset_url: body,
        }

    @staticmethod
    def make_opener(routes: dict[str, bytes]):
        def opener(request, **_kwargs):
            url = request.full_url
            if url not in routes:
                raise urllib.error.URLError("not mapped")
            return FakeResponse(routes[url], url)

        return opener

    def check_routes(self, entries: list[dict], routes: dict[str, bytes]) -> None:
        routes[
            "https://api.github.com/repos/reftuhiuhiefquhiefrvqhiufrehiu/myos/releases?per_page=100"
        ] = json.dumps(entries).encode()

    def test_semver_comparison_and_prerelease_order(self) -> None:
        self.assertEqual(compare_versions("1.2.0", "1.2.0-rc.2"), 1)
        self.assertEqual(compare_versions("1.2.0-beta.10", "1.2.0-beta.2"), 1)
        self.assertEqual(compare_versions("2.0.0", "1.99.99"), 1)
        self.assertEqual(infer_channel("1.2.0-beta.1"), "beta")
        self.assertEqual(infer_channel("1.2.0-rc.1"), "developer")
        with self.assertRaises(ValueError):
            parse_version("01.2.0")

    def test_channel_selection_excludes_beta_from_stable_and_includes_for_beta(self) -> None:
        stable_entry, stable_routes = self.make_release_entry("1.1.0")
        beta_entry, beta_routes = self.make_release_entry("1.2.0-beta.1")
        routes = stable_routes | beta_routes
        self.check_routes([beta_entry, stable_entry], routes)

        stable = self.manager(opener=self.make_opener(routes))
        self.assertEqual(stable.check_for_updates().version, "1.1.0")

        beta = self.manager(opener=self.make_opener(routes), channel="beta")
        self.assertEqual(beta.check_for_updates().version, "1.2.0-beta.1")

    def test_empty_release_list_reports_no_update(self) -> None:
        routes: dict[str, bytes] = {}
        self.check_routes([], routes)
        manager = self.manager(opener=self.make_opener(routes))
        self.assertIsNone(manager.check_for_updates())
        self.assertEqual(manager.status()["message"], "NeonVeil ist auf dem neuesten Stand.")

    def test_download_verifies_sha256_and_reports_interruption(self) -> None:
        entry, routes = self.make_release_entry("1.1.0", body=b"verified bundle")
        self.check_routes([entry], routes)
        manager = self.manager(opener=self.make_opener(routes))
        release = manager.check_for_updates()
        downloaded = manager.download(release)
        self.assertEqual(downloaded.read_bytes(), b"verified bundle")

        broken_routes = dict(routes)
        broken_routes[release.download_url] = b"partial"
        broken = self.manager(opener=self.make_opener(broken_routes))
        broken_release = broken.check_for_updates()
        with self.assertRaisesRegex(UpdateError, "nicht verifiziert"):
            broken.download(broken_release)
        self.assertFalse(broken.downloaded_file(broken_release).with_suffix(".gz.part").exists())

    def test_download_fails_cleanly_when_connection_is_offline(self) -> None:
        manager = self.manager(
            opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                urllib.error.URLError("offline")
            )
        )
        with self.assertRaises(UpdateError) as context:
            manager.check_for_updates()
        self.assertIn("Keine Internetverbindung", context.exception.user_message)

    def test_download_checks_free_space_before_writing(self) -> None:
        entry, routes = self.make_release_entry("1.1.0", body=b"bundle")
        self.check_routes([entry], routes)
        manager = self.manager(opener=self.make_opener(routes))
        release = manager.check_for_updates()
        usage = type("DiskUsage", (), {"free": 1})()
        with patch("update_manager.core.shutil.disk_usage", return_value=usage):
            with self.assertRaises(UpdateError) as context:
                manager.download(release)
        self.assertIn("nicht genug freier Platz", context.exception.user_message)

    def test_invalid_asset_urls_are_rejected(self) -> None:
        with self.assertRaisesRegex(UpdateError, "nicht vertrauenswürdige"):
            UpdateManager._validate_asset_url(
                "https://example.com/Malware.tar.gz",
                "reftuhiuhiefquhiefrvqhiufrehiu",
                "myos",
                tag="v1.2.0",
                name="MyOS-1.2.0-arm64.tar.gz",
            )

    def make_archive(self, path: Path, version: str, *, unsafe: bool = False) -> None:
        with tarfile.open(path, "w:gz") as archive:
            for name, contents in (
                ("VERSION", version),
                ("desktop/main.py", "pass\n"),
                ("desktop/theme.py", "pass\n"),
                ("desktop/shell.py", "pass\n"),
                ("desktop/taskbar.py", "pass\n"),
                ("apps/browser/app.py", "pass\n"),
                ("apps/update_manager/app.py", "pass\n"),
                ("apps/app_store/app.py", "pass\n"),
                ("appstore/core.py", "pass\n"),
                ("update_manager/core.py", "pass\n"),
            ):
                data = contents.encode()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, BytesIO(data))
            if unsafe:
                data = b"outside"
                info = tarfile.TarInfo("../outside")
                info.size = len(data)
                archive.addfile(info, BytesIO(data))

    def test_install_activates_staged_release_and_preserves_user_settings(self) -> None:
        manager = self.manager()
        system_root = manager.system_root
        releases = system_root / "releases"
        initial = releases / "1.0.0"
        initial.mkdir(parents=True)
        (initial / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        (system_root / "current").symlink_to("releases/1.0.0")
        settings_dir = self.home / ".config/neonveil"
        settings_dir.mkdir(parents=True)
        profile_file = settings_dir / "neonveil.conf"
        profile_file.write_text("personal-settings", encoding="utf-8")
        archive_path = manager.downloaded_file(
            UpdateRelease(
                version="1.1.0",
                channel="stable",
                release_date="2026-10-08",
                size=1,
                changelog="",
                download_url=(
                    "https://github.com/reftuhiuhiefquhiefrvqhiufrehiu/myos/"
                    "releases/download/v1.1.0/MyOS-1.1.0-arm64.tar.gz"
                ),
                sha256="0" * 64,
                asset_name="MyOS-1.1.0-arm64.tar.gz",
                tag="v1.1.0",
            )
        )
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        self.make_archive(archive_path, "1.1.0")
        release = UpdateRelease(
            version="1.1.0",
            channel="stable",
            release_date="2026-10-08",
            size=archive_path.stat().st_size,
            changelog="",
            download_url=(
                "https://github.com/reftuhiuhiefquhiefrvqhiufrehiu/myos/"
                "releases/download/v1.1.0/MyOS-1.1.0-arm64.tar.gz"
            ),
            sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            asset_name=archive_path.name,
            tag="v1.1.0",
        )
        with patch("update_manager.core.os.geteuid", return_value=0):
            target = manager.install(release)
        self.assertEqual((system_root / "current").resolve(), target.resolve())
        self.assertEqual((target / "VERSION").read_text(encoding="utf-8").strip(), "1.1.0")
        self.assertEqual(profile_file.read_text(encoding="utf-8"), "personal-settings")
        self.assertEqual(len(manager.history()), 1)
        self.assertTrue(Path(manager.history()[0]["backup"]).is_file())
        with patch("update_manager.core.os.geteuid", return_value=0):
            with self.assertRaisesRegex(UpdateError, "Startprüfung"):
                manager.install(release)

    def test_startup_recovery_restores_previous_release_after_failed_trial(self) -> None:
        manager = self.manager(current_version="1.1.0")
        releases = manager.system_root / "releases"
        previous = releases / "1.0.0"
        active = releases / "1.1.0"
        previous.mkdir(parents=True)
        active.mkdir()
        current = manager.system_root / "current"
        current.symlink_to("releases/1.1.0")
        manager._write_recovery_state("1.1.0", "1.0.0")

        with patch("update_manager.core.os.geteuid", return_value=0):
            self.assertIn("wird geprüft", manager.prepare_startup_recovery())
            self.assertEqual(current.resolve(), active.resolve())
            self.assertIn("automatisch wiederhergestellt", manager.prepare_startup_recovery())

        self.assertEqual(current.resolve(), previous.resolve())
        self.assertFalse(manager.recovery_path.exists())

    def test_healthy_startup_confirms_pending_release(self) -> None:
        manager = self.manager(current_version="1.1.0")
        releases = manager.system_root / "releases"
        previous = releases / "1.0.0"
        active = releases / "1.1.0"
        previous.mkdir(parents=True)
        active.mkdir()
        current = manager.system_root / "current"
        current.symlink_to("releases/1.1.0")
        manager._write_recovery_state("1.1.0", "1.0.0", attempts=1)

        with patch("update_manager.core.os.geteuid", return_value=0):
            self.assertTrue(manager.confirm_healthy_startup())

        self.assertEqual(current.resolve(), active.resolve())
        self.assertFalse(manager.recovery_path.exists())

    def test_unsafe_archive_fails_without_changing_active_release(self) -> None:
        manager = self.manager()
        system_root = manager.system_root
        releases = system_root / "releases"
        active = releases / "1.0.0"
        active.mkdir(parents=True)
        (active / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        current = system_root / "current"
        current.symlink_to("releases/1.0.0")
        archive_path = manager.cache_dir / "MyOS-1.1.0-arm64.tar.gz"
        archive_path.parent.mkdir(parents=True)
        self.make_archive(archive_path, "1.1.0", unsafe=True)
        release = UpdateRelease(
            "1.1.0",
            "stable",
            "2026-10-08",
            archive_path.stat().st_size,
            "",
            "https://github.com/reftuhiuhiefquhiefrvqhiufrehiu/myos/releases/download/v1.1.0/MyOS-1.1.0-arm64.tar.gz",
            hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            archive_path.name,
            "v1.1.0",
        )
        with patch("update_manager.core.os.geteuid", return_value=0):
            with self.assertRaisesRegex(UpdateError, "ungültigen Dateipfad"):
                manager.install(release)
        self.assertEqual(current.resolve(), active.resolve())

    def test_rollback_switches_to_previous_version_and_records_history(self) -> None:
        manager = self.manager(current_version="1.1.0")
        releases = manager.system_root / "releases"
        previous = releases / "1.0.0"
        active = releases / "1.1.0"
        previous.mkdir(parents=True)
        active.mkdir()
        current = manager.system_root / "current"
        current.symlink_to("releases/1.1.0")
        with patch("update_manager.core.os.geteuid", return_value=0):
            self.assertEqual(manager.rollback(), "1.0.0")
        self.assertEqual(current.resolve(), previous.resolve())
        self.assertEqual(manager.history()[0]["status"], "Vorherige Version wiederhergestellt")

    def test_channel_and_automation_preferences_persist_with_install_off_by_default(self) -> None:
        manager = self.manager()
        self.assertFalse(manager.preferences["auto_install"])
        manager.save_preferences(channel="beta", auto_check=True, auto_download=True)
        reopened = self.manager()
        self.assertEqual(reopened.channel, "beta")
        self.assertTrue(reopened.preferences["auto_check"])
        self.assertTrue(reopened.preferences["auto_download"])
        self.assertFalse(reopened.preferences["auto_install"])

    def test_release_metadata_rejects_mismatched_api_digest(self) -> None:
        entry, routes = self.make_release_entry("1.1.0")
        entry["assets"][1]["digest"] = f"sha256:{'0' * 64}"
        self.check_routes([entry], routes)
        manager = self.manager(opener=self.make_opener(routes))
        with self.assertRaisesRegex(UpdateError, "nicht verifiziert"):
            manager.check_for_updates()

    def test_invalid_release_version_is_reported_instead_of_ignored(self) -> None:
        routes: dict[str, bytes] = {}
        self.check_routes(
            [{"tag_name": "vlatest", "draft": False}],
            routes,
        )
        manager = self.manager(opener=self.make_opener(routes))
        with self.assertRaises(UpdateError) as context:
            manager.check_for_updates()
        self.assertIn("keine gültige Semantic Version", context.exception.user_message)

    def test_release_tag_must_start_with_v(self) -> None:
        routes: dict[str, bytes] = {}
        self.check_routes(
            [{"tag_name": "1.1.0", "draft": False}],
            routes,
        )
        manager = self.manager(opener=self.make_opener(routes))
        with self.assertRaisesRegex(UpdateError, "müssen mit „v“ beginnen"):
            manager.check_for_updates()

    def test_polkit_cannot_run_update_commands_that_write_to_user_paths_as_root(self) -> None:
        error_output = io.StringIO()
        with patch("update_manager.cli.os.geteuid", return_value=0), patch.dict(
            os.environ, {"PKEXEC_UID": str(os.getuid())}
        ), redirect_stderr(error_output):
            result = update_cli_main(["download"])
        self.assertEqual(result, 1)
        self.assertIn("nicht erlaubt", error_output.getvalue())


class UpdateManagerWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_native_manager_shows_version_channel_status_and_update_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = UpdateManager(
                repository=DEFAULT_REPOSITORY,
                current_version=APP_VERSION,
                home=Path(directory),
                system_root=Path(directory) / "system",
                architecture="aarch64",
                is_raspberry_pi4=lambda: True,
            )
            window = UpdateManagerWindow(manager)
            self.assertEqual(window.windowTitle(), "NeonVeil Update Manager")
            self.assertEqual(window.installed_version.text(), APP_VERSION)
            self.assertEqual(window.channel_combo.currentData(), "stable")
            self.assertFalse(window.auto_install.isChecked())
            self.assertTrue(window.check_button.isEnabled())
            self.assertFalse(window.download_button.isEnabled())
            self.assertEqual(window.history_list.count(), 1)
            window.close()

    def test_update_notification_has_details_later_and_install_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(
                str(Path(directory) / "notifications.ini"),
                QSettings.Format.IniFormat,
            )
            center = NotificationCenter(NotificationStore(settings))
            events = []
            center.notify_update(
                "1.1.0",
                details=lambda: events.append("details"),
                later=lambda: events.append("later"),
                install=lambda: events.append("install"),
            )
            self.assertEqual(
                [button.text() for button in center.popup._action_buttons],
                ["Details", "Später", "Update installieren"],
            )
            center.popup._action_buttons[1].click()
            self.assertEqual(events, ["later"])
            self.assertFalse(center.popup.isVisible())
            center.popup.close()

if __name__ == "__main__":
    unittest.main()
