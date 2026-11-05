# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Pull localisation files out of a local Minecraft installation.

Translated language files are not in the version jar -- the launcher stores
them content-addressed under assets/objects, with assets/indexes/<id>.json as
the filename -> hash map. en_us.json is the exception and lives in the jar.

Shared by scripts/extract_lang.py and by the dictionary registry, which uses
it to fetch a language the first time somebody asks for one that is not on
disk yet. That is what makes changing language in the mod's settings work
without a separate manual step.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_MC_ROOT = Path(os.environ.get("APPDATA", str(Path.home()))) / ".minecraft"
_RELEASE = re.compile(r"^\d+(\.\d+)*$")


def version_tuple(name: str) -> tuple[int, ...]:
    return tuple(int(part) for part in name.split("."))


def installed_versions(root: Path) -> dict[str, str]:
    """{version name: asset index id} for every installed release."""
    found: dict[str, str] = {}
    versions = root / "versions"
    if not versions.is_dir():
        return found
    for child in sorted(versions.iterdir()):
        meta = child / f"{child.name}.json"
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        index_id = (data.get("assetIndex") or {}).get("id") or data.get("assets")
        if index_id:
            found[child.name] = str(index_id)
    return found


def pick_version(root: Path, wanted: str) -> tuple[str, str]:
    """(version name, asset index id), falling back to the closest release."""
    versions = installed_versions(root)
    if not versions:
        raise FileNotFoundError(f"no installed versions under {root / 'versions'}")
    if wanted in versions:
        return wanted, versions[wanted]

    releases = [name for name in versions if _RELEASE.match(name)]
    if not releases:
        raise FileNotFoundError(f"{wanted} not installed and no releases found")

    want = version_tuple(wanted) if _RELEASE.match(wanted) else ()
    if not want:
        best = sorted(releases, key=version_tuple)[-1]
        return best, versions[best]

    def distance(name: str) -> tuple[int, ...]:
        got = version_tuple(name)
        same_line = 0 if got[:2] == want[:2] else 1
        gap = abs((got + (0, 0))[2] - (want + (0, 0))[2]) if same_line == 0 else 0
        return (same_line, gap, -got[0], -(got + (0,))[1])

    best = sorted(releases, key=distance)[0]
    log.info("%s is not installed; using %s for language assets", wanted, best)
    return best, versions[best]


def available_languages(root: Path = DEFAULT_MC_ROOT, version: str = "1.20.4") -> list[str]:
    """Language codes present in the asset index, whether or not downloaded."""
    try:
        _, index_id = pick_version(root, version)
    except FileNotFoundError:
        return []
    index_path = root / "assets" / "indexes" / f"{index_id}.json"
    if not index_path.is_file():
        return []
    objects = json.loads(index_path.read_text(encoding="utf-8")).get("objects", {})
    return sorted(
        key.rsplit("/", 1)[-1][:-5]
        for key in objects
        if key.startswith("minecraft/lang/") and key.endswith(".json")
    )


def extract_language(code: str, out_dir: Path, *, root: Path = DEFAULT_MC_ROOT,
                     version: str = "1.20.4") -> Path | None:
    """Copy one language file into out_dir. Returns the path, or None."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if code == "en_us":
        return extract_en_us(out_dir, root=root, version=version)

    try:
        _, index_id = pick_version(root, version)
    except FileNotFoundError as exc:
        log.warning("cannot extract %s: %s", code, exc)
        return None

    index_path = root / "assets" / "indexes" / f"{index_id}.json"
    if not index_path.is_file():
        log.warning("asset index missing: %s", index_path)
        return None

    objects = json.loads(index_path.read_text(encoding="utf-8")).get("objects", {})
    meta = objects.get(f"minecraft/lang/{code}.json")
    if not meta:
        log.warning("%s is not a Minecraft language code", code)
        return None

    digest = meta["hash"]
    blob = root / "assets" / "objects" / digest[:2] / digest
    if not blob.is_file():
        # The launcher downloads assets lazily in some configurations.
        log.warning("%s is in the index but not downloaded; launch the game "
                    "once with that language selected", code)
        return None

    dest = out_dir / f"{code}.json"
    shutil.copyfile(blob, dest)
    log.info("extracted %s (%d keys)", code,
             len(json.loads(dest.read_text(encoding="utf-8"))))
    return dest


def extract_en_us(out_dir: Path, *, root: Path = DEFAULT_MC_ROOT,
                  version: str = "1.20.4") -> Path | None:
    """en_us lives inside the version jar rather than the asset store."""
    try:
        name, _ = pick_version(root, version)
    except FileNotFoundError:
        return None
    jar = root / "versions" / name / f"{name}.jar"
    if not jar.is_file():
        log.warning("version jar not found: %s", jar)
        return None
    member = "assets/minecraft/lang/en_us.json"
    with zipfile.ZipFile(jar) as archive:
        if member not in archive.namelist():
            return None
        data = archive.read(member)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / "en_us.json"
    dest.write_bytes(data)
    return dest
