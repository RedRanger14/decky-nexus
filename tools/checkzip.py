"""Refuse a release zip that will not install.

Standalone so release.ps1 can gate on it immediately after building, rather
than the mistake only surfacing at index time (or, as happened with v1.4.0,
not at all until a user could not update). The checks themselves live in
makestore.py, which is the step nobody can skip on the way to publishing.

Usage:  python tools/checkzip.py dist/Nexus-Mods-1.4.0.zip
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from makestore import check_zip  # noqa: E402


def main(zip_path: str) -> int:
    if not os.path.isfile(zip_path):
        print("no such file:", zip_path)
        return 1
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pkg = json.load(io.open(os.path.join(root, "package.json"), encoding="utf-8"))
    plug = json.load(io.open(os.path.join(root, "plugin.json"), encoding="utf-8"))
    problems = check_zip(zip_path, plug["name"], pkg["version"])
    if problems:
        print(f"This zip will not install ({os.path.basename(zip_path)}):")
        for p in problems:
            print("  -", p)
        return 1
    print(
        f"OK: {os.path.basename(zip_path)} installs as "
        f"{plug['name']!r} v{pkg['version']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
