import os
import re
import threading
import textwrap
import time
import unicodedata
import zipfile
from io import BytesIO
from typing import Any, List, Literal, Optional
from xml.sax.saxutils import escape

import fitz  # PyMuPDF
from PIL import Image, ImageStat
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
MAX_LONG_IMAGE_PAGES = 5
TXT_WRAP_WIDTH = 78
MARKDOWN_WRAP_WIDTH = 88
CONVERT_PROGRESS_TTL_SECONDS = 3600
DOCX_MARGIN_PT = 36.0
DOCX_DEFAULT_FONT_SIZE = 11.0
DOCX_LAYOUT_FONT_SCALE = 0.92
TEXT_BLOCK_TYPE = 0
SYMBOL_FONT_MAP = {
    "\uf044": "\u0394",
    "\uf059": "\u03a8",
    "\uf061": "\u03b1",
    "\uf063": "\u03c7",
    "\uf065": "\u03b5",
    "\uf06a": "\u03c6",
    "\uf06e": "\u03bd",
    "\uf0b9": "\u2032",
    "\uf0d1": "\u2207",
    "\uf0e5": "\u2211",
    "\uf0e9": "\u23a1",
    "\uf0ea": "\u23a2",
    "\uf0eb": "\u23a3",
    "\uf0f2": "\u222b",
    "\uf0f9": "\u23a4",
    "\uf0fa": "\u23a5",
    "\uf0fb": "\u23a6",
}
NO_SPACE_BEFORE_CHARS = (
    ",.;:!?%)]}>"
    "\u3001\u3002\uff0c\uff1b\uff1a\uff01\uff1f\uff09\u3011\u300b\u201d\u2019"
)
NO_SPACE_AFTER_CHARS = "([{<\uff08\u3010\u300a\u201c\u2018"
TERMINAL_PUNCTUATION = (
    ".!?;:"
    "\u3002\uff01\uff1f\uff1b\uff1a\u2026"
    "\")]}>\u201d\u2019\uff09\u3011\u300b"
)

_convert_progress_lock = threading.Lock()
_convert_progress: dict[str, dict[str, Any]] = {}


class ConvertRequest(BaseModel):
    path: str
    out_path: str
    fmt: Literal["txt", "html", "markdown", "docx", "jpg_single", "jpg_long"]
    dpi: int = 150
    pages: Optional[List[int]] = None  # None = all pages
    quality: int = 85                  # JPEG quality
    formula_ocr: bool = False          # deprecated compatibility flag; DOCX now preserves formulas as images
    task_id: Optional[str] = None


def _cleanup_convert_progress_locked(now: Optional[float] = None) -> None:
    current_time = time.time() if now is None else now
    expired = [
        task_id
        for task_id, item in _convert_progress.items()
        if current_time - float(item.get("updated_at") or item.get("created_at") or 0) > CONVERT_PROGRESS_TTL_SECONDS
    ]
    for task_id in expired:
        _convert_progress.pop(task_id, None)


def _set_convert_progress(task_id: Optional[str], **fields: Any) -> None:
    if not task_id:
        return
    now = time.time()
    with _convert_progress_lock:
        _cleanup_convert_progress_locked(now)
        current = _convert_progress.get(task_id) or {
            "task_id": task_id,
            "status": "running",
            "stage": "准备转换",
            "progress": 0,
            "current": 0,
            "total": 0,
            "done": False,
            "error": None,
            "created_at": now,
        }
        current.update(fields)
        progress = current.get("progress")
        if isinstance(progress, (int, float)):
            current["progress"] = max(0, min(100, int(round(progress))))
        current["updated_at"] = now
        _convert_progress[task_id] = current


def _set_convert_page_progress(
    task_id: Optional[str],
    current: int,
    total: int,
    *,
    base: float,
    span: float,
    stage: str,
) -> None:
    safe_total = max(1, total)
    safe_current = max(0, min(current, safe_total))
    progress = base + (safe_current / safe_total) * span
    _set_convert_progress(
        task_id,
        progress=progress,
        current=safe_current,
        total=safe_total,
        stage=stage,
    )


@router.get("/status/{task_id}")
def convert_status(task_id: str) -> dict:
    with _convert_progress_lock:
        _cleanup_convert_progress_locked()
        item = _convert_progress.get(task_id)
        if not item:
            return {"task_id": task_id, "status": "not_found", "progress": 0, "done": False}
        return dict(item)


@router.post("/")
def convert(req: ConvertRequest) -> dict:
    if not os.path.exists(req.path):
        raise HTTPException(status_code=400, detail="Input file not found")

    dispatch = {
        "txt":        _to_txt,
        "html":       _to_html,
        "markdown":   _to_markdown,
        "docx":       _to_docx,
        "jpg_single": _to_jpg_pages,
        "jpg_long":   _to_jpg_long,
    }
    _set_convert_progress(
        req.task_id,
        status="running",
        stage="准备转换",
        progress=1,
        current=0,
        total=0,
        done=False,
        error=None,
        out_path=os.path.normpath(req.out_path),
        fmt=req.fmt,
    )
    try:
        result = dispatch[req.fmt](req)
    except HTTPException as e:
        _set_convert_progress(
            req.task_id,
            status="error",
            stage="转换失败",
            error=str(e.detail),
            done=True,
        )
        raise
    except Exception as e:
        _set_convert_progress(
            req.task_id,
            status="error",
            stage="转换失败",
            error=str(e),
            done=True,
        )
        raise

    _set_convert_progress(
        req.task_id,
        status="done",
        stage="转换完成",
        progress=100,
        done=True,
        out_path=result.get("out_path") or req.out_path,
    )
    return result


def _to_txt(req: ConvertRequest) -> dict:
    try:
        doc = fitz.open(req.path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot open PDF: {e}")

    valid_pages = _valid_page_indexes(req.pages, len(doc))
    paragraphs: List[str] = []
    try:
        skip_page_markers = len(valid_pages) > 1
        _set_convert_progress(req.task_id, stage="正在导出文本", progress=6, current=0, total=len(valid_pages))
        for page_pos, i in enumerate(valid_pages):
            page_paragraphs = _extract_txt_paragraphs(doc[i], skip_page_markers)
            _append_page_paragraphs(paragraphs, page_paragraphs)
            _set_convert_page_progress(
                req.task_id,
                page_pos + 1,
                len(valid_pages),
                base=8,
                span=84,
                stage=f"正在导出文本 {page_pos + 1}/{len(valid_pages)}",
            )
    finally:
        doc.close()

    out_path = os.path.normpath(req.out_path)
    _set_convert_progress(req.task_id, stage="正在写入文本文件", progress=96)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_format_txt_document(paragraphs))

    return {"status": "ok", "out_path": out_path, "page_count": len(valid_pages)}


def _valid_page_indexes(pages: Optional[List[int]], page_count: int) -> List[int]:
    requested_pages = pages or list(range(page_count))
    return [i for i in requested_pages if 0 <= i < page_count]


def _extract_txt_paragraphs(page: Any, skip_page_markers: bool) -> List[str]:
    paragraphs: List[str] = []
    for block in _iter_docx_blocks(page, skip_page_markers):
        if block["type"] != "text":
            continue

        raw_text = str(block.get("text") or "")
        if not raw_text.strip() or raw_text.lstrip().startswith("<image:"):
            continue

        for paragraph in _normalize_plain_text_block(raw_text):
            paragraphs.append(paragraph)

    return paragraphs


def _get_text_blocks(page: Any) -> List[tuple]:
    try:
        blocks = page.get_text("blocks", sort=True)
    except TypeError:
        blocks = page.get_text("blocks")
        blocks = sorted(blocks, key=lambda b: (b[1], b[0]))
    return list(blocks)


def _is_text_block(block: tuple) -> bool:
    block_type = block[6] if len(block) > 6 else TEXT_BLOCK_TYPE
    return block_type == TEXT_BLOCK_TYPE


def _normalize_plain_text_block(raw_text: str) -> List[str]:
    lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    paragraphs: List[str] = []
    current = ""

    def flush_current() -> None:
        nonlocal current
        if current:
            paragraphs.append(current)
            current = ""

    for line in lines:
        stripped = _normalize_inline_spaces(line.strip())
        if not stripped:
            flush_current()
            continue

        if _is_plain_text_structural_line(stripped):
            flush_current()
            paragraphs.append(stripped)
            continue

        if not current:
            current = stripped
        else:
            current = _merge_soft_wrapped_line(current, stripped)

    flush_current()
    return paragraphs


def _normalize_inline_spaces(text: str) -> str:
    text = _strip_private_use_chars(text)
    return re.sub(r"[ \t\f\v]+", " ", text).strip()


def _strip_private_use_chars(text: str) -> str:
    return "".join(ch for ch in text if unicodedata.category(ch) != "Co")


def _append_page_paragraphs(document_paragraphs: List[str], page_paragraphs: List[str]) -> None:
    if not page_paragraphs:
        return

    first = page_paragraphs[0]
    rest = page_paragraphs[1:]
    if document_paragraphs and _should_merge_across_page(document_paragraphs[-1], first):
        document_paragraphs[-1] = _merge_soft_wrapped_line(document_paragraphs[-1], first)
        document_paragraphs.extend(rest)
        return

    document_paragraphs.extend(page_paragraphs)


def _should_merge_across_page(previous: str, current: str) -> bool:
    previous = previous.strip()
    current = current.strip()
    if not previous or not current:
        return False
    if "\n" in previous or "\n" in current:
        return False
    if _is_plain_text_structural_line(previous) or _is_plain_text_structural_line(current):
        return False
    if previous[-1] in TERMINAL_PUNCTUATION:
        return False
    if previous.endswith("-"):
        return True
    if len(previous) < 40:
        return False
    return not re.match(r"^[A-Z]", current)


def _format_txt_document(paragraphs: List[str]) -> str:
    formatted = [_wrap_plain_text_paragraph(p) for p in paragraphs if p.strip()]
    if not formatted:
        return ""
    return "\n\n".join(formatted).strip() + "\n"


def _wrap_plain_text_paragraph(text: str) -> str:
    text = _normalize_inline_spaces(text)
    if _is_toc_leader_line(text):
        return text
    list_match = re.match(r"^((?:[-*+]|\d+[.)])\s+)(.+)$", text)
    if list_match:
        prefix, body = list_match.groups()
        return _wrap_text_line(body, initial_indent=prefix, subsequent_indent=" " * len(prefix))
    return _wrap_text_line(text)


def _wrap_text_line(text: str, initial_indent: str = "", subsequent_indent: str = "") -> str:
    if _contains_cjk(text):
        return _wrap_by_display_width(text, TXT_WRAP_WIDTH, initial_indent, subsequent_indent)
    return textwrap.fill(
        text,
        width=TXT_WRAP_WIDTH,
        initial_indent=initial_indent,
        subsequent_indent=subsequent_indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def _wrap_by_display_width(
    text: str,
    width: int,
    initial_indent: str = "",
    subsequent_indent: str = "",
) -> str:
    lines: List[str] = []
    indent = initial_indent
    current = ""
    current_width = _display_width(indent)

    for ch in text:
        if ch.isspace():
            ch = " "

        ch_width = _char_display_width(ch)
        if current and current_width + ch_width > width and ch not in NO_SPACE_BEFORE_CHARS:
            lines.append(indent + current.rstrip())
            indent = subsequent_indent
            current = ch.lstrip()
            current_width = _display_width(indent) + _display_width(current)
            continue

        current += ch
        current_width += ch_width

    if current:
        lines.append(indent + current.rstrip())
    return "\n".join(lines)


def _display_width(text: str) -> int:
    return sum(_char_display_width(ch) for ch in text)


def _char_display_width(ch: str) -> int:
    if unicodedata.east_asian_width(ch) in {"F", "W"}:
        return 2
    return 1


def _contains_cjk(text: str) -> bool:
    return any(_is_cjk(ch) for ch in text)


def _is_plain_text_structural_line(line: str) -> bool:
    return bool(
        re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", line)
        or re.match(r"^\s{0,3}[-*_]{3,}\s*$", line)
        or _is_toc_leader_line(line)
    )


def _is_likely_page_marker(text: str, block: tuple, page_rect: Any) -> bool:
    marker = text.strip()
    if len(marker) > 24:
        return False

    marker_patterns = (
        r"^\d{1,5}$",
        r"^[-\u2013\u2014]\s*\d{1,5}\s*[-\u2013\u2014]$",
        r"^(?:page|p\.)\s*\d{1,5}$",
        r"^\u7b2c\s*\d{1,5}\s*\u9875$",
    )
    if not any(re.match(pattern, marker, flags=re.IGNORECASE) for pattern in marker_patterns):
        return False

    y0 = float(block[1])
    y1 = float(block[3])
    page_height = float(page_rect.height)
    return y1 < page_height * 0.12 or y0 > page_height * 0.88


def _to_html(req: ConvertRequest) -> dict:
    return _export_text_format(req, "html")


def _to_docx(req: ConvertRequest) -> dict:
    try:
        doc = fitz.open(req.path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot open PDF: {e}")

    valid_pages = _valid_page_indexes(req.pages, len(doc))
    out_path = os.path.normpath(req.out_path)
    try:
        _write_docx_from_pdf(doc, valid_pages, out_path, req.task_id)
    finally:
        doc.close()

    return {"status": "ok", "out_path": out_path, "page_count": len(valid_pages)}


def _to_markdown(req: ConvertRequest) -> dict:
    try:
        doc = fitz.open(req.path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot open PDF: {e}")

    valid_pages = _valid_page_indexes(req.pages, len(doc))
    out_path = os.path.normpath(req.out_path)
    out_dir = os.path.dirname(out_path) or "."
    out_name = os.path.splitext(os.path.basename(out_path))[0]
    assets_dir_name = f"{out_name}_assets"
    assets_dir = os.path.join(out_dir, assets_dir_name)

    chunks = []
    assets_count = 0
    assets_created = False
    converted_pages = 0

    try:
        skip_page_markers = True
        _set_convert_progress(req.task_id, stage="正在整理 Markdown", progress=6, current=0, total=len(valid_pages))
        for page_pos, i in enumerate(valid_pages):
            page = doc[i]
            md_text = _get_markdown_text(page, skip_page_markers)
            if md_text:
                chunks.append(md_text)
            converted_pages += 1

            image_links = []
            for image_idx, image_meta in enumerate(page.get_images(full=True), 1):
                xref = image_meta[0]
                if not xref:
                    continue
                try:
                    image_info = doc.extract_image(xref)
                except Exception:
                    continue

                image_bytes = image_info.get("image")
                if not image_bytes:
                    continue

                ext = (image_info.get("ext") or "png").lower()
                if not assets_created:
                    os.makedirs(assets_dir, exist_ok=True)
                    assets_created = True

                image_name = f"page_{i + 1:04d}_img_{image_idx:02d}.{ext}"
                image_path = os.path.join(assets_dir, image_name)
                with open(image_path, "wb") as f:
                    f.write(image_bytes)
                assets_count += 1

                rel_path = f"{assets_dir_name}/{image_name}".replace("\\", "/")
                image_links.append(f"![Page {i + 1} Image {image_idx}]({rel_path})")

            if image_links:
                chunks.append("\n\n".join(image_links))
            _set_convert_page_progress(
                req.task_id,
                page_pos + 1,
                len(valid_pages),
                base=8,
                span=84,
                stage=f"正在整理 Markdown {page_pos + 1}/{len(valid_pages)}",
            )
    finally:
        doc.close()

    _set_convert_progress(req.task_id, stage="正在写入 Markdown 文件", progress=96)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(_format_markdown_document(chunks))

    return {
        "status": "ok",
        "out_path": out_path,
        "page_count": converted_pages,
        "assets_count": assets_count,
        "assets_dir": assets_dir if assets_created else None,
    }


def _get_markdown_text(page: Any, skip_page_markers: bool = False) -> str:
    return _markdown_from_text_blocks(page, skip_page_markers)


def _markdown_from_text_blocks(page: Any, skip_page_markers: bool = False) -> str:
    paragraphs = []
    for block in _iter_docx_blocks(page, skip_page_markers):
        if block["type"] != "text":
            continue

        text = str(block.get("text") or "").strip()
        if not text or text.startswith("<image:"):
            continue

        normalized = _normalize_wrapped_markdown(text)
        if normalized:
            heading = _markdown_heading_from_plain_text(normalized)
            paragraphs.append(heading or normalized)

    return "\n\n".join(paragraphs).strip()


def _format_markdown_document(chunks: List[str]) -> str:
    raw_text = "\n\n".join(chunk.strip() for chunk in chunks if chunk.strip())
    if not raw_text:
        return ""
    return _normalize_wrapped_markdown(raw_text).strip() + "\n"


def _normalize_wrapped_markdown(raw_text: str) -> str:
    lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: List[str] = []
    current_paragraph = ""
    list_lines: List[str] = []
    quote_lines: List[str] = []
    table_lines: List[str] = []

    def emit(block: str) -> None:
        block = block.strip("\n")
        if block:
            blocks.append(block)

    def flush_current() -> None:
        nonlocal current_paragraph
        if current_paragraph:
            emit(_wrap_markdown_paragraph(current_paragraph))
            current_paragraph = ""

    def flush_list() -> None:
        nonlocal list_lines
        if list_lines:
            emit(_format_markdown_list(list_lines))
            list_lines = []

    def flush_quote() -> None:
        nonlocal quote_lines
        if quote_lines:
            emit("\n".join(quote_lines))
            quote_lines = []

    def flush_table() -> None:
        nonlocal table_lines
        if table_lines:
            emit("\n".join(table_lines))
            table_lines = []

    def flush_all() -> None:
        flush_current()
        flush_list()
        flush_quote()
        flush_table()

    i = 0
    while i < len(lines):
        raw_line = lines[i].rstrip()
        stripped = raw_line.strip()

        if not stripped:
            flush_all()
            i += 1
            continue

        fence = _normalize_markdown_fence(stripped)
        if fence:
            flush_all()
            fence_char = fence[0]
            code_lines = [fence]
            i += 1
            while i < len(lines):
                code_line = lines[i].rstrip()
                code_stripped = code_line.strip()
                if _is_markdown_closing_fence(code_stripped, fence_char):
                    code_lines.append(fence_char * 3)
                    i += 1
                    break
                code_lines.append(code_line)
                i += 1
            emit("\n".join(code_lines))
            continue

        setext_heading = _setext_heading_level(stripped)
        if setext_heading and current_paragraph:
            text = current_paragraph
            current_paragraph = ""
            flush_list()
            flush_quote()
            flush_table()
            emit(f"{'#' * setext_heading} {text.strip()}")
            i += 1
            continue

        heading = _normalize_markdown_heading(stripped)
        if heading:
            flush_all()
            emit(heading)
            i += 1
            continue

        if _is_markdown_thematic_break(stripped):
            flush_all()
            emit("---")
            i += 1
            continue

        image = _normalize_markdown_image(stripped)
        if image:
            flush_all()
            emit(image)
            i += 1
            continue

        if _is_markdown_table_line(stripped):
            flush_current()
            flush_list()
            flush_quote()
            table_lines.append(_normalize_inline_spaces(stripped))
            i += 1
            continue

        if _is_toc_leader_line(stripped):
            flush_all()
            emit(_normalize_inline_spaces(stripped))
            i += 1
            continue

        list_item = _normalize_markdown_list_item(stripped)
        if list_item:
            flush_current()
            flush_quote()
            flush_table()
            list_lines.append(list_item)
            i += 1
            continue

        if list_lines:
            list_lines[-1] = _merge_markdown_list_continuation(list_lines[-1], stripped)
            i += 1
            continue

        quote = _normalize_markdown_quote(stripped)
        if quote:
            flush_current()
            flush_list()
            flush_table()
            quote_lines.append(quote)
            i += 1
            continue

        flush_list()
        flush_quote()
        flush_table()
        stripped = _normalize_inline_spaces(stripped)
        if not current_paragraph:
            current_paragraph = stripped
        else:
            current_paragraph = _merge_soft_wrapped_line(current_paragraph, stripped)

        i += 1

    flush_all()
    return "\n\n".join(blocks).strip()


def _wrap_markdown_paragraph(text: str) -> str:
    return _wrap_markdown_text(_normalize_inline_spaces(text))


def _wrap_markdown_text(text: str, initial_indent: str = "", subsequent_indent: str = "") -> str:
    if _contains_cjk(text):
        return _wrap_by_display_width(text, MARKDOWN_WRAP_WIDTH, initial_indent, subsequent_indent)
    return textwrap.fill(
        text,
        width=MARKDOWN_WRAP_WIDTH,
        initial_indent=initial_indent,
        subsequent_indent=subsequent_indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def _normalize_markdown_fence(line: str) -> str:
    match = re.match(r"^(`{3,}|~{3,})(.*)$", line)
    if not match:
        return ""
    fence, info = match.groups()
    info = _normalize_inline_spaces(info.strip())
    return fence[0] * max(3, len(fence)) + info


def _is_markdown_closing_fence(line: str, fence_char: str) -> bool:
    return bool(re.match(rf"^{re.escape(fence_char)}{{3,}}\s*$", line))


def _normalize_markdown_heading(line: str) -> str:
    match = re.match(r"^(#{1,6})(?!#)\s*(.*?)\s*#*\s*$", line)
    if not match:
        return ""
    hashes, title = match.groups()
    title = _normalize_inline_spaces(title)
    if not title:
        return hashes
    return f"{hashes} {title}"


def _markdown_heading_from_plain_text(text: str) -> str:
    normalized = _normalize_inline_spaces(text)
    if not normalized or "\n" in text or _is_toc_leader_line(normalized):
        return ""

    if re.match(r"^(?:摘\s*要|Abstract|目\s*录|致\s*谢|参考文献)$", normalized, flags=re.IGNORECASE):
        return f"# {normalized}"
    if re.match(r"^第[一二三四五六七八九十\d]+章\b", normalized):
        return f"# {normalized}"

    numbered = re.match(r"^(\d+(?:[.．]\d+)+)\s+\S", normalized)
    if numbered:
        level = min(6, numbered.group(1).replace("．", ".").count(".") + 1)
        return f"{'#' * level} {normalized}"

    return ""


def _is_toc_leader_line(line: str) -> bool:
    normalized = _normalize_inline_spaces(line)
    if not normalized:
        return False
    return bool(re.search(r"(?:\.|\u2026){6,}\s*(?:[IVXLCDM]+|\d+)\s*$", normalized, flags=re.IGNORECASE))


def _setext_heading_level(line: str) -> int:
    if re.match(r"^=+\s*$", line):
        return 1
    if re.match(r"^-{2,}\s*$", line):
        return 2
    return 0


def _is_markdown_thematic_break(line: str) -> bool:
    return bool(re.match(r"^\s{0,3}(?:[-*_]\s*){3,}$", line))


def _normalize_markdown_image(line: str) -> str:
    match = re.match(r"^!\[(.*?)\]\((.*?)\)\s*$", line)
    if not match:
        return ""
    alt_text, target = match.groups()
    return f"![{_normalize_inline_spaces(alt_text)}]({_normalize_inline_spaces(target)})"


def _is_markdown_table_line(line: str) -> bool:
    if line.startswith("|"):
        return True
    return bool(re.match(r"^\s{0,3}:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+$", line))


def _normalize_markdown_list_item(line: str) -> str:
    unordered = re.match(r"^\s{0,3}[-*+]\s+(.*)$", line)
    if unordered:
        return f"- {_normalize_inline_spaces(unordered.group(1))}"

    ordered = re.match(r"^\s{0,3}(\d+)[.)]\s+(.*)$", line)
    if ordered:
        number, body = ordered.groups()
        return f"{number}. {_normalize_inline_spaces(body)}"

    return ""


def _merge_markdown_list_continuation(previous: str, current: str) -> str:
    match = re.match(r"^((?:-|\d+\.)\s+)(.*)$", previous)
    if not match:
        return previous
    prefix, body = match.groups()
    return prefix + _merge_soft_wrapped_line(body, _normalize_inline_spaces(current))


def _format_markdown_list(lines: List[str]) -> str:
    formatted = []
    for line in lines:
        match = re.match(r"^((?:-|\d+\.)\s+)(.*)$", line)
        if not match:
            formatted.append(line)
            continue
        prefix, body = match.groups()
        formatted.append(
            _wrap_markdown_text(
                _normalize_inline_spaces(body),
                initial_indent=prefix,
                subsequent_indent=" " * len(prefix),
            )
        )
    return "\n".join(formatted)


def _normalize_markdown_quote(line: str) -> str:
    match = re.match(r"^(>+)\s?(.*)$", line)
    if not match:
        return ""
    markers, body = match.groups()
    body = _normalize_inline_spaces(body)
    return f"{markers} {body}" if body else markers


def _write_docx_from_pdf(
    doc: Any,
    pages: List[int],
    out_path: str,
    task_id: Optional[str] = None,
) -> None:
    body_parts: List[str] = []
    relationships: List[str] = []
    media_files: List[tuple[str, bytes]] = []
    image_counter = 1
    doc_pr_id = 1

    first_page = doc[pages[0]] if pages else (doc[0] if len(doc) else None)
    page_width = float(first_page.rect.width) if first_page is not None else 595.0
    page_height = float(first_page.rect.height) if first_page is not None else 842.0

    total_pages = len(pages)
    _set_convert_progress(task_id, stage="正在生成 Word", progress=6, current=0, total=total_pages)
    for page_pos, page_index in enumerate(pages):
        if page_index < 0 or page_index >= len(doc):
            continue

        page = doc[page_index]
        _set_convert_page_progress(
            task_id,
            page_pos,
            total_pages,
            base=8,
            span=84,
            stage=f"正在生成 Word {page_pos + 1}/{total_pages}",
        )
        if page_pos > 0:
            body_parts.append(_docx_page_break())

        previous_bottom: Optional[float] = None
        layout_blocks = _iter_docx_layout_blocks(page, skip_page_markers=True)
        table_blocks = _detect_docx_table_blocks(page)
        protected_regions = [{"bbox": block["bbox"]} for block in table_blocks]
        if table_blocks:
            layout_blocks = _remove_docx_blocks_overlapping_tables(layout_blocks, table_blocks)
        layout_blocks = _replace_complex_formula_regions_with_images(page, layout_blocks, protected_regions)
        if table_blocks:
            layout_blocks = _annotate_docx_layout_metrics(
                sorted(layout_blocks + table_blocks, key=lambda item: (item["bbox"][1], item["bbox"][0]))
            )

        for block in layout_blocks:
            bbox = block["bbox"]
            if block["type"] == "text":
                text = str(block.get("text") or "")
                if not text.strip() or _is_likely_page_marker(text, _docx_bbox_tuple(bbox, text), page.rect):
                    continue

                before = int(block.get("spacing_before", _docx_layout_spacing_before(previous_bottom, bbox[1])))
                body_parts.append(_docx_layout_text_paragraph(block, float(page.rect.width), before))
                previous_bottom = bbox[3]
                continue

            if block["type"] == "table":
                before = int(block.get("spacing_before", _docx_layout_spacing_before(previous_bottom, bbox[1])))
                body_parts.append(_docx_table_xml(block, float(page.rect.width), before))
                previous_bottom = bbox[3]
                continue

            if block["type"] in {"image", "formula_image"}:
                image_bytes = block.get("image")
                if not image_bytes:
                    continue

                ext, normalized_bytes, image_width, image_height = _prepare_docx_image(
                    image_bytes,
                    str(block.get("ext") or "png"),
                )
                if not normalized_bytes:
                    continue

                media_name = f"image{image_counter}.{ext}"
                rel_id = f"rId{image_counter}"
                image_counter += 1
                relationships.append(
                    f'<Relationship Id="{rel_id}" '
                    f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
                    f'Target="media/{media_name}"/>'
                )
                media_files.append((media_name, normalized_bytes))
                before = int(block.get("spacing_before", _docx_layout_spacing_before(previous_bottom, bbox[1])))
                body_parts.append(
                    _docx_image_paragraph(
                        rel_id,
                        doc_pr_id,
                        bbox,
                        float(page.rect.width),
                        image_width,
                        image_height,
                        before,
                        str(block.get("description") or block.get("latex") or ""),
                    )
                )
                doc_pr_id += 1
                previous_bottom = bbox[3]
        _set_convert_page_progress(
            task_id,
            page_pos + 1,
            total_pages,
            base=8,
            span=84,
            stage=f"正在生成 Word {page_pos + 1}/{total_pages}",
        )

    sect_pr = _docx_section_properties(page_width, page_height)
    document_xml = _docx_document_xml("\n".join(body_parts), sect_pr)

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    _set_convert_progress(task_id, stage="正在写入 Word 文件", progress=96)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _docx_content_types(media_files))
        zf.writestr("_rels/.rels", _docx_root_rels())
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/styles.xml", _docx_styles_xml())
        zf.writestr("word/settings.xml", _docx_settings_xml())
        zf.writestr("word/_rels/document.xml.rels", _docx_document_rels(relationships))
        for media_name, data in media_files:
            zf.writestr(f"word/media/{media_name}", data)


def _iter_docx_layout_blocks(page: Any, skip_page_markers: bool = False) -> List[dict]:
    try:
        raw = page.get_text("dict", sort=True)
    except TypeError:
        raw = page.get_text("dict")

    line_segments: List[dict] = []
    image_blocks: List[dict] = []
    for block in raw.get("blocks", []):
        bbox = [float(v) for v in block.get("bbox", [0, 0, 0, 0])]
        block_type = int(block.get("type", TEXT_BLOCK_TYPE))

        if block_type == TEXT_BLOCK_TYPE:
            for line in block.get("lines", []):
                line_bbox = [float(v) for v in line.get("bbox", bbox)]
                runs = _docx_runs_from_pdf_line(line, line_bbox)
                text = "".join(str(run.get("text") or "") for run in runs)
                if not text.strip():
                    continue
                if skip_page_markers and _is_likely_non_content_margin_text(text, line_bbox, page.rect):
                    continue
                line_segments.append(
                    {
                        "type": "line_segment",
                        "bbox": line_bbox,
                        "text": text,
                        "runs": runs,
                    }
                )
            continue

        image_bytes = block.get("image")
        if image_bytes:
            image_bytes, image_ext = _prepare_docx_extracted_image_block(
                page,
                bbox,
                image_bytes,
                str(block.get("ext") or "png"),
            )
            if not image_bytes:
                continue
            image_blocks.append(
                {
                    "type": "image",
                    "bbox": bbox,
                    "image": image_bytes,
                    "ext": image_ext,
                }
            )

    text_lines = _group_docx_line_segments(line_segments)
    return _annotate_docx_layout_metrics(
        sorted(text_lines + image_blocks, key=lambda item: (item["bbox"][1], item["bbox"][0]))
    )


def _detect_docx_table_blocks(page: Any) -> List[dict]:
    find_tables = getattr(page, "find_tables", None)
    if not callable(find_tables):
        return []

    try:
        result = find_tables()
    except Exception:
        return []

    tables = list(getattr(result, "tables", []) or [])
    blocks: List[dict] = []
    for table in tables:
        block = _build_docx_table_block(page, table)
        if block is None:
            block = _build_docx_table_image_fallback(page, table)
        if block is not None:
            blocks.append(block)
    return blocks


def _build_docx_table_block(page: Any, table: Any) -> Optional[dict]:
    rows = list(getattr(table, "rows", []) or [])
    row_count = int(getattr(table, "row_count", 0) or 0)
    col_count = int(getattr(table, "col_count", 0) or 0)
    if row_count <= 0 or col_count <= 0 or not rows:
        return None

    try:
        raw_data = table.extract()
    except Exception:
        raw_data = []

    column_groups = _docx_table_column_groups(table, raw_data)
    if not column_groups:
        return None

    row_ranges = _docx_table_row_ranges(rows)
    if not row_ranges or len(row_ranges) != row_count:
        return None

    normalized_col_count = len(column_groups)
    occupancy: List[List[Optional[int]]] = [[None] * normalized_col_count for _ in range(row_count)]
    cells: List[dict] = []

    for row_index, row in enumerate(rows):
        row_cells = list(getattr(row, "cells", []) or [])
        values = raw_data[row_index] if row_index < len(raw_data) else []
        for col_index, cell_bbox in enumerate(row_cells):
            if cell_bbox is None:
                continue

            text = ""
            if col_index < len(values):
                text = _normalize_docx_table_cell_text(values[col_index])
            if not text and _docx_table_is_separator_cell(cell_bbox, table.bbox):
                continue

            start_col, end_col = _docx_table_bbox_group_span(cell_bbox, column_groups)
            if start_col is None or end_col is None:
                continue
            end_row = _docx_table_bbox_row_end(cell_bbox, row_ranges, row_index)
            if end_row < row_index:
                end_row = row_index

            if any(occupancy[r][c] is not None for r in range(row_index, end_row + 1) for c in range(start_col, end_col + 1)):
                continue

            cell_id = len(cells)
            cell_info = {
                "id": cell_id,
                "row": row_index,
                "col": start_col,
                "row_span": (end_row - row_index) + 1,
                "col_span": (end_col - start_col) + 1,
                "bbox": [float(v) for v in cell_bbox],
                "text": text,
            }
            cells.append(cell_info)
            for cover_row in range(row_index, end_row + 1):
                for cover_col in range(start_col, end_col + 1):
                    occupancy[cover_row][cover_col] = cell_id

    if not cells:
        return None

    table_bbox = [float(v) for v in getattr(table, "bbox", (0, 0, 0, 0))]
    non_empty_cells = sum(1 for cell in cells if str(cell.get("text") or "").strip())
    if non_empty_cells <= 1:
        return None

    column_widths = [max(18.0, float(group[1]) - float(group[0])) for group in column_groups]
    return {
        "type": "table",
        "bbox": table_bbox,
        "rows": row_count,
        "cols": normalized_col_count,
        "row_ranges": row_ranges,
        "column_widths": column_widths,
        "occupancy": occupancy,
        "cells": cells,
    }


def _build_docx_table_image_fallback(page: Any, table: Any) -> Optional[dict]:
    row_count = int(getattr(table, "row_count", 0) or 0)
    col_count = int(getattr(table, "col_count", 0) or 0)
    bbox = [float(v) for v in getattr(table, "bbox", (0, 0, 0, 0))]
    width = max(0.0, bbox[2] - bbox[0])
    height = max(0.0, bbox[3] - bbox[1])
    if row_count < 2 or col_count < 2:
        return None
    if width < float(page.rect.width) * 0.20 or height < 24.0:
        return None

    image_bytes = _render_docx_page_region(page, _pad_bbox_to_page(bbox, page.rect, 2.0, 2.0))
    if not image_bytes or _is_blank_docx_image(image_bytes):
        return None
    return {
        "type": "image",
        "bbox": bbox,
        "image": image_bytes,
        "ext": "png",
        "description": "Table screenshot from source PDF",
    }


def _docx_table_column_groups(table: Any, raw_data: List[List[Any]]) -> List[List[float]]:
    rows = list(getattr(table, "rows", []) or [])
    col_count = int(getattr(table, "col_count", 0) or 0)
    if col_count <= 0 or not rows:
        return []

    widths: List[float] = []
    samples: List[Optional[List[float]]] = []
    text_counts = [0] * col_count
    for col_index in range(col_count):
        sample_width: Optional[float] = None
        sample_bbox: Optional[List[float]] = None
        for row_index, row in enumerate(rows):
            row_cells = list(getattr(row, "cells", []) or [])
            if col_index >= len(row_cells):
                continue
            bbox = row_cells[col_index]
            if bbox is not None:
                width = max(0.0, float(bbox[2]) - float(bbox[0]))
                if sample_width is None or (0 < width < sample_width):
                    sample_width = width
                    sample_bbox = [float(v) for v in bbox]
            values = raw_data[row_index] if row_index < len(raw_data) else []
            if col_index < len(values) and str(values[col_index] or "").strip():
                text_counts[col_index] += 1
        widths.append(sample_width or 0.0)
        samples.append(sample_bbox)

    table_bbox = [float(v) for v in getattr(table, "bbox", (0, 0, 0, 0))]
    table_width = max(1.0, table_bbox[2] - table_bbox[0])
    separator_threshold = max(6.0, table_width * 0.035)
    anchor_indexes = [index for index, width in enumerate(widths) if width > separator_threshold]
    if not anchor_indexes:
        anchor_indexes = [index for index, count in enumerate(text_counts) if count > 0]
    if not anchor_indexes:
        anchor_indexes = list(range(col_count))

    groups: dict[int, List[int]] = {anchor: [anchor] for anchor in anchor_indexes}
    for col_index in range(col_count):
        if col_index in groups:
            continue
        nearest_anchor = min(anchor_indexes, key=lambda anchor: (abs(anchor - col_index), anchor))
        groups[nearest_anchor].append(col_index)

    column_groups: List[List[float]] = []
    for anchor in anchor_indexes:
        merged_bbox: Optional[List[float]] = None
        for col_index in groups[anchor]:
            current = samples[col_index]
            if current is None:
                continue
            merged_bbox = current if merged_bbox is None else _union_bbox(merged_bbox, current)
        if merged_bbox is not None:
            column_groups.append([merged_bbox[0], merged_bbox[2]])

    return sorted(column_groups, key=lambda item: item[0])


def _docx_table_row_ranges(rows: List[Any]) -> List[List[float]]:
    ranges: List[List[float]] = []
    for row in rows:
        row_cells = [cell for cell in list(getattr(row, "cells", []) or []) if cell is not None]
        if not row_cells:
            continue
        top = min(float(cell[1]) for cell in row_cells)
        bottoms = [float(cell[3]) for cell in row_cells]
        if not bottoms:
            continue
        bottom = min(bottoms)
        if bottom <= top:
            bottom = max(bottoms)
        ranges.append([top, bottom])
    return ranges


def _docx_table_bbox_group_span(
    bbox: Any,
    column_groups: List[List[float]],
) -> tuple[Optional[int], Optional[int]]:
    left = float(bbox[0]) + 0.1
    right = float(bbox[2]) - 0.1
    overlaps = [
        index
        for index, group in enumerate(column_groups)
        if right > float(group[0]) and left < float(group[1])
    ]
    if not overlaps:
        return None, None
    return overlaps[0], overlaps[-1]


def _docx_table_bbox_row_end(bbox: Any, row_ranges: List[List[float]], start_row: int) -> int:
    bottom = float(bbox[3]) - 0.1
    end_row = start_row
    for index in range(start_row, len(row_ranges)):
        if bottom > float(row_ranges[index][0]):
            end_row = index
    return end_row


def _docx_table_is_separator_cell(cell_bbox: Any, table_bbox: Any) -> bool:
    width = max(0.0, float(cell_bbox[2]) - float(cell_bbox[0]))
    table_width = max(1.0, float(table_bbox[2]) - float(table_bbox[0]))
    return width <= max(4.5, table_width * 0.02)


def _normalize_docx_table_cell_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    lines = [_normalize_inline_spaces(line) for line in text.split("\n")]
    cleaned = "\n".join(line for line in lines if line)
    cleaned = cleaned.replace("`∗`", "∗").replace("`", "")
    return cleaned.strip()


def _remove_docx_blocks_overlapping_tables(blocks: List[dict], tables: List[dict]) -> List[dict]:
    if not tables:
        return blocks

    kept: List[dict] = []
    for block in blocks:
        if any(_bbox_overlap_ratio(block["bbox"], table["bbox"]) > 0.18 for table in tables):
            continue
        kept.append(block)
    return kept


def _docx_runs_from_pdf_line(line: dict, line_bbox: List[float]) -> List[dict]:
    raw_runs: List[dict] = []
    for span in line.get("spans", []):
        font = str(span.get("font") or "")
        text = _normalize_pdf_span_text(str(span.get("text") or ""), font)
        if not text:
            continue

        color = span.get("color")
        raw_runs.append(
            {
                "text": text,
                "bbox": [float(v) for v in span.get("bbox", line_bbox)],
                "font_size": float(span.get("size") or DOCX_DEFAULT_FONT_SIZE),
                "font": font,
                "bold": "bold" in font.lower(),
                "italic": "italic" in font.lower() or "oblique" in font.lower(),
                "color": color if isinstance(color, int) else None,
            }
        )

    _annotate_docx_run_vertical_align(raw_runs)
    return raw_runs


def _normalize_pdf_span_text(text: str, font: str) -> str:
    if not text:
        return ""
    if "symbol" in font.lower():
        text = text.replace("\uf0d1+", "\u2207\u00b2")
        text = "".join(SYMBOL_FONT_MAP.get(ch, ch) for ch in text)
    return "".join(ch for ch in text if ch == "\t" or ch == "\n" or unicodedata.category(ch)[0] != "C").replace("\n", " ")


def _annotate_docx_run_vertical_align(runs: List[dict]) -> None:
    visible_runs = [run for run in runs if str(run.get("text") or "").strip()]
    if not visible_runs:
        return

    dominant_size = max(float(run.get("font_size") or DOCX_DEFAULT_FONT_SIZE) for run in visible_runs)
    dominant_centers = [
        (run["bbox"][1] + run["bbox"][3]) / 2
        for run in visible_runs
        if float(run.get("font_size") or DOCX_DEFAULT_FONT_SIZE) >= dominant_size * 0.85
    ]
    dominant_center = sum(dominant_centers) / len(dominant_centers) if dominant_centers else None
    for run in runs:
        run["dominant_font_size"] = dominant_size
        run["vertical_align"] = ""
        if dominant_center is None:
            continue
        font_size = float(run.get("font_size") or DOCX_DEFAULT_FONT_SIZE)
        if font_size > dominant_size * 0.86:
            continue
        center = (run["bbox"][1] + run["bbox"][3]) / 2
        if center < dominant_center - 1.0:
            run["vertical_align"] = "superscript"
        elif center > dominant_center + 1.0:
            run["vertical_align"] = "subscript"


def _group_docx_line_segments(segments: List[dict]) -> List[dict]:
    if not segments:
        return []

    rows: List[List[dict]] = []
    for segment in sorted(segments, key=lambda item: (_bbox_center_y(item["bbox"]), item["bbox"][0])):
        center_y = _bbox_center_y(segment["bbox"])
        height = max(1.0, segment["bbox"][3] - segment["bbox"][1])
        target_row: Optional[List[dict]] = None
        for row in rows[-3:]:
            row_center = sum(_bbox_center_y(item["bbox"]) for item in row) / len(row)
            row_height = max(max(1.0, item["bbox"][3] - item["bbox"][1]) for item in row)
            if abs(center_y - row_center) <= max(2.4, min(height, row_height) * 0.35):
                target_row = row
                break

        if target_row is None:
            rows.append([segment])
        else:
            target_row.append(segment)

    lines: List[dict] = []
    for row in rows:
        row = sorted(row, key=lambda item: item["bbox"][0])
        bbox = row[0]["bbox"]
        for segment in row[1:]:
            bbox = _union_bbox(bbox, segment["bbox"])

        line_height = max(
            max(float(run.get("dominant_font_size") or run.get("font_size") or DOCX_DEFAULT_FONT_SIZE) for run in segment["runs"])
            for segment in row
        )
        lines.append(
            {
                "type": "text",
                "bbox": bbox,
                "text": " ".join(_normalize_inline_spaces(segment["text"]) for segment in row if segment["text"].strip()),
                "segments": row,
                "font_size": line_height,
            }
        )

    return lines


def _bbox_center_y(bbox: List[float]) -> float:
    return (bbox[1] + bbox[3]) / 2


def _annotate_docx_layout_metrics(blocks: List[dict]) -> List[dict]:
    text_blocks = [block for block in blocks if block["type"] == "text"]
    for index, block in enumerate(text_blocks):
        next_text = text_blocks[index + 1] if index + 1 < len(text_blocks) else None
        block["line_height"] = _estimate_docx_layout_line_height(block, next_text)

    previous: Optional[dict] = None
    for block in blocks:
        top = float(block["bbox"][1])
        if previous is None:
            spacing_points = max(0.0, top - DOCX_MARGIN_PT)
        else:
            spacing_points = max(0.0, top - _docx_layout_block_bottom(previous))
            if spacing_points < 1.0:
                spacing_points = 0.0
        block["spacing_before"] = _pt_to_twips(spacing_points)
        previous = block

    return blocks


def _estimate_docx_layout_line_height(block: dict, next_text: Optional[dict]) -> float:
    bbox = block["bbox"]
    bbox_height = max(1.0, float(bbox[3]) - float(bbox[1]))
    font_size = max(6.0, float(block.get("font_size") or DOCX_DEFAULT_FONT_SIZE) * DOCX_LAYOUT_FONT_SCALE)
    fallback = max(bbox_height, font_size * 1.18)

    if next_text is None:
        return min(36.0, fallback)

    top_delta = float(next_text["bbox"][1]) - float(bbox[1])
    max_regular_pitch = max(26.0, font_size * 2.15)
    min_regular_pitch = max(6.0, font_size * 0.72)
    if min_regular_pitch <= top_delta <= max_regular_pitch:
        return top_delta
    return min(36.0, fallback)


def _docx_layout_block_bottom(block: dict) -> float:
    if block["type"] == "text":
        return float(block["bbox"][1]) + float(block.get("line_height") or (block["bbox"][3] - block["bbox"][1]))
    return float(block["bbox"][3])


def _replace_complex_formula_regions_with_images(
    page: Any,
    blocks: List[dict],
    protected_regions: Optional[List[dict]] = None,
) -> List[dict]:
    regions = _detect_complex_formula_regions(page, blocks)
    if protected_regions:
        regions = [
            region
            for region in regions
            if not any(
                _bbox_overlap_ratio(region["bbox"], protected["bbox"]) > 0.25
                or _bbox_overlap_ratio(protected["bbox"], region["bbox"]) > 0.25
                for protected in protected_regions
            )
        ]
    if not regions:
        return blocks

    formula_blocks: List[dict] = []
    for index, region in enumerate(regions, 1):
        image_bytes, image_width, image_height = _render_formula_region(page, region["bbox"])
        if not image_bytes:
            continue
        # DOCX currently uses the original PDF region as the visible formula.
        # Do not depend on formula OCR here: the recognizer can return an empty
        # result or crash in packaged Paddle runtimes, while the page render is
        # the most faithful visual fallback.
        if _is_blank_docx_image(image_bytes):
            continue
        formula_blocks.append(
            {
                "type": "formula_image",
                "bbox": region["bbox"],
                "image": image_bytes,
                "ext": "png",
                "image_width": image_width,
                "image_height": image_height,
                "latex": "",
                "description": "Formula screenshot from source PDF",
                "formula_index": index,
            }
        )

    if not formula_blocks:
        return blocks

    kept: List[dict] = []
    for block in blocks:
        if any(_bbox_overlap_ratio(block["bbox"], formula["bbox"]) > 0.28 for formula in formula_blocks):
            continue
        kept.append(block)

    return _annotate_docx_layout_metrics(
        sorted(kept + formula_blocks, key=lambda item: (item["bbox"][1], item["bbox"][0]))
    )


def _is_blank_docx_image(image_bytes: bytes) -> bool:
    stats = _docx_image_content_stats(image_bytes)
    if stats is None:
        return True
    width, height, mean, dark_ratio, non_white_ratio = stats
    if width * height <= 400:
        return False
    # Sparse formulas often have very little pure black ink after antialiasing,
    # so dark-pixel ratio alone misclassifies valid formula crops as blank.
    return mean > 253.5 and non_white_ratio < 0.002 and dark_ratio < 0.0005


def _docx_image_content_stats(image_bytes: bytes) -> Optional[tuple[int, int, float, float, float]]:
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            gray = img.convert("L")
            width, height = img.size
            sample_width = min(240, max(1, width))
            sample_height = max(1, int(height * sample_width / max(1, width))) if width > sample_width else max(1, height)
            sample = gray.resize((sample_width, sample_height)) if (sample_width, sample_height) != (width, height) else gray
            pixels = list(sample.getdata())
            total = max(1, len(pixels))
            mean = sum(pixels) / total
            dark_ratio = sum(1 for value in pixels if value < 55) / total
            non_white_ratio = sum(1 for value in pixels if value < 245) / total
            return width, height, mean, dark_ratio, non_white_ratio
    except Exception:
        return None


def _detect_complex_formula_regions(page: Any, blocks: List[dict]) -> List[dict]:
    text_blocks = [block for block in blocks if block["type"] == "text"]
    image_blocks = [block for block in blocks if block["type"] == "image"]
    candidates: List[dict] = []
    for block_index, block in enumerate(text_blocks):
        score, reasons = _formula_candidate_score(block, page.rect)
        item = dict(block)
        item["_formula_score"] = score
        item["_formula_reasons"] = reasons
        item["_formula_block_index"] = block_index
        if score >= 5:
            item["formula_score"] = score
            item["formula_reasons"] = reasons
            candidates.append(item)

    if not candidates:
        return []

    groups: List[List[dict]] = []
    for block in sorted(candidates, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        if not groups:
            groups.append([block])
            continue
        last = groups[-1][-1]
        y_gap = block["bbox"][1] - last["bbox"][3]
        left_delta = abs(block["bbox"][0] - last["bbox"][0])
        if y_gap <= 24.0 and _are_formula_lines_aligned(block["bbox"], last["bbox"], page.rect):
            groups[-1].append(block)
        else:
            groups.append([block])

    regions: List[dict] = []
    for group in groups:
        bbox = list(group[0]["bbox"])
        reasons: List[str] = []
        score = 0
        for block in group:
            bbox = _union_bbox(bbox, block["bbox"])
            score += int(block.get("formula_score") or 0)
            reasons.extend(str(item) for item in block.get("formula_reasons") or [])

        padded = _pad_bbox_to_page(bbox, page.rect, 8.0, 5.0)
        has_formula_number = any("equation_number" in block.get("formula_reasons", []) for block in group)
        image_fragments = _find_formula_image_fragments_near_bbox(bbox, image_blocks, page.rect) if has_formula_number else []
        if image_fragments:
            for image_block in image_fragments:
                bbox = _union_bbox(bbox, image_block["bbox"])
            padded = _pad_bbox_to_page(bbox, page.rect, 8.0, 5.0)
        drawing_lines = _count_formula_drawing_lines(page, padded)
        multi_row_group = _formula_group_row_count(group) >= 2
        complex_group = multi_row_group or drawing_lines > 0 or bool(image_fragments)
        if not complex_group:
            continue
        if _is_likely_table_formula_group(group, page.rect, has_formula_number):
            continue
        if score < 9 and not has_formula_number and drawing_lines == 0:
            continue
        if _is_oversized_weak_formula_region(padded, page.rect, has_formula_number, drawing_lines):
            continue

        reasons = sorted(
            set(
                reasons
                + (["drawing_line"] if drawing_lines else [])
                + (["image_fragment"] if image_fragments else [])
            )
        )
        regions.append(
            {
                "bbox": padded,
                "confidence": min(1.0, score / 18.0),
                "reason": reasons,
            }
        )

    rule_regions = _merge_formula_regions(regions, page.rect)
    layout_regions = _detect_paddle_layout_formula_regions(page, rule_regions)
    if layout_regions:
        return _combine_rule_and_layout_formula_regions(rule_regions, layout_regions, page.rect)
    if _is_paddle_layout_detector_ready():
        return [region for region in rule_regions if _is_strong_rule_formula_region(region)]
    return rule_regions


def _is_strong_rule_formula_region(region: dict) -> bool:
    reasons = set(region.get("reason") or [])
    return "equation_number" in reasons or "drawing_line" in reasons or "image_fragment" in reasons


def _find_formula_image_fragments_near_bbox(
    formula_bbox: List[float],
    image_blocks: List[dict],
    page_rect: Any,
) -> List[dict]:
    if not image_blocks:
        return []

    page_width = float(page_rect.width)
    formula_height = max(1.0, float(formula_bbox[3]) - float(formula_bbox[1]))
    search_bbox = _pad_bbox_to_page(formula_bbox, page_rect, 18.0, max(8.0, formula_height * 0.65))
    fragments: List[dict] = []

    for image_block in image_blocks:
        bbox = image_block["bbox"]
        width = float(bbox[2]) - float(bbox[0])
        height = float(bbox[3]) - float(bbox[1])
        if width < 2.0 or height < 2.0:
            continue
        if width > page_width * 0.55 or height > 90.0:
            continue

        vertical_overlap = _bbox_y_overlap(search_bbox, bbox)
        center_delta = abs(_bbox_center_y(bbox) - _bbox_center_y(formula_bbox))
        if vertical_overlap <= 0 and center_delta > max(14.0, formula_height * 0.95):
            continue
        if _bbox_gap(search_bbox, bbox) > max(18.0, page_width * 0.06):
            continue
        fragments.append(image_block)

    return fragments


def _bbox_overlaps_any_region(bbox: List[float], regions: List[dict], min_ratio: float) -> bool:
    for region in regions:
        other = region["bbox"]
        if _bbox_overlap_ratio(bbox, other) >= min_ratio or _bbox_overlap_ratio(other, bbox) >= min_ratio:
            return True
    return False


def _combine_rule_and_layout_formula_regions(
    rule_regions: List[dict],
    layout_regions: List[dict],
    page_rect: Any,
) -> List[dict]:
    combined: List[dict] = []
    used_rule_indexes: set[int] = set()

    for layout_region in layout_regions:
        matching_rules = [
            (index, rule_region)
            for index, rule_region in enumerate(rule_regions)
            if _formula_regions_match(layout_region["bbox"], rule_region["bbox"], page_rect)
        ]
        if matching_rules:
            for index, _ in matching_rules:
                used_rule_indexes.add(index)
            combined.append(_choose_mixed_formula_region(layout_region, [region for _, region in matching_rules]))
        else:
            combined.append(layout_region)

    for index, rule_region in enumerate(rule_regions):
        if index in used_rule_indexes:
            continue
        if _is_strong_rule_formula_region(rule_region) and not _bbox_overlaps_any_region(rule_region["bbox"], combined, 0.35):
            combined.append(rule_region)

    return _merge_formula_regions(combined, page_rect)


def _formula_regions_match(left: List[float], right: List[float], page_rect: Any) -> bool:
    if _bbox_overlap_ratio(left, right) >= 0.12 or _bbox_overlap_ratio(right, left) >= 0.12:
        return True
    if _bbox_gap(left, right) > 18.0:
        return False
    page_width = float(page_rect.width)
    center_delta = abs(((float(left[0]) + float(left[2])) / 2.0) - ((float(right[0]) + float(right[2])) / 2.0))
    return center_delta <= page_width * 0.20


def _choose_mixed_formula_region(layout_region: dict, rule_regions: List[dict]) -> dict:
    if not rule_regions:
        return layout_region

    refined_rule = dict(rule_regions[0])
    for rule_region in rule_regions[1:]:
        refined_rule["bbox"] = _union_bbox(refined_rule["bbox"], rule_region["bbox"])
        refined_rule["confidence"] = max(float(refined_rule.get("confidence") or 0), float(rule_region.get("confidence") or 0))
        refined_rule["reason"] = sorted(set(list(refined_rule.get("reason") or []) + list(rule_region.get("reason") or [])))

    layout_area = _bbox_area(layout_region["bbox"])
    rule_area = _bbox_area(refined_rule["bbox"])
    if rule_area <= 0:
        return layout_region

    # Mixed mode: keep simple formula-like text editable, and only screenshot
    # the tighter complex-formula region. PP-DocLayout-S is useful as a signal,
    # but its formula boxes can include neighboring prose.
    if layout_area > rule_area * 1.18:
        refined_rule["reason"] = sorted(set(list(refined_rule.get("reason") or []) + ["layout_confirmed"]))
        refined_rule["confidence"] = max(float(refined_rule.get("confidence") or 0), float(layout_region.get("confidence") or 0))
        return refined_rule

    if rule_area > layout_area * 1.35:
        return layout_region

    return layout_region if float(layout_region.get("confidence") or 0) >= float(refined_rule.get("confidence") or 0) else refined_rule


def _bbox_area(bbox: List[float]) -> float:
    return max(0.0, float(bbox[2]) - float(bbox[0])) * max(0.0, float(bbox[3]) - float(bbox[1]))


def _is_oversized_weak_formula_region(
    bbox: List[float],
    page_rect: Any,
    has_formula_number: bool,
    drawing_lines: int,
) -> bool:
    if has_formula_number or drawing_lines > 0:
        return False
    height = float(bbox[3]) - float(bbox[1])
    width = float(bbox[2]) - float(bbox[0])
    page_height = float(page_rect.height)
    page_width = float(page_rect.width)
    return height > page_height * 0.35 or (height > page_height * 0.22 and width > page_width * 0.72)


def _detect_paddle_layout_formula_regions(page: Any, seed_regions: List[dict]) -> List[dict]:
    if not seed_regions:
        return []
    try:
        from api import ocr as ocr_api
    except Exception:
        return []

    scale = 1.5
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False)
        image_bytes = pix.tobytes("png")
    except Exception:
        return []

    boxes = ocr_api.detect_formula_layout_image_bytes(image_bytes)
    if not boxes:
        return []

    page_rect = page.rect
    formula_boxes: List[dict] = []
    number_boxes: List[dict] = []
    for box in boxes:
        label = str(box.get("label") or "").lower()
        try:
            score = float(box.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        raw_bbox = box.get("bbox")
        if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) < 4:
            continue
        bbox = [
            float(raw_bbox[0]) / scale,
            float(raw_bbox[1]) / scale,
            float(raw_bbox[2]) / scale,
            float(raw_bbox[3]) / scale,
        ]
        bbox = _pad_bbox_to_page(bbox, page_rect, 8.0, 6.0)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width < 12.0 or height < 5.0:
            continue
        if label == "formula":
            if score < 0.45:
                continue
            if width < float(page_rect.width) * 0.08 and height < 12.0 and score < 0.70:
                continue
            if not _bbox_near_any_region(bbox, seed_regions, max_gap=42.0) and score < 0.78:
                continue
            formula_boxes.append({"bbox": bbox, "score": score, "reason": ["layout_formula"]})
        elif label == "formula_number":
            if score < 0.42:
                continue
            number_boxes.append({"bbox": bbox, "score": score, "reason": ["layout_formula_number"]})

    if not formula_boxes:
        return []

    regions: List[dict] = []
    for formula in formula_boxes:
        bbox = list(formula["bbox"])
        reasons = list(formula.get("reason") or [])
        for number in number_boxes:
            if _formula_number_belongs_to_region(number["bbox"], bbox, page_rect):
                bbox = _union_bbox(bbox, number["bbox"])
                reasons.extend(number.get("reason") or [])
        final_bbox = _pad_bbox_to_page(bbox, page_rect, 10.0, 8.0)
        if "layout_formula_number" not in reasons and not _is_display_layout_formula_bbox(final_bbox, page_rect):
            continue
        regions.append(
            {
                "bbox": final_bbox,
                "confidence": min(1.0, max(0.5, float(formula.get("score") or 0.0))),
                "reason": sorted(set(reasons)),
            }
        )

    merged = _merge_formula_regions(regions, page_rect)
    if not merged:
        return []
    return [
        region
        for region in merged
        if _bbox_near_any_region(region["bbox"], seed_regions, max_gap=52.0)
    ]


def _is_display_layout_formula_bbox(bbox: List[float], page_rect: Any) -> bool:
    page_width = float(page_rect.width)
    width = float(bbox[2]) - float(bbox[0])
    height = float(bbox[3]) - float(bbox[1])
    center = (float(bbox[0]) + float(bbox[2])) / 2.0
    centered = abs(center - page_width / 2.0) <= page_width * 0.22
    if width >= page_width * 0.34 and height >= 12.0:
        return True
    return centered and width >= page_width * 0.18 and height >= 10.0


def _is_paddle_layout_detector_ready() -> bool:
    try:
        from api import ocr as ocr_api
    except Exception:
        return False
    ready = getattr(ocr_api, "is_formula_layout_detector_ready", None)
    if not callable(ready):
        return False
    try:
        return bool(ready())
    except Exception:
        return False


def _bbox_near_any_region(bbox: List[float], regions: List[dict], max_gap: float) -> bool:
    return any(_bbox_gap(bbox, region["bbox"]) <= max_gap for region in regions)


def _bbox_gap(left: List[float], right: List[float]) -> float:
    dx = max(float(right[0]) - float(left[2]), float(left[0]) - float(right[2]), 0.0)
    dy = max(float(right[1]) - float(left[3]), float(left[1]) - float(right[3]), 0.0)
    return (dx * dx + dy * dy) ** 0.5


def _formula_number_belongs_to_region(number_bbox: List[float], formula_bbox: List[float], page_rect: Any) -> bool:
    page_width = float(page_rect.width)
    number_center_y = (float(number_bbox[1]) + float(number_bbox[3])) / 2.0
    formula_center_y = (float(formula_bbox[1]) + float(formula_bbox[3])) / 2.0
    vertical_close = abs(number_center_y - formula_center_y) <= max(18.0, (float(formula_bbox[3]) - float(formula_bbox[1])) * 0.75)
    y_overlap = _bbox_y_overlap(number_bbox, formula_bbox)
    right_side = float(number_bbox[0]) >= float(formula_bbox[0]) and float(number_bbox[0]) > page_width * 0.55
    horizontal_gap = max(0.0, float(number_bbox[0]) - float(formula_bbox[2]))
    return right_side and (vertical_close or y_overlap > 0.12) and horizontal_gap < page_width * 0.45


def _are_formula_lines_aligned(left: List[float], right: List[float], page_rect: Any) -> bool:
    page_width = float(page_rect.width)
    left_center = (float(left[0]) + float(left[2])) / 2.0
    right_center = (float(right[0]) + float(right[2])) / 2.0
    center_delta = abs(left_center - right_center)
    left_delta = abs(float(left[0]) - float(right[0]))
    right_delta = abs(float(left[2]) - float(right[2]))
    return (
        _bbox_x_overlap(left, right) > 0.10
        or center_delta < page_width * 0.18
        or left_delta < 120.0
        or right_delta < 120.0
    )


def _formula_group_row_count(group: List[dict]) -> int:
    if not group:
        return 0
    rows: List[List[float]] = []
    for block in sorted(group, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        bbox = block["bbox"]
        center_y = _bbox_center_y(bbox)
        height = max(1.0, float(bbox[3]) - float(bbox[1]))
        placed = False
        for row_bbox in rows:
            row_center = (float(row_bbox[1]) + float(row_bbox[3])) / 2.0
            row_height = max(1.0, float(row_bbox[3]) - float(row_bbox[1]))
            if abs(center_y - row_center) <= max(3.0, min(height, row_height) * 0.45):
                row_bbox[0] = min(float(row_bbox[0]), float(bbox[0]))
                row_bbox[1] = min(float(row_bbox[1]), float(bbox[1]))
                row_bbox[2] = max(float(row_bbox[2]), float(bbox[2]))
                row_bbox[3] = max(float(row_bbox[3]), float(bbox[3]))
                placed = True
                break
        if not placed:
            rows.append(list(bbox))
    return len(rows)


def _formula_candidate_score(block: dict, page_rect: Any) -> tuple[int, List[str]]:
    text = _normalize_inline_spaces(str(block.get("text") or ""))
    if not text:
        return 0, []
    if _is_toc_leader_line(text) or re.search(r"^\[\d+\]", text):
        return 0, []

    bbox = block["bbox"]
    width = bbox[2] - bbox[0]
    page_width = float(page_rect.width)
    center = (bbox[0] + bbox[2]) / 2.0
    centered = width < page_width * 0.74 and abs(center - page_width / 2.0) < page_width * 0.20
    cjk_count = sum(1 for ch in text if _is_cjk(ch))
    visible_count = sum(1 for ch in text if not ch.isspace())
    cjk_ratio = cjk_count / max(1, visible_count)
    math_chars = sum(1 for ch in text if ch in "=+-−×÷*/→↔⇌∑∫√Δμµχεν∂∇|<>≤≥≈")
    equation_number = bool(re.search(r"[（(]\s*\d+(?:[.．]\d+)+\s*[）)]\s*$", text))

    runs = [run for segment in block.get("segments") or [] for run in segment.get("runs") or []]
    math_font_count = 0
    script_count = 0
    italic_count = 0
    for run in runs:
        font = str(run.get("font") or "").lower()
        if "symbol" in font or "cambria" in font or "math" in font:
            math_font_count += 1
        if run.get("vertical_align"):
            script_count += 1
        if run.get("italic"):
            italic_count += 1

    score = 0
    reasons: List[str] = []
    if equation_number:
        score += 6
        reasons.append("equation_number")
    if math_font_count:
        score += min(5, math_font_count)
        reasons.append("math_font")
    if script_count:
        score += min(4, script_count)
        reasons.append("script")
    if math_chars >= 3:
        score += min(4, math_chars // 2)
        reasons.append("math_symbols")
    if centered and math_chars >= 1:
        score += 2
        reasons.append("centered")
    if italic_count >= 2 and math_chars >= 1:
        score += 1
        reasons.append("italic_vars")
    if cjk_ratio > 0.35 and not equation_number:
        score -= 4
    if len(text) > 130 and not equation_number:
        score -= 3
    if width > page_width * 0.86 and not equation_number:
        score -= 2

    return score, reasons


def _is_likely_table_formula_group(group: List[dict], page_rect: Any, has_formula_number: bool) -> bool:
    if has_formula_number or len(group) < 4:
        return False
    page_width = float(page_rect.width)
    bbox = list(group[0]["bbox"])
    for block in group[1:]:
        bbox = _union_bbox(bbox, block["bbox"])
    width = bbox[2] - bbox[0]
    arrow_rows = sum(1 for block in group if "→" in str(block.get("text") or ""))
    similar_lefts = len({round(float(block["bbox"][0]) / 8.0) for block in group}) <= 3
    return width > page_width * 0.58 and arrow_rows >= 3 and similar_lefts


def _count_formula_drawing_lines(page: Any, bbox: List[float]) -> int:
    try:
        drawings = page.get_drawings()
    except Exception:
        return 0
    rect = fitz.Rect(bbox)
    count = 0
    for drawing in drawings:
        drawing_rect = drawing.get("rect")
        if drawing_rect is not None:
            try:
                if not fitz.Rect(drawing_rect).intersects(rect):
                    continue
            except Exception:
                pass
        for item in drawing.get("items", []):
            if not item or item[0] != "l" or len(item) < 3:
                continue
            p0, p1 = item[1], item[2]
            x0, y0 = float(p0.x), float(p0.y)
            x1, y1 = float(p1.x), float(p1.y)
            if abs(y0 - y1) > 1.5:
                continue
            if max(x0, x1) < rect.x0 or min(x0, x1) > rect.x1 or y0 < rect.y0 or y0 > rect.y1:
                continue
            if abs(x1 - x0) >= 18.0:
                count += 1
    return count


def _merge_formula_regions(regions: List[dict], page_rect: Any) -> List[dict]:
    if not regions:
        return []
    merged: List[dict] = []
    for region in sorted(regions, key=lambda item: (item["bbox"][1], item["bbox"][0])):
        if not merged:
            merged.append(region)
            continue
        last = merged[-1]
        if _should_merge_formula_region_pair(last, region, page_rect):
            last["bbox"] = _pad_bbox_to_page(_union_bbox(last["bbox"], region["bbox"]), page_rect, 0, 0)
            last["confidence"] = max(float(last.get("confidence") or 0), float(region.get("confidence") or 0))
            last["reason"] = sorted(set(list(last.get("reason") or []) + list(region.get("reason") or [])))
        else:
            merged.append(region)
    return merged


def _should_merge_formula_region_pair(left: dict, right: dict, page_rect: Any) -> bool:
    left_bbox = left["bbox"]
    right_bbox = right["bbox"]
    overlap = _bbox_overlap_ratio(left_bbox, right_bbox)
    if overlap > 0.05:
        return True

    y_gap = float(right_bbox[1]) - float(left_bbox[3])
    if y_gap < 6.0:
        return True
    if y_gap > 28.0:
        return False

    page_width = float(page_rect.width)
    left_center = (float(left_bbox[0]) + float(left_bbox[2])) / 2.0
    right_center = (float(right_bbox[0]) + float(right_bbox[2])) / 2.0
    center_delta = abs(left_center - right_center)
    left_delta = abs(float(left_bbox[0]) - float(right_bbox[0]))
    right_delta = abs(float(left_bbox[2]) - float(right_bbox[2]))
    x_overlap = _bbox_x_overlap(left_bbox, right_bbox)
    has_equation_number = "equation_number" in set(left.get("reason") or []) or "equation_number" in set(right.get("reason") or [])
    aligned = (
        x_overlap > 0.08
        or center_delta < page_width * 0.16
        or left_delta < 120.0
        or right_delta < 120.0
        or has_equation_number
    )
    return aligned


def _render_formula_region(page: Any, bbox: List[float]) -> tuple[bytes, int, int]:
    rect = fitz.Rect(bbox)
    if rect.width < 4 or rect.height < 4:
        return b"", 0, 0
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(3.0, 3.0), colorspace=fitz.csRGB, clip=rect, alpha=False)
        return pix.tobytes("png"), int(pix.width), int(pix.height)
    except Exception:
        return b"", 0, 0


def _prepare_docx_extracted_image_block(
    page: Any,
    bbox: List[float],
    image_bytes: bytes,
    ext: str,
) -> tuple[bytes, str]:
    raw_stats = _docx_image_luminance_stats(image_bytes)
    if _should_rerender_extracted_image_block(image_bytes, bbox, raw_stats):
        rendered = _render_docx_page_region(page, bbox)
        if rendered:
            return rendered, "png"
    if _is_suspicious_dark_extracted_image(raw_stats):
        rendered = _render_docx_page_region(page, bbox)
        if rendered and _rendered_region_is_better_than_raw(raw_stats, rendered):
            return rendered, "png"
    return image_bytes, ext


def _should_rerender_extracted_image_block(
    image_bytes: bytes,
    bbox: List[float],
    stats: Optional[tuple[int, int, float, float]] = None,
) -> bool:
    display_width = max(0.0, float(bbox[2]) - float(bbox[0]))
    display_height = max(0.0, float(bbox[3]) - float(bbox[1]))
    if display_width < 4.0 or display_height < 4.0:
        return False

    if stats is None:
        stats = _docx_image_luminance_stats(image_bytes)
    if stats is None:
        return False

    width, height, _, _ = stats
    if width <= 0 or height <= 0:
        return False
    if width <= 4 and height <= 4 and (display_width >= 8.0 or display_height >= 8.0):
        return True

    scale_x = display_width / max(1.0, float(width))
    scale_y = display_height / max(1.0, float(height))
    return min(width, height) <= 8 and max(scale_x, scale_y) >= 8.0


def _docx_image_luminance_stats(image_bytes: bytes) -> Optional[tuple[int, int, float, float]]:
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            rgb = img.convert("RGB")
            width, height = img.size
            mean = sum(ImageStat.Stat(rgb).mean) / 3.0
            sample_width = min(180, max(1, width))
            sample_height = max(1, int(height * sample_width / max(1, width))) if width > sample_width else max(1, height)
            sample = rgb.resize((sample_width, sample_height)) if (sample_width, sample_height) != (width, height) else rgb
            pixels = list(sample.getdata())
            total = max(1, len(pixels))
            dark_ratio = sum(1 for r, g, b in pixels if (r + g + b) / 3.0 < 55.0) / total
            return width, height, mean, dark_ratio
    except Exception:
        return None


def _is_suspicious_dark_extracted_image(stats: Optional[tuple[int, int, float, float]]) -> bool:
    if stats is None:
        return False
    width, height, mean, dark_ratio = stats
    if width < 16 or height < 16:
        return False
    return mean < 70.0 and dark_ratio > 0.70


def _rendered_region_is_better_than_raw(
    raw_stats: Optional[tuple[int, int, float, float]],
    rendered_bytes: bytes,
) -> bool:
    if raw_stats is None:
        return False
    rendered_stats = _docx_image_luminance_stats(rendered_bytes)
    if rendered_stats is None:
        return False
    _, _, raw_mean, raw_dark_ratio = raw_stats
    _, _, rendered_mean, rendered_dark_ratio = rendered_stats
    return rendered_mean > max(150.0, raw_mean + 80.0) and rendered_dark_ratio < max(0.35, raw_dark_ratio - 0.35)


def _render_docx_page_region(page: Any, bbox: List[float]) -> bytes:
    rect = fitz.Rect(bbox)
    if rect.width < 1 or rect.height < 1:
        return b""
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(3.0, 3.0), colorspace=fitz.csRGB, clip=rect, alpha=False)
        return pix.tobytes("png")
    except Exception:
        return b""


def _pad_bbox_to_page(bbox: List[float], page_rect: Any, pad_x: float, pad_y: float) -> List[float]:
    return [
        max(float(page_rect.x0), float(bbox[0]) - pad_x),
        max(float(page_rect.y0), float(bbox[1]) - pad_y),
        min(float(page_rect.x1), float(bbox[2]) + pad_x),
        min(float(page_rect.y1), float(bbox[3]) + pad_y),
    ]


def _bbox_x_overlap(left: List[float], right: List[float]) -> float:
    overlap = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    width = max(1.0, min(left[2] - left[0], right[2] - right[0]))
    return overlap / width


def _bbox_y_overlap(left: List[float], right: List[float]) -> float:
    overlap = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    height = max(1.0, min(left[3] - left[1], right[3] - right[1]))
    return overlap / height


def _bbox_overlap_ratio(left: List[float], right: List[float]) -> float:
    x_overlap = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    y_overlap = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = x_overlap * y_overlap
    if intersection <= 0:
        return 0.0
    left_area = max(1.0, (left[2] - left[0]) * (left[3] - left[1]))
    return intersection / left_area


def _iter_docx_blocks(page: Any, skip_page_markers: bool = False) -> List[dict]:
    try:
        raw = page.get_text("dict", sort=True)
    except TypeError:
        raw = page.get_text("dict")

    blocks: List[dict] = []
    for block in raw.get("blocks", []):
        bbox = [float(v) for v in block.get("bbox", [0, 0, 0, 0])]
        block_type = int(block.get("type", TEXT_BLOCK_TYPE))

        if block_type == TEXT_BLOCK_TYPE:
            lines: List[str] = []
            line_bboxes: List[List[float]] = []
            sizes: List[float] = []
            fonts: List[str] = []
            colors: List[int] = []
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                line_text = "".join(str(span.get("text") or "") for span in spans).strip()
                if line_text:
                    lines.append(line_text)
                    line_bboxes.append([float(v) for v in line.get("bbox", bbox)])
                for span in spans:
                    span_text = str(span.get("text") or "")
                    if not span_text.strip():
                        continue
                    size = float(span.get("size") or DOCX_DEFAULT_FONT_SIZE)
                    sizes.extend([size] * max(1, len(span_text)))
                    fonts.append(str(span.get("font") or ""))
                    color = span.get("color")
                    if isinstance(color, int):
                        colors.append(color)

            text = "\n".join(lines).strip()
            if text:
                if skip_page_markers and _is_likely_non_content_margin_text(text, bbox, page.rect):
                    continue
                blocks.append(
                    {
                        "type": "text",
                        "bbox": bbox,
                        "text": text,
                        "first_bbox": line_bboxes[0] if line_bboxes else bbox,
                        "last_bbox": line_bboxes[-1] if line_bboxes else bbox,
                        "line_count": max(1, len(lines)),
                        "font_size": _weighted_average(sizes, DOCX_DEFAULT_FONT_SIZE),
                        "bold": any("bold" in font.lower() for font in fonts),
                        "italic": any(("italic" in font.lower() or "oblique" in font.lower()) for font in fonts),
                        "color": colors[0] if colors else None,
                    }
                )
            continue

        image_bytes = block.get("image")
        if image_bytes:
            blocks.append(
                {
                    "type": "image",
                    "bbox": bbox,
                    "image": image_bytes,
                    "ext": block.get("ext") or "png",
                }
            )

    return _merge_docx_text_blocks(sorted(blocks, key=lambda item: (item["bbox"][1], item["bbox"][0])), float(page.rect.width))


def _is_likely_non_content_margin_text(text: str, bbox: List[float], page_rect: Any) -> bool:
    normalized = _normalize_inline_spaces(text)
    if _is_likely_page_marker(normalized, _docx_bbox_tuple(bbox, normalized), page_rect):
        return True

    page_height = float(page_rect.height)
    page_width = float(page_rect.width)
    width = bbox[2] - bbox[0]
    if bbox[1] < page_height * 0.075 and width < page_width * 0.72 and len(normalized) <= 50:
        return True
    if bbox[3] < page_height * 0.09 and width < page_width * 0.55 and len(normalized) <= 40:
        return True
    if bbox[1] > page_height * 0.92 and len(normalized) <= 40:
        return True
    return False


def _merge_docx_text_blocks(blocks: List[dict], page_width: float) -> List[dict]:
    text_blocks = [block for block in blocks if block["type"] == "text"]
    body_left, body_right = _estimate_body_edges(text_blocks, page_width)
    merged: List[dict] = []
    current: Optional[dict] = None

    def flush_current() -> None:
        nonlocal current
        if current is not None:
            current.pop("last_bbox", None)
            current.pop("line_count", None)
            merged.append(current)
            current = None

    for block in blocks:
        if block["type"] != "text":
            flush_current()
            merged.append(block)
            continue

        block = dict(block)
        block["text"] = _flatten_extracted_text(block["text"])
        block["first_bbox"] = list(block.get("first_bbox") or block["bbox"])
        block["last_bbox"] = list(block.get("last_bbox") or block["bbox"])
        block["line_count"] = int(block.get("line_count") or 1)

        if current is not None and _should_merge_docx_text_block(current, block, page_width, body_left, body_right):
            current["text"] = _merge_soft_wrapped_line(current["text"], block["text"])
            current["bbox"] = _union_bbox(current["bbox"], block["bbox"])
            current["last_bbox"] = list(block.get("last_bbox") or block["bbox"])
            current["font_size"] = _merge_weighted_value(
                float(current.get("font_size") or DOCX_DEFAULT_FONT_SIZE),
                int(current.get("line_count") or 1),
                float(block.get("font_size") or DOCX_DEFAULT_FONT_SIZE),
                int(block.get("line_count") or 1),
            )
            current["line_count"] = int(current.get("line_count") or 1) + int(block.get("line_count") or 1)
            current["bold"] = bool(current.get("bold")) and bool(block.get("bold"))
            current["italic"] = bool(current.get("italic")) and bool(block.get("italic"))
            if current.get("color") != block.get("color"):
                current["color"] = None
            continue

        flush_current()
        current = block

    flush_current()
    return merged


def _estimate_body_edges(text_blocks: List[dict], page_width: float) -> tuple[float, float]:
    wide_blocks = [
        block
        for block in text_blocks
        if block["bbox"][2] - block["bbox"][0] > page_width * 0.45
    ]
    if not wide_blocks:
        return DOCX_MARGIN_PT, page_width - DOCX_MARGIN_PT

    lefts = sorted(block["bbox"][0] for block in wide_blocks)
    rights = sorted(block["bbox"][2] for block in wide_blocks)
    return lefts[len(lefts) // 2], rights[len(rights) // 2]


def _flatten_extracted_text(text: str) -> str:
    if _contains_toc_leader(text):
        lines = [_normalize_inline_spaces(line.strip()) for line in text.splitlines()]
        return "\n".join(line for line in lines if line)

    paragraphs = _normalize_plain_text_block(text)
    if not paragraphs:
        return ""
    return " ".join(paragraphs)


def _contains_toc_leader(text: str) -> bool:
    return any(_is_toc_leader_line(line) for line in text.splitlines())


def _should_merge_docx_text_block(
    previous: dict,
    current: dict,
    page_width: float,
    body_left: float,
    body_right: float,
) -> bool:
    previous_text = str(previous.get("text") or "").strip()
    current_text = str(current.get("text") or "").strip()
    if not previous_text or not current_text:
        return False

    if _is_likely_text_heading(previous, page_width, body_left) or _is_likely_text_heading(current, page_width, body_left):
        return False
    if "\n" in previous_text or "\n" in current_text:
        return False
    if _contains_toc_leader(previous_text) or _contains_toc_leader(current_text):
        return False

    prev_bbox = previous.get("last_bbox") or previous["bbox"]
    curr_bbox = current.get("first_bbox") or current["bbox"]
    y_gap = curr_bbox[1] - prev_bbox[3]
    max_font = max(
        float(previous.get("font_size") or DOCX_DEFAULT_FONT_SIZE),
        float(current.get("font_size") or DOCX_DEFAULT_FONT_SIZE),
    )
    if y_gap < -2 or y_gap > max(10.0, max_font * 1.25):
        return False

    current_is_first_line = curr_bbox[0] > body_left + max(12.0, max_font * 1.2)
    previous_is_short = prev_bbox[2] < body_right - 36.0
    if current_is_first_line and (
        previous_is_short
        or previous_text[-1] in TERMINAL_PUNCTUATION
        or _starts_new_cjk_paragraph(current_text)
    ):
        return False

    return True


def _is_likely_text_heading(block: dict, page_width: float, body_left: float) -> bool:
    text = _normalize_inline_spaces(str(block.get("text") or ""))
    if not text or len(text) > 80:
        return False

    bbox = block["bbox"]
    width = bbox[2] - bbox[0]
    center = (bbox[0] + bbox[2]) / 2
    centered = width < page_width * 0.55 and abs(center - page_width / 2) < page_width * 0.12
    font_size = float(block.get("font_size") or DOCX_DEFAULT_FONT_SIZE)
    if centered and len(text) <= 50:
        return True
    if font_size >= DOCX_DEFAULT_FONT_SIZE + 3 and len(text) <= 60:
        return True
    if re.match(r"^(第[一二三四五六七八九十\d]+章|[一二三四五六七八九十\d]+(?:[.．]\d+)+\s+\S|摘\s*要|目\s*录|致\s*谢|参考文献)", text):
        return True
    return False


def _starts_new_cjk_paragraph(text: str) -> bool:
    return bool(re.match(r"^([（(]\s*[一二三四五六七八九十\d]+\s*[）)]|第[一二三四五六七八九十\d]+章|[一二三四五六七八九十\d]+(?:[.．]\d+)+\s+\S)", text))


def _union_bbox(left: List[float], right: List[float]) -> List[float]:
    return [
        min(left[0], right[0]),
        min(left[1], right[1]),
        max(left[2], right[2]),
        max(left[3], right[3]),
    ]


def _merge_weighted_value(left: float, left_count: int, right: float, right_count: int) -> float:
    total = max(1, left_count + right_count)
    return (left * left_count + right * right_count) / total


def _weighted_average(values: List[float], fallback: float) -> float:
    if not values:
        return fallback
    return sum(values) / len(values)


def _docx_bbox_tuple(bbox: List[float], text: str) -> tuple:
    return (bbox[0], bbox[1], bbox[2], bbox[3], text, 0, TEXT_BLOCK_TYPE)


def _docx_text_paragraph(
    text: str,
    bbox: List[float],
    first_bbox: Optional[List[float]],
    page_width: float,
    font_size: float,
    bold: bool,
    italic: bool,
    color: Any,
    spacing_before: int,
) -> str:
    paragraph = _normalize_inline_spaces(text)
    if not paragraph:
        return ""

    p_pr = _docx_paragraph_properties(bbox, page_width, spacing_before, first_bbox)
    r_pr = _docx_run_properties(font_size, bold, italic, color)
    return f"<w:p>{p_pr}<w:r>{r_pr}<w:t xml:space=\"preserve\">{escape(paragraph)}</w:t></w:r></w:p>"


def _docx_layout_text_paragraph(block: dict, page_width: float, spacing_before: int) -> str:
    bbox = block["bbox"]
    segments = list(block.get("segments") or [])
    if not segments:
        text = _normalize_inline_spaces(str(block.get("text") or ""))
        if not text:
            return ""
        return _docx_text_paragraph(
            text,
            bbox,
            None,
            page_width,
            float(block.get("font_size") or DOCX_DEFAULT_FONT_SIZE),
            False,
            False,
            None,
            spacing_before,
        )

    tab_stops = [
        max(0.0, float(segment["bbox"][0]) - DOCX_MARGIN_PT)
        for segment in segments[1:]
        if segment["bbox"][0] - segments[0]["bbox"][0] > 12.0
    ]
    line_height = max(7.0, float(block.get("line_height") or _docx_fallback_layout_line_height(block)))
    p_pr = _docx_paragraph_properties(
        bbox,
        page_width,
        spacing_before,
        None,
        spacing_after=0,
        line_height=line_height,
        tab_stops=tab_stops,
    )

    run_parts: List[str] = []
    for segment_index, segment in enumerate(segments):
        if segment_index > 0 and segment["bbox"][0] - segments[0]["bbox"][0] > 12.0:
            run_parts.append("<w:r><w:tab/></w:r>")
        for run in segment.get("runs") or []:
            run_parts.append(_docx_layout_run(run))

    return f"<w:p>{p_pr}{''.join(run_parts)}</w:p>"


def _docx_layout_run(run: dict) -> str:
    text = str(run.get("text") or "")
    if not text:
        return ""
    r_pr = _docx_run_properties(
        _docx_layout_font_size(run),
        bool(run.get("bold")),
        bool(run.get("italic")),
        run.get("color"),
        _docx_font_family(str(run.get("font") or "")),
        str(run.get("vertical_align") or ""),
    )
    return f"<w:r>{r_pr}{_docx_text_nodes(text)}</w:r>"


def _docx_fallback_layout_line_height(block: dict) -> float:
    bbox = block["bbox"]
    font_size = float(block.get("font_size") or DOCX_DEFAULT_FONT_SIZE) * DOCX_LAYOUT_FONT_SCALE
    return max(float(bbox[3]) - float(bbox[1]), font_size * 1.18)


def _docx_layout_font_size(run: dict) -> float:
    font_size = float(run.get("font_size") or DOCX_DEFAULT_FONT_SIZE)
    return max(4.5, min(72.0, font_size * DOCX_LAYOUT_FONT_SCALE))


def _docx_text_nodes(text: str) -> str:
    parts: List[str] = []
    tab_split = text.split("\t")
    for idx, piece in enumerate(tab_split):
        if idx:
            parts.append("<w:tab/>")
        if piece:
            parts.append(f'<w:t xml:space="preserve">{escape(piece)}</w:t>')
    return "".join(parts)


def _docx_font_family(font: str) -> str:
    normalized = font.lower().replace("-", "").replace(" ", "")
    if "cambriamath" in normalized:
        return "Cambria Math"
    if "symbol" in normalized:
        return "Symbol"
    if "timesnewroman" in normalized:
        return "Times New Roman"
    if "simsun" in normalized:
        return "SimSun"
    if "dengxian" in normalized:
        return "DengXian"
    if "simhei" in normalized:
        return "SimHei"
    return ""


def _docx_image_paragraph(
    rel_id: str,
    doc_pr_id: int,
    bbox: List[float],
    page_width: float,
    image_width: int,
    image_height: int,
    spacing_before: int,
    description: str = "",
) -> str:
    box_width = max(1.0, bbox[2] - bbox[0])
    box_height = max(1.0, bbox[3] - bbox[1])
    ratio = image_width / image_height if image_width and image_height else box_width / box_height
    width_pt = box_width
    height_pt = width_pt / ratio if ratio else box_height
    if height_pt > box_height * 1.2:
        height_pt = box_height
        width_pt = height_pt * ratio

    cx = _pt_to_emu(width_pt)
    cy = _pt_to_emu(height_pt)
    p_pr = _docx_paragraph_properties(bbox, page_width, spacing_before, spacing_after=0)
    name = f"Picture {doc_pr_id}"
    escaped_description = escape(description, {'"': "&quot;"}) if description else ""
    descr_attr = f' descr="{escaped_description}"' if escaped_description else ""
    return (
        f"<w:p>{p_pr}<w:r><w:drawing><wp:inline distT=\"0\" distB=\"0\" distL=\"0\" distR=\"0\">"
        f"<wp:extent cx=\"{cx}\" cy=\"{cy}\"/>"
        f"<wp:docPr id=\"{doc_pr_id}\" name=\"{name}\"{descr_attr}/>"
        f"<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect=\"1\"/></wp:cNvGraphicFramePr>"
        f"<a:graphic><a:graphicData uri=\"http://schemas.openxmlformats.org/drawingml/2006/picture\">"
        f"<pic:pic><pic:nvPicPr><pic:cNvPr id=\"{doc_pr_id}\" name=\"{name}\"/>"
        f"<pic:cNvPicPr/></pic:nvPicPr><pic:blipFill>"
        f"<a:blip r:embed=\"{rel_id}\"/><a:stretch><a:fillRect/></a:stretch>"
        f"</pic:blipFill><pic:spPr><a:xfrm><a:off x=\"0\" y=\"0\"/>"
        f"<a:ext cx=\"{cx}\" cy=\"{cy}\"/></a:xfrm>"
        f"<a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>"
        f"</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
    )


def _docx_table_xml(block: dict, page_width: float, spacing_before: int) -> str:
    bbox = block["bbox"]
    table_width_twips = _pt_to_twips(max(1.0, float(bbox[2]) - float(bbox[0])))
    table_indent = _pt_to_twips(max(0.0, float(bbox[0]) - DOCX_MARGIN_PT))
    column_widths = [max(120, _pt_to_twips(float(width))) for width in block.get("column_widths", [])]
    if not column_widths:
        column_widths = [max(120, table_width_twips)]

    border_mode = _docx_table_border_mode(block)
    tbl_pr = (
        "<w:tblPr>"
        f'<w:tblW w:w="{table_width_twips}" w:type="dxa"/>'
        f'<w:tblInd w:w="{table_indent}" w:type="dxa"/>'
        '<w:tblLayout w:type="fixed"/>'
        '<w:tblCellMar>'
        '<w:top w:w="40" w:type="dxa"/><w:left w:w="60" w:type="dxa"/>'
        '<w:bottom w:w="40" w:type="dxa"/><w:right w:w="60" w:type="dxa"/>'
        '</w:tblCellMar>'
        f"{_docx_table_borders_xml(border_mode)}"
        "</w:tblPr>"
    )
    tbl_grid = "<w:tblGrid>" + "".join(f'<w:gridCol w:w="{width}"/>' for width in column_widths) + "</w:tblGrid>"

    occupancy = block.get("occupancy") or []
    cells_by_id = {int(cell["id"]): cell for cell in block.get("cells", [])}
    row_ranges = block.get("row_ranges") or []
    row_xml: List[str] = []
    for row_index, row in enumerate(occupancy):
        row_height = 0
        if row_index < len(row_ranges):
            row_height = _pt_to_twips(max(6.0, float(row_ranges[row_index][1]) - float(row_ranges[row_index][0])))
        tr_pr = f'<w:trPr><w:trHeight w:val="{row_height}" w:hRule="atLeast"/></w:trPr>' if row_height else ""
        cell_parts: List[str] = []
        col_index = 0
        while col_index < len(row):
            cell_id = row[col_index]
            if cell_id is None:
                cell_parts.append(_docx_table_cell_xml("", column_widths[col_index], border_mode, row_index, len(occupancy)))
                col_index += 1
                continue

            cell = cells_by_id.get(int(cell_id))
            if not cell:
                col_index += 1
                continue
            cell_row = int(cell.get("row", row_index))
            cell_col = int(cell.get("col", col_index))
            col_span = max(1, int(cell.get("col_span") or 1))
            if cell_row < row_index and cell_col == col_index:
                width = sum(column_widths[col_index: col_index + col_span])
                cell_parts.append(
                    _docx_table_cell_xml(
                        "",
                        width,
                        border_mode,
                        row_index,
                        len(occupancy),
                        col_span=col_span,
                        v_merge="continue",
                    )
                )
                col_index += col_span
                continue
            if cell_row != row_index or cell_col != col_index:
                col_index += 1
                continue

            row_span = max(1, int(cell.get("row_span") or 1))
            width = sum(column_widths[col_index: col_index + col_span])
            v_merge = "restart" if row_span > 1 else ""
            cell_parts.append(
                _docx_table_cell_xml(
                    str(cell.get("text") or ""),
                    width,
                    border_mode,
                    row_index,
                    len(occupancy),
                    col_span=col_span,
                    v_merge=v_merge,
                )
            )
            col_index += col_span
        row_xml.append(f"<w:tr>{tr_pr}{''.join(cell_parts)}</w:tr>")

    return (
        f"<w:p><w:pPr>{_docx_spacing_properties(spacing_before, 0, None)}</w:pPr></w:p>"
        f"<w:tbl>{tbl_pr}{tbl_grid}{''.join(row_xml)}</w:tbl>"
    )


def _docx_table_border_mode(block: dict) -> str:
    cols = int(block.get("cols") or 0)
    rows = int(block.get("rows") or 0)
    if cols >= 4 and rows <= 18:
        return "grid"
    return "horizontal"


def _docx_table_borders_xml(mode: str) -> str:
    if mode == "grid":
        edge = '<w:{name} w:val="single" w:sz="4" w:space="0" w:color="666666"/>'
        return (
            "<w:tblBorders>"
            + "".join(edge.format(name=name) for name in ("top", "left", "bottom", "right", "insideH", "insideV"))
            + "</w:tblBorders>"
        )
    nil = '<w:{name} w:val="nil"/>'
    return (
        "<w:tblBorders>"
        + "".join(nil.format(name=name) for name in ("top", "left", "bottom", "right", "insideH", "insideV"))
        + "</w:tblBorders>"
    )


def _docx_table_cell_xml(
    text: str,
    width_twips: int,
    border_mode: str,
    row_index: int,
    total_rows: int,
    col_span: int = 1,
    v_merge: str = "",
) -> str:
    tc_pr = [f'<w:tcW w:w="{max(120, width_twips)}" w:type="dxa"/>']
    if col_span > 1:
        tc_pr.append(f'<w:gridSpan w:val="{col_span}"/>')
    if v_merge == "restart":
        tc_pr.append('<w:vMerge w:val="restart"/>')
    elif v_merge == "continue":
        tc_pr.append("<w:vMerge/>")
    cell_borders = _docx_table_cell_borders_xml(border_mode, row_index, total_rows)
    if cell_borders:
        tc_pr.append(cell_borders)
    paragraphs = _docx_table_cell_paragraphs(text)
    return f"<w:tc><w:tcPr>{''.join(tc_pr)}</w:tcPr>{paragraphs}</w:tc>"


def _docx_table_cell_borders_xml(border_mode: str, row_index: int, total_rows: int) -> str:
    if border_mode != "horizontal":
        return ""

    def border(name: str, enabled: bool) -> str:
        if enabled:
            return f'<w:{name} w:val="single" w:sz="4" w:space="0" w:color="666666"/>'
        return f'<w:{name} w:val="nil"/>'

    return (
        "<w:tcBorders>"
        f"{border('top', row_index == 0)}"
        f"{border('bottom', row_index == 0 or row_index == total_rows - 1)}"
        f"{border('left', False)}"
        f"{border('right', False)}"
        "</w:tcBorders>"
    )


def _docx_table_cell_paragraphs(text: str) -> str:
    lines = _docx_table_display_lines(text)
    if not lines:
        lines = [""]
    paragraphs = []
    for line in lines:
        p_pr = (
            "<w:pPr>"
            '<w:spacing w:before="0" w:after="0"/>'
            f'<w:jc w:val="{_docx_table_cell_alignment(line)}"/>'
            "</w:pPr>"
        )
        r_pr = _docx_run_properties(DOCX_DEFAULT_FONT_SIZE, False, False, None, "Times New Roman")
        paragraphs.append(f"<w:p>{p_pr}<w:r>{r_pr}{_docx_text_nodes(line)}</w:r></w:p>")
    return "".join(paragraphs)


def _docx_table_display_lines(text: str) -> List[str]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return []
    lines = [_normalize_inline_spaces(line) for line in cleaned.split("\n") if _normalize_inline_spaces(line)]
    if len(lines) <= 1:
        return lines
    if len(lines) >= 3 and all(len(line) <= 2 for line in lines):
        return lines
    if len(lines) == 2 and lines[1].startswith(("(", "（")):
        return lines
    return [_normalize_inline_spaces("".join(lines))]


def _docx_table_cell_alignment(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "center"
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?", stripped):
        return "center"
    if len(stripped) > 18 and not re.search(r"[A-Za-z0-9∗*/+=]", stripped):
        return "left"
    return "center"


def _docx_paragraph_properties(
    bbox: List[float],
    page_width: float,
    spacing_before: int,
    first_bbox: Optional[List[float]] = None,
    spacing_after: int = 120,
    line_height: Optional[float] = None,
    tab_stops: Optional[List[float]] = None,
) -> str:
    alignment = _docx_alignment(bbox, page_width)
    left_indent = max(0.0, bbox[0] - DOCX_MARGIN_PT)
    right_indent = max(0.0, page_width - bbox[2] - DOCX_MARGIN_PT)
    first_line = 0.0
    if first_bbox:
        first_line = max(0.0, float(first_bbox[0]) - float(bbox[0]))

    indent_attrs = [
        f'w:left="{_pt_to_twips(left_indent)}"',
        f'w:right="{_pt_to_twips(right_indent)}"',
    ]
    if first_line >= 6.0:
        indent_attrs.append(f'w:firstLine="{_pt_to_twips(first_line)}"')

    parts = [
        "<w:pPr>",
        _docx_spacing_properties(spacing_before, spacing_after, line_height),
        f'<w:jc w:val="{alignment}"/>',
        f"<w:ind {' '.join(indent_attrs)}/>",
    ]
    if tab_stops:
        tab_xml = "".join(f'<w:tab w:val="left" w:pos="{_pt_to_twips(pos)}"/>' for pos in tab_stops)
        parts.append(f"<w:tabs>{tab_xml}</w:tabs>")
    parts.append("</w:pPr>")
    return "".join(parts)


def _docx_spacing_properties(spacing_before: int, spacing_after: int, line_height: Optional[float]) -> str:
    if line_height is None:
        return f'<w:spacing w:before="{spacing_before}" w:after="{spacing_after}"/>'
    return (
        f'<w:spacing w:before="{spacing_before}" w:after="{spacing_after}" '
        f'w:line="{_pt_to_twips(line_height)}" w:lineRule="exact"/>'
    )


def _docx_alignment(bbox: List[float], page_width: float) -> str:
    x0, _, x1, _ = bbox
    width = max(0.0, x1 - x0)
    center = (x0 + x1) / 2
    left_gap = x0
    right_gap = page_width - x1
    if width < page_width * 0.75 and abs(center - page_width / 2) < page_width * 0.08:
        return "center"
    if right_gap < page_width * 0.08 and left_gap > page_width * 0.2:
        return "right"
    return "left"


def _docx_spacing_before(previous_bottom: Optional[float], current_top: float) -> int:
    if previous_bottom is None:
        return 0
    gap = current_top - previous_bottom
    if gap <= 4:
        return 0
    return int(min(360, max(0, (gap - 4) * 10)))


def _docx_layout_spacing_before(previous_bottom: Optional[float], current_top: float) -> int:
    if previous_bottom is None:
        return int(max(0.0, current_top - DOCX_MARGIN_PT) * 20)
    gap = current_top - previous_bottom
    if gap <= 1:
        return 0
    return int(min(420, max(0, gap * 14)))


def _docx_run_properties(
    font_size: float,
    bold: bool,
    italic: bool,
    color: Any,
    font_family: str = "",
    vertical_align: str = "",
) -> str:
    half_points = max(12, min(96, int(round(font_size * 2))))
    font_attrs = _docx_font_attrs(font_family)
    parts = [
        "<w:rPr>",
        font_attrs,
        f'<w:sz w:val="{half_points}"/>',
        f'<w:szCs w:val="{half_points}"/>',
    ]
    if bold:
        parts.append("<w:b/>")
        parts.append("<w:bCs/>")
    if italic:
        parts.append("<w:i/>")
        parts.append("<w:iCs/>")
    color_value = _docx_color(color)
    if color_value:
        parts.append(f'<w:color w:val="{color_value}"/>')
    if vertical_align in {"superscript", "subscript"}:
        parts.append(f'<w:vertAlign w:val="{vertical_align}"/>')
    parts.append("</w:rPr>")
    return "".join(parts)


def _docx_font_attrs(font_family: str) -> str:
    if not font_family:
        return '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/>'
    font = escape(font_family, {'"': "&quot;"})
    return f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" w:eastAsia="{font}" w:cs="{font}"/>'


def _docx_color(color: Any) -> str:
    if not isinstance(color, int):
        return ""
    value = max(0, min(0xFFFFFF, color))
    if value == 0:
        return ""
    return f"{value:06X}"


def _prepare_docx_image(image_bytes: bytes, ext: str) -> tuple[str, bytes, int, int]:
    clean_ext = ext.lower().lstrip(".")
    if clean_ext == "jpg":
        clean_ext = "jpeg"
    supported = {"png", "jpeg", "gif", "bmp"}
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            width, height = img.size
            if _image_needs_white_background(img):
                out = BytesIO()
                _flatten_image_on_white(img).save(out, format="PNG")
                return "png", out.getvalue(), width, height
            if clean_ext in supported:
                return clean_ext, image_bytes, width, height
            out = BytesIO()
            img.convert("RGB").save(out, format="PNG")
            return "png", out.getvalue(), width, height
    except Exception:
        if clean_ext in supported:
            return clean_ext, image_bytes, 1, 1
    return "", b"", 0, 0


def _image_needs_white_background(img: Image.Image) -> bool:
    if img.mode in {"RGBA", "LA"}:
        return True
    if img.mode == "P" and "transparency" in img.info:
        return True
    return "transparency" in img.info


def _flatten_image_on_white(img: Image.Image) -> Image.Image:
    rgba = img.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    background.alpha_composite(rgba)
    return background.convert("RGB")


def _docx_page_break() -> str:
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def _docx_section_properties(page_width: float, page_height: float) -> str:
    return (
        "<w:sectPr>"
        f'<w:pgSz w:w="{_pt_to_twips(page_width)}" w:h="{_pt_to_twips(page_height)}"/>'
        f'<w:pgMar w:top="{_pt_to_twips(DOCX_MARGIN_PT)}" '
        f'w:right="{_pt_to_twips(DOCX_MARGIN_PT)}" '
        f'w:bottom="{_pt_to_twips(DOCX_MARGIN_PT)}" '
        f'w:left="{_pt_to_twips(DOCX_MARGIN_PT)}" '
        'w:header="0" w:footer="0" w:gutter="0"/>'
        "</w:sectPr>"
    )


def _docx_document_xml(body_xml: str, sect_pr: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:o="urn:schemas-microsoft-com:office:office" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
        'xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:w10="urn:schemas-microsoft-com:office:word" '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
        'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" '
        'xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk" '
        'xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
        'mc:Ignorable="w14">'
        f"<w:body>{body_xml}{sect_pr}</w:body></w:document>"
    )


def _docx_content_types(media_files: List[tuple[str, bytes]]) -> str:
    defaults = {
        "rels": "application/vnd.openxmlformats-package.relationships+xml",
        "xml": "application/xml",
    }
    for media_name, _ in media_files:
        ext = os.path.splitext(media_name)[1].lstrip(".").lower()
        if ext == "jpeg":
            defaults["jpeg"] = "image/jpeg"
        elif ext == "png":
            defaults["png"] = "image/png"
        elif ext == "gif":
            defaults["gif"] = "image/gif"
        elif ext == "bmp":
            defaults["bmp"] = "image/bmp"

    default_xml = "".join(
        f'<Default Extension="{ext}" ContentType="{content_type}"/>'
        for ext, content_type in sorted(defaults.items())
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f"{default_xml}"
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/settings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
        "</Types>"
    )


def _docx_root_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )


def _docx_document_rels(image_relationships: List[str]) -> str:
    rels = [
        '<Relationship Id="rStyle" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>',
        '<Relationship Id="rSettings" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" '
        'Target="settings.xml"/>',
    ]
    rels.extend(image_relationships)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{''.join(rels)}</Relationships>"
    )


def _docx_styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        '<w:name w:val="Normal"/><w:qFormat/>'
        '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/>'
        '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>'
        '</w:style></w:styles>'
    )


def _docx_settings_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:compat/><w:themeFontLang w:val="en-US" w:eastAsia="zh-CN"/>'
        '</w:settings>'
    )


def _pt_to_twips(points: float) -> int:
    return int(round(points * 20))


def _pt_to_emu(points: float) -> int:
    return int(round(points * 12700))


def _merge_soft_wrapped_line(previous: str, current: str) -> str:
    left = previous.rstrip()
    right = current.lstrip()
    if not left:
        return right
    if not right:
        return left

    # Handle line-break hyphenation, e.g. "conver-\nsion" -> "conversion".
    if left.endswith("-") and right[0].isalnum():
        return left[:-1] + right

    if _should_join_without_space(left[-1], right[0]):
        return left + right

    return left + " " + right


def _should_join_without_space(left_char: str, right_char: str) -> bool:
    if right_char in NO_SPACE_BEFORE_CHARS:
        return True
    if left_char in NO_SPACE_AFTER_CHARS:
        return True
    if _is_cjk(left_char) or _is_cjk(right_char):
        return True
    return False


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0xF900 <= code <= 0xFAFF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


def _export_text_format(req: ConvertRequest, text_mode: str) -> dict:
    try:
        doc = fitz.open(req.path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot open PDF: {e}")

    pages = req.pages or list(range(len(doc)))
    chunks = []
    _set_convert_progress(req.task_id, stage="正在导出内容", progress=6, current=0, total=len(pages))
    for page_pos, i in enumerate(pages):
        if i < 0 or i >= len(doc):
            continue
        text = doc[i].get_text(text_mode).strip()
        if text_mode == "html":
            chunks.append(f"<!-- Page {i + 1} -->\n{text}")
        else:
            chunks.append(f"# Page {i + 1}\n\n{text}")
        _set_convert_page_progress(
            req.task_id,
            page_pos + 1,
            len(pages),
            base=8,
            span=84,
            stage=f"正在导出内容 {page_pos + 1}/{len(pages)}",
        )

    doc.close()

    out_path = os.path.normpath(req.out_path)
    _set_convert_progress(req.task_id, stage="正在写入文件", progress=96)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(chunks))

    return {"status": "ok", "out_path": out_path, "page_count": len(chunks)}


def _to_jpg_pages(req: ConvertRequest) -> dict:
    doc = fitz.open(req.path)
    pages = req.pages or list(range(len(doc)))
    out_dir = req.out_path
    os.makedirs(out_dir, exist_ok=True)

    mat = fitz.Matrix(req.dpi / 72, req.dpi / 72)
    paths = []
    _set_convert_progress(req.task_id, stage="正在导出图片", progress=6, current=0, total=len(pages))
    for page_pos, i in enumerate(pages):
        pix = doc[i].get_pixmap(matrix=mat)
        fp = os.path.join(out_dir, f"page_{i+1:04d}.jpg")
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        img.save(fp, "JPEG", quality=req.quality)
        paths.append(fp)
        _set_convert_page_progress(
            req.task_id,
            page_pos + 1,
            len(pages),
            base=8,
            span=88,
            stage=f"正在导出图片 {page_pos + 1}/{len(pages)}",
        )

    doc.close()
    return {"status": "ok", "paths": paths, "count": len(paths)}


def _to_jpg_long(req: ConvertRequest) -> dict:
    try:
        doc = fitz.open(req.path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot open PDF: {e}")

    try:
        if len(doc) > MAX_LONG_IMAGE_PAGES:
            raise HTTPException(
                status_code=400,
                detail=f"长图导出仅支持页数小于等于 {MAX_LONG_IMAGE_PAGES} 页的 PDF",
            )

        pages = req.pages or list(range(len(doc)))
        mat = fitz.Matrix(req.dpi / 72, req.dpi / 72)

        imgs = []
        _set_convert_progress(req.task_id, stage="正在渲染长图", progress=6, current=0, total=len(pages))
        for page_pos, i in enumerate(pages):
            pix = doc[i].get_pixmap(matrix=mat)
            imgs.append(Image.frombytes("RGB", [pix.width, pix.height], pix.samples))
            _set_convert_page_progress(
                req.task_id,
                page_pos + 1,
                len(pages),
                base=8,
                span=72,
                stage=f"正在渲染长图 {page_pos + 1}/{len(pages)}",
            )
    finally:
        doc.close()

    total_h = sum(img.height for img in imgs)
    _set_convert_progress(req.task_id, stage="正在拼接长图", progress=88)
    canvas = Image.new("RGB", (imgs[0].width, total_h), "white")
    y = 0
    for img in imgs:
        canvas.paste(img, (0, y))
        y += img.height

    _set_convert_progress(req.task_id, stage="正在写入长图文件", progress=96)
    canvas.save(req.out_path, "JPEG", quality=req.quality)
    return {"status": "ok", "out_path": req.out_path}
