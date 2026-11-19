"""Archive the portable app without bundling live captures or session data."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import zipfile


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "release" / "CoalVolume"
ARCHIVE = ROOT / "release" / "CoalVolume_Windows_x64_v1.0.0.zip"


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def main():
    if not (APP / "CoalVolume.exe").is_file():
        raise RuntimeError("Run tools/build_windows.ps1 first")
    files = sorted(path for path in APP.rglob("*") if path.is_file()
                   and path.relative_to(APP).parts[0] != "runs"
                   and path.name != "package_manifest.json")
    manifest = {"version": "1.0.0", "platform": "Windows x64",
                "created_at": datetime.now().astimezone().isoformat(),
                "files": [{"path": path.relative_to(APP).as_posix(), "size": path.stat().st_size,
                           "sha256": digest(path)} for path in files],
                "sources": {name: digest(ROOT / "tools" / name) for name in
                            ("live_coal_volume.py", "measure_coal_volume.py", "capture_rgbd.py", "coal_volume_app.py")}}
    manifest_path = APP / "package_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with zipfile.ZipFile(ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files + [manifest_path]:
            archive.write(path, "CoalVolume/" + path.relative_to(APP).as_posix())
    with zipfile.ZipFile(ARCHIVE) as archive:
        bad_file = archive.testzip()
        if bad_file:
            raise RuntimeError(f"Archive CRC validation failed: {bad_file}")
        if any("/runs/" in name for name in archive.namelist()):
            raise RuntimeError("Live session data must not be included in the portable archive")
    checksum = digest(ARCHIVE)
    ARCHIVE.with_suffix(".zip.sha256").write_text(f"{checksum}  {ARCHIVE.name}\n", encoding="ascii")
    print(json.dumps({"archive": str(ARCHIVE), "archive_mb": ARCHIVE.stat().st_size / 1024 ** 2,
                      "uncompressed_mb": sum(p.stat().st_size for p in files) / 1024 ** 2,
                      "files": len(files) + 1, "crc": "passed", "sha256": checksum}, indent=2))


if __name__ == "__main__":
    main()
