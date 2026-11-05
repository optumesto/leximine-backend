# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Pull real Minecraft localisation files out of your own installation.

The backend now fetches a missing language by itself the first time a
challenge asks for one, so this is mostly for pre-loading several at once or
for seeing what your install actually has.

    python scripts/extract_lang.py                      # es_es + en_us
    python scripts/extract_lang.py --langs es_es fr_fr ja_jp
    python scripts/extract_lang.py --list

Nothing is downloaded from the internet: if a language is not already in your
assets folder, launch the game once with it selected and re-run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import mcassets  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "lang"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mc-root", type=Path, default=mcassets.DEFAULT_MC_ROOT)
    parser.add_argument("--version", default="1.20.4")
    parser.add_argument("--langs", nargs="+", default=["es_es"])
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--list", action="store_true",
                        help="list the languages your install can provide")
    args = parser.parse_args()

    if not args.mc_root.is_dir():
        raise SystemExit(f"minecraft root not found: {args.mc_root}  (pass --mc-root)")

    if args.list:
        codes = mcassets.available_languages(args.mc_root, args.version)
        print(f"{len(codes)} languages available:\n")
        for row in range(0, len(codes), 8):
            print("  " + "  ".join(codes[row:row + 8]))
        return 0

    print(f"minecraft root : {args.mc_root}")
    print(f"output         : {args.out}\n")

    written = []
    for code in args.langs:
        path = mcassets.extract_language(code, args.out, root=args.mc_root,
                                         version=args.version)
        if path:
            count = len(json.loads(path.read_text(encoding="utf-8")))
            print(f"   {path.name}  ({count} keys)")
            written.append(code)
        else:
            print(f"!! {code} could not be extracted")

    english = mcassets.extract_en_us(args.out, root=args.mc_root, version=args.version)
    if english:
        count = len(json.loads(english.read_text(encoding="utf-8")))
        print(f"   {english.name} ({count} keys)")

    if not written:
        print("\nNothing written; the backend will fall back to its built-in table.")
        return 1
    print(f"\nDone: {', '.join(written)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
