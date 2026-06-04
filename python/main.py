import argparse
import multiprocessing
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os, sys, io
from api import viewer, annotation, translate, converter, compress, page_ops, search, importer, ocr

if sys.platform == 'win32':
    os.environ.setdefault('PYTHONUTF8', '1')
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

app = FastAPI(title="PDFusion Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(viewer.router,      prefix="/pdf/render")
app.include_router(annotation.router,  prefix="/pdf/annotate")
app.include_router(translate.router,   prefix="/pdf/translate")
app.include_router(converter.router,   prefix="/pdf/convert")
app.include_router(compress.router,    prefix="/pdf/compress")
app.include_router(page_ops.router,    prefix="/pdf/page_ops")
app.include_router(search.router,      prefix="/pdf/search")
app.include_router(importer.router,    prefix="/pdf/import")
app.include_router(ocr.router,         prefix="/pdf/ocr")


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


if __name__ == "__main__":
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
