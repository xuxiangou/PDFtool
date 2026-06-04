import os
import fitz  # PyMuPDF


def get_page_count(path: str) -> int:
    doc = fitz.open(path)
    n = len(doc)
    doc.close()
    return n


def render_page_to_bytes(path: str, page: int, dpi: int = 150, rotate: int = 0) -> bytes:
    doc = fitz.open(path)
    mat = fitz.Matrix(dpi / 72, dpi / 72).prerotate(rotate)
    pix = doc[page].get_pixmap(matrix=mat, alpha=False)
    doc.close()
    return pix.tobytes("png")


def extract_text(path: str, page: int) -> str:
    doc = fitz.open(path)
    text = doc[page].get_text("text")
    doc.close()
    return text


def file_size_mb(path: str) -> float:
    return round(os.path.getsize(path) / 1024 / 1024, 2)
