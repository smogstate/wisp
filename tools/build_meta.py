"""Used by the build workflow, on the machine that builds Wisp.exe.

    python tools/build_meta.py stamp
        writes wisp/version.json: the commit, the time, and the "runtime": which exe this
        code needs. The runtime changes when the libraries (requirements.lock: the exact
        versions inside the exe) or the launcher (wisp_app.py) change, and only then does an
        update replace the exe.

    python tools/build_meta.py pack dist/Wisp.exe
        writes wisp-code.zip (Wisp's own code and its addon) and version.json, the file a
        running Wisp reads to learn there is something newer and to check what it downloads.
"""
import hashlib
import json
import os
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stamp():
    # line endings differ between checkouts; the runtime must not
    parts = [(ROOT / name).read_bytes().replace(b"\r\n", b"\n") for name in ("requirements.lock", "wisp_app.py")]
    info = {"commit": os.environ["GITHUB_SHA"], "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "runtime": sha(b"\0".join(parts))[:12]}
    (ROOT / "wisp" / "version.json").write_text(json.dumps(info), encoding="utf-8")
    # the addon says in every snapshot which Wisp it came with, so that Wisp can tell when the game still runs the one before
    addon = ROOT / "addon" / "Wisp" / "Wisp.lua"
    source, mark = addon.read_bytes(), b'local ADDON_BUILD = "dev"'
    assert source.count(mark) == 1, "the addon's version line is gone"
    addon.write_bytes(source.replace(mark, b'local ADDON_BUILD = "%s"' % info["commit"][:7].encode()))
    print(info)


def pack(exe: Path):
    info = json.loads((ROOT / "wisp" / "version.json").read_text(encoding="utf-8"))
    archive = ROOT / "wisp-code.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, prefix in ((ROOT / "wisp", "wisp"), (ROOT / "addon" / "Wisp", "addon/Wisp")):
            for file in sorted(folder.rglob("*")):
                if file.is_file() and "__pycache__" not in file.parts:
                    z.write(file, prefix + "/" + file.relative_to(folder).as_posix())
    code = archive.read_bytes()
    info.update(sha256=sha(exe.read_bytes()), size=exe.stat().st_size, code_sha256=sha(code), code_size=len(code))
    (ROOT / "version.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    print(json.dumps(info, indent=1))


if __name__ == "__main__":
    stamp() if sys.argv[1] == "stamp" else pack(Path(sys.argv[2]))
