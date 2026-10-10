from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

DEFAULT_REPOSITORY = "https://github.com/reftuhiuhiefquhiefrvqhiufrehiu/myos"
DEFAULT_REF = "main"
STORE_DIRECTORY = "store-apps"
CATALOG_PATH = "appstore/catalog.json"

MAX_CATALOG_SIZE = 512 * 1024
MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_DEB_SIZE = 200 * 1024 * 1024  # 200 MB for .deb packages
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
    ".deb",
}
ALLOWED_HOSTS = {"raw.githubusercontent.com", "github.com"}


class AppType(Enum):
    PYTHON = "python"
    DEB = "deb"


# Well-known CA bundle locations. Python's compiled-in default verify paths are
# empty in several real setups (python.org builds on macOS, slim container
# images, some custom Python builds), so every HTTPS request fails with
# CERTIFICATE_VERIFY_FAILED even though the operating system has a trust store
# and curl works. The App Store must keep working there.
CA_BUNDLE_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",  # Debian, Ubuntu, Raspberry Pi OS
    "/etc/ssl/cert.pem",  # macOS system trust store
    "/etc/pki/tls/certs/ca-bundle.crt",  # RHEL, Fedora, CentOS
    "/usr/local/etc/openssl@3/cert.pem",  # Homebrew OpenSSL 3
    "/usr/local/etc/openssl/cert.pem",  # Homebrew OpenSSL
    "/opt/homebrew/etc/openssl@3/cert.pem",  # Apple Silicon Homebrew
)


def _ca_bundle_candidates() -> list[str]:
    """Ordered list of CA bundles to try, best source first."""
    candidates: list[str] = []
    try:
        import certifi  # type: ignore[import-not-found]

        candidates.append(certifi.where())
    except Exception:
        pass
    candidates.extend(CA_BUNDLE_CANDIDATES)
    return candidates


def build_ssl_context() -> ssl.SSLContext:
    """Return a verifying SSL context that actually has root certificates.

    Returns a context that verifies certificates whenever a system trust store
    can be found. If none is available the default context is returned so the
    failure stays visible instead of silently disabling verification.
    """
    for cafile in _ca_bundle_candidates():
        try:
            if not cafile or not Path(cafile).is_file():
                continue
            context = ssl.create_default_context(cafile=cafile)
            if context.get_ca_certs():
                return context
        except Exception:
            continue
    return ssl.create_default_context()


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
    app_type: AppType = AppType.PYTHON
    # Deb-specific fields (used when app_type == AppType.DEB)
    deb_url: str = ""
    deb_sha256: str = ""
    deb_size: int = 0
    architecture: str = "arm64"
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    desktop_file: str = ""


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
        # The repository's own store-apps/ is the catalog *source*, never an
        # installation target. Installing into it would overwrite the published
        # app sources, and uninstalling from it would delete them.
        self.source_dir = self.root_path / STORE_DIRECTORY
        self.store_dir = Path(store_dir).resolve() if store_dir else user_store.resolve()

        self.catalog_path = Path(catalog_path or (self.root_path / CATALOG_PATH)).resolve()
        self.urlopen = urlopen or urllib.request.urlopen
        self.ssl_context = build_ssl_context()
        self._urlopen_accepts_context = self._supports_ssl_context(self.urlopen)

        self.repository = repository
        self.ref = ref
        self.owner, self.repo = self.validate_repository(repository)
        self.validate_ref(ref)

        self._catalog_cache: list[CatalogApp] | None = None
        self.store_dir.mkdir(parents=True, exist_ok=True)

        # Directories that hold apps installed for this user. The catalog source
        # is deliberately excluded so a repository checkout reports its apps as
        # available and installable instead of already installed.
        self.install_dirs: list[Path] = []
        for candidate in (
            self.store_dir,
            Path("/usr/share/neonveil/current") / STORE_DIRECTORY,
        ):
            self._add_unique(self.install_dirs, candidate)

        # Launching also works straight from the catalog source, so a checkout
        # can run its own apps without installing them first.
        self.search_dirs: list[Path] = list(self.install_dirs)
        self._add_unique(self.search_dirs, self.source_dir)

    @staticmethod
    def _supports_ssl_context(urlopen: Callable[..., Any]) -> bool:
        """Whether ``urlopen`` accepts the ``context`` keyword argument."""
        try:
            parameters = inspect.signature(urlopen).parameters
        except (TypeError, ValueError):
            return False
        if "context" in parameters:
            return True
        return any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )

    @staticmethod
    def _add_unique(dirs: list[Path], candidate: Path, require_dir: bool = True) -> None:
        try:
            resolved = candidate.resolve()
        except OSError:
            return
        if require_dir and not resolved.is_dir():
            return
        if resolved not in dirs:
            dirs.append(resolved)

    def _open_url(self, request: Any, timeout: int) -> Any:
        """Open a request, using the discovered CA bundle when supported."""
        if self._urlopen_accepts_context:
            return self.urlopen(request, timeout=timeout, context=self.ssl_context)
        return self.urlopen(request, timeout=timeout)

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
            with self._open_url(req, 15) as resp:
                data = resp.read(MAX_CATALOG_SIZE + 1)
                if len(data) > MAX_CATALOG_SIZE:
                    raise AppStoreError("Der heruntergeladene Katalog überschreitet die Maximalgröße.")
                return json.loads(data.decode("utf-8"))
        except urllib.error.HTTPError as error:
            error.close()
            raise AppStoreError(f"GitHub-Fehler beim Laden des Katalogs (HTTP {error.code}).", str(error)) from error
        except urllib.error.URLError as error:
            raise AppStoreError(
                "GitHub-Katalog ist derzeit nicht erreichbar. Bitte Internetverbindung prüfen.",
                str(error),
            ) from error
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

            summary = str(raw.get("summary", ""))
            description = str(raw.get("description", ""))
            category = str(raw.get("category", "Werkzeuge"))
            author = str(raw.get("author", "NeonVeil"))
            size = int(raw.get("size", 0))

            # Parse app type
            app_type_str = str(raw.get("app_type", "python")).lower()
            try:
                app_type = AppType(app_type_str)
            except ValueError:
                app_type = AppType.PYTHON

            if app_type == AppType.DEB:
                # For deb apps, entry can be a .desktop file name, no strict pattern required
                if not entry:
                    entry = f"{app_id}.desktop"
                
                # Validate deb-specific fields
                deb_url = str(raw.get("deb_url", ""))
                if not deb_url:
                    continue
                deb_sha256 = str(raw.get("deb_sha256", ""))
                if not SHA256_PATTERN.fullmatch(deb_sha256.lower()):
                    continue
                deb_size = int(raw.get("deb_size", 0))
                if deb_size <= 0 or deb_size > MAX_DEB_SIZE:
                    continue
                architecture = str(raw.get("architecture", "arm64")).lower()
                if architecture not in ("arm64", "amd64", "all"):
                    continue
                deps_raw = raw.get("dependencies", [])
                if not isinstance(deps_raw, list):
                    continue
                dependencies = tuple(str(d) for d in deps_raw if isinstance(d, str) and re.fullmatch(r"^[a-z0-9][a-z0-9+.-]{0,40}$", d))
                desktop_file = str(raw.get("desktop_file", ""))

                # For deb apps, files can be empty or contain metadata
                files = raw.get("files", {})
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
                        app_type=AppType.DEB,
                        deb_url=deb_url,
                        deb_sha256=deb_sha256.lower(),
                        deb_size=deb_size,
                        architecture=architecture,
                        dependencies=dependencies,
                        desktop_file=desktop_file,
                    )
                )
            else:
                # Python app validation (existing logic)
                entry = raw.get("entry", f"{app_id}.app:{app_id.capitalize()}Window")
                if not ENTRY_PATTERN.fullmatch(entry):
                    continue

                files = raw.get("files", {})
                if not isinstance(files, dict) or not files:
                    continue

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
        """Scan user installation directories for installed applications."""
        installed_dict: dict[str, InstalledApp] = {}

        for search_dir in self.install_dirs:
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

    def _install_deb_app(
        self,
        cat_app: CatalogApp,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> InstalledApp:
        """Download and install a .deb package with dependency resolution."""
        if cat_app.app_type != AppType.DEB:
            raise AppStoreError("Nur für .deb-Pakete anwendbar.")

        staging_dir = self.store_dir / f".staging_{cat_app.id}_{os.getpid()}"
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        target_dir = self.store_dir / cat_app.id
        deb_path = staging_dir / f"{cat_app.id}.deb"

        try:
            # 1. Download .deb file
            if progress_callback:
                progress_callback(0, 4, f"Lade {cat_app.name} herunter...")
            deb_data = self._download_and_verify(cat_app.deb_url, cat_app.deb_sha256, f"{cat_app.id}.deb")
            if len(deb_data) != cat_app.deb_size:
                raise AppStoreError("Größe des .deb-Pakets stimmt nicht mit dem Katalog überein.")
            deb_path.write_bytes(deb_data)

            # 2. Install dependencies
            if progress_callback:
                progress_callback(1, 4, "Installiere Abhängigkeiten...")
            if cat_app.dependencies:
                self._install_dependencies(cat_app.dependencies, progress_callback)

            # 3. Install .deb package
            if progress_callback:
                progress_callback(2, 4, f"Installiere {cat_app.name}...")
            self._install_deb_package(deb_path, cat_app.architecture)

            # 4. Extract desktop file for application menu
            if progress_callback:
                progress_callback(3, 4, "Richte Anwendung ein...")
            desktop_file = self._extract_desktop_file(deb_path, cat_app.id, cat_app.name, cat_app.desktop_file)

            # 5. Create manifest and finalize
            if progress_callback:
                progress_callback(4, 4, "Installation wird abgeschlossen...")
            now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
            manifest_path = staging_dir / "app.json"
            manifest_data = {
                "id": cat_app.id,
                "name": cat_app.name,
                "version": cat_app.version,
                "entry": cat_app.entry,
                "summary": cat_app.summary,
                "description": cat_app.description,
                "category": cat_app.category,
                "author": cat_app.author,
                "installed": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "app_type": "deb",
                "desktop_file": desktop_file,
            }
            manifest_path.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")

            # Atomically replace destination
            target_dir = self.store_dir / cat_app.id
            if target_dir.exists():
                old_dir = self.store_dir / f".old_{cat_app.id}_{os.getpid()}"
                target_dir.replace(old_dir)
                staging_dir.replace(target_dir)
                shutil.rmtree(target_dir, ignore_errors=True)
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

    def _install_dependencies(
        self,
        dependencies: tuple[str, ...],
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> None:
        """Install apt dependencies."""
        if not dependencies:
            return
        try:
            # Update package list
            subprocess.run(["apt-get", "update"], check=True, capture_output=True)
            # Install dependencies
            cmd = ["apt-get", "install", "-y", "--no-install-recommends"] + list(dependencies)
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            if progress_callback:
                progress_callback(1, 1, f"Abhängigkeiten installiert: {', '.join(dependencies)}")
        except subprocess.CalledProcessError as e:
            raise AppStoreError(
                f"Fehler beim Installieren der Abhängigkeiten: {e.stderr or str(e)}"
            ) from e

    def _install_deb_package(self, deb_path: Path, architecture: str) -> None:
        """Install a .deb package using dpkg."""
        try:
            # Check architecture compatibility
            if architecture != "all" and architecture != "arm64":
                # Could add multiarch support here if needed
                pass
            # Install with dpkg
            subprocess.run(["dpkg", "-i", str(deb_path)], check=True, capture_output=True, text=True)
            # Fix any missing dependencies
            subprocess.run(["apt-get", "install", "-f", "-y"], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            raise AppStoreError(
                f"Fehler beim Installieren des .deb-Pakets: {e.stderr or str(e)}"
            ) from e

    def _extract_desktop_file(self, deb_path: Path, app_id: str, app_name: str, fallback: str) -> str:
        """Extract .desktop file from .deb package for application menu."""
        try:
            # Use dpkg to extract control files and data
            import tarfile
            import tempfile

            with tempfile.TemporaryDirectory() as tmpdir:
                tmpdir = Path(tmpdir)
                # Extract the .deb (ar archive)
                subprocess.run(["ar", "x", str(deb_path)], cwd=tmpdir, check=True, capture_output=True)
                # Find data.tar.xz or data.tar.gz or data.tar
                data_tar = None
                for name in ("data.tar.xz", "data.tar.gz", "data.tar.zst", "data.tar"):
                    candidate = tmpdir / name
                    if candidate.exists():
                        data_tar = candidate
                        break
                if not data_tar:
                    return fallback

                # Extract data.tar
                with tarfile.open(data_tar) as tf:
                    tf.extractall(tmpdir)

                # Look for .desktop files in usr/share/applications/
                apps_dir = tmpdir / "usr" / "share" / "applications"
                if apps_dir.exists():
                    for desktop_file in apps_dir.glob("*.desktop"):
                        content = desktop_file.read_text(encoding="utf-8", errors="ignore")
                        # Check if this desktop file matches our app
                        if app_id in content or app_name.lower() in content.lower():
                            return content

            return fallback
        except Exception:
            return fallback

    def install_app(
        self,
        app_id: str,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> InstalledApp:
        """Download and install an application from GitHub with integrity checks."""
        cat_app = self.get_catalog_app(app_id)
        if not cat_app:
            raise AppStoreError(f"Anwendung '{app_id}' wurde im Store-Katalog nicht gefunden.")

        if cat_app.app_type == AppType.DEB:
            return self._install_deb_app(cat_app, progress_callback)

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
        # Local fallback, only useful in a repository checkout or in tests: the
        # published file is identical to the one in the catalog. It never
        # replaces a real download when one is possible.
        candidate_local = self.root_path / STORE_DIRECTORY / rel_path

        data: bytes | None = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NeonVeil-AppStore/1.0"})
            with self._open_url(req, 20) as resp:
                data = resp.read(MAX_FILE_SIZE + 1)
                if len(data) > MAX_FILE_SIZE:
                    raise AppStoreError(f"Datei '{rel_path}' überschreitet das Größenlimit.")
        except urllib.error.HTTPError as error:
            error.close()
            if candidate_local.is_file():
                data = candidate_local.read_bytes()
            else:
                raise AppStoreError(
                    f"Download von '{Path(rel_path).name}' von GitHub fehlgeschlagen (HTTP {error.code}). "
                    f"Bitte Internetverbindung und Repository '{self.repository}' prüfen.",
                    str(error),
                ) from error
        except urllib.error.URLError as error:
            # Covers DNS, connection refused, timeouts and TLS trust failures.
            if candidate_local.is_file():
                data = candidate_local.read_bytes()
            else:
                raise AppStoreError(
                    f"Download von '{Path(rel_path).name}' fehlgeschlagen. "
                    "Bitte Internetverbindung prüfen; für HTTPS müssen "
                    "CA-Zertifikate installiert sein.",
                    str(error),
                ) from error
        except Exception as error:
            if candidate_local.is_file():
                data = candidate_local.read_bytes()
            else:
                raise AppStoreError(
                    f"Download von '{Path(rel_path).name}' fehlgeschlagen: {error}",
                    str(error),
                ) from error

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
