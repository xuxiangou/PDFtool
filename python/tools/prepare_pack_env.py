import importlib.metadata
import os
import subprocess
import sys
from typing import List


def _has_dist(name: str) -> bool:
    try:
        importlib.metadata.version(name)
        return True
    except importlib.metadata.PackageNotFoundError:
        return False


def _run_pip(args: List[str]) -> None:
    cmd = [sys.executable, "-m", "pip", *args]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")


def _ensure_cv2_runtime() -> None:
    try:
        import cv2  # noqa: F401
    except Exception as e:
        raise RuntimeError(
            "OpenCV runtime is not available in venv_pack. "
            "Install opencv-python-headless before packing."
        ) from e


def _ensure_formula_runtime() -> None:
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "modelscope")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    missing = [name for name in ("safetensors", "tokenizers") if not _has_dist(name)]
    if missing:
        raise RuntimeError(
            "Formula OCR runtime dependencies are missing in venv_pack: "
            + ", ".join(missing)
            + ". Install requirements-ocr.txt before packing."
        )
    try:
        import safetensors.paddle  # noqa: F401
        from tokenizers import Tokenizer  # noqa: F401
        from paddleocr import FormulaRecognitionPipeline  # noqa: F401
    except Exception as e:
        raise RuntimeError(
            "PaddleOCR formula runtime is not available in venv_pack. "
            "Install/upgrade paddleocr with formula recognition support before packing."
        ) from e


def main() -> int:
    contrib_pkgs = [
        "opencv-contrib-python",
        "opencv-contrib-python-headless",
    ]
    installed_contrib = [name for name in contrib_pkgs if _has_dist(name)]
    if installed_contrib:
        print(f"[prepare_pack_env] Removing contrib OpenCV packages: {installed_contrib}")
        _run_pip(["uninstall", "-y", *installed_contrib])
    else:
        print("[prepare_pack_env] No contrib OpenCV package detected.")

    if not _has_dist("opencv-python-headless"):
        raise RuntimeError(
            "opencv-python-headless is required but not installed in venv_pack. "
            "Please install it and retry."
        )

    _ensure_cv2_runtime()
    import cv2

    print(f"[prepare_pack_env] Using cv2 version: {getattr(cv2, '__version__', 'unknown')}")
    _ensure_formula_runtime()
    print("[prepare_pack_env] PaddleOCR formula runtime: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
