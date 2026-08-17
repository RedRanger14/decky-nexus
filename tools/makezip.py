"""Build the release zip with POSIX separators.

PowerShell's Compress-Archive writes Windows path separators into zip
entries, so Linux tools see one file named "Nexus-Mods\LICENSE" rather than
a folder, and Decky's installer hangs on "PARSING ZIP FILE". Windows hides
this on read, so it survived a verification pass.
"""
import os
import sys
import zipfile


def main(stage: str, out: str) -> int:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(stage):
            for name in sorted(files):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, stage).replace(os.sep, "/")
                z.write(full, rel)
    names = zipfile.ZipFile(out).namelist()
    bad = [n for n in names if "\\" in n]
    if bad:
        print("backslashes in zip entries:", bad[:3])
        return 1
    print(len(names), "entries, all forward-slashed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
