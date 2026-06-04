# PDFusion

> 基于 Electron + Python 的专业 PDF 工具，对标 WPS / 福昕 / Adobe Acrobat。

## 技术栈

| 层次 | 技术 |
|------|------|
| 桌面框架 | Electron 30 |
| 前端 | 原生 JS + HTML/CSS（无框架依赖，可迁移至 React） |
| 后端 | Python 3.11 + FastAPI + uvicorn |
| PDF 渲染/注释 | PyMuPDF（MuPDF C++ 引擎） |
| 页面操作/压缩 | pikepdf（QPDF C++ 引擎） |
| 格式转换 | pdf2docx、Pillow |
| AI 接口 | Qwen / Kimi / OpenAI 兼容协议 |

---

## 功能列表

- **多标签浏览**：像浏览器一样同时打开多个 PDF，支持拖入文件
- **PDF 阅读**：缩放（50%–200%）、旋转（±90°）、页面缩略图导航
- **批注工具**：高亮、下划线、删除线、文字标注（可隐藏）
- **划词翻译**：选中文字自动填入，支持 Google 翻译 / Qwen / Kimi
- **格式转换**：PDF → Word（.docx）、多图 JPG、长图 JPG
- **智能压缩**：高保留（仅压图）/ 低保留（整页光栅化），可调质量
- **页面操作**：拆分、合并、复制页、插入空白页、跨文档复制、删除、重排
- **全文搜索**：高亮显示匹配位置，跳转至对应页
- **OCR 识别**：Tesseract 本地引擎 / 视觉大模型（Qwen-VL / Kimi Vision）

---

## 快速开始

### 1. 环境要求

- Node.js ≥ 18
- Python ≥ 3.10
- （可选）Tesseract OCR：`brew install tesseract tesseract-lang`（macOS）

### 2. 安装依赖

```bash
# Node 依赖
npm install

# Python 依赖
pip install -r requirements.txt
```

### 3. 开发模式启动

```bash
# 终端 1：启动 Python 后端
cd python
python main.py --port 18765

# 终端 2：启动 Electron
npm start
```

---

## 项目结构

```
pdftool/
├── electron/
│   ├── main.js          # 主进程：窗口管理、Python 子进程
│   ├── preload.js       # contextBridge 安全 API 暴露
│   └── ipc_router.js    # IPC 路由分发 → Python REST
├── renderer/
│   ├── index.html       # 完整 UI
│   └── app.js           # 前端交互逻辑
├── python/
│   ├── main.py          # FastAPI 入口
│   ├── api/
│   │   ├── viewer.py    # 渲染、页面信息
│   │   ├── annotation.py# 批注读写
│   │   ├── translate.py # 翻译（Google / 大模型）
│   │   ├── converter.py # 格式转换
│   │   ├── compress.py  # 压缩
│   │   ├── page_ops.py  # 页面操作
│   │   ├── search.py    # 全文搜索
│   │   └── ocr.py       # OCR
│   ├── prompts/
│   │   ├── translate_system.txt
│   │   └── ocr_system.txt
│   └── utils/
│       ├── llm_client.py  # 统一大模型客户端
│       └── pdf_utils.py   # 工具函数
├── build/
│   └── entitlements.mac.plist
├── requirements.txt
└── package.json
```

---

## 打包发布

```bash
# 1. 冻结 Python 后端为可执行文件
pip install pyinstaller
pyinstaller --onedir --name pdf_backend \
  --add-data "python/prompts:prompts" \
  python/main.py

# 2. 打包 Electron 应用
npm run build
# 输出在 dist/ 目录
```

---

## API Key 配置

各功能在右侧面板中填入 API Key 即可使用，无需配置文件。

| 服务 | 申请地址 |
|------|---------|
| Google 翻译 | https://console.cloud.google.com |
| 通义千问 (Qwen) | https://dashscope.aliyuncs.com |
| Kimi (Moonshot) | https://platform.moonshot.cn |

---

## 许可证说明

- **PyMuPDF**：AGPL-3.0（闭源商业使用需购买 [Artifex 商业授权](https://artifex.com/licensing)）
- **pikepdf**：MPL-2.0（可商业使用）
- **本项目代码**：MIT

---

## 后续扩展建议

| 功能 | 推荐库 |
|------|--------|
| 数字签名 | `pyhanko` |
| 表单填写 | PyMuPDF `page.widgets()` |
| 水印 | PyMuPDF `page.insert_text()` |
| PDF 加密 | `pikepdf.Encryption`（AES-256）|
| 书签编辑 | PyMuPDF `doc.set_toc()` |
