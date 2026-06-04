# AGENTS.md - PDFusion Development Guide

## Project Overview

PDFusion is an Electron + Python desktop PDF application:
- **Frontend**: Vanilla JS + HTML/CSS (renderer/)
- **Backend**: Python 3.11 + FastAPI + uvicorn (python/)
- **PDF Libraries**: PyMuPDF (AGPL-3.0), pikepdf (MPL-2.0)

---

## Build / Run / Test Commands

### Development Mode
```bash
# Terminal 1: Start Python backend
cd python && python main.py --port 18765

# Terminal 2: Start Electron
npm start
```

### Build Commands
```bash
npm install
pip install -r requirements.txt
npm run pack:py          # Package Python backend with PyInstaller
npm run build            # Build Electron app for distribution
```

### Testing
No formal test framework. Manual API testing:
```bash
# Health check
curl http://127.0.0.1:18765/health

# Render a page
curl -X POST http://127.0.0.1:18765/pdf/render/info \
  -H "Content-Type: application/json" \
  -d "{\"path\": \"test.pdf\"}"
```

### Linting
No linter configured. Run manually if needed:
```bash
# Python (optional)
pip install ruff && ruff check python/

# JavaScript (optional)
npx eslint electron/ renderer/
```

---

## Code Style Guidelines

### Python

**Imports** (order: stdlib -> third-party -> local):
```python
import os
import fitz  # PyMuPDF
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Literal, List, Optional
from utils.llm_client import LLMClient
```

**Type Hints**: Required for all functions
```python
def get_page_count(path: str) -> int: ...
async def chat(system: str, user: str) -> str: ...
def render_page_to_bytes(path: str, page: int, dpi: int = 150) -> bytes: ...
```

**Naming Conventions**:
- Functions/variables: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Pydantic request models: `{Action}Request`
- Private helpers: `_snake_case` (underscore prefix)

**Error Handling**:
```python
try:
    doc = fitz.open(path)
except Exception as e:
    raise HTTPException(status_code=400, detail=f"Cannot open PDF: {e}")
```

**Resource Management**: Always close PDF handles
```python
doc = fitz.open(path)
try:
    ...
finally:
    doc.close()

# Or with pikepdf context manager:
with pikepdf.open(path) as pdf:
    ...
```

**FastAPI Router Pattern** (python/api/*.py):
```python
router = APIRouter()

class ActionRequest(BaseModel):
    path: str
    action: Literal["split", "merge"]
    pages: Optional[List[int]] = None

@router.post("/")
def handler(req: ActionRequest):
    dispatch = {"split": _split, "merge": _merge}
    fn = dispatch.get(req.action)
    if not fn:
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")
    return fn(req)

def _split(req: ActionRequest):
    # Private helper implementation
    return {"status": "ok", "files": out_files}
```

**Async for External Calls**:
```python
async def _llm_translate(req: TranslateRequest):
    client = LLMClient(engine=req.engine, api_key=req.api_key)
    result = await client.chat(system=SYSTEM, user=prompt)
    return {"result": result}
```

### JavaScript

**Electron Main** (electron/*.js):
```javascript
const { app, BrowserWindow, ipcMain } = require('electron')
const { spawn } = require('child_process')
const axios = require('axios')
```

**Renderer** (renderer/app.js):
```javascript
const state = { tabs: [], activeTab: null }
const $ = id => document.getElementById(id)
$('btn-open').addEventListener('click', openFiles)
```

**Naming Conventions**:
- Variables/functions: `camelCase`
- DOM element helpers: `$` prefix (e.g., `$('btn-open')`)
- Constants: `UPPER_SNAKE_CASE`
- Private/helpers: `_prefix` or inline

**IPC Pattern**:
```javascript
// electron/main.js - Main process spawns Python
pythonProcess = spawn(pyExe, args, { env: { PYTHONUTF8: '1' } })

// electron/ipc_router.js - Main process handlers
ipcMain.handle('pdf:render', (_, p) => proxy('/pdf/render/page', p))

// electron/preload.js - Context bridge
contextBridge.exposeInMainWorld('pdfAPI', {
  render: (p) => ipcRenderer.invoke('pdf:render', p),
})

// renderer/app.js - Renderer calls
const res = await window.pdfAPI.render({ path, page, dpi })
```

**Async Error Handling**:
```javascript
try {
  const res = await window.pdfAPI.convert({ path, out_path, fmt })
  $('status').textContent = `Done: ${res.out_path}`
} catch (e) {
  $('status').textContent = 'Failed: ' + (e.message || e)
}
```

---

## Architecture

```
pdftool/
├── electron/
│   ├── main.js        # App lifecycle, Python spawn
│   ├── preload.js     # Context bridge for renderer
│   └── ipc_router.js  # IPC handlers proxying to Python API
├── renderer/
│   ├── index.html     # Main UI
│   ├── app.js         # Renderer logic (PDF.js + IPC)
│   └── style.css      # Styles
├── python/
│   ├── main.py        # FastAPI entry, CORS, router registration
│   ├── api/           # Route modules (viewer, translate, etc.)
│   ├── utils/         # Shared utilities (llm_client, pdf_utils)
│   └── prompts/       # LLM prompt templates
├── build/             # Electron entitlements
├── package.json       # npm scripts, electron-builder config
└── requirements.txt   # Python dependencies
```

**API Endpoints** (python/main.py):
- `/health` - Health check
- `/pdf/render/*` - Page rendering, info
- `/pdf/annotate/*` - Annotations
- `/pdf/translate/` - Translation (Google/LLM)
- `/pdf/convert/` - PDF to DOCX/images
- `/pdf/compress/` - PDF compression
- `/pdf/page_ops/` - Split, merge, reorder
- `/pdf/search/` - Text search
- `/pdf/ocr/` - OCR recognition

---

## Important Notes

1. **Port**: Backend uses 18765 by default; Electron finds available port on startup
2. **Paths**: Use `os.path.normpath()` for Windows compatibility
3. **UTF-8**: Set `PYTHONUTF8=1` on Windows for Chinese text support
4. **License**: PyMuPDF is AGPL-3.0; consider licensing for commercial use
5. **Chinese Comments**: Codebase contains Chinese comments; maintain consistency
6. **State Management**: Frontend uses a simple `state` object; no framework

---

## Cursor/Copilot Rules

No .cursor/rules, .cursorrules, or .github/copilot-instructions.md exist.
