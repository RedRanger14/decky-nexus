"""Generate the Decky custom-store index from the current release.

Decky fetches <store url>?sort_by=... and expects a JSON array of plugins.
Schema, read from decky-loader/frontend/src/store.tsx rather than guessed:

    { id, name, author, description, tags[], image_url,
      versions: [ { name, hash, artifact } ] }

`hash` is the sha256 of the zip and Decky VERIFIES it before extracting, so
this must be generated from the actual release artifact rather than written
by hand. `name` must match plugin.json's name, which is the folder Decky
installs into.

Usage:  python tools/makestore.py dist/Nexus-Mods-0.259.0.zip
"""
import hashlib
import io
import json
import os
import sys

REPO = "RedRanger14/decky-nexus"


def main(zip_path: str) -> int:
    if not os.path.isfile(zip_path):
        print("no such file:", zip_path)
        return 1
    with open(zip_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()

    pkg = json.load(io.open("package.json", encoding="utf-8"))
    plug = json.load(io.open("plugin.json", encoding="utf-8"))
    version = pkg["version"]

    entry = {
        "id": 1,
        "name": plug["name"],
        "author": plug.get("author") or pkg.get("author", ""),
        "description": plug.get("description") or pkg.get("description", ""),
        "tags": ["mods", "nexus", "unofficial", "beta"],
        "image_url": (
            f"https://raw.githubusercontent.com/{REPO}/main/store/banner.png"
        ),
        "versions": [{
            "name": version,
            "hash": digest,
            "artifact": (
                f"https://github.com/{REPO}/releases/download/"
                f"v{version}/{os.path.basename(zip_path)}"
            ),
        }],
    }

    os.makedirs("store", exist_ok=True)

    # Keep older releases listed. The schema takes a list, and a version
    # somebody already installed should not vanish out from under them.
    versions = [entry["versions"][0]]
    try:
        previous = json.load(
            io.open(os.path.join("store", "plugins"), encoding="utf-8")
        )
        for old in previous[0].get("versions", []):
            if old.get("name") != version:
                versions.append(old)
    except (OSError, ValueError, IndexError, KeyError):
        pass
    entry["versions"] = versions

    # BOTH files, every time. "plugins" carries no extension because Decky
    # appends "?sort_by=..." to whatever URL it is given, so an index served
    # directly has to live at exactly that path; "plugins.json" is what the
    # Cloudflare worker fetches from raw.githubusercontent. They drifted
    # once - plugins.json sat two releases behind, and it is the one the
    # worker would have served - so writing one without the other is not an
    # option this script offers.
    for name in ("plugins", "plugins.json"):
        out = os.path.join("store", name)
        io.open(out, "w", encoding="utf-8", newline="\n").write(
            json.dumps([entry], indent=2) + "\n"
        )
    print(f"store/plugins + store/plugins.json: {plug['name']} v{version}")
    print(f"  sha256   {digest}")
    print(f"  artifact {entry['versions'][0]['artifact']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
