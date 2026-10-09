"""NeonVeil App Store (GitHub-backed)."""

from .core import (
    ALLOWED_EXTENSIONS,
    ALLOWED_HOSTS,
    CATALOG_PATH,
    DEFAULT_REF,
    DEFAULT_REPOSITORY,
    STORE_DIRECTORY,
    AppStore,
    AppStoreError,
    CatalogApp,
    InstalledApp,
)

__all__ = [
    "ALLOWED_EXTENSIONS",
    "ALLOWED_HOSTS",
    "CATALOG_PATH",
    "DEFAULT_REF",
    "DEFAULT_REPOSITORY",
    "STORE_DIRECTORY",
    "AppStore",
    "AppStoreError",
    "CatalogApp",
    "InstalledApp",
]
