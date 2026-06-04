import base64
import atexit
import os
import queue
import shutil
import subprocess
import tempfile
import threading
from typing import List, Tuple

import fitz  # PyMuPDF
from fastapi import APIRouter, HTTPException
from PIL import Image
from pydantic import BaseModel

router = APIRouter()

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}
WORD_EXTENSIONS = {".doc", ".docx"}
DEFAULT_IMAGE_DPI = 300.0
MIN_IMAGE_DPI = 72.0
MAX_IMAGE_DPI = 600.0


class ImportRequest(BaseModel):
    paths: List[str]


class MergeRequest(BaseModel):
    paths: List[str]


@router.post("/")
def import_as_pdf(req: ImportRequest):
    if not req.paths:
        raise HTTPException(status_code=400, detail="No input files provided")

    normalized_paths = [os.path.normpath(p) for p in req.paths]
    for path in normalized_paths:
        if not os.path.exists(path):
            raise HTTPException(status_code=400, detail=f"Input file not found: {path}")

    suffixes = {os.path.splitext(path)[1].lower() for path in normalized_paths}
    if len(suffixes) != 1 and not all(ext in IMAGE_EXTENSIONS for ext in suffixes):
        raise HTTPException(status_code=400, detail="Mixed file types are not supported for import")

    ext = os.path.splitext(normalized_paths[0])[1].lower()
    if ext in WORD_EXTENSIONS:
        temp_path = _make_temp_pdf_path()
        _word_to_pdf(normalized_paths[0], temp_path)
        return {"temp_path": temp_path}

    if ext in IMAGE_EXTENSIONS:
        temp_path = _make_temp_pdf_path()
        _images_to_pdf(normalized_paths, temp_path)
        return {"temp_path": temp_path}

    raise HTTPException(status_code=400, detail=f"Unsupported import format: {ext}")


@router.post("/merge")
def merge_files_as_pdf(req: MergeRequest):
    if not req.paths:
        raise HTTPException(status_code=400, detail="No input files provided")
    if len(req.paths) < 2:
        raise HTTPException(status_code=400, detail="Please select at least 2 files to merge")

    normalized_paths = [os.path.normpath(p) for p in req.paths]
    for path in normalized_paths:
        if not os.path.exists(path):
            raise HTTPException(status_code=400, detail=f"Input file not found: {path}")

    temp_sources: List[str] = []
    pdf_sources: List[str] = []

    try:
        for path in normalized_paths:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".pdf":
                pdf_sources.append(path)
                continue

            if ext in WORD_EXTENSIONS:
                temp_pdf = _make_temp_pdf_path(prefix="merge_src_")
                _word_to_pdf(path, temp_pdf)
                pdf_sources.append(temp_pdf)
                temp_sources.append(temp_pdf)
                continue

            if ext in IMAGE_EXTENSIONS:
                temp_pdf = _make_temp_pdf_path(prefix="merge_src_")
                _images_to_pdf([path], temp_pdf)
                pdf_sources.append(temp_pdf)
                temp_sources.append(temp_pdf)
                continue

            raise HTTPException(status_code=400, detail=f"Unsupported merge format: {ext}")

        out_path = _make_temp_pdf_path(prefix="merge_")
        page_count, max_width, max_height = _merge_pdfs_with_max_page_size(pdf_sources, out_path)
        return {
            "temp_path": out_path,
            "page_count": page_count,
            "max_page_width": max_width,
            "max_page_height": max_height,
        }
    finally:
        for temp_path in temp_sources:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


def _make_temp_pdf_path(prefix: str = "import_") -> str:
    temp_dir = os.path.join(tempfile.gettempdir(), "pdfusion")
    os.makedirs(temp_dir, exist_ok=True)
    return os.path.join(temp_dir, f"{prefix}{next(tempfile._get_candidate_names())}.pdf")


def _make_temp_docx_path() -> str:
    temp_dir = os.path.join(tempfile.gettempdir(), "pdfusion")
    os.makedirs(temp_dir, exist_ok=True)
    return os.path.join(temp_dir, f"import_{next(tempfile._get_candidate_names())}.docx")


def _word_to_pdf(src_path: str, out_path: str) -> None:
    ext = os.path.splitext(src_path)[1].lower()
    if ext not in WORD_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported Word format: {ext}")

    if sys_platform_is_windows():
        _word_to_pdf_windows(src_path, out_path)
        return

    if sys_platform_is_macos():
        _word_to_pdf_macos(src_path, out_path, ext)
        return

    raise HTTPException(status_code=501, detail="Word import is only supported on Windows and macOS")


def sys_platform_is_windows() -> bool:
    return os.name == "nt"


def sys_platform_is_macos() -> bool:
    return os.uname().sysname == "Darwin" if hasattr(os, "uname") else False


WORD_CONVERT_TIMEOUT_SECONDS = 180
WORD_IDLE_QUIT_SECONDS = 120
_word_worker = None
_word_worker_guard = threading.Lock()


class _WordComWorker:
    def __init__(self, idle_quit_seconds: int = WORD_IDLE_QUIT_SECONDS):
        self._idle_quit_seconds = idle_quit_seconds
        self._tasks: "queue.Queue" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True, name="pdfusion-word-com-worker")
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread.start()

    def warmup(self) -> None:
        self.start()
        self._tasks.put(("warmup", None, None, None, None))

    def convert(self, src_path: str, out_path: str, timeout: int = WORD_CONVERT_TIMEOUT_SECONDS) -> None:
        self.start()
        done = threading.Event()
        result = {}
        self._tasks.put(("convert", src_path, out_path, done, result))
        if not done.wait(timeout):
            raise TimeoutError(f"Word conversion timed out after {timeout} seconds")
        error = result.get("error")
        if error:
            raise RuntimeError(error)

    def stop(self) -> None:
        if not self._started:
            return
        self._tasks.put(("stop", None, None, None, None))
        self._thread.join(timeout=5)

    def _run(self) -> None:
        pythoncom = None
        win32com_client = None
        init_error = None
        word = None
        try:
            try:
                import pythoncom as _pythoncom
                import win32com.client as _win32com_client
                pythoncom = _pythoncom
                win32com_client = _win32com_client
            except Exception as e:
                init_error = f"Word COM initialization failed: {e}"

            if pythoncom is not None:
                pythoncom.CoInitialize()

            while True:
                try:
                    task = self._tasks.get(timeout=self._idle_quit_seconds)
                except queue.Empty:
                    if word is not None:
                        try:
                            word.Quit()
                        except Exception:
                            pass
                        word = None
                    continue

                action, src_path, out_path, done, result = task
                if action == "stop":
                    break

                if init_error:
                    if isinstance(result, dict):
                        result["error"] = init_error
                    if done is not None:
                        done.set()
                    continue

                if action == "warmup":
                    if word is None:
                        try:
                            word = self._create_word_app(win32com_client)
                        except Exception as e:
                            init_error = f"Word COM warmup failed: {e}"
                    continue

                if action != "convert":
                    if done is not None:
                        done.set()
                    continue

                try:
                    if word is None:
                        word = self._create_word_app(win32com_client)
                    self._convert_single_file(word, src_path, out_path)
                    if not os.path.exists(out_path):
                        raise RuntimeError("Microsoft Word did not produce a PDF output")
                except Exception as e:
                    if isinstance(result, dict):
                        result["error"] = str(e)
                    if word is not None:
                        try:
                            word.Quit()
                        except Exception:
                            pass
                        word = None
                finally:
                    if done is not None:
                        done.set()
        finally:
            if word is not None:
                try:
                    word.Quit()
                except Exception:
                    pass
            if pythoncom is not None:
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    @staticmethod
    def _create_word_app(win32com_client):
        word = win32com_client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        if hasattr(word, "AutomationSecurity"):
            word.AutomationSecurity = 3
        return word

    @staticmethod
    def _convert_single_file(word, src_path: str, out_path: str) -> None:
        doc = None
        try:
            # Open as read-only and do not add to recent files.
            doc = word.Documents.Open(src_path, False, True, False)
            if doc is None:
                raise RuntimeError(f"Microsoft Word could not open the file: {src_path}")
            doc.ExportAsFixedFormat(out_path, 17)
        finally:
            if doc is not None:
                try:
                    doc.Close(False)
                except Exception:
                    pass


def _get_word_worker() -> _WordComWorker:
    global _word_worker
    with _word_worker_guard:
        if _word_worker is None:
            _word_worker = _WordComWorker()
            _word_worker.start()
        return _word_worker


def _stop_word_worker() -> None:
    global _word_worker
    with _word_worker_guard:
        worker = _word_worker
        _word_worker = None
    if worker is not None:
        worker.stop()


atexit.register(_stop_word_worker)


if sys_platform_is_windows():
    try:
        _get_word_worker().warmup()
    except Exception:
        pass


def _word_to_pdf_windows(src_path: str, out_path: str) -> None:
    try:
        _get_word_worker().convert(src_path, out_path, timeout=WORD_CONVERT_TIMEOUT_SECONDS)
        _ensure_output_exists(out_path, None, "Word to PDF failed. Microsoft Word did not produce a PDF output")
        return
    except TimeoutError as e:
        raise HTTPException(status_code=500, detail=f"Word to PDF timeout: {e}")
    except RuntimeError as e:
        message = str(e)
        init_failure = (
            "Word COM initialization failed" in message
            or "Word COM warmup failed" in message
            or "No module named" in message
        )
        if not init_failure:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Word to PDF failed. Please confirm Microsoft Word is installed and can open this file: "
                    f"{message}"
                ),
            )
        # Fallback to PowerShell flow for environments where pywin32 is unavailable
        # or COM initialization fails.
        pass

    _word_to_pdf_windows_via_powershell(src_path, out_path)


def _word_to_pdf_windows_via_powershell(src_path: str, out_path: str) -> None:
    script = """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$word = $null
$doc = $null
try {
    $srcPath = $env:PDFUSION_WORD_SRC
    $outPath = $env:PDFUSION_WORD_OUT
    if ([string]::IsNullOrWhiteSpace($srcPath) -or [string]::IsNullOrWhiteSpace($outPath)) {
        throw 'Missing Word conversion paths'
    }
    if (-not (Test-Path -LiteralPath $srcPath)) {
        throw "Input file not found: $srcPath"
    }

    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $doc = $word.Documents.Open($srcPath, $false, $true, $false)
    if ($doc -eq $null) {
        throw "Microsoft Word could not open the file: $srcPath"
    }
    $doc.ExportAsFixedFormat($outPath, 17)
}
finally {
    if ($doc -ne $null) { $doc.Close([ref]$false) }
    if ($word -ne $null) { $word.Quit() }
}
"""
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    env = os.environ.copy()
    env["PDFUSION_WORD_SRC"] = src_path
    env["PDFUSION_WORD_OUT"] = out_path

    result = _run_subprocess(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded_script,
        ],
        timeout=180,
        failure_prefix="Word to PDF failed. Please confirm Microsoft Word is installed and can open this file",
        env=env,
    )
    _ensure_output_exists(out_path, result, "Word to PDF failed. Microsoft Word did not produce a PDF output")


def _word_to_pdf_macos(src_path: str, out_path: str, ext: str) -> None:
    working_input = src_path
    temp_docx_path = None

    if ext == ".doc":
        temp_docx_path = _make_temp_docx_path()
        _run_subprocess(
            ["doc2docx", src_path, temp_docx_path],
            timeout=180,
            failure_prefix="DOC to DOCX failed on macOS. Please confirm Microsoft Word is installed and can open this file",
        )
        _ensure_output_exists(
            temp_docx_path,
            None,
            "DOC to DOCX failed on macOS. Microsoft Word did not produce a DOCX output",
        )
        working_input = temp_docx_path

    _run_subprocess(
        ["docx2pdf", working_input, out_path],
        timeout=180,
        failure_prefix="DOCX to PDF failed on macOS. Please confirm Microsoft Word is installed and can open this file",
    )
    _ensure_output_exists(out_path, None, "DOCX to PDF failed on macOS. Microsoft Word did not produce a PDF output")

    if temp_docx_path and os.path.exists(temp_docx_path):
        try:
            os.remove(temp_docx_path)
        except OSError:
            pass


def _run_subprocess(command: List[str], timeout: int, failure_prefix: str, env=None):
    executable = shutil.which(command[0]) or command[0]
    try:
        result = subprocess.run(
            [executable, *command[1:]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail=f"{failure_prefix}: required command not found: {command[0]}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{failure_prefix}: {e}")

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or f"command exited with code {result.returncode}"
        raise HTTPException(status_code=500, detail=f"{failure_prefix}: {detail}")

    return result


def _ensure_output_exists(path: str, result, failure_prefix: str) -> None:
    if os.path.exists(path):
        return

    detail = ""
    if result is not None:
      detail = ((result.stderr or "").strip() or (result.stdout or "").strip())
    if detail:
        raise HTTPException(status_code=500, detail=f"{failure_prefix}: {detail}")
    raise HTTPException(status_code=500, detail=failure_prefix)


def _images_to_pdf(paths: List[str], out_path: str) -> None:
    pdf = fitz.open()
    try:
        image_sizes = []
        for path in paths:
            try:
                with Image.open(path) as image:
                    width, height = _resolve_image_page_size_points(image)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Cannot open image: {path}: {e}")

            image_sizes.append((path, width, height))

        if not image_sizes:
            raise HTTPException(status_code=400, detail="No valid images to import")

        max_width = max(width for _, width, _ in image_sizes)
        max_height = max(height for _, _, height in image_sizes)

        for path, width, height in image_sizes:
            page = pdf.new_page(width=max_width, height=max_height)
            x0 = (max_width - width) / 2
            y0 = (max_height - height) / 2
            rect = fitz.Rect(x0, y0, x0 + width, y0 + height)
            page.insert_image(rect, filename=path, keep_proportion=True)

        pdf.save(out_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image to PDF failed: {e}")
    finally:
        pdf.close()


def _resolve_image_page_size_points(image: Image.Image) -> Tuple[float, float]:
    width_px, height_px = image.size
    dpi_x, dpi_y = _resolve_image_dpi(image)
    width_pt = max(1.0, float(width_px) * 72.0 / dpi_x)
    height_pt = max(1.0, float(height_px) * 72.0 / dpi_y)
    return width_pt, height_pt


def _resolve_image_dpi(image: Image.Image) -> Tuple[float, float]:
    dpi_info = image.info.get("dpi")
    if isinstance(dpi_info, tuple) and len(dpi_info) >= 2:
        return _normalize_dpi(dpi_info[0]), _normalize_dpi(dpi_info[1])
    if isinstance(dpi_info, (int, float)):
        dpi = _normalize_dpi(dpi_info)
        return dpi, dpi
    return DEFAULT_IMAGE_DPI, DEFAULT_IMAGE_DPI


def _normalize_dpi(value) -> float:
    try:
        dpi = float(value)
    except (TypeError, ValueError):
        return DEFAULT_IMAGE_DPI
    if dpi <= 0:
        return DEFAULT_IMAGE_DPI
    return max(MIN_IMAGE_DPI, min(MAX_IMAGE_DPI, dpi))


def _merge_pdfs_with_max_page_size(paths: List[str], out_path: str) -> Tuple[int, float, float]:
    if not paths:
        raise HTTPException(status_code=400, detail="No valid PDF sources to merge")

    docs: List[fitz.Document] = []
    merged: fitz.Document = fitz.open()

    try:
        max_width = 0.0
        max_height = 0.0
        page_count = 0

        for path in paths:
            try:
                doc = fitz.open(path)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Cannot open PDF for merge: {path}: {e}")
            docs.append(doc)

            if doc.page_count <= 0:
                continue

            for page in doc:
                rect = page.rect
                max_width = max(max_width, float(rect.width))
                max_height = max(max_height, float(rect.height))

            # Preserve each source page size/content to avoid large blank background.
            merged.insert_pdf(doc)
            page_count += doc.page_count

        if page_count <= 0 or max_width <= 0 or max_height <= 0:
            raise HTTPException(status_code=400, detail="No pages found in selected files")

        merged.save(out_path)
        return page_count, max_width, max_height
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Merge failed: {e}")
    finally:
        merged.close()
        for doc in docs:
            doc.close()
