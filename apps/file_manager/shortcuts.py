from __future__ import annotations

import configparser
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QUrl


def create_desktop_shortcut(
    desktop_path: Path,
    name: str,
    *,
    target: Path | None = None,
    app_id: str | None = None,
) -> Path:
    if (target is None) == (app_id is None):
        raise ValueError("Eine Verknüpfung benötigt genau ein Ziel.")
    if app_id is not None and not re.fullmatch(r"[a-z0-9-]+", app_id):
        raise ValueError("Ungültige MyOS-Anwendung.")

    desktop_path = desktop_path.expanduser().absolute()
    desktop_path.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[\x00-\x1f/\\\\]", "_", name).strip(" .") or "Verknüpfung"
    destination = desktop_path / f"{safe_name}.desktop"
    suffix = 2
    while destination.exists():
        destination = desktop_path / f"{safe_name} ({suffix}).desktop"
        suffix += 1

    url = (
        f"myos-app://{app_id}"
        if app_id is not None
        else QUrl.fromLocalFile(str(target.expanduser().absolute())).toString()
    )
    content = (
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Link\n"
        f"Name={safe_name}\n"
        f"URL={url}\n"
        "Icon=application-x-executable\n"
    )
    temporary = destination.with_suffix(".desktop.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(destination)
    return destination


def read_desktop_shortcut(path: Path) -> tuple[Path | None, str | None]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError) as error:
        raise ValueError(f"Die Verknüpfung ist ungültig: {error}") from error
    if not parser.has_section("Desktop Entry"):
        raise ValueError("Die Verknüpfung enthält keinen Desktop-Entry.")
    entry = parser["Desktop Entry"]
    if entry.get("Type") != "Link":
        raise ValueError("Nur sichere MyOS-Datei- und Anwendungsverknüpfungen werden geöffnet.")
    address = entry.get("URL", "")
    if address.startswith("myos-app://"):
        app_id = address.removeprefix("myos-app://")
        if not re.fullmatch(r"[a-z0-9-]+", app_id):
            raise ValueError("Die Anwendungsverknüpfung ist ungültig.")
        return None, app_id

    parsed = urlsplit(address)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("Die Verknüpfung verweist nicht auf eine lokale Datei.")
    target = Path(unquote(parsed.path))
    if not target.is_absolute():
        raise ValueError("Das Verknüpfungsziel muss ein absoluter Pfad sein.")
    return target, None
