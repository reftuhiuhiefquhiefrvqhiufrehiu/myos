from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import pwd
import re
import shutil
import tarfile
import tempfile
from logging.handlers import RotatingFileHandler
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable


DEFAULT_REPOSITORY = "https://github.com/reftuhiuhiefquhiefrvqhiufrehiu/myos"
CHANNELS = ("stable", "beta", "developer")
MAX_MANIFEST_SIZE = 128 * 1024
MAX_DOWNLOAD_SIZE = 512 * 1024 * 1024
MAX_UNPACKED_SIZE = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
INSTALL_RESERVE = 64 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
VERSION_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
ASSET_PATTERN = re.compile(r"^MyOS-[A-Za-z0-9.+-]+-arm64\.tar\.gz$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GITHUB_DOWNLOAD_HOSTS = {
    "api.github.com",
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}
LOGGER = logging.getLogger("myos.update")


class UpdateError(Exception):
    def __init__(self, user_message: str, detail: str | None = None) -> None:
        super().__init__(detail or user_message)
        self.user_message = user_message


@dataclass(frozen=True)
class UpdateRelease:
    version: str
    channel: str
    release_date: str
    size: int
    changelog: str
    download_url: str
    sha256: str
    asset_name: str
    tag: str


def parse_version(version: str) -> tuple[int, int, int, tuple[str, ...]]:
    value = version.removeprefix("v")
    match = VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError(f"Ungültige Semantic-Version: {version}")
    prerelease = tuple(match.group(4).split(".")) if match.group(4) else ()
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease


def _compare_prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    if not left and not right:
        return 0
    if not left:
        return 1
    if not right:
        return -1
    for left_part, right_part in zip(left, right):
        if left_part == right_part:
            continue
        left_numeric = left_part.isdigit()
        right_numeric = right_part.isdigit()
        if left_numeric and right_numeric:
            return 1 if int(left_part) > int(right_part) else -1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return 1 if left_part > right_part else -1
    if len(left) == len(right):
        return 0
    return 1 if len(left) > len(right) else -1


def compare_versions(left: str, right: str) -> int:
    parsed_left = parse_version(left)
    parsed_right = parse_version(right)
    if parsed_left[:3] != parsed_right[:3]:
        return 1 if parsed_left[:3] > parsed_right[:3] else -1
    return _compare_prerelease(parsed_left[3], parsed_right[3])


def infer_channel(version: str) -> str:
    prerelease = parse_version(version)[3]
    if not prerelease:
        return "stable"
    if any(part.casefold() == "beta" or part.casefold().startswith("beta") for part in prerelease):
        return "beta"
    return "developer"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


class UpdateManager:
    def __init__(
        self,
        *,
        repository: str | None = None,
        current_version: str,
        home: Path | None = None,
        system_root: Path = Path("/usr/share/neonveil"),
        architecture: str | None = None,
        is_raspberry_pi4: Callable[[], bool] | None = None,
        urlopen: Callable[..., Any] | None = None,
    ) -> None:
        self.home = (home or Path.home()).expanduser()
        self.system_root = system_root
        self.current_version = current_version
        self.architecture = architecture or platform.machine()
        self._is_raspberry_pi4 = is_raspberry_pi4 or self._detect_raspberry_pi4
        self._urlopen = urlopen or urllib.request.urlopen
        self._preference_owner = self._owner()
        self.preferences_path = self.home / ".config/myos/update.json"
        self._running_as_root = os.geteuid() == 0
        if self._running_as_root:
            self.state_dir = Path("/var/lib/myos-update")
            self.backup_dir = (
                self.state_dir / "backups" / str(self._preference_owner[0])
            )
            self.history_path = self.state_dir / f"history-{self._preference_owner[0]}.json"
            self.status_path = self.state_dir / f"status-{self._preference_owner[0]}.json"
            self.log_path = Path("/var/log/myos-update.log")
        else:
            self.state_dir = self.home / ".local/state/myos/update"
            self.backup_dir = self.state_dir / "backups"
            self.history_path = self.state_dir / "history.json"
            self.status_path = self.state_dir / "status.json"
            self.log_path = self.state_dir / "myos-update.log"
        self.cache_dir = self.home / ".cache/myos/updates"
        self.repository = repository or self._configured_repository()
        self._validate_repository(self.repository)
        self.preferences = self._load_preferences()
        self.channel = self.preferences["channel"]
        self._configure_logging()

    def _configure_logging(self) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if not any(
                getattr(handler, "baseFilename", None) == str(self.log_path)
                for handler in LOGGER.handlers
            ):
                handler = RotatingFileHandler(
                    self.log_path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"
                )
                LOGGER.addHandler(handler)
                LOGGER.setLevel(logging.INFO)
            if self._running_as_root:
                os.chmod(self.log_path, 0o600)
        except OSError as error:
            LOGGER.warning("Update-Log kann nicht vorbereitet werden: %s", error)

    @staticmethod
    def _owner() -> tuple[int, int]:
        if os.geteuid() != 0:
            return os.getuid(), os.getgid()
        for key in ("PKEXEC_UID", "SUDO_UID"):
            value = os.environ.get(key, "")
            if value.isdecimal():
                account = pwd.getpwuid(int(value))
                return account.pw_uid, account.pw_gid
        return 0, 0

    def _write_user_json(self, path: Path, value: object) -> None:
        uid, gid = self._preference_owner
        path.parent.mkdir(parents=True, exist_ok=True)
        if self._running_as_root and uid != 0:
            os.chown(path.parent, uid, gid)
        _atomic_json(path, value)
        if self._running_as_root and uid != 0:
            os.chown(path, uid, gid)

    @staticmethod
    def _write_system_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        _atomic_json(path, value)
        os.chmod(path, 0o644)

    @staticmethod
    def _detect_raspberry_pi4() -> bool:
        try:
            model = Path("/proc/device-tree/model").read_text(errors="replace")
        except OSError:
            return False
        return "Raspberry Pi 4" in model

    @staticmethod
    def _validate_repository(repository: str) -> tuple[str, str]:
        parsed = urllib.parse.urlsplit(repository)
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or len(parts) != 2
            or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts)
        ):
            raise UpdateError("Die konfigurierte GitHub-Updatequelle ist ungültig.")
        return parts[0], parts[1].removesuffix(".git")

    @classmethod
    def _configured_repository(cls) -> str:
        config_path = Path("/etc/myos-update/config.json")
        if not config_path.exists():
            return DEFAULT_REPOSITORY
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise UpdateError(
                "Die Updatequelle konnte nicht gelesen werden.", str(error)
            ) from error
        if not isinstance(config, dict) or not isinstance(config.get("repository"), str):
            raise UpdateError("Die konfigurierte GitHub-Updatequelle ist ungültig.")
        return config["repository"]

    def _load_preferences(self) -> dict[str, Any]:
        defaults: dict[str, Any] = {
            "channel": "stable",
            "auto_check": False,
            "auto_download": False,
            "auto_install": False,
        }
        if not self.preferences_path.exists():
            return defaults
        try:
            stored = json.loads(self.preferences_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            LOGGER.warning("Update-Einstellungen konnten nicht gelesen werden: %s", error)
            return defaults
        if not isinstance(stored, dict):
            return defaults
        if stored.get("channel") in CHANNELS:
            defaults["channel"] = stored["channel"]
        for key in ("auto_check", "auto_download", "auto_install"):
            if isinstance(stored.get(key), bool):
                defaults[key] = stored[key]
        return defaults

    def save_preferences(
        self,
        *,
        channel: str | None = None,
        auto_check: bool | None = None,
        auto_download: bool | None = None,
        auto_install: bool | None = None,
    ) -> dict[str, Any]:
        values = dict(self.preferences)
        if channel is not None:
            if channel not in CHANNELS:
                raise UpdateError("Unbekannter Update-Kanal.")
            values["channel"] = channel
        for key, value in (
            ("auto_check", auto_check),
            ("auto_download", auto_download),
            ("auto_install", auto_install),
        ):
            if value is not None:
                values[key] = value
        self._write_user_json(self.preferences_path, values)
        self.preferences = values
        self.channel = values["channel"]
        return dict(values)

    def reload_preferences(self) -> dict[str, Any]:
        self.preferences = self._load_preferences()
        self.channel = self.preferences["channel"]
        return dict(self.preferences)

    def _repository_parts(self) -> tuple[str, str]:
        return self._validate_repository(self.repository)

    def _request(self, url: str, *, limit: int) -> bytes:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise UpdateError("Die Updateverbindung ist nicht sicher (HTTPS erforderlich).")
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "NeonVeil-Update-Manager",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self._urlopen(request, timeout=30) as response:
                final_url = urllib.parse.urlsplit(response.geturl())
                if (
                    final_url.scheme != "https"
                    or final_url.hostname not in GITHUB_DOWNLOAD_HOSTS
                ):
                    raise UpdateError(
                        "Die Updateverbindung wurde auf einen nicht vertrauenswürdigen Server umgeleitet."
                    )
                content = response.read(limit + 1)
        except UpdateError:
            raise
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise UpdateError(
                    "Das GitHub-Repository oder Release wurde nicht gefunden.",
                    str(error),
                ) from error
            raise UpdateError(
                "GitHub ist momentan nicht erreichbar.", str(error)
            ) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise UpdateError(
                "Keine Internetverbindung. Es konnte nicht nach Updates gesucht werden.",
                str(error),
            ) from error
        if len(content) > limit:
            raise UpdateError("Die Update-Metadaten sind unerwartet groß.")
        return content

    def _json_at(self, url: str, *, limit: int) -> Any:
        try:
            return json.loads(self._request(url, limit=limit))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise UpdateError("Die Update-Metadaten sind ungültig.", str(error)) from error

    def _allowed_channel(self, release_channel: str) -> bool:
        if self.channel == "stable":
            return release_channel == "stable"
        if self.channel == "beta":
            return release_channel in {"stable", "beta"}
        return True

    def _release_from_api(self, entry: dict[str, Any], owner: str, repo: str) -> UpdateRelease:
        tag = entry.get("tag_name")
        if not isinstance(tag, str):
            raise UpdateError("Ein Release besitzt keinen gültigen Versions-Tag.")
        version = tag.removeprefix("v")
        try:
            parse_version(version)
        except ValueError as error:
            raise UpdateError(
                "Ein GitHub-Release besitzt keine gültige Semantic Version.",
                str(error),
            ) from error
        channel = infer_channel(version)
        if (
            not isinstance(entry.get("prerelease"), bool)
            or entry["prerelease"] != (channel != "stable")
        ):
            raise UpdateError("Release-Tag und GitHub-Kanal stimmen nicht überein.")
        assets = entry.get("assets")
        if not isinstance(assets, list):
            raise UpdateError("Das GitHub-Release enthält keine Update-Dateien.")
        by_name = {
            item.get("name"): item
            for item in assets
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        metadata_asset = by_name.get("myos-update.json")
        if metadata_asset is None:
            raise UpdateError("Im GitHub-Release fehlt myos-update.json.")
        metadata_url = metadata_asset.get("browser_download_url")
        self._validate_asset_url(
            metadata_url, owner, repo, tag=tag, name="myos-update.json"
        )
        manifest = self._json_at(metadata_url, limit=MAX_MANIFEST_SIZE)
        if not isinstance(manifest, dict):
            raise UpdateError("Die Update-Metadaten sind ungültig.")
        asset_name = manifest.get("asset")
        size = manifest.get("size")
        digest = manifest.get("sha256")
        manifest_release_date = manifest.get("release_date")
        if (
            manifest.get("schema") != 1
            or manifest.get("version") != version
            or manifest.get("channel") != channel
            or manifest.get("architecture") != "arm64"
            or manifest.get("platform") != "raspberry-pi-4"
            or not isinstance(asset_name, str)
            or ASSET_PATTERN.fullmatch(asset_name) is None
            or asset_name != f"MyOS-{version}-arm64.tar.gz"
            or not isinstance(size, int)
            or isinstance(size, bool)
            or not 0 < size <= MAX_DOWNLOAD_SIZE
            or not isinstance(digest, str)
            or SHA256_PATTERN.fullmatch(digest) is None
            or not isinstance(manifest_release_date, str)
        ):
            raise UpdateError("Das Update konnte nicht verifiziert werden.")
        release_asset = by_name.get(asset_name)
        if release_asset is None:
            raise UpdateError("Das Updatepaket fehlt im GitHub-Release.")
        if release_asset.get("size") != size:
            raise UpdateError("Die angegebene Updategröße stimmt nicht überein.")
        api_digest = release_asset.get("digest")
        if api_digest is not None and api_digest != f"sha256:{digest}":
            raise UpdateError("Das Update konnte nicht verifiziert werden.")
        download_url = release_asset.get("browser_download_url")
        self._validate_asset_url(download_url, owner, repo, tag=tag, name=asset_name)
        if manifest.get("download_url") != download_url:
            raise UpdateError("Die Download-Adresse stimmt nicht mit dem Release überein.")
        release_date = entry.get("published_at")
        if not isinstance(release_date, str):
            raise UpdateError("Das Veröffentlichungsdatum des Releases fehlt.")
        try:
            datetime.fromisoformat(release_date.replace("Z", "+00:00"))
            datetime.fromisoformat(manifest_release_date.replace("Z", "+00:00"))
        except ValueError as error:
            raise UpdateError("Das Veröffentlichungsdatum des Releases ist ungültig.", str(error)) from error
        changelog = manifest.get("changelog")
        if not isinstance(changelog, str):
            raise UpdateError("Im GitHub-Release fehlt der Changelog.")
        return UpdateRelease(
            version=version,
            channel=channel,
            release_date=release_date,
            size=size,
            changelog=changelog,
            download_url=download_url,
            sha256=digest,
            asset_name=asset_name,
            tag=tag,
        )

    @staticmethod
    def _validate_asset_url(
        url: object,
        owner: str,
        repo: str,
        *,
        tag: str | None = None,
        name: str | None = None,
    ) -> None:
        if not isinstance(url, str):
            raise UpdateError("Das GitHub-Release enthält eine ungültige Download-Adresse.")
        parsed = urllib.parse.urlsplit(url)
        expected_prefix = f"/{owner}/{repo}/releases/download/"
        valid_path = parsed.path.startswith(expected_prefix)
        if tag is not None and name is not None:
            valid_path = parsed.path == f"{expected_prefix}{tag}/{name}"
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not valid_path
        ):
            raise UpdateError("Die Updatequelle verweist auf eine nicht vertrauenswürdige Adresse.")

    def check_for_updates(self) -> UpdateRelease | None:
        if self.architecture.lower() not in {"aarch64", "arm64"}:
            raise UpdateError(
                "Dieses Update ist für Raspberry Pi 4 mit ARM64 vorgesehen."
            )
        owner, repo = self._repository_parts()
        raw_releases: list[dict[str, Any]] = []
        for page in range(1, 11):
            query = "?per_page=100" if page == 1 else f"?per_page=100&page={page}"
            api_url = f"https://api.github.com/repos/{owner}/{repo}/releases{query}"
            page_entries = self._json_at(api_url, limit=5 * 1024 * 1024)
            if not isinstance(page_entries, list):
                raise UpdateError("GitHub lieferte keine gültige Release-Liste.")
            raw_releases.extend(
                entry for entry in page_entries if isinstance(entry, dict)
            )
            if len(page_entries) < 100:
                break
        candidates: list[dict[str, Any]] = []
        for item in raw_releases:
            if item.get("draft") is True:
                continue
            tag = item.get("tag_name")
            if not isinstance(tag, str):
                raise UpdateError("Ein GitHub-Release besitzt keinen Versions-Tag.")
            if not tag.startswith("v"):
                raise UpdateError("GitHub-Release-Tags müssen mit „v“ beginnen.")
            try:
                version = tag.removeprefix("v")
                parse_version(version)
            except ValueError as error:
                raise UpdateError(
                    "Ein GitHub-Release besitzt keine gültige Semantic Version.",
                    str(error),
                ) from error
            channel = infer_channel(version)
            if self._allowed_channel(channel) and compare_versions(version, self.current_version) > 0:
                candidates.append(item)
        candidates.sort(
            key=lambda item: _VersionSortKey(item["tag_name"].removeprefix("v")),
            reverse=True,
        )
        if not candidates:
            self._save_status("check", "NeonVeil ist auf dem neuesten Stand.")
            return None
        release = self._release_from_api(candidates[0], owner, repo)
        self._save_status("check", f"Version {release.version} ist verfügbar.")
        return release

    def download(self, release: UpdateRelease) -> Path:
        try:
            parse_version(release.version)
        except ValueError as error:
            raise UpdateError("Die Update-Metadaten sind ungültig.", str(error)) from error
        if release.channel not in CHANNELS:
            raise UpdateError("Die Update-Metadaten sind ungültig.")
        if not self._allowed_channel(release.channel):
            raise UpdateError("Dieses Release gehört nicht zum ausgewählten Update-Kanal.")
        self._validate_asset_url(
            release.download_url,
            *self._repository_parts(),
            tag=release.tag,
            name=release.asset_name,
        )
        if release.size <= 0 or release.size > MAX_DOWNLOAD_SIZE:
            raise UpdateError("Die Update-Datei hat eine ungültige Größe.")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            free_space = shutil.disk_usage(self.cache_dir).free
        except OSError as error:
            raise UpdateError("Der verfügbare Speicherplatz konnte nicht geprüft werden.", str(error)) from error
        if free_space < release.size + INSTALL_RESERVE:
            raise UpdateError("Auf dem Speicherplatz ist nicht genug freier Platz für das Update.")
        destination = self.cache_dir / release.asset_name
        partial = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        received = 0
        try:
            request = urllib.request.Request(
                release.download_url,
                headers={"Accept": "application/octet-stream", "User-Agent": "NeonVeil-Update-Manager"},
            )
            with self._urlopen(request, timeout=60) as response, partial.open("wb") as output:
                final_url = urllib.parse.urlsplit(response.geturl())
                if (
                    final_url.scheme != "https"
                    or final_url.hostname not in GITHUB_DOWNLOAD_HOSTS
                ):
                    raise UpdateError(
                        "Die Updateverbindung wurde auf einen nicht vertrauenswürdigen Server umgeleitet."
                    )
                while chunk := response.read(CHUNK_SIZE):
                    received += len(chunk)
                    if received > release.size or received > MAX_DOWNLOAD_SIZE:
                        raise UpdateError("Die Update-Datei ist größer als angekündigt.")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        except UpdateError:
            partial.unlink(missing_ok=True)
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            partial.unlink(missing_ok=True)
            raise UpdateError("Der Download wurde unterbrochen. Bitte erneut versuchen.", str(error)) from error
        if received != release.size or digest.hexdigest() != release.sha256:
            partial.unlink(missing_ok=True)
            raise UpdateError("Das Update konnte nicht verifiziert werden.")
        try:
            os.replace(partial, destination)
        except OSError as error:
            partial.unlink(missing_ok=True)
            raise UpdateError("Das Update konnte nicht im Download-Ordner gespeichert werden.", str(error)) from error
        self._save_status("download", f"Version {release.version} wurde heruntergeladen.")
        return destination

    def downloaded_file(self, release: UpdateRelease) -> Path:
        return self.cache_dir / release.asset_name

    def install(self, release: UpdateRelease) -> Path:
        if os.geteuid() != 0:
            raise UpdateError("Zum Installieren ist eine Systemberechtigung erforderlich.")
        if not self.architecture.lower() in {"aarch64", "arm64"}:
            raise UpdateError("Dieses Update ist für Raspberry Pi 4 mit ARM64 vorgesehen.")
        if not self._is_raspberry_pi4():
            raise UpdateError("Dieses Update kann nur auf einem Raspberry Pi 4 installiert werden.")
        if compare_versions(release.version, self.current_version) <= 0:
            raise UpdateError("Die heruntergeladene Version ist nicht neuer als die installierte.")
        archive = self.downloaded_file(release)
        if not archive.is_file():
            raise UpdateError("Bitte lade das Update zuerst herunter.")
        self._verify_file(archive, release)
        releases_dir = self.system_root / "releases"
        current_link = self.system_root / "current"
        self._validate_current_link(current_link, releases_dir)
        target = releases_dir / release.version
        if target.exists():
            raise UpdateError("Diese Version ist bereits installiert.")
        backup = self._backup_settings(release.version)
        releases_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=releases_dir))
        try:
            self._extract_release(archive, staging, release)
            os.replace(staging, target)
            self._atomic_activate(target, current_link)
        except (OSError, tarfile.TarError, UpdateError) as error:
            shutil.rmtree(staging, ignore_errors=True)
            if target.exists() and self._current_target(current_link) != target.resolve():
                shutil.rmtree(target, ignore_errors=True)
            self._save_status("install", "Die Installation ist fehlgeschlagen.")
            if isinstance(error, UpdateError):
                raise
            raise UpdateError("Die Installation ist fehlgeschlagen. Die bisherige Version bleibt aktiv.", str(error)) from error
        self._append_history(
            {
                "version": release.version,
                "channel": release.channel,
                "status": "Erfolgreich installiert",
                "date": _now(),
                "backup": str(backup) if backup else "",
            }
        )
        self._save_status("install", f"Version {release.version} wurde installiert. Neustart erforderlich.")
        return target

    @staticmethod
    def _verify_file(archive: Path, release: UpdateRelease) -> None:
        if archive.stat().st_size != release.size:
            raise UpdateError("Das Update konnte nicht verifiziert werden.")
        digest = hashlib.sha256()
        try:
            with archive.open("rb") as source:
                while chunk := source.read(CHUNK_SIZE):
                    digest.update(chunk)
        except OSError as error:
            raise UpdateError("Die Update-Datei konnte nicht gelesen werden.", str(error)) from error
        if digest.hexdigest() != release.sha256:
            raise UpdateError("Das Update konnte nicht verifiziert werden.")

    @staticmethod
    def _validate_archive_path(name: str) -> PurePosixPath:
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or not path.parts
            or any(part in {"", ".", ".."} for part in path.parts)
            or path.parts[0] not in {"VERSION", "apps", "desktop", "update_manager"}
            or "\\" in name
        ):
            raise UpdateError("Das Updatearchiv enthält einen ungültigen Dateipfad.")
        return path

    def _extract_release(self, archive: Path, staging: Path, release: UpdateRelease) -> None:
        total_size = 0
        members: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
        try:
            with tarfile.open(archive, mode="r:gz") as bundle:
                for member in bundle.getmembers():
                    if len(members) >= MAX_ARCHIVE_MEMBERS:
                        raise UpdateError("Das Updatearchiv enthält zu viele Dateien.")
                    path = self._validate_archive_path(member.name)
                    if not (member.isdir() or member.isfile()):
                        raise UpdateError("Das Updatearchiv enthält nicht unterstützte Dateitypen.")
                    if member.isfile():
                        total_size += member.size
                        if member.size < 0 or total_size > MAX_UNPACKED_SIZE:
                            raise UpdateError("Das Updatearchiv ist unerwartet groß.")
                    members.append((member, path))
                if not any(path.as_posix() == "VERSION" and member.isfile() for member, path in members):
                    raise UpdateError("Im Updatearchiv fehlt die Versionsdatei.")
                free = shutil.disk_usage(staging).free
                if free < total_size + INSTALL_RESERVE:
                    raise UpdateError("Auf dem Speicherplatz ist nicht genug freier Platz für das Update.")
                for member, relative_path in members:
                    destination = staging.joinpath(*relative_path.parts)
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    source = bundle.extractfile(member)
                    if source is None:
                        raise UpdateError("Eine Datei im Updatearchiv ist beschädigt.")
                    with source, destination.open("xb") as output:
                        shutil.copyfileobj(source, output, CHUNK_SIZE)
                    os.chmod(destination, 0o644)
        except UpdateError:
            raise
        except (OSError, tarfile.TarError) as error:
            raise UpdateError("Das Updatearchiv ist beschädigt.", str(error)) from error
        try:
            extracted_version = (staging / "VERSION").read_text(encoding="utf-8").strip()
        except OSError as error:
            raise UpdateError("Im Updatearchiv fehlt die Versionsdatei.", str(error)) from error
        if (
            extracted_version != release.version
            or not (staging / "desktop/main.py").is_file()
            or not (staging / "apps/update_manager/app.py").is_file()
            or not (staging / "update_manager/core.py").is_file()
        ):
            raise UpdateError("Die Inhalte des Updatearchivs stimmen nicht mit dem Release überein.")

    def _backup_settings(self, version: str) -> Path | None:
        candidates = (
            self.home / ".config/neonveil",
            self.home / ".config/myos",
            self.home / ".local/share/MyOS",
        )
        existing = [path for path in candidates if path.exists()]
        if not existing:
            return None
        backups = self.backup_dir
        backups.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(backups, 0o700)
        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        backup_path = backups / f"{timestamp}-{version}.tar.gz"
        try:
            with tarfile.open(backup_path, mode="w:gz") as backup:
                for path in existing:
                    backup.add(path, arcname=path.relative_to(self.home))
        except (OSError, tarfile.TarError) as error:
            backup_path.unlink(missing_ok=True)
            raise UpdateError("Die NeonVeil-Einstellungen konnten nicht gesichert werden.", str(error)) from error
        os.chmod(backup_path, 0o600)
        return backup_path

    @staticmethod
    def _validate_current_link(current_link: Path, releases_dir: Path) -> None:
        if not current_link.is_symlink():
            raise UpdateError(
                "Dieses System verwendet noch kein Update-fähiges Installationslayout. "
                "Installiere zuerst ein aktuelles NeonVeil-Image."
            )
        target = current_link.resolve(strict=False)
        try:
            target.relative_to(releases_dir.resolve())
        except ValueError as error:
            raise UpdateError("Die aktive NeonVeil-Version liegt außerhalb des Update-Verzeichnisses.") from error
        if not target.is_dir():
            raise UpdateError("Die aktuell installierte NeonVeil-Version ist nicht verfügbar.")

    @staticmethod
    def _current_target(current_link: Path) -> Path | None:
        try:
            return current_link.resolve(strict=True)
        except OSError:
            return None

    @staticmethod
    def _atomic_activate(target: Path, current_link: Path) -> None:
        temporary_link = current_link.with_name(f".current-{os.getpid()}")
        temporary_link.unlink(missing_ok=True)
        relative_target = os.path.relpath(target, current_link.parent)
        os.symlink(relative_target, temporary_link)
        os.replace(temporary_link, current_link)

    def rollback(self) -> str:
        if os.geteuid() != 0:
            raise UpdateError("Zum Wiederherstellen ist eine Systemberechtigung erforderlich.")
        releases_dir = self.system_root / "releases"
        current_link = self.system_root / "current"
        self._validate_current_link(current_link, releases_dir)
        current = self._current_target(current_link)
        if current is None:
            raise UpdateError("Die aktuell installierte NeonVeil-Version ist nicht verfügbar.")
        current_version = current.name
        candidates = []
        for candidate in releases_dir.iterdir():
            if not candidate.is_dir() or candidate.is_symlink() or candidate == current:
                continue
            try:
                if compare_versions(candidate.name, current_version) < 0:
                    candidates.append(candidate)
            except ValueError:
                continue
        if not candidates:
            raise UpdateError("Es wurde keine vorherige NeonVeil-Version für den Rollback gefunden.")
        previous = max(candidates, key=lambda item: _VersionSortKey(item.name))
        self._atomic_activate(previous, current_link)
        self._append_history(
            {
                "version": previous.name,
                "channel": infer_channel(previous.name),
                "status": "Vorherige Version wiederhergestellt",
                "date": _now(),
                "backup": "",
            }
        )
        self._save_status("rollback", f"NeonVeil-Version {previous.name} wurde wiederhergestellt.")
        return previous.name

    def history(self) -> list[dict[str, str]]:
        history_paths = [self.history_path]
        if not self._running_as_root:
            uid, _gid = self._preference_owner
            history_paths.append(Path(f"/var/lib/myos-update/history-{uid}.json"))
        entries: list[dict[str, str]] = []
        for path in history_paths:
            if not path.exists():
                continue
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                LOGGER.warning("Update-Historie konnte nicht gelesen werden: %s", error)
                continue
            if not isinstance(stored, list):
                continue
            entries.extend(
                {
                    key: value
                    for key, value in entry.items()
                    if key in {"version", "channel", "status", "date", "backup"}
                    and isinstance(value, str)
                }
                for entry in stored
                if isinstance(entry, dict)
            )
        return sorted(entries, key=lambda entry: entry.get("date", ""))[-100:]

    def status(self) -> dict[str, str]:
        status_paths = [self.status_path]
        if not self._running_as_root:
            uid, _gid = self._preference_owner
            status_paths.append(Path(f"/var/lib/myos-update/status-{uid}.json"))
        results = []
        for path in status_paths:
            if not path.exists():
                continue
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                LOGGER.warning("Update-Status konnte nicht gelesen werden: %s", error)
                continue
            if isinstance(stored, dict) and isinstance(stored.get("date"), str):
                results.append(stored)
        if not results:
            return {"action": "", "message": "Noch kein Updatevorgang.", "date": ""}
        stored = max(results, key=lambda item: item["date"])
        return {
            key: stored[key]
            for key in ("action", "message", "date")
            if isinstance(stored.get(key), str)
        }

    def _save_status(self, action: str, message: str) -> None:
        value = {"action": action, "message": message, "date": _now()}
        if self._running_as_root:
            self._write_system_json(self.status_path, value)
        else:
            self._write_user_json(self.status_path, value)

    def record_status(self, action: str, message: str) -> None:
        self._save_status(action, message)

    def _append_history(self, entry: dict[str, str]) -> None:
        entries = self.history()
        entries.append(entry)
        if self._running_as_root:
            self._write_system_json(self.history_path, entries[-100:])
        else:
            self._write_user_json(self.history_path, entries[-100:])


class _VersionSortKey:
    def __init__(self, version: str) -> None:
        self.version = version

    def __lt__(self, other: _VersionSortKey) -> bool:
        return compare_versions(self.version, other.version) < 0

    def __gt__(self, other: _VersionSortKey) -> bool:
        return compare_versions(self.version, other.version) > 0

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _VersionSortKey) and compare_versions(self.version, other.version) == 0
