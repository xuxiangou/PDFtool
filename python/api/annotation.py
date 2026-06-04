import fitz
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal, Optional, List
import traceback
import tempfile
import shutil

router = APIRouter()


class AnnotateRequest(BaseModel):
    path: str
    out_path: str
    page: int
    action: Literal["highlight", "underline", "freetext", "delete", "strikeout"]
    rect: Optional[List[float]] = None
    text: Optional[str] = None
    color: Optional[List[float]] = [1, 1, 0]
    annot_id: Optional[str] = None


class ListRequest(BaseModel):
    path: str


@router.post("/")
def annotate(req: AnnotateRequest):
    try:
        doc = fitz.open(req.path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot open PDF: {e}")

    try:
        if req.page < 0 or req.page >= doc.page_count:
            raise HTTPException(status_code=400, detail=f"Page {req.page} out of range (0-{doc.page_count-1})")
        
        page = doc[req.page]

        if req.action in ["highlight", "underline", "strikeout"]:
            if not req.rect or len(req.rect) != 4:
                raise HTTPException(status_code=400, detail="rect must be [x0, y0, x1, y1]")
            
            rect = fitz.Rect(req.rect)
            if rect.is_empty or rect.is_infinite:
                raise HTTPException(status_code=400, detail=f"Invalid rect: {req.rect}")
            
            if req.action == "highlight":
                annot = page.add_highlight_annot(rect)
            elif req.action == "underline":
                annot = page.add_underline_annot(rect)
            elif req.action == "strikeout":
                annot = page.add_strikeout_annot(rect)
            
            annot.set_colors(stroke=req.color)
            annot.update()

        elif req.action == "freetext":
            if not req.rect or len(req.rect) != 4:
                raise HTTPException(status_code=400, detail="rect must be [x0, y0, x1, y1]")
            
            rect = fitz.Rect(req.rect)
            annot = page.add_freetext_annot(
                rect,
                req.text or "",
                fontsize=11,
                text_color=[0, 0, 0],
                fill_color=[1, 1, 0.8],
            )
            annot.update()

        elif req.action == "delete":
            if not req.annot_id:
                raise HTTPException(status_code=400, detail="annot_id required for delete")
            for annot in page.annots():
                if str(annot.xref) == req.annot_id:
                    page.delete_annot(annot)
                    break

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        
        try:
            doc.save(tmp_path, garbage=4, deflate=True)
            doc.close()
            shutil.copy(tmp_path, req.out_path)
        finally:
            import os
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        
        return {"status": "ok", "out_path": req.out_path}
        
    except HTTPException:
        raise
    except Exception as e:
        doc.close()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/list")
def list_annots(req: ListRequest):
    try:
        doc = fitz.open(req.path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = []
    for i, page in enumerate(doc):
        for annot in page.annots():
            result.append({
                "page": i,
                "id": str(annot.xref),
                "type": annot.type[1],
                "rect": list(annot.rect),
                "content": annot.info.get("content", ""),
                "color": annot.colors.get("stroke", []),
            })
    doc.close()
    return result
