from __future__ import annotations

import argparse
import logging
import os
import pwd
import subprocess
import sys
from pathlib import Path

if __package__ in {None, ""}:
    release_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(release_root))

from update_manager.core import CHANNELS, UpdateError, UpdateManager


SYSTEM_ROOT = Path("/usr/share/neonveil")


def _user_home() -> Path:
    if os.geteuid() == 0:
        for key in ("PKEXEC_UID", "SUDO_UID"):
            value = os.environ.get(key, "")
            if value.isdecimal():
                return Path(pwd.getpwuid(int(value)).pw_dir)
    return Path.home()


def _installed_version() -> str:
    candidates = (
        SYSTEM_ROOT / "current/VERSION",
        Path(__file__).resolve().parent.parent / "VERSION",
    )
    for candidate in candidates:
        try:
            version = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if version:
            return version
    raise UpdateError("Die installierte MyOS-Version konnte nicht gelesen werden.")


def _elevate(command: str) -> int:
    if not Path("/usr/bin/pkexec").is_file() or not Path("/usr/bin/myos-update").is_file():
        raise UpdateError("Die Systemberechtigung für diesen Vorgang ist nicht verfügbar.")
    try:
        completed = subprocess.run(
            ["/usr/bin/pkexec", "/usr/bin/myos-update", command],
            check=False,
            text=True,
        )
    except OSError as error:
        raise UpdateError("Die Systemberechtigung konnte nicht angefordert werden.", str(error)) from error
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="myos-update",
        description="MyOS-Aktualisierungen sicher verwalten.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "download", "install", "status", "rollback", "history"):
        subparsers.add_parser(name)
    channel_parser = subparsers.add_parser("channel")
    channel_parser.add_argument("name", nargs="?", choices=CHANNELS)
    return parser


def main(arguments: list[str] | None = None) -> int:
    args = build_parser().parse_args(arguments)
    if (
        os.geteuid() == 0
        and "PKEXEC_UID" in os.environ
        and args.command not in {"install", "rollback"}
    ):
        print(
            "Dieser privilegierte Update-Befehl ist nicht erlaubt.",
            file=sys.stderr,
        )
        return 1
    home = _user_home()
    log_path = home / ".local/state/myos/update/myos-update.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=log_path,
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
        )
    except OSError:
        logging.basicConfig(level=logging.WARNING)
        logging.getLogger("myos.update").warning(
            "Das technische Update-Log kann nicht angelegt werden."
        )
    manager: UpdateManager | None = None
    try:
        manager = UpdateManager(current_version=_installed_version(), home=home)
        if args.command == "channel":
            if args.name is None:
                print(manager.channel)
            else:
                manager.save_preferences(channel=args.name)
                print(f"Update-Kanal: {manager.channel}")
            return 0
        if args.command == "status":
            print(f"MyOS-Version: {manager.current_version}")
            print(f"Update-Kanal: {manager.channel}")
            print(manager.status().get("message", "Noch kein Updatevorgang."))
            return 0
        if args.command == "history":
            entries = manager.history()
            if not entries:
                print("Noch keine Updates installiert.")
            for entry in entries:
                print(
                    f"{entry['version']} · {entry['channel']} · "
                    f"{entry['status']} · {entry['date']}"
                )
            return 0
        if args.command == "rollback":
            if os.geteuid() != 0:
                return _elevate("rollback")
            print(f"MyOS-Version {manager.rollback()} wurde wiederhergestellt.")
            return 0
        release = manager.check_for_updates()
        if release is None:
            print("MyOS ist auf dem neuesten Stand.")
            return 0
        if args.command == "check":
            print(f"Update verfügbar: MyOS {release.version} ({release.channel})")
            print(f"Veröffentlicht: {release.release_date}")
            print(f"Größe: {release.size} Byte")
            return 0
        if args.command == "download":
            print(f"Update gespeichert unter: {manager.download(release)}")
            return 0
        if args.command == "install":
            if os.geteuid() != 0:
                return _elevate("install")
            if not manager.downloaded_file(release).is_file():
                raise UpdateError("Bitte lade das Update zuerst herunter.")
            manager.install(release)
            print(f"MyOS {release.version} ist installiert. Bitte starte das System neu.")
            return 0
    except UpdateError as error:
        logging.getLogger("myos.update").error(
            "%s Detail: %s", error.user_message, error
        )
        if manager is not None:
            manager.record_status("error", error.user_message)
        print(error.user_message, file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        logging.getLogger("myos.update").exception("Updatevorgang fehlgeschlagen.")
        if manager is not None:
            manager.record_status(
                "error", "Der Updatevorgang ist fehlgeschlagen. Details stehen im Update-Log."
            )
        print("Der Updatevorgang ist fehlgeschlagen. Details stehen im Update-Log.", file=sys.stderr)
        return 1
    except Exception:
        logging.getLogger("myos.update").exception("Unerwarteter Fehler im Update-Manager.")
        if manager is not None:
            manager.record_status(
                "error", "Der Updatevorgang ist fehlgeschlagen. Details stehen im Update-Log."
            )
        print("Der Updatevorgang ist fehlgeschlagen. Details stehen im Update-Log.", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
