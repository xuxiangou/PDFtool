import os
import shutil
import sys


def _ensure_backend_entry(target_dir: str) -> None:
    expected = os.path.join(target_dir, "pdf_backend.exe")
    if os.path.isfile(expected):
        return

    legacy = os.path.join(target_dir, "pdf_backend_slim.exe")
    if os.path.isfile(legacy):
        shutil.copy2(legacy, expected)
        print(f"[stage_backend] created backend entry: {expected}")
        return

    candidates = [
        item
        for item in os.listdir(target_dir)
        if item.lower().startswith("pdf_backend") and item.lower().endswith(".exe")
    ]
    if candidates:
        src = os.path.join(target_dir, candidates[0])
        shutil.copy2(src, expected)
        print(f"[stage_backend] mapped {src} -> {expected}")
        return

    raise RuntimeError(f"No backend executable found in {target_dir}")


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: stage_backend.py <source_dir>")

    source = os.path.normpath(sys.argv[1])
    target = os.path.normpath(os.path.join("dist", "pdf_backend"))

    if not os.path.isdir(source):
        raise SystemExit(f"Source backend directory not found: {source}")

    abs_source = os.path.abspath(source)
    abs_target = os.path.abspath(target)
    if abs_source == abs_target:
        _ensure_backend_entry(target)
        print(f"[stage_backend] Source already staged: {target}")
        return 0

    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(source, target)
    _ensure_backend_entry(target)
    print(f"[stage_backend] staged {source} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
