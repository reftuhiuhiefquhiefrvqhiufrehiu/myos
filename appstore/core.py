from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_REPOSITORY = "https://github.com/reftuhiuhiefquhiefrvqhiufrehiu/myos"
DEFAULT_REF = "main"
STORE_DIRECTORY = "store-apps"
CATALOG_PATH = "appstore/catalog.json"

MAX_CATALOG_SIZE = 512 * 1024
MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_TOTAL_SIZE = 32 * 1024 * 1024
MAX_FILE_COUNT = 200
CHUNK_SIZE = 64 * 1024

ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
ENTRY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,39}(\.[a-z][a-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$")
VERSION_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
SAFE_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
ALLOWED_EXTENSIONS = {
    ".py",
    ".json",
    ".qss",
    ".txt",
    ".md",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".webp",
}
ALLOWED_HOSTS = {"raw.githubusercontent.com", "github.com"}


class AppStoreError(Exception):
    def __init__(self, user_message: str, detail: str | None = None) -> None:
        super().__init__(detail or user_message)
        self.user_message = user_message


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


@dataclass(frozen=True)
class CatalogApp:
    id: str
    name: str
    version: str
    entry: str
    summary: str = ""
    description: str = ""
    category: str = "Werkzeuge"
    author: str = "NeonVeil"
    size: int = 0
    files: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class InstalledApp:
    id: str
    name: str
    version: str
    entry: str
    installed: str = ""
    category: str = "Werkzeuge"
    summary: str = ""
    author: str = "NeonVeil"
    path: Path | None = None


class AppStore:
    """GitHub-backed application manager for NeonVeil."""

    def __init__(
        self,
        *,
        repository: str = DEFAULT_REPOSITORY,
        ref: str = DEFAULT_REF,
        store_dir: Path | str | None = None,
        catalog_path: Path | str | None = None,
        root_path: Path | str | None = None,
        urlopen: Callable[..., Any] | None = None,
    ) -> None:
        # Use the installed NeonVeil release as the local catalog location;
        # the desktop can be started from any working directory.
        self.root_path = Path(
            root_path or Path(__file__).resolve().parent.parent
        ).expanduser().resolve()
        user_store = Path.home() / ".local" / "share" / "NeonVeil" / STORE_DIRECTORY
        default_dir = self.root_path / STORE_DIRECTORY
        if not store_dir:
            if default_dir.is_dir() and os.access(default_dir, os.W_OK):
                self.store_dir = default_dir.resolve()
            else:
                self.store_dir = user_store.resolve()
        else:
            self.store_dir = Path(store_dir).resolve()

        self.catalog_path = Path(catalog_path or (self.root_path / CATALOG_PATH)).resolve()
        self.urlopen = urlopen or urllib.request.urlopen

        self.repository = repository
        self.ref = ref
        self.owner, self.repo = self.validate_repository(repository)
        self.validate_ref(ref)

        self._catalog_cache: list[CatalogApp] | None = None
        self.store_dir.mkdir(parents=True, exist_ok=True)

        self.search_dirs: list[Path] = [self.store_dir]
        for extra in (user_store, default_dir, Path("/usr/share/neonveil/current") / STORE_DIRECTORY):
            try:
                resolved = extra.resolve()
                if resolved not in self.search_dirs and resolved.is_dir():
                    self.search_dirs.append(resolved)
            except Exception:
                pass

    @staticmethod
    def validate_repository(repository: str) -> tuple[str, str]:
        if not repository or not isinstance(repository, str):
            raise AppStoreError("Ungültige Repository-URL.")
        repo_str = repository.strip()
        if repo_str.startswith("https://github.com/"):
            parts = repo_str.removeprefix("https://github.com/").strip("/").split("/")
        elif "/" in repo_str and not repo_str.startswith("http"):
            parts = repo_str.strip("/").split("/")
        else:
            raise AppStoreError("Nur GitHub-Repositories (https://github.com/owner/repo) werden unterstützt.")

        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise AppStoreError("Das Repository muss im Format 'owner/repo' angegeben sein.")

        owner = parts[0]
        repo_name = parts[1].removesuffix(".git")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo_name):
            raise AppStoreError("Ungültige Zeichen im GitHub-Repository-Namen.")
        return owner, repo_name

    @staticmethod
    def validate_ref(ref: str) -> str:
        if (
            not ref
            or not REF_PATTERN.fullmatch(ref)
            or any(part in {"", ".", ".."} for part in ref.split("/"))
        ):
            raise AppStoreError("Ungültiger Git-Branch oder Tag.")
        return ref

    def set_repository(self, repository: str, ref: str = "main") -> None:
        self.owner, self.repo = self.validate_repository(repository)
        self.validate_ref(ref)
        self.repository = repository
        self.ref = ref
        self._catalog_cache = None

    def raw_base_url(self) -> str:
        return f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{self.ref}"

    def catalog_url(self) -> str:
        return f"{self.raw_base_url()}/{CATALOG_PATH}"

    def file_url(self, rel_path: str) -> str:
        clean = rel_path.lstrip("/")
        if clean.startswith("store-apps/"):
            return f"{self.raw_base_url()}/{clean}"
        return f"{self.raw_base_url()}/store-apps/{clean}"

    def fetch_catalog(self, force_remote: bool = False) -> list[CatalogApp]:
        """Fetch and validate catalog from GitHub or local fallback."""
        catalog_data: dict[str, Any] | None = None
        remote_error: Exception | None = None

        if force_remote:
            try:
                catalog_data = self._fetch_remote_catalog()
            except Exception as error:
                remote_error = error

        if catalog_data is None:
            # Try local file first if not force_remote, or as fallback
            if self.catalog_path.is_file():
                try:
                    content = self.catalog_path.read_text(encoding="utf-8")
                    catalog_data = json.loads(content)
                except Exception as local_err:
                    if remote_error:
                        raise AppStoreError(
                            "Katalog konnte weder von GitHub noch lokal geladen werden.",
                            f"Remote: {remote_error}, Local: {local_err}",
                        ) from remote_error
                    raise AppStoreError("Lokaler Katalog konnte nicht gelesen werden.", str(local_err)) from local_err
            else:
                # If local does not exist, fetch from remote
                try:
                    catalog_data = self._fetch_remote_catalog()
                except Exception as error:
                    raise AppStoreError("Katalog konnte von GitHub nicht geladen werden.", str(error)) from error

        apps = self._parse_and_validate_catalog(catalog_data)
        self._catalog_cache = apps
        return apps

    def _fetch_remote_catalog(self) -> dict[str, Any]:
        url = self.catalog_url()
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "NeonVeil-AppStore/1.0",
                "Accept": "application/json",
            },
        )
        try:
            with self.urlopen(req, timeout=15) as resp:
                data = resp.read(MAX_CATALOG_SIZE + 1)
                if len(data) > MAX_CATALOG_SIZE:
                    raise AppStoreError("Der heruntergeladene Katalog überschreitet die Maximalgröße.")
                return json.loads(data.decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise AppStoreError(f"GitHub-Fehler beim Laden des Katalogs (HTTP {error.code}).", str(error)) from error
        except urllib.error.URLError as error:
            raise AppStoreError("GitHub-Katalog ist derzeit nicht erreichbar.", str(error)) from error
        except json.JSONDecodeError as error:
            raise AppStoreError("Ungültiges JSON-Format im GitHub-Katalog.", str(error)) from error

    def _parse_and_validate_catalog(self, data: Any) -> list[CatalogApp]:
        if not isinstance(data, dict):
            raise AppStoreError("Ungültiges Katalogformat: Stammobjekt muss ein Dictionary sein.")

        raw_apps = data.get("apps")
        if not isinstance(raw_apps, list):
            raise AppStoreError("Ungültiges Katalogformat: 'apps' muss eine Liste sein.")

        apps: list[CatalogApp] = []
        for raw in raw_apps:
            if not isinstance(raw, dict):
                continue
            app_id = raw.get("id")
            if not isinstance(app_id, str) or not ID_PATTERN.fullmatch(app_id):
                continue

            name = raw.get("name", app_id)
            version = str(raw.get("version", "1.0.0"))
            if not VERSION_PATTERN.fullmatch(version):
                continue

            entry = raw.get("entry", f"{app_id}.app:{app_id.capitalize()}Window")
            if not ENTRY_PATTERN.fullmatch(entry):
                continue

            summary = str(raw.get("summary", ""))
            description = str(raw.get("description", ""))
            category = str(raw.get("category", "Werkzeuge"))
            author = str(raw.get("author", "NeonVeil"))
            size = int(raw.get("size", 0))

            files = raw.get("files", {})
            if not isinstance(files, dict) or not files:
                continue

            # Validate files
            validated_files: dict[str, str] = {}
            for rel_path, sha in files.items():
                if not isinstance(rel_path, str) or not isinstance(sha, str):
                    continue
                clean_rel = rel_path.strip().lstrip("/")
                if (
                    not SAFE_PATH_PATTERN.fullmatch(clean_rel)
                    or ".." in clean_rel
                    or not clean_rel.startswith(f"{app_id}/")
                ):
                    continue
                ext = Path(clean_rel).suffix.lower()
                if ext not in ALLOWED_EXTENSIONS:
                    continue
                if not SHA256_PATTERN.fullmatch(sha.lower()):
                    continue
                validated_files[clean_rel] = sha.lower()

            if not validated_files:
                continue

            apps.append(
                CatalogApp(
                    id=app_id,
                    name=name,
                    version=version,
                    entry=entry,
                    summary=summary,
                    description=description,
                    category=category,
                    author=author,
                    size=size,
                    files=validated_files,
                )
            )

        return apps

    def list_catalog(self) -> list[CatalogApp]:
        if self._catalog_cache is None:
            return self.fetch_catalog(force_remote=False)
        return list(self._catalog_cache)

    def get_catalog_app(self, app_id: str) -> CatalogApp | None:
        for app in self.list_catalog():
            if app.id == app_id:
                return app
        return None

    def list_installed(self) -> list[InstalledApp]:
        """Scan store directories for installed applications."""
        installed_dict: dict[str, InstalledApp] = {}

        for search_dir in self.search_dirs:
            if not search_dir.is_dir():
                continue
            for child in sorted(search_dir.iterdir()):
                if not child.is_dir() or child.name.startswith("."):
                    continue
                if child.name in installed_dict:
                    continue

                manifest_path = child / "app.json"
                if manifest_path.is_file():
                    try:
                        data = json.loads(manifest_path.read_text(encoding="utf-8"))
                        app_id = data.get("id", child.name)
                        name = data.get("name", child.name)
                        version = data.get("version", "1.0.0")
                        entry = data.get("entry", f"{child.name}.app:{child.name.capitalize()}Window")
                        installed_date = data.get("installed", "")
                        category = data.get("category", "Werkzeuge")
                        summary = data.get("summary", "")
                        author = data.get("author", "NeonVeil")
                        installed_dict[app_id] = InstalledApp(
                            id=app_id,
                            name=name,
                            version=version,
                            entry=entry,
                            installed=installed_date,
                            category=category,
                            summary=summary,
                            author=author,
                            path=child,
                        )
                    except Exception:
                        continue
                else:
                    app_py = child / "app.py"
                    if app_py.is_file():
                        installed_dict[child.name] = InstalledApp(
                            id=child.name,
                            name=child.name.replace("_", " ").title(),
                            version="1.0.0",
                            entry=f"{child.name}.app:{child.name.replace('_', ' ').title().replace(' ', '')}Window",
                            installed="",
                            category="Werkzeuge",
                            summary="",
                            author="Lokal",
                            path=child,
                        )

        return list(installed_dict.values())

    def get_installed(self, app_id: str) -> InstalledApp | None:
        for app in self.list_installed():
            if app.id == app_id:
                return app
        return None

    def is_installed(self, app_id: str) -> bool:
        return self.get_installed(app_id) is not None

    def has_update(self, app_id: str) -> bool:
        installed = self.get_installed(app_id)
        if not installed:
            return False
        cat_app = self.get_catalog_app(app_id)
        if not cat_app:
            return False
        try:
            return compare_versions(cat_app.version, installed.version) > 0
        except ValueError:
            return cat_app.version != installed.version

    def install_app(
        self,
        app_id: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> InstalledApp:
        """Download and install an application from GitHub with integrity checks."""
        cat_app = self.get_catalog_app(app_id)
        if not cat_app:
            raise AppStoreError(f"Anwendung '{app_id}' wurde im Store-Katalog nicht gefunden.")

        staging_dir = self.store_dir / f".staging_{app_id}_{os.getpid()}"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        target_dir = self.store_dir / app_id
        try:
            total_files = len(cat_app.files)
            if total_files > MAX_FILE_COUNT:
                raise AppStoreError(f"Zu viele Dateien in der Anwendung ({total_files}).")

            downloaded_bytes = 0
            for index, (rel_path, expected_sha) in enumerate(cat_app.files.items(), start=1):
                msg = f"Lade {Path(rel_path).name} herunter ({index}/{total_files})..."
                if progress_callback:
                    progress_callback(index - 1, total_files, msg)

                # The catalog validator guarantees that every file belongs to
                # this exact app folder, so it cannot spill into another app.
                dest_rel = rel_path.removeprefix(f"{app_id}/")
                
                dest_file = staging_dir / dest_rel
                dest_file.parent.mkdir(parents=True, exist_ok=True)

                # Download file
                file_url = self.file_url(rel_path)
                data = self._download_and_verify(file_url, expected_sha, rel_path)
                downloaded_bytes += len(data)
                if downloaded_bytes > MAX_TOTAL_SIZE:
                    raise AppStoreError("Gesamtgröße der Anwendung überschreitet das Limit.")

                dest_file.write_bytes(data)

            # Write app manifest if not already present
            manifest_path = staging_dir / "app.json"
            now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
            manifest_data = {
                "id": cat_app.id,
                "name": cat_app.name,
                "version": cat_app.version,
                "entry": cat_app.entry,
                "summary": cat_app.summary,
                "description": cat_app.description,
                "category": cat_app.category,
                "author": cat_app.author,
                "installed": now_iso,
            }
            manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")

            if progress_callback:
                progress_callback(total_files, total_files, "Installation wird abgeschlossen...")

            # Atomically replace destination
            if target_dir.exists():
                old_dir = self.store_dir / f".old_{app_id}_{os.getpid()}"
                target_dir.replace(old_dir)
                staging_dir.replace(target_dir)
                shutil.rmtree(old_dir, ignore_errors=True)
            else:
                staging_dir.replace(target_dir)

            return InstalledApp(
                id=cat_app.id,
                name=cat_app.name,
                version=cat_app.version,
                entry=cat_app.entry,
                installed=now_iso,
                category=cat_app.category,
                summary=cat_app.summary,
                author=cat_app.author,
                path=target_dir,
            )

        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

    def _download_and_verify(self, url: str, expected_sha: str, rel_path: str) -> bytes:
        # Check if local fallback file exists (useful for testing or offline dev repo)
        candidate_local = self.root_path / "store-apps" / rel_path
        if not candidate_local.is_file() and not rel_path.startswith(self.store_dir.name):
            candidate_local = self.root_path / rel_path

        data: bytes | None = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NeonVeil-AppStore/1.0"})
            with self.urlopen(req, timeout=20) as resp:
                data = resp.read(MAX_FILE_SIZE + 1)
                if len(data) > MAX_FILE_SIZE:
                    raise AppStoreError(f"Datei '{rel_path}' überschreitet das Größenlimit.")
        except urllib.error.HTTPError as error:
            if candidate_local.is_file():
                data = candidate_local.read_bytes()
            else:
                raise AppStoreError(
                    f"Download von '{Path(rel_path).name}' von GitHub fehlgeschlagen (HTTP {error.code}). "
                    f"Bitte prüfe Internetverbindung oder Repository-Status.",
                    str(error),
                ) from error
        except Exception as error:
            if candidate_local.is_file():
                data = candidate_local.read_bytes()
            else:
                raise AppStoreError(f"Download von '{Path(rel_path).name}' fehlgeschlagen: {error}") from error

        # Verify SHA256
        sha = hashlib.sha256(data).hexdigest().lower()
        if sha != expected_sha.lower():
            raise AppStoreError(
                f"Integritätsprüfung für '{Path(rel_path).name}' fehlgeschlagen. "
                "Die Prüfsumme stimmt nicht mit dem GitHub-Katalog überein."
            )

        return data

    def uninstall_app(self, app_id: str) -> bool:
        """Uninstall an application by removing its directory."""
        target_dir = self.store_dir / app_id
        if not target_dir.exists():
            return False
        shutil.rmtree(target_dir, ignore_errors=True)
        return True

    def load_app_window(self, app_id: str) -> Any:
        """Dynamically load and instantiate the application window."""
        app_dir: Path | None = None
        for sdir in self.search_dirs:
            candidate = sdir / app_id
            if candidate.is_dir():
                app_dir = candidate
                break
        if app_dir is None:
            raise AppStoreError(f"Anwendung '{app_id}' ist nicht installiert.")

        manifest_path = app_dir / "app.json"
        entry = f"{app_id}.app:{app_id.capitalize()}Window"
        if manifest_path.is_file():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                entry = data.get("entry", entry)
            except Exception:
                pass

        if ":" not in entry:
            raise AppStoreError(f"Ungültiger Einstiegspunkt: {entry}")

        module_name, class_name = entry.split(":", 1)

        # Ensure all search dirs are in sys.path
        for sdir in reversed(self.search_dirs):
            s_str = str(sdir)
            if s_str not in sys.path:
                sys.path.insert(0, s_str)

        try:
            # Invalidate module cache for fresh reload if updating
            if module_name in sys.modules:
                importlib.reload(sys.modules[module_name])
            mod = importlib.import_module(module_name)
            window_cls = getattr(mod, class_name)
            return window_cls()
        except Exception as error:
            raise AppStoreError(f"Fehler beim Starten von '{app_id}': {error}") from error
