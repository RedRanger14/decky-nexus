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
import zipfile

REPO = "RedRanger14/decky-nexus"

# The six files release.ps1 stages. A zip missing any of them installs a
# plugin that cannot load.
REQUIRED = ("plugin.json", "package.json", "main.py", "dist/index.js")


def check_zip(zip_path: str, plugin_name: str, version: str) -> list:
    """Everything that would make Decky or install.sh refuse this zip.

    This runs at the one step nobody can skip on the way to publishing: the
    store index is generated FROM the artifact, so a zip that cannot install
    must not be able to produce an index for itself.

    It exists because the checks living in release.ps1's comments were not
    enough. v1.4.0 shipped with its top folder named "Nexus-Mods" against a
    plugin.json name of "Nexus Mods", because the zip was built by hand
    instead of by release.ps1 - the identical mismatch release.ps1 was
    written to prevent, and which Decky reports by sitting on "PARSING ZIP
    FILE" forever rather than erroring. install.sh is blunter about it and
    dies with "extraction did not produce Nexus Mods/plugin.json".
    """
    problems = []
    try:
        infos = zipfile.ZipFile(zip_path).infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"not a readable zip: {exc}"]
    if not infos:
        return ["the zip is empty"]
    names = [i.filename for i in infos]

    # Windows separators: a Linux tool sees one file called
    # "Nexus Mods\LICENSE" rather than a folder. Invisible on Windows,
    # which is how it shipped once already.
    #
    # Read from orig_filename, NOT namelist(). On Windows zipfile rewrites
    # os.sep to "/" as it reads, so namelist() reports a clean name for a
    # zip whose bytes are broken - which would have made this check dead
    # code on the very machine where release zips are built. orig_filename
    # is the name as stored, on both platforms.
    backslashed = [i.orig_filename for i in infos if "\\" in i.orig_filename]
    if backslashed:
        problems.append(
            f"{len(backslashed)} entries use Windows separators "
            f"(e.g. {backslashed[0]!r}) - build with tools/makezip.py"
        )

    tops = {n.split("/")[0] for n in names if "\\" not in n}
    if len(tops) != 1:
        problems.append(f"expected ONE top-level folder, found {sorted(tops)}")
    elif tops != {plugin_name}:
        problems.append(
            f"top-level folder is {tops.pop()!r} but plugin.json says "
            f"{plugin_name!r} - Decky and install.sh both use the "
            f"plugin.json name, so the two must match exactly"
        )
    else:
        for rel in REQUIRED:
            if f"{plugin_name}/{rel}" not in names:
                problems.append(f"missing {plugin_name}/{rel}")

    # The version in the zip is the one that will run, whatever the
    # filename claims.
    try:
        inner = json.loads(
            zipfile.ZipFile(zip_path)
            .read(f"{plugin_name}/package.json")
            .decode("utf-8")
        )
        if inner.get("version") != version:
            problems.append(
                f"zip contains version {inner.get('version')!r} but this "
                f"repo is at {version!r}"
            )
    except (KeyError, OSError, ValueError):
        pass  # already reported as a missing file above
    return problems


def main(zip_path: str) -> int:
    if not os.path.isfile(zip_path):
        print("no such file:", zip_path)
        return 1

    pkg = json.load(io.open("package.json", encoding="utf-8"))
    plug = json.load(io.open("plugin.json", encoding="utf-8"))
    version = pkg["version"]

    problems = check_zip(zip_path, plug["name"], version)
    if problems:
        print(f"REFUSING to index {zip_path}: this zip will not install.")
        for p in problems:
            print("  -", p)
        print("\nBuild it with .\\release.ps1, which takes the folder name")
        print("from plugin.json, then run this again.")
        return 1

    with open(zip_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()

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
