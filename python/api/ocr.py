import os
import shutil
import tempfile
import threading
import time
import inspect
import sys
import re
import math
import unicodedata
from io import BytesIO
from numbers import Real
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Dict, List, Literal, Optional, Tuple

import fitz  # PyMuPDF
from PIL import Image
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


DEFAULT_OCR_DPI = 200
MIN_OCR_DPI = 96
MAX_OCR_DPI = 400
OCR_TIMEOUT_SECONDS = 180
MODEL_LANG_DEFAULT = "ch"
FORMULA_MODEL_DEFAULT = "PP-FormulaNet_plus-M"
LAYOUT_MODEL_DEFAULT = "PP-DocLayout-S"
DEFAULT_OCR_CPU_THREADS = 4
MAX_OCR_SEGMENTS_PER_BLOCK = 96
MAX_OCR_ALIGN_SEGMENTS_PER_LINE = 256
OCR_APPLY_CANCELLED_DETAIL = "OCR apply cancelled by user"
NETWORK_ERROR_MARKERS: Tuple[str, ...] = (
    "winerror 10013",
    "failed to establish a new connection",
    "max retries exceeded",
    "no model source is available",
    "no model hoster is available",
    "connectionerror",
    "connecterror",
)


def _is_dir_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".pdfusion_write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.unlink(probe)
        return True
    except OSError:
        return False


def _get_install_base_dir() -> str:
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        parent = os.path.dirname(exe_dir)
        # Packaged backend path is typically: <install>/resources/python/pdf_backend.exe
        if os.path.basename(parent).lower() == "resources":
            return os.path.dirname(parent)
        return parent
    # Dev mode: project root (../.. from python/api/ocr.py)
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _dedupe_existing_paths(paths: List[str]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for path in paths:
        if not path:
            continue
        norm = os.path.normpath(path)
        if norm in seen or not os.path.isdir(norm):
            continue
        seen.add(norm)
        result.append(norm)
    return result


def _bundled_ocr_model_roots() -> List[str]:
    candidates: List[str] = []
    if getattr(sys, "frozen", False):
        meipass = str(getattr(sys, "_MEIPASS", "") or "")
        if meipass:
            candidates.append(os.path.join(meipass, "model", "ocr_model"))
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.append(os.path.join(exe_dir, "_internal", "model", "ocr_model"))
        candidates.append(os.path.join(exe_dir, "model", "ocr_model"))
    candidates.append(os.path.join(_get_install_base_dir(), "model", "ocr_model"))
    return _dedupe_existing_paths(candidates)


def _find_bundled_ocr_model_dir(kind: str, model_name: str) -> str:
    for root in _bundled_ocr_model_roots():
        model_dir = os.path.join(root, kind, model_name)
        if _has_model_files(model_dir):
            return model_dir
    return ""


def _get_ocr_data_root() -> str:
    candidates: List[str] = []
    install_base = _get_install_base_dir()
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates.extend(_bundled_ocr_model_roots())
        candidates.append(os.path.join(exe_dir, "model", "ocr_model"))
    candidates.append(os.path.join(install_base, "model", "ocr_model"))
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(os.path.join(local_app_data, "pdfusion", "paddleocr"))
    else:
        candidates.append(os.path.join(os.path.expanduser("~/.cache"), "pdfusion", "paddleocr"))
    candidates.append(os.path.join(tempfile.gettempdir(), "pdfusion", "paddleocr"))

    for root in candidates:
        if _is_dir_writable(root):
            return root

    # As a last resort, keep app alive even in restricted environments.
    fallback = os.path.join(tempfile.gettempdir(), "pdfusion", "paddleocr_fallback")
    os.makedirs(fallback, exist_ok=True)
    return fallback


def _ensure_paddle_runtime_env(root: str) -> None:
    runtime_home = os.path.join(root, "_runtime_home")
    os.makedirs(runtime_home, exist_ok=True)

    # Paddle 3.x still reads os.path.expanduser("~") in some paths
    # (for example paddle.dataset.common), so redirect when default home is not writable.
    user_home = os.path.expanduser("~")
    if not _is_dir_writable(os.path.join(user_home, ".cache", "paddle")):
        os.environ["USERPROFILE"] = runtime_home
        os.environ["HOME"] = runtime_home

    paddle_home = os.path.join(root, "_paddle_home")
    if not _is_dir_writable(paddle_home):
        paddle_home = root
    os.environ["PADDLE_HOME"] = paddle_home

    # Improve download success in restricted networks:
    # - prefer ModelScope in CN environments
    # - skip preflight hoster connectivity check and try sources directly
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "modelscope")
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    # Stability first for packaged Windows runtime:
    # disable oneDNN/MKLDNN and PIR executor by default to avoid
    # ConvertPirAttribute2RuntimeAttribute crashes in some Paddle builds.
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    os.environ.setdefault("FLAGS_enable_pir_in_executor", "0")


OCR_DATA_ROOT = _get_ocr_data_root()
_ensure_paddle_runtime_env(OCR_DATA_ROOT)


class OcrStatusResponse(BaseModel):
    runtime_ready: bool
    models_ready: bool
    downloading: bool
    applying: bool
    apply_workers: int
    apply_total_pages: int
    apply_done_pages: int
    apply_current_page: int
    apply_recognized_blocks: int
    apply_cancel_requested: bool
    message: str
    error: str
    lang: str
    model_root: str


class OcrDownloadRequest(BaseModel):
    lang: str = MODEL_LANG_DEFAULT


class FormulaDownloadRequest(BaseModel):
    model: str = FORMULA_MODEL_DEFAULT


class OcrApplyRequest(BaseModel):
    path: str
    out_path: str
    pages: Optional[List[int]] = None
    dpi: int = DEFAULT_OCR_DPI
    lang: str = MODEL_LANG_DEFAULT


class OcrAlignPageRequest(BaseModel):
    path: str
    page: int = 0
    dpi: int = DEFAULT_OCR_DPI
    lang: str = MODEL_LANG_DEFAULT
    granularity: Literal["auto", "char"] = "char"
    max_segments: int = 160


_state_lock = threading.Lock()
_engine_lock = threading.Lock()
_formula_engine_lock = threading.Lock()
_layout_engine_lock = threading.Lock()
_ocr_engine: Any = None
_ocr_lang: Optional[str] = None
_np_module: Any = None
_formula_engine: Any = None
_formula_model_name: Optional[str] = None
_formula_np_module: Any = None
_layout_engine: Any = None
_layout_model_name: Optional[str] = None
_layout_np_module: Any = None
_thread_local = threading.local()
_download_thread: Optional[threading.Thread] = None
_formula_download_thread: Optional[threading.Thread] = None
_pool_lock = threading.Lock()
_ocr_process_pool: Optional[ProcessPoolExecutor] = None
_ocr_process_workers: int = 0
_runtime_state: Dict[str, Any] = {
    "runtime_ready": False,
    "models_ready": False,
    "downloading": False,
    "applying": False,
    "apply_workers": 1,
    "apply_total_pages": 0,
    "apply_done_pages": 0,
    "apply_current_page": 0,
    "apply_recognized_blocks": 0,
    "apply_cancel_requested": False,
    "message": "OCR 模型未下载",
    "error": "",
    "lang": MODEL_LANG_DEFAULT,
    "updated_at": time.time(),
}
_formula_state: Dict[str, Any] = {
    "runtime_ready": False,
    "models_ready": False,
    "downloading": False,
    "message": "公式识别模型未下载",
    "error": "",
    "model": FORMULA_MODEL_DEFAULT,
    "updated_at": time.time(),
}


def _update_state(**kwargs: Any) -> None:
    with _state_lock:
        _runtime_state.update(kwargs)
        _runtime_state["updated_at"] = time.time()


def _get_state_snapshot() -> Dict[str, Any]:
    with _state_lock:
        state = dict(_runtime_state)
    state["model_root"] = OCR_DATA_ROOT
    return state


def _update_formula_state(**kwargs: Any) -> None:
    with _state_lock:
        _formula_state.update(kwargs)
        _formula_state["updated_at"] = time.time()


def _get_formula_state_snapshot() -> Dict[str, Any]:
    with _state_lock:
        state = dict(_formula_state)
    state["model_root"] = _formula_model_dir(str(state.get("model") or FORMULA_MODEL_DEFAULT))
    return state


def _is_apply_cancel_requested() -> bool:
    with _state_lock:
        return bool(_runtime_state.get("apply_cancel_requested"))


def _mark_apply_cancel_requested(lang: str) -> None:
    _update_state(
        apply_cancel_requested=True,
        message="正在取消 OCR，请稍候...",
        error="",
        lang=lang,
    )


def _finish_apply_as_cancelled(
    workers: int,
    total_pages: int,
    done_pages: int,
    recognized_blocks: int,
    lang: str,
) -> None:
    safe_total_pages = max(0, int(total_pages))
    safe_done_pages = max(0, min(safe_total_pages, int(done_pages)))
    safe_recognized_blocks = max(0, int(recognized_blocks))
    _update_state(
        applying=False,
        apply_workers=max(1, int(workers or 1)),
        apply_total_pages=safe_total_pages,
        apply_done_pages=safe_done_pages,
        apply_current_page=0,
        apply_recognized_blocks=safe_recognized_blocks,
        apply_cancel_requested=False,
        message="OCR 已取消",
        error="",
        runtime_ready=True,
        models_ready=_models_exist(lang) or _is_engine_cached(lang),
        lang=lang,
    )


def _raise_if_apply_cancel_requested(
    workers: int,
    total_pages: int,
    done_pages: int,
    recognized_blocks: int,
    lang: str,
) -> None:
    if not _is_apply_cancel_requested():
        return
    _finish_apply_as_cancelled(workers, total_pages, done_pages, recognized_blocks, lang)
    raise HTTPException(status_code=409, detail=OCR_APPLY_CANCELLED_DETAIL)


def _patch_paddlex_opencv_dependency(cv2_module: Any) -> None:
    """Bridge PaddleX's strict package-name check to available cv2 runtime.

    PaddleOCR 3.x relies on PaddleX `ocr-core` dependency checks that require
    package name `opencv-contrib-python`. Our backend ships `cv2` runtime via
    `opencv-python-headless` to keep packaging predictable. For OCR pipeline
    usage, `cv2` is sufficient, so we patch dependency probes accordingly.
    """
    try:
        import paddlex.utils.deps as paddlex_deps  # type: ignore
    except Exception:
        return

    original = getattr(paddlex_deps, "is_dep_available", None)
    original_require_extra = getattr(paddlex_deps, "require_extra", None)
    if not callable(original):
        return
    if getattr(paddlex_deps, "_pdfusion_cv2_bridge_applied", False):
        return

    def _patched_is_dep_available(dep: str, /, check_version: bool = False) -> bool:
        if dep == "opencv-contrib-python":
            return True
        return original(dep, check_version=check_version)

    paddlex_deps.is_dep_available = _patched_is_dep_available  # type: ignore[attr-defined]

    if callable(original_require_extra):
        def _patched_require_extra(extra: str, *, obj_name: Optional[str] = None, alt: Optional[str] = None) -> None:
            # PaddleX marks formula_recognition as requiring full paddlex[ocr],
            # which pulls large document/table dependencies that this app does
            # not use. For our cropped formula-image path, OpenCV + tokenizers
            # are the actual runtime requirements.
            if extra == "ocr" and obj_name == "formula_recognition":
                missing = [
                    dep
                    for dep in ("opencv-contrib-python", "tokenizers")
                    if not _patched_is_dep_available(dep)
                ]
                if missing:
                    raise paddlex_deps.DependencyError(
                        "Formula recognition runtime is missing dependencies: "
                        + ", ".join(missing)
                    )
                return
            original_require_extra(extra, obj_name=obj_name, alt=alt)

        paddlex_deps.require_extra = _patched_require_extra  # type: ignore[attr-defined]

    paddlex_deps._pdfusion_cv2_bridge_applied = True  # type: ignore[attr-defined]

    # If PaddleX modules were imported before the patch, conditional imports
    # like `if is_dep_available("opencv-contrib-python"): import cv2` may have
    # skipped `cv2`, causing runtime NameError later. Inject cv2 symbol into
    # already-loaded PaddleX modules to make them resilient.
    for mod_name, mod in list(sys.modules.items()):
        if not mod_name.startswith("paddlex.") or mod is None:
            continue
        try:
            if not hasattr(mod, "cv2"):
                setattr(mod, "cv2", cv2_module)
        except Exception:
            continue

    # Keep explicit compatibility for known hot path module when it is already
    # loaded. Do not import it here: in PyInstaller builds that can initialize
    # PaddleX before PaddleOCR finishes importing.
    image_reader = sys.modules.get("paddlex.inference.common.reader.image_reader")
    if image_reader is not None:
        try:
            image_reader.cv2 = cv2_module
        except Exception:
            pass


def _optional_import_runtime() -> Tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
        from paddleocr import PaddleOCR
        _patch_paddlex_opencv_dependency(cv2)
    except Exception as e:
        if getattr(sys, "frozen", False):
            raise RuntimeError(
                "OCR runtime is not available in this packaged build (likely Slim). "
                "The Slim package does not include PaddleOCR runtime dependencies "
                "(paddlepaddle, paddleocr, opencv-python-headless). "
                "Please use the full installer to enable OCR. "
                f"Error: {e}"
            )
        raise RuntimeError(
            "PaddleOCR runtime is not available. "
            "Please install minimal OCR runtime dependencies in backend environment: "
            "paddlepaddle, paddleocr, opencv-python-headless. "
            f"Error: {e}"
        )
    return np, PaddleOCR


def _optional_import_formula_runtime() -> Tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
        from paddleocr import FormulaRecognitionPipeline
        _patch_paddlex_opencv_dependency(cv2)
    except Exception as e:
        if getattr(sys, "frozen", False):
            raise RuntimeError(
                "Formula OCR runtime is not available in this packaged build. "
                "Please use the full installer with PaddleOCR formula runtime. "
                f"Error: {e}"
            )
        raise RuntimeError(
            "PaddleOCR formula runtime is not available. "
            "Please install paddleocr with formula recognition support. "
            f"Error: {e}"
        )
    return np, FormulaRecognitionPipeline


def _optional_import_layout_runtime() -> Tuple[Any, Any]:
    try:
        import cv2
        import numpy as np
        from paddleocr import LayoutDetection
        _patch_paddlex_opencv_dependency(cv2)
    except Exception as e:
        if getattr(sys, "frozen", False):
            raise RuntimeError(
                "PaddleOCR layout detection runtime is not available in this packaged build. "
                "Please use the full installer with PaddleOCR runtime. "
                f"Error: {e}"
            )
        raise RuntimeError(
            "PaddleOCR layout detection runtime is not available. "
            "Please install paddleocr with layout detection support. "
            f"Error: {e}"
        )
    return np, LayoutDetection


def _format_exception_chain(exc: Exception, limit: int = 6) -> str:
    parts: List[str] = []
    seen: set[int] = set()
    current: Optional[BaseException] = exc
    while current is not None and len(parts) < limit and id(current) not in seen:
        seen.add(id(current))
        message = str(current).strip()
        if message:
            parts.append(f"{type(current).__name__}: {message}")
        else:
            parts.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    return " | caused by: ".join(parts)


def _debug_layout(message: str) -> None:
    if os.environ.get("PDFUSION_DEBUG_LAYOUT") != "1":
        return
    try:
        print(f"[PDFusion layout] {message}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _model_dirs(lang: str) -> Tuple[str, str, str]:
    safe_lang = (lang or MODEL_LANG_DEFAULT).strip().lower() or MODEL_LANG_DEFAULT
    base = os.path.join(OCR_DATA_ROOT, safe_lang)
    det_dir = os.path.join(base, "det")
    rec_dir = os.path.join(base, "rec")
    cls_dir = os.path.join(base, "cls")
    os.makedirs(det_dir, exist_ok=True)
    os.makedirs(rec_dir, exist_ok=True)
    os.makedirs(cls_dir, exist_ok=True)
    return det_dir, rec_dir, cls_dir


def _normalize_formula_model_name(model: str) -> str:
    value = (model or FORMULA_MODEL_DEFAULT).strip()
    if not value:
        return FORMULA_MODEL_DEFAULT
    safe = re.sub(r"[^A-Za-z0-9_.+-]+", "_", value)
    return safe or FORMULA_MODEL_DEFAULT


def _formula_model_dir(model: str = FORMULA_MODEL_DEFAULT) -> str:
    model_name = _normalize_formula_model_name(model)
    path = os.path.join(OCR_DATA_ROOT, "formula", model_name)
    os.makedirs(path, exist_ok=True)
    return path


def _layout_model_dir(model: str = LAYOUT_MODEL_DEFAULT) -> str:
    safe_model = re.sub(r"[^A-Za-z0-9_.+-]+", "_", (model or LAYOUT_MODEL_DEFAULT).strip())
    model_name = safe_model or LAYOUT_MODEL_DEFAULT
    path = os.path.join(OCR_DATA_ROOT, "layout", model_name)
    os.makedirs(path, exist_ok=True)
    return path


def _has_any_file_with_suffix(path: str, suffixes: Tuple[str, ...]) -> bool:
    if not os.path.isdir(path):
        return False
    try:
        for name in os.listdir(path):
            full = os.path.join(path, name)
            if os.path.isfile(full) and name.lower().endswith(suffixes):
                return True
    except OSError:
        return False
    return False


def _has_model_files(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    has_params = _has_any_file_with_suffix(path, (".pdiparams",))
    has_structure = _has_any_file_with_suffix(path, (".pdmodel", ".json", ".onnx"))
    return has_params and has_structure


def _has_v3_model_files(path: str) -> bool:
    return _has_model_files(path) and _has_any_file_with_suffix(path, (".yml", ".yaml"))


def _paddlex_official_model_roots() -> List[str]:
    candidates = [
        os.path.join(os.path.expanduser("~"), ".paddlex", "official_models"),
        os.path.join(OCR_DATA_ROOT, "_runtime_home", ".paddlex", "official_models"),
        os.path.join(OCR_DATA_ROOT, ".paddlex", "official_models"),
    ]
    roots: List[str] = []
    seen: set[str] = set()
    for item in candidates:
        norm = os.path.normpath(item)
        if norm in seen:
            continue
        seen.add(norm)
        roots.append(norm)
    return roots


def _models_exist_in_paddlex_cache(min_model_count: int = 2) -> bool:
    for root in _paddlex_official_model_roots():
        if not os.path.isdir(root):
            continue
        ready_count = 0
        try:
            entries = os.listdir(root)
        except OSError:
            entries = []
        for name in entries:
            sub_dir = os.path.join(root, name)
            if _has_model_files(sub_dir):
                ready_count += 1
        if ready_count >= min_model_count:
            return True
    return False


def _formula_model_exists_in_paddlex_cache(model_name: str) -> bool:
    normalized = _normalize_formula_model_name(model_name).lower()
    for root in _paddlex_official_model_roots():
        if not os.path.isdir(root):
            continue
        try:
            entries = os.listdir(root)
        except OSError:
            entries = []
        for name in entries:
            if normalized not in name.lower():
                continue
            if _has_model_files(os.path.join(root, name)):
                return True
    return False


def _models_exist(lang: str) -> bool:
    det_dir, rec_dir, cls_dir = _model_dirs(lang)
    if _has_model_files(det_dir) and _has_model_files(rec_dir):
        return True
    return _models_exist_in_paddlex_cache()


def _formula_models_exist(model: str = FORMULA_MODEL_DEFAULT) -> bool:
    model_name = _normalize_formula_model_name(model)
    if _formula_model_files_exist(model_name):
        return True
    return _is_formula_engine_cached(model_name)


def _formula_model_files_exist(model: str = FORMULA_MODEL_DEFAULT) -> bool:
    model_name = _normalize_formula_model_name(model)
    if _has_model_files(_formula_model_dir(model_name)):
        return True
    if _find_bundled_ocr_model_dir("formula", model_name):
        return True
    if _formula_model_exists_in_paddlex_cache(model_name):
        return True
    return False


def _layout_model_files_exist(model: str = LAYOUT_MODEL_DEFAULT) -> bool:
    model_name = re.sub(r"[^A-Za-z0-9_.+-]+", "_", (model or LAYOUT_MODEL_DEFAULT).strip()) or LAYOUT_MODEL_DEFAULT
    if _has_model_files(_layout_model_dir(model_name)):
        return True
    if _find_bundled_ocr_model_dir("layout", model_name):
        return True
    for root in _paddlex_official_model_roots():
        model_dir = os.path.join(root, model_name)
        if _has_model_files(model_dir):
            return True
    return False


def _is_paddleocr_v3(PaddleOCR: Any) -> bool:
    try:
        params = inspect.signature(PaddleOCR.__init__).parameters
    except (TypeError, ValueError):
        return False
    return "use_doc_orientation_classify" in params and "text_detection_model_name" in params


def _read_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _read_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_ocr_cpu_threads() -> int:
    return _read_int_env("PDFUSION_OCR_CPU_THREADS", DEFAULT_OCR_CPU_THREADS, 1, 16)


def _resolve_ocr_workers(total_pages: int) -> int:
    if total_pages <= 1:
        return 1
    cpu_cores = os.cpu_count() or 2
    fixed_workers = max(1, cpu_cores // 2)
    return min(total_pages, fixed_workers)


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _coerce_confidence(value: Any, default: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return max(0.0, min(1.0, parsed))


def _get_ocr_process_pool(workers: int) -> ProcessPoolExecutor:
    global _ocr_process_pool, _ocr_process_workers
    with _pool_lock:
        if _ocr_process_pool is None or _ocr_process_workers != workers:
            if _ocr_process_pool is not None:
                try:
                    _ocr_process_pool.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
            _ocr_process_pool = ProcessPoolExecutor(max_workers=workers)
            _ocr_process_workers = workers
        return _ocr_process_pool


def _reset_ocr_process_pool() -> None:
    global _ocr_process_pool, _ocr_process_workers
    with _pool_lock:
        if _ocr_process_pool is not None:
            try:
                _ocr_process_pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        _ocr_process_pool = None
        _ocr_process_workers = 0


def _get_paddleocr_init_params(PaddleOCR: Any) -> Dict[str, Any]:
    try:
        return dict(inspect.signature(PaddleOCR.__init__).parameters)
    except (TypeError, ValueError):
        return {}


def _supports_paddleocr_kwarg(init_params: Dict[str, Any], key: str) -> bool:
    if key in init_params:
        return True
    for param in init_params.values():
        if isinstance(param, inspect.Parameter) and param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
    return False


def _set_ocr_kwarg_if_supported(kwargs: Dict[str, Any], init_params: Dict[str, Any], key: str, value: Any) -> None:
    if _supports_paddleocr_kwarg(init_params, key):
        kwargs[key] = value


def _extract_unknown_argument_name(error_text: str) -> Optional[str]:
    match = re.search(r"Unknown argument:\s*['\"]?([A-Za-z0-9_]+)['\"]?", error_text or "")
    if not match:
        return None
    return match.group(1)


def _build_paddleocr_with_arg_fallback(PaddleOCR: Any, kwargs: Dict[str, Any]) -> Any:
    trial_kwargs = dict(kwargs)
    max_rounds = max(1, len(trial_kwargs) + 1)
    for _ in range(max_rounds):
        try:
            return PaddleOCR(**trial_kwargs)
        except Exception as e:
            unknown_key = _extract_unknown_argument_name(str(e))
            if not unknown_key or unknown_key not in trial_kwargs:
                raise
            trial_kwargs.pop(unknown_key, None)
    return PaddleOCR(**trial_kwargs)


def _is_engine_cached(lang: str) -> bool:
    with _engine_lock:
        return _ocr_engine is not None and _ocr_lang == lang and _np_module is not None


def _is_formula_engine_cached(model: str = FORMULA_MODEL_DEFAULT) -> bool:
    model_name = _normalize_formula_model_name(model)
    with _formula_engine_lock:
        return (
            _formula_engine is not None
            and _formula_model_name == model_name
            and _formula_np_module is not None
        )


def _is_formula_engine_cached_nowait(model: str = FORMULA_MODEL_DEFAULT) -> bool:
    model_name = _normalize_formula_model_name(model)
    if not _formula_engine_lock.acquire(blocking=False):
        return False
    try:
        return (
            _formula_engine is not None
            and _formula_model_name == model_name
            and _formula_np_module is not None
        )
    finally:
        _formula_engine_lock.release()


def _build_ocr_engine(lang: str) -> Tuple[Any, Any]:
    np, PaddleOCR = _optional_import_runtime()
    init_params = _get_paddleocr_init_params(PaddleOCR)
    cpu_threads = _resolve_ocr_cpu_threads()
    enable_mkldnn = _read_bool_env("PDFUSION_OCR_ENABLE_MKLDNN", False)
    kwargs: Dict[str, Any] = {"lang": lang}
    _set_ocr_kwarg_if_supported(kwargs, init_params, "cpu_threads", cpu_threads)
    _set_ocr_kwarg_if_supported(kwargs, init_params, "enable_mkldnn", enable_mkldnn)
    if _is_paddleocr_v3(PaddleOCR):
        # For PaddleOCR 3.x, avoid extra document preprocessor models.
        # In packaged Windows, PP-OCRv5 may hit backend incompatibilities;
        # prefer PP-OCRv4 for better runtime stability.
        ocr_version = os.environ.get("PDFUSION_OCR_VERSION", "PP-OCRv4").strip()
        _set_ocr_kwarg_if_supported(kwargs, init_params, "use_doc_orientation_classify", False)
        _set_ocr_kwarg_if_supported(kwargs, init_params, "use_doc_unwarping", False)
        _set_ocr_kwarg_if_supported(kwargs, init_params, "enable_cinn", False)
        _set_ocr_kwarg_if_supported(kwargs, init_params, "ocr_version", ocr_version or "PP-OCRv4")
        det_dir, rec_dir, cls_dir = _model_dirs(lang)
        if _has_v3_model_files(det_dir) and _has_v3_model_files(rec_dir):
            _set_ocr_kwarg_if_supported(kwargs, init_params, "det_model_dir", det_dir)
            _set_ocr_kwarg_if_supported(kwargs, init_params, "rec_model_dir", rec_dir)
            if _has_v3_model_files(cls_dir):
                _set_ocr_kwarg_if_supported(kwargs, init_params, "use_angle_cls", True)
                _set_ocr_kwarg_if_supported(kwargs, init_params, "cls_model_dir", cls_dir)
            else:
                _set_ocr_kwarg_if_supported(kwargs, init_params, "use_textline_orientation", False)
        else:
            _set_ocr_kwarg_if_supported(kwargs, init_params, "use_textline_orientation", False)
    else:
        det_dir, rec_dir, cls_dir = _model_dirs(lang)
        # PaddleOCR 2.x will download missing model files into these dirs when needed.
        _set_ocr_kwarg_if_supported(kwargs, init_params, "use_angle_cls", True)
        _set_ocr_kwarg_if_supported(kwargs, init_params, "det_model_dir", det_dir)
        _set_ocr_kwarg_if_supported(kwargs, init_params, "rec_model_dir", rec_dir)
        _set_ocr_kwarg_if_supported(kwargs, init_params, "cls_model_dir", cls_dir)
    engine = _build_paddleocr_with_arg_fallback(PaddleOCR, kwargs)
    return engine, np


def _ensure_ocr_engine(lang: str) -> Tuple[Any, Any]:
    global _ocr_engine, _ocr_lang, _np_module
    with _engine_lock:
        if _ocr_engine is not None and _ocr_lang == lang and _np_module is not None:
            return _ocr_engine, _np_module

        engine, np = _build_ocr_engine(lang)
        _ocr_engine = engine
        _ocr_lang = lang
        _np_module = np
        _update_state(
            runtime_ready=True,
            models_ready=_models_exist(lang),
            lang=lang,
            error="",
            message="OCR 模型已就绪",
        )
        return _ocr_engine, _np_module


def _build_formula_engine(model: str = FORMULA_MODEL_DEFAULT) -> Tuple[Any, Any]:
    np, FormulaRecognitionPipeline = _optional_import_formula_runtime()
    init_params = _get_paddleocr_init_params(FormulaRecognitionPipeline)
    cpu_threads = _resolve_ocr_cpu_threads()
    enable_mkldnn = _read_bool_env("PDFUSION_OCR_ENABLE_MKLDNN", False)
    model_name = _normalize_formula_model_name(model)
    kwargs: Dict[str, Any] = {}
    _set_ocr_kwarg_if_supported(kwargs, init_params, "formula_recognition_model_name", model_name)
    _set_ocr_kwarg_if_supported(kwargs, init_params, "model_name", model_name)
    _set_ocr_kwarg_if_supported(kwargs, init_params, "cpu_threads", cpu_threads)
    _set_ocr_kwarg_if_supported(kwargs, init_params, "enable_mkldnn", enable_mkldnn)
    _set_ocr_kwarg_if_supported(kwargs, init_params, "enable_cinn", False)
    _set_ocr_kwarg_if_supported(kwargs, init_params, "use_doc_orientation_classify", False)
    _set_ocr_kwarg_if_supported(kwargs, init_params, "use_doc_unwarping", False)
    _set_ocr_kwarg_if_supported(kwargs, init_params, "use_layout_detection", False)

    local_model_dir = _formula_model_dir(model_name)
    bundled_model_dir = _find_bundled_ocr_model_dir("formula", model_name)
    model_dir = local_model_dir if _has_model_files(local_model_dir) else bundled_model_dir
    if model_dir:
        _set_ocr_kwarg_if_supported(kwargs, init_params, "formula_recognition_model_dir", model_dir)
        _set_ocr_kwarg_if_supported(kwargs, init_params, "model_dir", model_dir)

    engine = _build_paddleocr_with_arg_fallback(FormulaRecognitionPipeline, kwargs)
    return engine, np


def _ensure_formula_engine(model: str = FORMULA_MODEL_DEFAULT) -> Tuple[Any, Any]:
    global _formula_engine, _formula_model_name, _formula_np_module
    model_name = _normalize_formula_model_name(model)
    with _formula_engine_lock:
        if (
            _formula_engine is not None
            and _formula_model_name == model_name
            and _formula_np_module is not None
        ):
            return _formula_engine, _formula_np_module

        engine, np = _build_formula_engine(model_name)
        _formula_engine = engine
        _formula_model_name = model_name
        _formula_np_module = np
        _update_formula_state(
            runtime_ready=True,
            models_ready=True,
            model=model_name,
            error="",
            message="公式识别模型已就绪",
        )
        return _formula_engine, _formula_np_module


def _find_layout_model_dir(model: str = LAYOUT_MODEL_DEFAULT) -> str:
    model_name = re.sub(r"[^A-Za-z0-9_.+-]+", "_", (model or LAYOUT_MODEL_DEFAULT).strip()) or LAYOUT_MODEL_DEFAULT
    local_model_dir = _layout_model_dir(model_name)
    if _has_model_files(local_model_dir):
        return local_model_dir
    bundled_model_dir = _find_bundled_ocr_model_dir("layout", model_name)
    if bundled_model_dir:
        return bundled_model_dir
    for root in _paddlex_official_model_roots():
        model_dir = os.path.join(root, model_name)
        if _has_model_files(model_dir):
            return model_dir
    return local_model_dir


def _build_layout_engine(model: str = LAYOUT_MODEL_DEFAULT) -> Tuple[Any, Any]:
    np, LayoutDetection = _optional_import_layout_runtime()
    model_name = re.sub(r"[^A-Za-z0-9_.+-]+", "_", (model or LAYOUT_MODEL_DEFAULT).strip()) or LAYOUT_MODEL_DEFAULT
    model_dir = _find_layout_model_dir(model_name)
    if not _has_model_files(model_dir):
        raise RuntimeError(f"Layout detection model not found: {model_dir}")
    _debug_layout(f"building layout engine model={model_name} dir={model_dir}")
    kwargs: Dict[str, Any] = {
        "model_name": model_name,
        "model_dir": model_dir,
        "device": "cpu",
        "enable_mkldnn": _read_bool_env("PDFUSION_OCR_ENABLE_MKLDNN", False),
        "cpu_threads": _resolve_ocr_cpu_threads(),
        "threshold": 0.45,
    }
    engine = _build_paddleocr_with_arg_fallback(LayoutDetection, kwargs)
    return engine, np


def _ensure_layout_engine(model: str = LAYOUT_MODEL_DEFAULT) -> Tuple[Any, Any]:
    global _layout_engine, _layout_model_name, _layout_np_module
    model_name = re.sub(r"[^A-Za-z0-9_.+-]+", "_", (model or LAYOUT_MODEL_DEFAULT).strip()) or LAYOUT_MODEL_DEFAULT
    with _layout_engine_lock:
        if (
            _layout_engine is not None
            and _layout_model_name == model_name
            and _layout_np_module is not None
        ):
            return _layout_engine, _layout_np_module
        engine, np = _build_layout_engine(model_name)
        _layout_engine = engine
        _layout_model_name = model_name
        _layout_np_module = np
        return _layout_engine, _layout_np_module


def is_formula_layout_detector_ready(model: str = LAYOUT_MODEL_DEFAULT) -> bool:
    if not _layout_model_files_exist(model):
        return False
    try:
        _ensure_layout_engine(model)
        return True
    except Exception:
        return False


def _ensure_worker_ocr_engine(lang: str) -> Tuple[Any, Any]:
    cache_lang = getattr(_thread_local, "ocr_lang", None)
    cache_engine = getattr(_thread_local, "ocr_engine", None)
    cache_np = getattr(_thread_local, "np_module", None)
    if cache_engine is not None and cache_np is not None and cache_lang == lang:
        return cache_engine, cache_np

    engine, np = _build_ocr_engine(lang)
    _thread_local.ocr_lang = lang
    _thread_local.ocr_engine = engine
    _thread_local.np_module = np
    return engine, np


def _insert_searchable_text(page: fitz.Page, rect: fitz.Rect, text: str) -> None:
    text_value = (text or "").strip()
    if not text_value:
        return
    if rect.width < 1 or rect.height < 1:
        return

    # PDF.js may drop text-layer items for render_mode=3 (invisible text).
    # Use normal rendering mode with transparent fill/stroke so OCR text remains extractable.
    base_kwargs = {
        "fontname": "china-s",
        "color": (0, 0, 0),
        "render_mode": 0,
        "fill_opacity": 0.0,
        "stroke_opacity": 0.0,
        "overlay": True,
    }
    try:
        font_obj = fitz.Font("china-s")
    except Exception:
        font_obj = fitz.Font("helv")

    # Fit both box width and height to reduce OCR text drift in PDF text layer.
    width_at_size_1 = max(0.01, float(font_obj.text_length(text_value, fontsize=1)))
    width_fit = (rect.width * 0.98) / width_at_size_1
    height_fit = rect.height * 0.90
    fontsize = max(4.0, min(42.0, width_fit, height_fit))

    for _ in range(5):
        rest = page.insert_textbox(
            rect,
            text_value,
            fontsize=fontsize,
            align=fitz.TEXT_ALIGN_LEFT,
            lineheight=1.0,
            **base_kwargs,
        )
        if rest >= -0.1:
            return
        fontsize *= 0.90

    baseline_y = min(rect.y1, rect.y0 + max(4.0, fontsize) * 0.95)
    page.insert_text(
        fitz.Point(rect.x0 + min(0.8, rect.width * 0.02), baseline_y),
        text_value,
        fontsize=max(4.0, fontsize),
        **base_kwargs,
    )


def _is_cjk_char(char: str) -> bool:
    if not char:
        return False
    code = ord(char)
    return (
        (0x3400 <= code <= 0x4DBF)
        or (0x4E00 <= code <= 0x9FFF)
        or (0xF900 <= code <= 0xFAFF)
        or (0x3040 <= code <= 0x30FF)
        or (0xAC00 <= code <= 0xD7AF)
    )


def _char_visual_weight(char: str) -> float:
    if not char:
        return 0.0
    if char.isspace():
        return 0.35
    if _is_cjk_char(char):
        return 1.0
    category = unicodedata.category(char)
    if category.startswith("P"):
        return 0.55
    if char.isdigit():
        return 0.62
    if "A" <= char <= "Z":
        return 0.72
    if "a" <= char <= "z":
        return 0.58
    east_asian = unicodedata.east_asian_width(char)
    if east_asian in ("W", "F"):
        return 0.92
    if east_asian == "A":
        return 0.74
    return 0.8


def _merge_segments_to_limit(
    segments: List[Tuple[int, int, str]],
    text_value: str,
    max_segments: int,
) -> List[Tuple[int, int, str]]:
    if not segments:
        if text_value:
            return [(0, len(text_value), text_value)]
        return []
    if max_segments <= 0 or len(segments) <= max_segments:
        return segments

    group_size = int(math.ceil(len(segments) / float(max_segments)))
    merged_segments: List[Tuple[int, int, str]] = []
    for idx in range(0, len(segments), group_size):
        chunk = segments[idx: idx + group_size]
        if not chunk:
            continue
        start = chunk[0][0]
        end = chunk[-1][1]
        token = text_value[start:end].replace("\n", " ").strip()
        if not token:
            token = "".join(part for _, _, part in chunk).strip()
        if not token:
            continue
        merged_segments.append((start, end, token))

    return merged_segments or [(0, len(text_value), text_value)]


def _build_ocr_text_segments(text: str) -> List[Tuple[int, int, str]]:
    text_value = str(text or "").strip()
    if not text_value:
        return []

    has_whitespace = any(ch.isspace() for ch in text_value)
    has_cjk = any(_is_cjk_char(ch) for ch in text_value)
    segments: List[Tuple[int, int, str]] = []

    # OCR Chinese lines often have no spaces; split by character for better selection precision.
    if has_cjk and not has_whitespace:
        for idx, char in enumerate(text_value):
            if char.isspace():
                continue
            segments.append((idx, idx + 1, char))
    else:
        for match in re.finditer(r"\S+", text_value):
            token = match.group(0).strip()
            if not token:
                continue
            segments.append((match.start(), match.end(), token))

    return _merge_segments_to_limit(segments, text_value, MAX_OCR_SEGMENTS_PER_BLOCK)


def _build_ocr_text_segments_with_granularity(
    text: str,
    granularity: str = "auto",
    max_segments: int = MAX_OCR_SEGMENTS_PER_BLOCK,
) -> List[Tuple[int, int, str]]:
    text_value = str(text or "").strip()
    if not text_value:
        return []

    normalized = (granularity or "auto").strip().lower()
    if normalized == "char":
        segments: List[Tuple[int, int, str]] = []
        for idx, char in enumerate(text_value):
            if char.isspace():
                continue
            segments.append((idx, idx + 1, char))
        return _merge_segments_to_limit(segments, text_value, max_segments)

    if max_segments == MAX_OCR_SEGMENTS_PER_BLOCK:
        return _build_ocr_text_segments(text_value)
    return _merge_segments_to_limit(_build_ocr_text_segments(text_value), text_value, max_segments)


def _split_ocr_block_with_offsets(
    raw_rect: Tuple[float, float, float, float],
    text: str,
    granularity: str = "auto",
    max_segments: int = MAX_OCR_SEGMENTS_PER_BLOCK,
) -> List[Tuple[int, int, str, Tuple[float, float, float, float]]]:
    text_value = str(text or "").strip()
    if not text_value:
        return []

    x0, y0, x1, y1 = [float(v) for v in raw_rect]
    if x1 - x0 < 1.0 or y1 - y0 < 1.0:
        return [(0, len(text_value), text_value, (x0, y0, x1, y1))]

    segments = _build_ocr_text_segments_with_granularity(text_value, granularity, max_segments)
    if len(segments) <= 1:
        return [(0, len(text_value), text_value, (x0, y0, x1, y1))]

    cumulative_weights: List[float] = [0.0]
    for char in text_value:
        cumulative_weights.append(cumulative_weights[-1] + _char_visual_weight(char))
    total_weight = cumulative_weights[-1]
    if total_weight <= 1e-6:
        return [(0, len(text_value), text_value, (x0, y0, x1, y1))]

    width = x1 - x0
    segment_rects: List[Tuple[int, int, str, Tuple[float, float, float, float]]] = []
    for start, end, token in segments:
        if start < 0 or end > len(text_value) or start >= end:
            continue
        seg_weight = cumulative_weights[end] - cumulative_weights[start]
        if seg_weight <= 1e-6:
            continue
        left = x0 + width * (cumulative_weights[start] / total_weight)
        right = x0 + width * (cumulative_weights[end] / total_weight)
        if right <= left:
            continue
        if right - left < 0.6:
            center = (left + right) / 2.0
            left = center - 0.3
            right = center + 0.3
        left = max(x0, left)
        right = min(x1, right)
        if right - left < 0.3:
            continue
        segment_rects.append((start, end, token, (left, y0, right, y1)))

    if not segment_rects:
        return [(0, len(text_value), text_value, (x0, y0, x1, y1))]
    return segment_rects


def _split_ocr_block_rect(
    raw_rect: Tuple[float, float, float, float],
    text: str,
) -> List[Tuple[Tuple[float, float, float, float], str]]:
    segments = _split_ocr_block_with_offsets(raw_rect, text)
    return [(segment_rect, segment_text) for _, _, segment_text, segment_rect in segments]


def _run_ocr_inference(ocr_engine: Any, image: Any) -> Any:
    predict_fn = getattr(ocr_engine, "predict", None)
    if callable(predict_fn):
        try:
            predict_params = inspect.signature(predict_fn).parameters
        except (TypeError, ValueError):
            predict_params = {}
        # PaddleOCR 3.x predict() no longer supports `cls`; call it directly.
        if "use_textline_orientation" in predict_params and "cls" not in predict_params:
            return predict_fn(image)

    try:
        return ocr_engine.ocr(image, cls=True)
    except TypeError as e:
        message = str(e)
        if "cls" not in message or "unexpected keyword argument" not in message:
            raise
    if callable(predict_fn):
        return predict_fn(image)
    try:
        return ocr_engine.ocr(image)
    except TypeError:
        # Some wrappers only expose predict() in newer versions.
        if callable(predict_fn):
            return predict_fn(image)
        raise


def _run_formula_inference(formula_engine: Any, image: Any) -> Any:
    predict_fn = getattr(formula_engine, "predict", None)
    if callable(predict_fn):
        kwargs = {
            "use_layout_detection": False,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
        }
        try:
            return predict_fn(image, **kwargs)
        except TypeError:
            return predict_fn(image)
    recognize_fn = getattr(formula_engine, "recognize", None)
    if callable(recognize_fn):
        return recognize_fn(image)
    raise RuntimeError("Formula recognition engine does not expose predict/recognize")


def _collect_formula_strings(value: Any, output: List[str], force: bool = False) -> None:
    if value is None:
        return
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and (force or _looks_like_latex_formula(stripped)):
            output.append(stripped)
        return
    if hasattr(value, "str"):
        try:
            _collect_formula_strings(getattr(value, "str"), output, force=force)
        except Exception:
            pass
    if hasattr(value, "json"):
        try:
            _collect_formula_strings(getattr(value, "json"), output, force=force)
        except Exception:
            pass
    if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
        try:
            _collect_formula_strings(value.to_dict(), output, force=force)
        except Exception:
            pass
    if isinstance(value, dict):
        priority_keys = (
            "rec_formula",
            "formula",
            "latex",
            "pred",
            "prediction",
            "formula_text",
            "formula_res",
            "formula_res_list",
            "block_content",
            "rec_text",
            "rec_texts",
            "res",
        )
        for key in priority_keys:
            if key in value:
                _collect_formula_strings(value.get(key), output, force=key in {"rec_formula", "formula", "latex", "formula_text", "block_content"})
        for key, item in value.items():
            if key in priority_keys:
                continue
            _collect_formula_strings(item, output, force=force)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_formula_strings(item, output, force=force)


def _looks_like_latex_formula(text: str) -> bool:
    if not text:
        return False
    math_markers = ("\\", "^", "_", "{", "}", "=", "+", "-", "\\frac", "\\sum", "\\int")
    return any(marker in text for marker in math_markers)


def _extract_formula_latex(result: Any) -> str:
    formulas: List[str] = []
    _collect_formula_strings(result, formulas)
    if not formulas:
        return ""
    formulas = sorted(set(formulas), key=lambda item: (-len(item), item))
    return formulas[0]


def recognize_formula_image_bytes(image_bytes: bytes, model: str = FORMULA_MODEL_DEFAULT) -> str:
    if not image_bytes:
        return ""
    model_name = _normalize_formula_model_name(model)
    if not _formula_models_exist(model_name):
        return ""
    try:
        formula_engine, np_module = _ensure_formula_engine(model_name)
        with Image.open(BytesIO(image_bytes)) as img:
            image = np_module.array(img.convert("RGB"))
        result = _run_formula_inference(formula_engine, image)
        return _extract_formula_latex(result)
    except Exception as e:
        _update_formula_state(
            runtime_ready=True,
            models_ready=_formula_models_exist(model_name),
            model=model_name,
            error=_format_exception_chain(e),
            message="公式识别失败，已回退为图片公式",
        )
        return ""


def detect_formula_layout_image_bytes(image_bytes: bytes, model: str = LAYOUT_MODEL_DEFAULT) -> List[Dict[str, Any]]:
    if not image_bytes:
        return []
    if not _layout_model_files_exist(model):
        _debug_layout(f"layout model files not found for {model}")
        return []
    try:
        layout_engine, np_module = _ensure_layout_engine(model)
        with Image.open(BytesIO(image_bytes)) as img:
            image = np_module.array(img.convert("RGB"))
        result = layout_engine.predict(image, batch_size=1, layout_nms=True)
        boxes = _extract_formula_layout_boxes(result)
        _debug_layout(f"layout boxes={len(boxes)}")
        return boxes
    except Exception as e:
        _debug_layout(f"layout detection failed: {_format_exception_chain(e)}")
        return []


def _extract_formula_layout_boxes(result: Any) -> List[Dict[str, Any]]:
    boxes: List[Dict[str, Any]] = []
    if result is None:
        return boxes
    items = result if isinstance(result, (list, tuple)) else [result]
    for item in items:
        if hasattr(item, "to_dict") and callable(getattr(item, "to_dict")):
            try:
                item = item.to_dict()
            except Exception:
                continue
        if not isinstance(item, dict):
            continue
        raw_boxes = item.get("boxes") or item.get("dt_polys") or []
        if not isinstance(raw_boxes, (list, tuple)):
            continue
        for raw_box in raw_boxes:
            if not isinstance(raw_box, dict):
                continue
            label = str(raw_box.get("label") or raw_box.get("class_name") or raw_box.get("cls") or "").strip().lower()
            if label not in {"formula", "formula_number"}:
                continue
            raw_coord = raw_box.get("coordinate") or raw_box.get("bbox") or raw_box.get("box")
            coord = _layout_coordinate_to_rect(raw_coord)
            if not coord:
                continue
            try:
                score = float(raw_box.get("score") or raw_box.get("confidence") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            boxes.append({"label": label, "score": score, "bbox": coord})
    return boxes


def _layout_coordinate_to_rect(value: Any) -> List[float]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)):
        return []
    if len(value) >= 4 and all(isinstance(item, Real) for item in value[:4]):
        x0, y0, x1, y1 = [float(item) for item in value[:4]]
        return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    points = _to_xy_points(value)
    if not points:
        return []
    x0, y0, x1, y1 = _points_to_axis_rect(points)
    return [x0, y0, x1, y1]


def _to_xy_points(raw_box: Any) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    value = raw_box.tolist() if hasattr(raw_box, "tolist") else raw_box
    if not isinstance(value, (list, tuple)):
        return points

    if len(value) >= 4 and all(isinstance(v, Real) for v in value):
        if len(value) % 2 != 0:
            return points
        for idx in range(0, len(value), 2):
            points.append((float(value[idx]), float(value[idx + 1])))
        return points

    for point in value:
        point_value = point.tolist() if hasattr(point, "tolist") else point
        if not isinstance(point_value, (list, tuple)) or len(point_value) < 2:
            continue
        points.append((float(point_value[0]), float(point_value[1])))
    return points


def _is_mostly_horizontal(p0: Tuple[float, float], p1: Tuple[float, float], tolerance_deg: float = 25.0) -> bool:
    dx = float(p1[0] - p0[0])
    dy = float(p1[1] - p0[1])
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return False
    angle = abs(math.degrees(math.atan2(dy, dx)))
    if angle > 90.0:
        angle = 180.0 - angle
    return angle <= tolerance_deg


def _points_to_axis_rect(points: List[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    xs: List[float] = [float(p[0]) for p in points]
    ys: List[float] = [float(p[1]) for p in points]
    if not xs or not ys:
        return (0.0, 0.0, 0.0, 0.0)

    fallback = (min(xs), min(ys), max(xs), max(ys))
    if len(points) < 4:
        return fallback

    # Order points around centroid to derive opposite edge lengths robustly.
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    ordered = sorted(points, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    if len(ordered) < 4:
        return fallback
    quad = ordered[:4]

    edges = [
        (quad[0], quad[1]),
        (quad[1], quad[2]),
        (quad[2], quad[3]),
        (quad[3], quad[0]),
    ]
    lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in edges]
    if not lengths or max(lengths) < 1e-3:
        return fallback

    longest_idx = max(range(len(lengths)), key=lambda i: lengths[i])
    if not _is_mostly_horizontal(edges[longest_idx][0], edges[longest_idx][1]):
        # For steep lines, keep conservative fallback to avoid over-rotation errors.
        return fallback

    side_a = (lengths[0] + lengths[2]) / 2.0
    side_b = (lengths[1] + lengths[3]) / 2.0
    width = max(side_a, side_b)
    height = min(side_a, side_b)
    if width < 0.5 or height < 0.5:
        return fallback

    x0 = cx - width / 2.0
    y0 = cy - height / 2.0
    x1 = cx + width / 2.0
    y1 = cy + height / 2.0
    return (x0, y0, x1, y1)


def _extract_ocr_lines(result: Any) -> List[Tuple[List[Tuple[float, float]], str, float]]:
    extracted: List[Tuple[List[Tuple[float, float]], str, float]] = []
    if not isinstance(result, list) or len(result) == 0:
        return extracted

    first = result[0]
    if hasattr(first, "get"):
        polys = first.get("rec_polys") or first.get("dt_polys") or []
        texts = first.get("rec_texts") or []
        scores = first.get("rec_scores") or first.get("scores") or []
        for line_idx, (poly, text) in enumerate(zip(polys, texts)):
            points = _to_xy_points(poly)
            if not points:
                continue
            text_value = text[0] if isinstance(text, (list, tuple)) and len(text) > 0 else text
            normalized = str(text_value or "").strip()
            if not normalized:
                continue
            confidence = 1.0
            if isinstance(scores, (list, tuple)):
                if line_idx < len(scores):
                    confidence = _coerce_confidence(scores[line_idx], default=1.0)
            extracted.append((points, normalized, confidence))
        if extracted:
            return extracted

    lines = result[0] if isinstance(result[0], (list, tuple)) else []
    for item in lines:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        points = _to_xy_points(item[0])
        if len(points) < 3:
            continue
        text_info = item[1]
        text_value = text_info[0] if isinstance(text_info, (list, tuple)) and len(text_info) > 0 else text_info
        normalized = str(text_value or "").strip()
        if not normalized:
            continue
        confidence = 1.0
        if isinstance(text_info, (list, tuple)) and len(text_info) > 1:
            confidence = _coerce_confidence(text_info[1], default=1.0)
        extracted.append((points, normalized, confidence))
    return extracted


def _format_download_error_with_hint(lang: str, raw_error: str) -> str:
    lower_error = (raw_error or "").lower()
    det_dir, rec_dir, cls_dir = _model_dirs(lang)
    layout_hint = (
        f"可离线放置模型后重试：det={det_dir}，rec={rec_dir}，cls={cls_dir}。"
        "每个目录至少需要 inference.pdiparams 与 inference.pdmodel（或 inference.json）；"
        "使用 PaddleOCR 3.x 时建议同时包含 inference.yml。"
    )
    if any(token in lower_error for token in NETWORK_ERROR_MARKERS):
        return f"{raw_error} | hint: 检测到模型源网络不可达。{layout_hint}"
    return f"{raw_error} | hint: {layout_hint}"


def _build_page_ocr_payload(page: fitz.Page, np_module: Any, dpi: int) -> Optional[Dict[str, Any]]:
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    if pix.width <= 0 or pix.height <= 0:
        return None
    channels = pix.n
    if channels not in (3, 4):
        return None

    image = np_module.frombuffer(pix.samples, dtype=np_module.uint8).reshape(pix.height, pix.width, channels)
    if channels == 4:
        image = image[:, :, :3]
    return {
        "image": image,
        "scale_x": page.rect.width / float(pix.width),
        "scale_y": page.rect.height / float(pix.height),
        "base_x": float(page.rect.x0),
        "base_y": float(page.rect.y0),
    }


def _infer_blocks_from_payload(payload: Dict[str, Any], ocr_engine: Any) -> List[Tuple[Tuple[float, float, float, float], str]]:
    image = payload["image"]
    try:
        result = _run_ocr_inference(ocr_engine, image)
    except Exception as e:
        raise RuntimeError(f"OCR inference failed: {_format_exception_chain(e)}")

    lines = _extract_ocr_lines(result)
    if not lines:
        return []

    scale_x = float(payload["scale_x"])
    scale_y = float(payload["scale_y"])
    base_x = float(payload["base_x"])
    base_y = float(payload["base_y"])
    blocks: List[Tuple[Tuple[float, float, float, float], str]] = []
    for points, text, _confidence in lines:
        if not points:
            continue

        page_points: List[Tuple[float, float]] = [
            (point[0] * scale_x + base_x, point[1] * scale_y + base_y) for point in points
        ]
        raw_rect = _points_to_axis_rect(page_points)
        blocks.append(
            (
                raw_rect,
                text,
            )
        )
    return blocks


def _normalize_ocr_granularity(raw_value: str) -> str:
    value = (raw_value or "auto").strip().lower()
    if value == "char":
        return "char"
    return "auto"


def _to_rounded_point_list(points: List[Tuple[float, float]]) -> List[List[float]]:
    return [[round(float(x), 3), round(float(y), 3)] for x, y in points]


def _to_rounded_rect(rect: Tuple[float, float, float, float]) -> List[float]:
    return [round(float(v), 3) for v in rect]


def _infer_alignment_from_payload(
    payload: Dict[str, Any],
    ocr_engine: Any,
    granularity: str,
    max_segments: int,
) -> Dict[str, Any]:
    image = payload["image"]
    try:
        result = _run_ocr_inference(ocr_engine, image)
    except Exception as e:
        raise RuntimeError(f"OCR inference failed: {_format_exception_chain(e)}")

    lines = _extract_ocr_lines(result)
    if not lines:
        return {
            "line_count": 0,
            "segment_count": 0,
            "text": "",
            "lines": [],
        }

    scale_x = float(payload["scale_x"])
    scale_y = float(payload["scale_y"])
    base_x = float(payload["base_x"])
    base_y = float(payload["base_y"])
    combined_lines: List[str] = []
    line_payloads: List[Dict[str, Any]] = []
    segment_count = 0
    global_cursor = 0

    for line_index, (points, text, confidence) in enumerate(lines):
        page_points: List[Tuple[float, float]] = [
            (point[0] * scale_x + base_x, point[1] * scale_y + base_y) for point in points
        ]
        line_rect = _points_to_axis_rect(page_points)
        line_text = str(text or "")
        line_start = global_cursor
        line_end = line_start + len(line_text)
        line_segments = _split_ocr_block_with_offsets(
            line_rect,
            line_text,
            granularity=granularity,
            max_segments=max_segments,
        )
        segment_payloads: List[Dict[str, Any]] = []
        for start, end, token, segment_rect in line_segments:
            segment_payloads.append(
                {
                    "text": token,
                    "start": int(start),
                    "end": int(end),
                    "global_start": int(line_start + start),
                    "global_end": int(line_start + end),
                    "bbox": _to_rounded_rect(segment_rect),
                    "confidence": round(_coerce_confidence(confidence), 6),
                }
            )
        segment_count += len(segment_payloads)
        line_payloads.append(
            {
                "index": int(line_index),
                "text": line_text,
                "confidence": round(_coerce_confidence(confidence), 6),
                "line_start": int(line_start),
                "line_end": int(line_end),
                "bbox": _to_rounded_rect(line_rect),
                "polygon": _to_rounded_point_list(page_points),
                "segments": segment_payloads,
            }
        )
        combined_lines.append(line_text)
        if line_index < len(lines) - 1:
            global_cursor = line_end + 1
        else:
            global_cursor = line_end

    return {
        "line_count": len(line_payloads),
        "segment_count": segment_count,
        "text": "\n".join(combined_lines),
        "lines": line_payloads,
    }


def _apply_blocks_to_page(page: fitz.Page, blocks: List[Tuple[Tuple[float, float, float, float], str]]) -> int:
    inserted = 0
    for raw_rect, text in blocks:
        segments = _split_ocr_block_rect(raw_rect, text)
        if not segments:
            continue
        for segment_rect, segment_text in segments:
            rect = fitz.Rect(segment_rect)
            _insert_searchable_text(page, rect, segment_text)
        inserted += 1

    return inserted


def _ocr_page_and_overlay(page: fitz.Page, ocr_engine: Any, np_module: Any, dpi: int) -> int:
    payload = _build_page_ocr_payload(page, np_module, dpi)
    if payload is None:
        return 0
    blocks = _infer_blocks_from_payload(payload, ocr_engine)
    return _apply_blocks_to_page(page, blocks)


def _ocr_extract_page_blocks_worker(
    src_path: str,
    page_index: int,
    lang: str,
    dpi: int,
) -> Tuple[int, List[Tuple[Tuple[float, float, float, float], str]]]:
    local_doc: Optional[fitz.Document] = None
    try:
        ocr_engine, np_module = _ensure_worker_ocr_engine(lang)
        local_doc = fitz.open(src_path)
        page = local_doc.load_page(page_index)
        payload = _build_page_ocr_payload(page, np_module, dpi)
        if payload is None:
            return page_index, []
        blocks = _infer_blocks_from_payload(payload, ocr_engine)
        return page_index, blocks
    except Exception as e:
        raise RuntimeError(f"Page {page_index + 1} OCR failed: {_format_exception_chain(e)}")
    finally:
        if local_doc is not None:
            local_doc.close()


def _validate_pages(req_pages: Optional[List[int]], total_pages: int) -> List[int]:
    if req_pages is None:
        return list(range(total_pages))
    validated = []
    for index in req_pages:
        if index < 0 or index >= total_pages:
            raise HTTPException(status_code=400, detail=f"Page index out of range: {index} (total: {total_pages})")
        validated.append(index)
    return validated


def _download_models_worker(lang: str) -> None:
    try:
        _update_state(downloading=True, message="正在下载 OCR 模型...", error="", lang=lang)
        ocr_engine, np_module = _ensure_ocr_engine(lang)
        blank = np_module.full((256, 256, 3), 255, dtype=np_module.uint8)
        _run_ocr_inference(ocr_engine, blank)
        _update_state(
            downloading=False,
            models_ready=_models_exist(lang) or _is_engine_cached(lang),
            runtime_ready=True,
            message="OCR 模型下载完成",
            error="",
            lang=lang,
        )
    except Exception as e:
        formatted = _format_exception_chain(e)
        _update_state(
            downloading=False,
            message="OCR 模型下载失败",
            error=_format_download_error_with_hint(lang, formatted),
            models_ready=_models_exist(lang),
            lang=lang,
        )


def _download_formula_models_worker(model: str) -> None:
    model_name = _normalize_formula_model_name(model)
    try:
        _update_formula_state(downloading=True, message="正在下载公式识别模型...", error="", model=model_name)
        formula_engine, np_module = _ensure_formula_engine(model_name)
        # Trigger lazy model download/init with a tiny synthetic formula-like image.
        image = np_module.full((96, 256, 3), 255, dtype=np_module.uint8)
        try:
            _run_formula_inference(formula_engine, image)
        except Exception:
            # Blank image may legitimately produce no formula. Engine initialization
            # already downloads the model; keep the model available and record no error.
            pass
        _update_formula_state(
            downloading=False,
            models_ready=_formula_models_exist(model_name) or _is_formula_engine_cached(model_name),
            runtime_ready=True,
            message="公式识别模型下载完成",
            error="",
            model=model_name,
        )
    except Exception as e:
        formatted = _format_exception_chain(e)
        _update_formula_state(
            downloading=False,
            message="公式识别模型下载失败",
            error=formatted,
            models_ready=_formula_models_exist(model_name),
            model=model_name,
        )


@router.get("/status", response_model=OcrStatusResponse)
def ocr_status():
    state = _get_state_snapshot()
    lang = state.get("lang") or MODEL_LANG_DEFAULT
    try:
        _optional_import_runtime()
        runtime_ready = True
    except Exception as e:
        runtime_ready = False
        _update_state(
            runtime_ready=False,
            error=_format_exception_chain(e),
            message="OCR 运行库未就绪",
        )

    models_ready = _models_exist(lang) or _is_engine_cached(lang)
    _update_state(models_ready=models_ready, runtime_ready=runtime_ready)
    is_busy = bool(state.get("downloading")) or bool(state.get("applying"))
    if runtime_ready and models_ready and not is_busy:
        _update_state(message="OCR 模型已就绪", error="")
    state = _get_state_snapshot()
    return OcrStatusResponse(**state)


@router.get("/formula/status")
def formula_status():
    state = _get_formula_state_snapshot()
    model = _normalize_formula_model_name(str(state.get("model") or FORMULA_MODEL_DEFAULT))
    is_busy = bool(state.get("downloading"))
    runtime_ready = bool(state.get("runtime_ready"))
    if not runtime_ready and not is_busy:
        try:
            _optional_import_formula_runtime()
            runtime_ready = True
            _update_formula_state(runtime_ready=True, error="", model=model)
        except Exception as e:
            runtime_ready = False
            _update_formula_state(
                runtime_ready=False,
                error=_format_exception_chain(e),
                message="公式识别运行库未就绪",
                model=model,
            )

    if is_busy:
        models_ready = bool(state.get("models_ready")) or _has_model_files(_formula_model_dir(model))
    else:
        models_ready = _formula_model_files_exist(model) or _is_formula_engine_cached_nowait(model)
    _update_formula_state(models_ready=models_ready, runtime_ready=runtime_ready, model=model)
    state = _get_formula_state_snapshot()
    if runtime_ready and models_ready and not is_busy:
        _update_formula_state(message="公式识别模型已就绪", error="", model=model)
        state = _get_formula_state_snapshot()
    return state


@router.post("/download")
def ocr_download(req: OcrDownloadRequest):
    lang = (req.lang or MODEL_LANG_DEFAULT).strip().lower() or MODEL_LANG_DEFAULT
    state = _get_state_snapshot()
    if state.get("downloading"):
        return {"status": "running", "message": state.get("message"), "lang": state.get("lang")}

    try:
        _optional_import_runtime()
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"OCR runtime check failed: {_format_exception_chain(e)}",
        )

    global _download_thread
    thread = threading.Thread(target=_download_models_worker, args=(lang,), daemon=True, name="pdfusion-ocr-download")
    _download_thread = thread
    thread.start()
    return {"status": "started", "lang": lang}


@router.post("/formula/download")
def formula_download(req: FormulaDownloadRequest):
    model = _normalize_formula_model_name(req.model or FORMULA_MODEL_DEFAULT)
    state = _get_formula_state_snapshot()
    if state.get("downloading"):
        return {"status": "running", "message": state.get("message"), "model": state.get("model")}

    try:
        _optional_import_formula_runtime()
        _update_formula_state(runtime_ready=True, error="", model=model)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Formula OCR runtime check failed: {_format_exception_chain(e)}",
        )

    global _formula_download_thread
    thread = threading.Thread(
        target=_download_formula_models_worker,
        args=(model,),
        daemon=True,
        name="pdfusion-formula-download",
    )
    _formula_download_thread = thread
    thread.start()
    return {"status": "started", "model": model}


@router.post("/cancel")
def ocr_cancel():
    state = _get_state_snapshot()
    lang = (state.get("lang") or MODEL_LANG_DEFAULT).strip().lower() or MODEL_LANG_DEFAULT
    if not state.get("applying"):
        _update_state(apply_cancel_requested=False)
        return {"status": "idle", "message": "No OCR job is running", "lang": lang}

    _mark_apply_cancel_requested(lang)
    return {"status": "cancelling", "message": "正在取消 OCR，请稍候...", "lang": lang}


@router.post("/align/page")
def ocr_align_page(req: OcrAlignPageRequest):
    src_path = os.path.normpath(req.path)
    if not os.path.exists(src_path):
        raise HTTPException(status_code=400, detail=f"Input PDF not found: {src_path}")

    lang = (req.lang or MODEL_LANG_DEFAULT).strip().lower() or MODEL_LANG_DEFAULT
    dpi = _clamp_int(req.dpi, DEFAULT_OCR_DPI, MIN_OCR_DPI, MAX_OCR_DPI)
    granularity = _normalize_ocr_granularity(req.granularity)
    max_segments = _clamp_int(req.max_segments, 160, 16, MAX_OCR_ALIGN_SEGMENTS_PER_LINE)

    try:
        ocr_engine, np_module = _ensure_ocr_engine(lang)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"OCR engine init failed: {_format_exception_chain(e)}",
        )

    doc: Optional[fitz.Document] = None
    try:
        doc = fitz.open(src_path)
        if req.page < 0 or req.page >= doc.page_count:
            raise HTTPException(status_code=400, detail=f"Page index out of range: {req.page} (total: {doc.page_count})")

        page = doc.load_page(req.page)
        payload = _build_page_ocr_payload(page, np_module, dpi)
        if payload is None:
            return {
                "status": "ok",
                "path": src_path,
                "page": req.page,
                "dpi": dpi,
                "lang": lang,
                "granularity": granularity,
                "max_segments": max_segments,
                "line_count": 0,
                "segment_count": 0,
                "text": "",
                "lines": [],
            }

        alignment = _infer_alignment_from_payload(payload, ocr_engine, granularity, max_segments)
        return {
            "status": "ok",
            "path": src_path,
            "page": req.page,
            "dpi": dpi,
            "lang": lang,
            "granularity": granularity,
            "max_segments": max_segments,
            **alignment,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OCR align failed: {e}")
    finally:
        if doc is not None:
            doc.close()


@router.post("/apply")
def ocr_apply(req: OcrApplyRequest):
    src_path = os.path.normpath(req.path)
    out_path = os.path.normpath(req.out_path)
    if not os.path.exists(src_path):
        raise HTTPException(status_code=400, detail=f"Input PDF not found: {src_path}")

    lang = (req.lang or MODEL_LANG_DEFAULT).strip().lower() or MODEL_LANG_DEFAULT
    dpi = int(req.dpi or DEFAULT_OCR_DPI)
    dpi = max(MIN_OCR_DPI, min(MAX_OCR_DPI, dpi))

    try:
        ocr_engine, np_module = _ensure_ocr_engine(lang)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"OCR engine init failed: {_format_exception_chain(e)}",
        )

    doc: Optional[fitz.Document] = None
    temp_path: Optional[str] = None
    try:
        doc = fitz.open(src_path)
        page_indexes = _validate_pages(req.pages, doc.page_count)
        total_pages = len(page_indexes)
        workers = _resolve_ocr_workers(total_pages)
        worker_suffix = f"（并行 {workers} 进程）" if workers > 1 else ""
        _update_state(
            applying=True,
            apply_workers=workers,
            apply_total_pages=total_pages,
            apply_done_pages=0,
            apply_current_page=0,
            apply_recognized_blocks=0,
            apply_cancel_requested=False,
            message=f"OCR 识别中{worker_suffix}",
            error="",
            runtime_ready=True,
            models_ready=_models_exist(lang) or _is_engine_cached(lang),
            lang=lang,
        )
        recognized_blocks = 0
        if workers <= 1:
            for idx, page_index in enumerate(page_indexes, start=1):
                _raise_if_apply_cancel_requested(
                    workers=workers,
                    total_pages=total_pages,
                    done_pages=idx - 1,
                    recognized_blocks=recognized_blocks,
                    lang=lang,
                )
                _update_state(
                    applying=True,
                    apply_total_pages=total_pages,
                    apply_done_pages=idx - 1,
                    apply_current_page=page_index + 1,
                    apply_recognized_blocks=recognized_blocks,
                    message=f"OCR 识别中：第 {idx}/{total_pages} 页{worker_suffix}",
                    error="",
                    lang=lang,
                )
                page = doc.load_page(page_index)
                recognized_blocks += _ocr_page_and_overlay(page, ocr_engine, np_module, dpi)
                _raise_if_apply_cancel_requested(
                    workers=workers,
                    total_pages=total_pages,
                    done_pages=idx,
                    recognized_blocks=recognized_blocks,
                    lang=lang,
                )
                _update_state(
                    applying=True,
                    apply_total_pages=total_pages,
                    apply_done_pages=idx,
                    apply_current_page=page_index + 1,
                    apply_recognized_blocks=recognized_blocks,
                    message=f"OCR 识别中：第 {idx}/{total_pages} 页{worker_suffix}",
                    error="",
                    lang=lang,
                )
        else:
            future_map = {}
            blocks_by_page: Dict[int, List[Tuple[Tuple[float, float, float, float], str]]] = {}
            parallel_error: Optional[Exception] = None
            try:
                executor = _get_ocr_process_pool(workers)
                for page_index in page_indexes:
                    _raise_if_apply_cancel_requested(
                        workers=workers,
                        total_pages=total_pages,
                        done_pages=0,
                        recognized_blocks=0,
                        lang=lang,
                    )
                    future = executor.submit(
                        _ocr_extract_page_blocks_worker,
                        src_path,
                        page_index,
                        lang,
                        dpi,
                    )
                    future_map[future] = page_index

                done_pages = 0
                pending_futures = set(future_map.keys())
                while pending_futures:
                    if _is_apply_cancel_requested():
                        for pending in pending_futures:
                            pending.cancel()
                        _reset_ocr_process_pool()
                        _raise_if_apply_cancel_requested(
                            workers=workers,
                            total_pages=total_pages,
                            done_pages=done_pages,
                            recognized_blocks=0,
                            lang=lang,
                        )

                    done_set, pending_futures = wait(
                        pending_futures,
                        timeout=0.25,
                        return_when=FIRST_COMPLETED,
                    )
                    if not done_set:
                        continue

                    for future in done_set:
                        try:
                            worker_page_index, blocks = future.result()
                        except Exception as e:
                            parallel_error = e
                            if isinstance(e, BrokenProcessPool):
                                _reset_ocr_process_pool()
                            for pending in pending_futures:
                                pending.cancel()
                            pending_futures = set()
                            break

                        blocks_by_page[worker_page_index] = blocks
                        done_pages += 1
                        _update_state(
                            applying=True,
                            apply_total_pages=total_pages,
                            apply_done_pages=done_pages,
                            apply_current_page=worker_page_index + 1,
                            apply_recognized_blocks=0,
                            message=f"OCR 识别中：第 {done_pages}/{total_pages} 页{worker_suffix}",
                            error="",
                            lang=lang,
                        )

                    if parallel_error is not None:
                        break
            except BrokenProcessPool as e:
                _reset_ocr_process_pool()
                parallel_error = e

            if parallel_error is not None:
                error_detail = _format_exception_chain(parallel_error)
                is_pool_broken = isinstance(parallel_error, BrokenProcessPool)
                is_onednn_issue = (
                    "ConvertPirAttribute2RuntimeAttribute" in error_detail
                    or "onednn_instruction" in error_detail
                    or "onednn" in error_detail.lower()
                )
                if not (is_onednn_issue or is_pool_broken):
                    raise parallel_error

                # Fallback path: keep feature available even if specific runtime
                # cannot execute multi-process OCR with oneDNN/PIR.
                if doc is not None:
                    doc.close()
                doc = fitz.open(src_path)
                recognized_blocks = 0
                for idx, page_index in enumerate(page_indexes, start=1):
                    _raise_if_apply_cancel_requested(
                        workers=workers,
                        total_pages=total_pages,
                        done_pages=idx - 1,
                        recognized_blocks=recognized_blocks,
                        lang=lang,
                    )
                    _update_state(
                        applying=True,
                        apply_total_pages=total_pages,
                        apply_done_pages=idx - 1,
                        apply_current_page=page_index + 1,
                        apply_recognized_blocks=recognized_blocks,
                        message=f"OCR 并行失败，自动回退单进程重试：第 {idx}/{total_pages} 页",
                        error="",
                        lang=lang,
                    )
                    page = doc.load_page(page_index)
                    recognized_blocks += _ocr_page_and_overlay(page, ocr_engine, np_module, dpi)
                    _raise_if_apply_cancel_requested(
                        workers=workers,
                        total_pages=total_pages,
                        done_pages=idx,
                        recognized_blocks=recognized_blocks,
                        lang=lang,
                    )
                    _update_state(
                        applying=True,
                        apply_total_pages=total_pages,
                        apply_done_pages=idx,
                        apply_current_page=page_index + 1,
                        apply_recognized_blocks=recognized_blocks,
                        message=f"OCR 回退重试中：第 {idx}/{total_pages} 页",
                        error="",
                        lang=lang,
                    )
            else:
                recognized_blocks = 0
                for idx, page_index in enumerate(page_indexes, start=1):
                    _raise_if_apply_cancel_requested(
                        workers=workers,
                        total_pages=total_pages,
                        done_pages=idx - 1,
                        recognized_blocks=recognized_blocks,
                        lang=lang,
                    )
                    page = doc.load_page(page_index)
                    recognized_blocks += _apply_blocks_to_page(page, blocks_by_page.get(page_index, []))
                    _raise_if_apply_cancel_requested(
                        workers=workers,
                        total_pages=total_pages,
                        done_pages=idx,
                        recognized_blocks=recognized_blocks,
                        lang=lang,
                    )
                    _update_state(
                        applying=True,
                        apply_total_pages=total_pages,
                        apply_done_pages=idx,
                        apply_current_page=page_index + 1,
                        apply_recognized_blocks=recognized_blocks,
                        message=f"OCR 回写中：第 {idx}/{total_pages} 页{worker_suffix}",
                        error="",
                        lang=lang,
                    )

        _raise_if_apply_cancel_requested(
            workers=workers,
            total_pages=total_pages,
            done_pages=total_pages,
            recognized_blocks=recognized_blocks,
            lang=lang,
        )
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        fd, temp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        doc.save(temp_path, garbage=4, deflate=True)
        shutil.copy2(temp_path, out_path)
        _update_state(
            applying=False,
            apply_workers=workers,
            apply_total_pages=total_pages,
            apply_done_pages=total_pages,
            apply_current_page=0,
            apply_recognized_blocks=recognized_blocks,
            apply_cancel_requested=False,
            message=f"OCR 识别完成{worker_suffix}",
            error="",
            runtime_ready=True,
            models_ready=_models_exist(lang) or _is_engine_cached(lang),
            lang=lang,
        )
        return {
            "status": "ok",
            "out_path": out_path,
            "page_count": len(page_indexes),
            "recognized_blocks": recognized_blocks,
            "used_workers": workers,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OCR apply failed: {e}")
    finally:
        if _get_state_snapshot().get("applying"):
            _update_state(applying=False, apply_current_page=0, apply_cancel_requested=False)
        if doc is not None:
            doc.close()
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
