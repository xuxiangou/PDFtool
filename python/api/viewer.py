import base64
import os
from typing import List

import fitz  # PyMuPDF
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class RenderRequest(BaseModel):
    path: str
    page: int = 0
    dpi: int = 150
    rotate: int = 0  # 0 / 90 / 180 / 270


class InfoRequest(BaseModel):
    path: str


class TextRequest(BaseModel):
    path: str
    page: int = 0


class TextWord(BaseModel):
    rect: List[float]
    text: str
    block: int
    line: int
    word: int


class TextResponse(BaseModel):
    page: int
    count: int
    words: List[TextWord]


@router.post("/page")
def render_page(req: RenderRequest):
    path = os.path.normpath(req.path)   # 统一路径格式
    try:
        doc = fitz.open(path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot open PDF: {e}")

    if req.page < 0 or req.page >= len(doc):
        raise HTTPException(status_code=400, detail="Page index out of range")

    page = doc[req.page]
    mat = fitz.Matrix(req.dpi / 72, req.dpi / 72).prerotate(req.rotate)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img_b64 = base64.b64encode(pix.tobytes("png")).decode()
    doc.close()

    return {
        "image": f"data:image/png;base64,{img_b64}",
        "width": pix.width,
        "height": pix.height,
    }


@router.post("/info")
def get_info(req: InfoRequest):
    try:
        doc = fitz.open(req.path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot open PDF: {e}")

    toc = doc.get_toc()
    info = {
        "page_count": len(doc),
        "title": doc.metadata.get("title", ""),
        "author": doc.metadata.get("author", ""),
        "subject": doc.metadata.get("subject", ""),
        "toc": toc,
    }
    doc.close()
    return info


@router.post("/text", response_model=TextResponse)
def get_page_text(req: TextRequest):
    path = os.path.normpath(req.path)
    try:
        doc = fitz.open(path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot open PDF: {e}")

    if req.page < 0 or req.page >= len(doc):
        doc.close()
        raise HTTPException(status_code=400, detail="Page index out of range")

    page = doc[req.page]
    raw_words = page.get_text("words", sort=True)
    words: List[TextWord] = []
    dedupe_keys = set()
    for item in raw_words:
        if len(item) < 5:
            continue
        x0, y0, x1, y1, value = item[:5]
        text_value = str(value or "").strip()
        if not text_value:
            continue
        block_no = int(item[5]) if len(item) > 5 else -1
        line_no = int(item[6]) if len(item) > 6 else -1
        word_no = int(item[7]) if len(item) > 7 else -1

        # De-duplicate near-identical words to keep selection order stable.
        key = (
            text_value,
            round(float(x0), 1),
            round(float(y0), 1),
            round(float(x1), 1),
            round(float(y1), 1),
        )
        if key in dedupe_keys:
            continue
        dedupe_keys.add(key)

        words.append(
            TextWord(
                rect=[float(x0), float(y0), float(x1), float(y1)],
                text=text_value,
                block=block_no,
                line=line_no,
                word=word_no,
            )
        )

    doc.close()
    return TextResponse(page=req.page, count=len(words), words=words)
