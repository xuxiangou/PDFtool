# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_data_files, copy_metadata, collect_dynamic_libs, collect_submodules

ROOT_DIR = os.path.abspath(globals().get('SPECPATH', os.getcwd()))
MAIN_SCRIPT = os.path.join(ROOT_DIR, 'python', 'main.py')
HOOKS_DIR = os.path.join(ROOT_DIR, 'hooks')


def safe_copy_metadata(name):
    try:
        return copy_metadata(name)
    except Exception:
        return []


def safe_collect_submodules(name):
    try:
        return collect_submodules(name)
    except Exception:
        return []


def collect_local_model_files(src_dir, dest_dir):
    entries = []
    if not os.path.isdir(src_dir):
        return entries
    for root, _, files in os.walk(src_dir):
        rel = os.path.relpath(root, src_dir)
        target = dest_dir if rel == '.' else os.path.join(dest_dir, rel)
        for name in files:
            entries.append((os.path.join(root, name), target))
    return entries


PADDLEX_CONFIG_DATAS = collect_data_files(
    'paddlex',
    includes=['configs/**/*.yaml', 'configs/**/*.yml', 'configs/**/*.json'],
)
LOCAL_MODEL_DATAS = collect_local_model_files(
    os.path.join(ROOT_DIR, 'model', 'ocr_model', 'layout', 'PP-DocLayout-S'),
    os.path.join('model', 'ocr_model', 'layout', 'PP-DocLayout-S'),
)
OCR_CORE_METADATA = (
    safe_copy_metadata('paddleocr')
    + safe_copy_metadata('paddlex')
    + safe_copy_metadata('paddlepaddle')
    + safe_copy_metadata('imagesize')
    + safe_copy_metadata('pyclipper')
    + safe_copy_metadata('python-bidi')
    + safe_copy_metadata('safetensors')
    + safe_copy_metadata('shapely')
    + safe_copy_metadata('tokenizers')
    + safe_copy_metadata('opencv-python-headless')
    + safe_copy_metadata('opencv-contrib-python')
    + safe_copy_metadata('pypdfium2')
    + safe_copy_metadata('pypdfium2_raw')
)
PADDLE_BINARIES = collect_dynamic_libs('paddle')
PADDLE_BINARIES += collect_dynamic_libs('pypdfium2_raw')
try:
    import paddle  # type: ignore

    paddle_lib_dir = os.path.join(os.path.dirname(paddle.__file__), 'libs')
    for dll_name in (
        'common.dll',
        'libiomp5md.dll',
        'mkldnn.dll',
        'mklml.dll',
        'phi.dll',
    ):
        src = os.path.join(paddle_lib_dir, dll_name)
        if not os.path.isfile(src):
            continue
        entry = (src, os.path.join('paddle', 'libs'))
        if entry not in PADDLE_BINARIES:
            PADDLE_BINARIES.append(entry)
except Exception:
    # Keep packaging resilient when OCR runtime is not installed.
    pass

LAYOUT_HIDDENIMPORTS = (
    safe_collect_submodules('paddleocr._models')
    + safe_collect_submodules('paddlex.inference.models.layout_analysis')
    + safe_collect_submodules('paddlex.inference.models.object_detection')
    + safe_collect_submodules('paddlex.modules.object_detection')
)

a = Analysis(
    [MAIN_SCRIPT],
    pathex=[],
    binaries=PADDLE_BINARIES,
    datas=PADDLEX_CONFIG_DATAS + OCR_CORE_METADATA + LOCAL_MODEL_DATAS,
    hiddenimports=LAYOUT_HIDDENIMPORTS + [
        # chardet 7.x ships optional mypyc-compiled pipeline extensions that
        # are loaded dynamically; include them explicitly for packaged runtime.
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
        # PaddleOCR/PaddleX formula recognition is config/dynamic-import driven.
        'paddleocr._pipelines.formula_recognition',
        'paddleocr._models.formula_recognition',
        'paddlex.inference.pipelines.formula_recognition',
        'paddlex.inference.pipelines.formula_recognition.pipeline',
        'paddlex.inference.pipelines.formula_recognition.result',
        'paddlex.inference.models.formula_recognition',
        'paddlex.inference.models.formula_recognition.predictor',
        'paddlex.inference.models.formula_recognition.processors',
        'paddlex.inference.models.formula_recognition.result',
        'paddleocr._models.layout_detection',
        'paddleocr._models._object_detection',
        'paddlex.inference.models.object_detection',
        'paddlex.inference.models.object_detection.predictor',
        'paddlex.inference.models.object_detection.processors',
        'paddlex.inference.models.object_detection.result',
        'safetensors',
        'safetensors._safetensors_rust',
        'safetensors.paddle',
        'tokenizers',
        'tokenizers.tokenizers',
    ],
    hookspath=[HOOKS_DIR],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'hf_xet',
        'rich',
        'pygments',
        'IPython',
        'jupyter',
        'notebook',
        'Pythonwin',
        'win32ui',
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
    name='pdf_backend',
)
