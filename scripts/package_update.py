#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from update_manager import DEFAULT_REPOSITORY, infer_channel, parse_version

PACKAGE_DIRECTORIES = (
    "apps",
    "appstore",
    "assets",
    "desktop",
    "update_manager",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Erstellt ein versioniertes NeonVeil-ARM64-Updatepaket."
    )
    parser.add_argument("--channel", choices=("stable", "beta", "developer"), required=True)
    parser.add_argument("--changelog", required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build/releases")
    args = parser.parse_args()

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    parse_version(version)
    if infer_channel(version) != args.channel:
        parser.error(f"VERSION {version} gehört zum Kanal {infer_channel(version)}.")
    if not args.changelog.strip():
        parser.error("--changelog darf nicht leer sein.")
    if not re.fullmatch(r"https://github\.com/[^/]+/[^/]+", DEFAULT_REPOSITORY):
        parser.error("Das offizielle GitHub-Repository ist ungültig.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    asset_name = f"MyOS-{version}-arm64.tar.gz"
    archive = args.output_dir / asset_name
    included_paths = [ROOT / "VERSION"] + [
        ROOT / directory for directory in PACKAGE_DIRECTORIES if (ROOT / directory).exists()
    ]
    with tarfile.open(archive, mode="w:gz", compresslevel=6) as output:
        for source in included_paths:
            output.add(
                source,
                arcname=source.relative_to(ROOT).as_posix(),
                filter=lambda item: None
                if "__pycache__" in Path(item.name).parts
                or item.name.endswith((".pyc", ".pyo"))
                else item,
            )

    owner_repo = DEFAULT_REPOSITORY.removeprefix("https://github.com/")
    tag = f"v{version}"
    asset_url = f"https://github.com/{owner_repo}/releases/download/{tag}/{asset_name}"
    manifest = {
        "schema": 1,
        "version": version,
        "channel": args.channel,
        "release_date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "architecture": "arm64",
        "platform": "raspberry-pi-4",
        "changelog": args.changelog.strip(),
        "asset": asset_name,
        "download_url": asset_url,
        "size": archive.stat().st_size,
        "sha256": _sha256(archive),
    }
    metadata_path = args.output_dir / "myos-update.json"
    metadata_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Updatepaket: {archive}")
    print(f"Release-Metadaten: {metadata_path}")
    print(f"SHA-256: {manifest['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
