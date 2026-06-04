import fitz
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import traceback

router = APIRouter()


class SearchRequest(BaseModel):
    path: str
    query: str
    case_sensitive: bool = False
    page: Optional[int] = None


class SearchResult(BaseModel):
    page: int
    rect: List[float]
    snippet: str


class SearchResponse(BaseModel):
    count: int
    results: List[SearchResult]


@router.post("/", response_model=SearchResponse)
def search(req: SearchRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query is empty")

    try:
        doc = fitz.open(req.path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot open PDF: {e}")

    try:
        results = []
        page_range = [req.page] if req.page is not None else range(len(doc))

        for i in page_range:
            if i < 0 or i >= len(doc):
                continue
            page = doc[i]
            quads = page.search_for(req.query, quads=True)
            for q in quads:
                rect = fitz.Rect(q.ul, q.lr)
                snippet = _get_snippet(page, rect, req.query)
                results.append({
                    "page": i,
                    "rect": [rect.x0, rect.y0, rect.x1, rect.y1],
                    "snippet": snippet,
                })

        doc.close()
        return {"count": len(results), "results": results}
        
    except Exception as e:
        doc.close()
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


def _get_snippet(page: fitz.Page, rect: fitz.Rect, query: str, context: int = 60) -> str:
    try:
        expanded = rect + (-200, -20, 200, 20)
        text = page.get_text("text", clip=expanded)
        text = text.strip().replace("\n", " ")
        idx = text.lower().find(query.lower())
        if idx == -1:
            return text[:context]
        start = max(0, idx - context // 2)
        end = min(len(text), idx + len(query) + context // 2)
        snippet = text[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(text):
            snippet = snippet + "..."
        return snippet
    except Exception:
        return query
