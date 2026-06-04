import io
import os
import shutil
import tempfile
from typing import Literal

import fitz  # PyMuPDF
import pikepdf
from fastapi import APIRouter, HTTPException
from PIL import Image
from pydantic import BaseModel

router = APIRouter()

DEFAULT_COMPRESS_QUALITY = 60
DEFAULT_RASTERIZE_DPI = 120
RASTERIZE_JPEG_QUALITY = 60


class CompressRequest(BaseModel):
    path: str
    out_path: str
    mode: Literal["compress", "rasterize", "high", "low"]
    quality: int = DEFAULT_COMPRESS_QUALITY
    dpi: int = DEFAULT_RASTERIZE_DPI


def _normalize_mode(mode: str) -> Literal["compress", "rasterize"]:
    return "compress" if mode in {"compress", "high"} else "rasterize"


@router.post("/")
def compress(req: CompressRequest):
    src_path = os.path.normpath(req.path)
    out_path = os.path.normpath(req.out_path)
    if not os.path.exists(src_path):
        raise HTTPException(status_code=400, detail=f"Input file not found: {src_path}")
    if os.path.abspath(src_path) == os.path.abspath(out_path):
        raise HTTPException(status_code=400, detail="out_path cannot be the same as input path")

    normalized_mode = _normalize_mode(req.mode)
    safe_quality = max(10, min(95, int(req.quality or DEFAULT_COMPRESS_QUALITY)))
    safe_dpi = max(72, min(300, int(req.dpi or DEFAULT_RASTERIZE_DPI)))
    normalized_quality = safe_quality if normalized_mode == "compress" else RASTERIZE_JPEG_QUALITY
    normalized_dpi = safe_dpi if normalized_mode == "rasterize" else DEFAULT_RASTERIZE_DPI
    normalized = CompressRequest(
        path=src_path,
        out_path=out_path,
        mode=normalized_mode,
        quality=normalized_quality,
        dpi=normalized_dpi,
    )

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    try:
        if normalized.mode == "compress":
            return _compress_images_only(normalized)
        return _compress_rasterize(normalized)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Compression failed: {e}")


def _compress_images_only(req: CompressRequest):
    """
    Compression mode:
    re-encode embedded raster images while preserving vector/text content.
    """
    try:
        with pikepdf.open(req.path) as pdf:
            for page in pdf.pages:
                for _, raw_img in page.images.items():
                    try:
                        img_obj = pikepdf.PdfImage(raw_img)
                        pil_img = img_obj.as_pil_image().convert("RGB")
                        buf = io.BytesIO()
                        pil_img.save(buf, "JPEG", quality=req.quality, optimize=True, progressive=True)
                        buf.seek(0)
                        raw_img.write(buf.read(), filter=pikepdf.Name("/DCTDecode"))
                        raw_img["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
                        raw_img["/BitsPerComponent"] = 8
                    except Exception:
                        continue

            pdf.save(
                req.out_path,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Compression mode failed: {e}")

    orig = os.path.getsize(req.path)
    new = os.path.getsize(req.out_path)
    ratio = (new / orig) if orig > 0 else 1.0
    return {
        "status": "ok",
        "out_path": req.out_path,
        "original_size": orig,
        "compressed_size": new,
        "ratio": ratio,
    }


def _pixmap_to_jpeg_bytes(pix: fitz.Pixmap, quality: int) -> bytes:
    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def _build_rasterized_pdf(src_path: str, out_path: str, dpi: int, jpeg_quality: int) -> int:
    doc = None
    new_doc = None
    try:
        doc = fitz.open(src_path)
        new_doc = fitz.open()
        mat = fitz.Matrix(dpi / 72, dpi / 72)

        for page in doc:
            # Force RGB to avoid CMYK / alpha mismatch errors in Pillow.
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
            jpeg_data = _pixmap_to_jpeg_bytes(pix, jpeg_quality)

            page_width = float(page.rect.width)
            page_height = float(page.rect.height)
            new_page = new_doc.new_page(width=page_width, height=page_height)
            new_page.insert_image(
                fitz.Rect(0, 0, page_width, page_height),
                stream=jpeg_data,
                keep_proportion=False,
            )

        new_doc.save(out_path, garbage=4, deflate=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Rasterize-page mode failed: {e}")
    finally:
        if doc is not None:
            doc.close()
        if new_doc is not None:
            new_doc.close()

    return os.path.getsize(out_path)


def _compress_rasterize(req: CompressRequest):
    """
    Rasterize-page mode:
    render each page to image at requested DPI and rebuild PDF pages.
    """
    src_size = os.path.getsize(req.path)
    if src_size <= 0:
        raise HTTPException(status_code=500, detail="Input file size is zero")

    requested_dpi = int(req.dpi)
    fd, tmp_out = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)

    try:
        _build_rasterized_pdf(req.path, tmp_out, requested_dpi, RASTERIZE_JPEG_QUALITY)
        shutil.copy2(tmp_out, req.out_path)
    finally:
        if os.path.exists(tmp_out):
            os.unlink(tmp_out)

    new_size = os.path.getsize(req.out_path)
    ratio = (new_size / src_size) if src_size > 0 else 1.0
    return {
        "status": "ok",
        "out_path": req.out_path,
        "original_size": src_size,
        "compressed_size": new_size,
        "ratio": ratio,
        "used_dpi": requested_dpi,
    }
