import json
import os
import shutil


def _read_version() -> str:
    with open("package.json", "r", encoding="utf-8") as f:
        pkg = json.load(f)
    return str(pkg.get("version", "")).strip()


def main() -> int:
    version = _read_version()
    if not version:
        print("[mark_slim_artifact] skip: package version not found")
        return 0

    src_name = f"PDFusion Setup {version}.exe"
    src_path = os.path.join("dist", src_name)
    if not os.path.isfile(src_path):
        print(f"[mark_slim_artifact] skip: installer not found: {src_path}")
        return 0

    dst_name = f"PDFusion Setup {version} Slim.exe"
    dst_path = os.path.join("dist", dst_name)
    shutil.copy2(src_path, dst_path)
    print(f"[mark_slim_artifact] created: {dst_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
