import pikepdf
import os
import tempfile
import shutil
import traceback
import fitz  # PyMuPDF
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal, List, Optional

router = APIRouter()


class PageOpsRequest(BaseModel):
    action: Literal[
        "split",
        "merge",
        "copy_page",
        "insert_blank",
        "cross_copy",
        "delete_pages",
        "reorder",
        "extract_pages",
        "rotate_pages",
        "normalize_size_by_max_width",
        "normalize_size_by_max_height",
    ]
    paths: List[str]
    out_path: str
    pages: Optional[List[int]] = None
    insert_at: Optional[int] = None
    src_path: Optional[str] = None
    src_pages: Optional[List[int]] = None
    new_order: Optional[List[int]] = None
    rotate: Optional[int] = None


@router.post("/")
def page_ops(req: PageOpsRequest):
    dispatch = {
        "split":        _split,
        "merge":        _merge,
        "copy_page":    _copy_page,
        "insert_blank": _insert_blank,
        "cross_copy":   _cross_copy,
        "delete_pages": _delete_pages,
        "reorder":      _reorder,
        "extract_pages": _extract_pages,
        "rotate_pages": _rotate_pages,
        "normalize_size_by_max_width": _normalize_size_by_max_width,
        "normalize_size_by_max_height": _normalize_size_by_max_height,
    }
    fn = dispatch.get(req.action)
    if not fn:
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")
    try:
        return fn(req)
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


def _split(req: PageOpsRequest):
    """Split each specified page into a separate PDF file."""
    import os
    out_files = []
    with pikepdf.open(req.paths[0]) as src:
        page_indices = req.pages if req.pages is not None else range(len(src.pages))
        for i in page_indices:
            with pikepdf.Pdf.new() as dst:
                dst.pages.append(src.pages[i])
                fp = req.out_path.replace(".pdf", f"_{i+1:04d}.pdf")
                dst.save(fp)
                out_files.append(fp)
    return {"status": "ok", "files": out_files}


def _merge(req: PageOpsRequest):
    """Merge multiple PDFs into one."""
    with pikepdf.Pdf.new() as merged:
        for path in req.paths:
            with pikepdf.open(path) as src:
                merged.pages.extend(src.pages)
        merged.save(req.out_path)
    return {"status": "ok", "out_path": req.out_path, "page_count": len(req.paths)}


def _copy_page(req: PageOpsRequest):
    """Duplicate pages within the same PDF and insert at position."""
    import tempfile
    import shutil
    
    with pikepdf.open(req.paths[0]) as pdf:
        insert_at = req.insert_at if req.insert_at is not None else len(pdf.pages)
        offset = 0
        for p in (req.pages or []):
            if p < 0 or p >= len(pdf.pages):
                raise HTTPException(status_code=400, detail=f"Page index {p} out of range (total: {len(pdf.pages)})")
            pdf.pages.insert(insert_at + offset, pdf.pages[p])
            offset += 1
        # Save to temp file first
        temp_fd, temp_path = tempfile.mkstemp(suffix='.pdf')
        os.close(temp_fd)
        pdf.save(temp_path)
        shutil.copy2(temp_path, req.out_path)
        os.unlink(temp_path)
    return {"status": "ok", "out_path": req.out_path}


def _insert_blank(req: PageOpsRequest):
    """Insert a blank page at a given position (inherits adjacent page size)."""
    with pikepdf.open(req.paths[0]) as pdf:
        insert_at = req.insert_at if req.insert_at is not None else len(pdf.pages)
        ref_idx = min(insert_at, len(pdf.pages) - 1)
        media_box = pdf.pages[ref_idx].MediaBox
        new_page = pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"),
            MediaBox=media_box,
        )
        pdf.pages.insert(insert_at, new_page)
        pdf.save(req.out_path)
    return {"status": "ok", "out_path": req.out_path}


def _cross_copy(req: PageOpsRequest):
    """Copy pages from src_path into paths[0] at insert_at position."""
    if not req.src_path:
        raise HTTPException(status_code=400, detail="src_path required for cross_copy")
    
    import tempfile
    import shutil
    
    same_file = os.path.normpath(req.paths[0]) == os.path.normpath(req.src_path)
    same_output = os.path.normpath(req.paths[0]) == os.path.normpath(req.out_path)
    
    if same_file and same_output:
        # Same file: copy pages within the PDF
        with pikepdf.open(req.paths[0]) as pdf:
            insert_at = req.insert_at if req.insert_at is not None else len(pdf.pages)
            pages_to_copy = []
            for p in (req.src_pages or []):
                if p < 0 or p >= len(pdf.pages):
                    raise HTTPException(status_code=400, detail=f"Page index {p} out of range (total: {len(pdf.pages)})")
                pages_to_copy.append(pdf.pages[p])
            for offset, page in enumerate(pages_to_copy):
                pdf.pages.insert(insert_at + offset, page)
            # Save to temp file first to avoid file locking issues on Windows
            temp_fd, temp_path = tempfile.mkstemp(suffix='.pdf')
            os.close(temp_fd)
            pdf.save(temp_path)
            shutil.copy2(temp_path, req.out_path)
            os.unlink(temp_path)
    else:
        # Different files or different output
        with pikepdf.open(req.paths[0]) as dst_pdf:
            with pikepdf.open(req.src_path) as src_pdf:
                insert_at = req.insert_at if req.insert_at is not None else len(dst_pdf.pages)
                for offset, p in enumerate(req.src_pages or []):
                    if p < 0 or p >= len(src_pdf.pages):
                        raise HTTPException(status_code=400, detail=f"Page index {p} out of range (total: {len(src_pdf.pages)})")
                    dst_pdf.pages.insert(insert_at + offset, src_pdf.pages[p])
            # Save to temp file first
            temp_fd, temp_path = tempfile.mkstemp(suffix='.pdf')
            os.close(temp_fd)
            dst_pdf.save(temp_path)
            shutil.copy2(temp_path, req.out_path)
            os.unlink(temp_path)
    return {"status": "ok", "out_path": req.out_path}


def _delete_pages(req: PageOpsRequest):
    """Delete specified pages from the PDF."""
    import tempfile
    import shutil
    
    with pikepdf.open(req.paths[0]) as pdf:
        for i in sorted(req.pages or [], reverse=True):
            if i < 0 or i >= len(pdf.pages):
                raise HTTPException(status_code=400, detail=f"Page index {i} out of range (total: {len(pdf.pages)})")
            del pdf.pages[i]
        # Save to temp file first
        temp_fd, temp_path = tempfile.mkstemp(suffix='.pdf')
        os.close(temp_fd)
        pdf.save(temp_path)
        shutil.copy2(temp_path, req.out_path)
        os.unlink(temp_path)
    return {"status": "ok", "out_path": req.out_path}


def _reorder(req: PageOpsRequest):
    """Reorder pages according to new_order list (list of original 0-based indices)."""
    import tempfile
    import shutil
    
    if not req.new_order:
        raise HTTPException(status_code=400, detail="new_order required for reorder")
    with pikepdf.open(req.paths[0]) as pdf:
        original_pages = list(pdf.pages)
        while len(pdf.pages) > 0:
            del pdf.pages[0]
        for i in req.new_order:
            if i < 0 or i >= len(original_pages):
                raise HTTPException(status_code=400, detail=f"Page index {i} out of range")
            pdf.pages.append(original_pages[i])
        # Save to temp file first
        temp_fd, temp_path = tempfile.mkstemp(suffix='.pdf')
        os.close(temp_fd)
        pdf.save(temp_path)
        shutil.copy2(temp_path, req.out_path)
        os.unlink(temp_path)
    return {"status": "ok", "out_path": req.out_path}


def _extract_pages(req: PageOpsRequest):
    """Extract selected pages into one new PDF."""
    if not req.pages:
        raise HTTPException(status_code=400, detail="pages required for extract_pages")

    out_dir = os.path.dirname(os.path.normpath(req.out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with pikepdf.open(req.paths[0]) as src:
        with pikepdf.Pdf.new() as dst:
            for i in req.pages:
                if i < 0 or i >= len(src.pages):
                    raise HTTPException(status_code=400, detail=f"Page index {i} out of range (total: {len(src.pages)})")
                dst.pages.append(src.pages[i])
            dst.save(req.out_path)

    return {"status": "ok", "out_path": req.out_path, "page_count": len(req.pages)}


def _rotate_pages(req: PageOpsRequest):
    """Rotate selected pages (or all pages) by a multiple of 90 degrees."""
    if not req.paths:
        raise HTTPException(status_code=400, detail="No input PDF provided")

    rotate = req.rotate if req.rotate is not None else 0
    rotate = ((rotate % 360) + 360) % 360
    if rotate not in (0, 90, 180, 270):
        raise HTTPException(status_code=400, detail=f"Invalid rotate value: {req.rotate}")

    src_path = os.path.normpath(req.paths[0])
    out_path = os.path.normpath(req.out_path)
    if not os.path.exists(src_path):
        raise HTTPException(status_code=400, detail=f"Input file not found: {src_path}")

    if rotate == 0:
        shutil.copy2(src_path, out_path)
        return {"status": "ok", "out_path": out_path, "rotate": rotate, "page_count": 0}

    doc = None
    temp_path = None
    try:
        doc = fitz.open(src_path)
        total_pages = doc.page_count
        if total_pages <= 0:
            raise HTTPException(status_code=400, detail="No pages found in PDF")

        target_pages = req.pages if req.pages is not None else list(range(total_pages))
        for page_index in target_pages:
            if page_index < 0 or page_index >= total_pages:
                raise HTTPException(
                    status_code=400,
                    detail=f"Page index {page_index} out of range (total: {total_pages})",
                )
            page = doc.load_page(page_index)
            current_rotation = int(page.rotation or 0)
            page.set_rotation((current_rotation + rotate) % 360)

        fd, temp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        doc.save(temp_path, garbage=4, deflate=True)
        shutil.copy2(temp_path, out_path)
        return {
            "status": "ok",
            "out_path": out_path,
            "rotate": rotate,
            "page_count": len(target_pages),
        }
    finally:
        if doc is not None:
            doc.close()
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def _normalize_size_by_max_width(req: PageOpsRequest):
    """Scale every page proportionally so all pages share the max page width."""
    return _normalize_pages_size(req, mode="max_width")


def _normalize_size_by_max_height(req: PageOpsRequest):
    """Scale every page proportionally so all pages share the max page height."""
    return _normalize_pages_size(req, mode="max_height")


def _normalize_pages_size(req: PageOpsRequest, mode: Literal["max_width", "max_height"]):
    if not req.paths:
        raise HTTPException(status_code=400, detail="No input PDF provided")

    src_path = os.path.normpath(req.paths[0])
    out_path = os.path.normpath(req.out_path)
    if not os.path.exists(src_path):
        raise HTTPException(status_code=400, detail=f"Input file not found: {src_path}")

    src_doc = None
    out_doc = None
    temp_path = None

    try:
        src_doc = fitz.open(src_path)
        if src_doc.page_count <= 0:
            raise HTTPException(status_code=400, detail="No pages found in PDF")

        max_width = 0.0
        max_height = 0.0
        for page in src_doc:
            rect = page.rect
            max_width = max(max_width, float(rect.width))
            max_height = max(max_height, float(rect.height))

        if max_width <= 0 or max_height <= 0:
            raise HTTPException(status_code=400, detail="Invalid page size detected")

        out_doc = fitz.open()
        for page_index, page in enumerate(src_doc):
            rect = page.rect
            src_w = float(rect.width)
            src_h = float(rect.height)
            if src_w <= 0 or src_h <= 0:
                raise HTTPException(status_code=400, detail=f"Invalid page size at page {page_index + 1}")

            if mode == "max_width":
                scale = max_width / src_w
            else:
                scale = max_height / src_h

            dst_w = src_w * scale
            dst_h = src_h * scale

            dst_page = out_doc.new_page(width=dst_w, height=dst_h)
            # True geometric scaling of page content (not adding empty background margins).
            dst_page.show_pdf_page(dst_page.rect, src_doc, page_index, keep_proportion=True)

        fd, temp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        out_doc.save(temp_path)
        shutil.copy2(temp_path, out_path)

        return {
            "status": "ok",
            "out_path": out_path,
            "page_count": out_doc.page_count,
            "mode": mode,
            "max_width": max_width,
            "max_height": max_height,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Normalize page size failed: {e}")
    finally:
        if src_doc is not None:
            src_doc.close()
        if out_doc is not None:
            out_doc.close()
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
