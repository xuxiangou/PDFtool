# -*- mode: python ; coding: utf-8 -*-

import os

ROOT_DIR = os.path.abspath(globals().get('SPECPATH', os.getcwd()))
MAIN_SCRIPT = os.path.join(ROOT_DIR, 'python', 'main.py')
HOOKS_DIR = os.path.join(ROOT_DIR, 'hooks')

a = Analysis(
    [MAIN_SCRIPT],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # chardet optional mypyc pipeline modules (safe to keep as optional)
        'chardet.pipeline.ascii__mypyc',
        'chardet.pipeline.confusion__mypyc',
        'chardet.pipeline.escape__mypyc',
        'chardet.pipeline.magic__mypyc',
        'chardet.pipeline.orchestrator__mypyc',
        'chardet.pipeline.statistical__mypyc',
        'chardet.pipeline.structural__mypyc',
        'chardet.pipeline.utf1632__mypyc',
        'chardet.pipeline.utf8__mypyc',
        'chardet.pipeline.validity__mypyc',
    ],
    hookspath=[HOOKS_DIR],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # OCR runtime stack (removed in slim build)
        'paddle',
        'paddleocr',
        'paddlex',
        'cv2',
        'opencv_python_headless',
        'opencv_contrib_python',
        'numpy',
        'pandas',
        'hf_xet',
        'safetensors',
        'google',
        'shapely',
        'pyclipper',
        'pypdfium2',
        'pypdfium2_raw',
        'python_bidi',
        # general exclusions
        'matplotlib',
        'IPython',
        'jupyter',
        'notebook',
        'pdf2docx',
        'tkinter',
        'watchfiles',
        'websockets',
        'httptools',
        'uvloop',
        'python_dotenv',
        'dotenv',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='pdf_backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='pdf_backend_slim',
)
