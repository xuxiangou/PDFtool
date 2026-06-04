const axios = require('axios')
const path = require('path')
const fs = require('fs')
const os = require('os')

module.exports = (ipcMain, port) => {
  const API = `http://127.0.0.1:${port}`
  const CONVERT_TIMEOUT = 3600000
  const OCR_STATUS_TIMEOUT = 120000
  const OCR_DOWNLOAD_START_TIMEOUT = 120000

  // Generic proxy helper
  async function proxy(endpoint, payload, timeout = 120000) {
    try {
      const { data } = await axios.post(`${API}${endpoint}`, payload, { timeout })
      return data
    } catch (error) {
      const detail =
        error?.response?.data?.detail ||
        error?.response?.data?.message ||
        error?.message ||
        String(error)
      throw new Error(detail)
    }
  }

  async function proxyGet(endpoint, timeout = 120000) {
    try {
      const { data } = await axios.get(`${API}${endpoint}`, { timeout })
      return data
    } catch (error) {
      const detail =
        error?.response?.data?.detail ||
        error?.response?.data?.message ||
        error?.message ||
        String(error)
      throw new Error(detail)
    }
  }

  async function proxyPost(endpoint, payload, timeout = 120000) {
    try {
      const { data } = await axios.post(`${API}${endpoint}`, payload, { timeout })
      return data
    } catch (error) {
      const detail =
        error?.response?.data?.detail ||
        error?.response?.data?.message ||
        error?.message ||
        String(error)
      throw new Error(detail)
    }
  }

  // ── File dialogs (must run in main process) ──────────────────────
  ipcMain.handle('dialog:open', async () => {
    const { dialog } = require('electron')
    const result = await dialog.showOpenDialog({
      filters: [
        { name: 'Supported Files', extensions: ['pdf', 'doc', 'docx', 'jpg', 'jpeg', 'png', 'bmp', 'tif', 'tiff'] },
        { name: 'PDF Files', extensions: ['pdf'] },
        { name: 'Word Files', extensions: ['doc', 'docx'] },
        { name: 'Images', extensions: ['jpg', 'jpeg', 'png', 'bmp', 'tif', 'tiff'] },
      ],
      properties: ['openFile', 'multiSelections']
    })
    return result.canceled ? [] : result.filePaths
  })

  ipcMain.handle('dialog:save', async (_, { defaultPath, filters }) => {
    const { dialog } = require('electron')
    const result = await dialog.showSaveDialog({ defaultPath, filters })
    return result.canceled ? null : result.filePath
  })

  ipcMain.handle('dialog:openDir', async () => {
    const { dialog } = require('electron')
    const result = await dialog.showOpenDialog({ properties: ['openDirectory'] })
    return result.canceled ? null : result.filePaths[0]
  })

  // ── Create temp copy for editing ─────────────────────────────────
  ipcMain.handle('file:createTemp', async (_, { srcPath }) => {
    const tmpDir = path.join(os.tmpdir(), 'pdfusion')
    if (!fs.existsSync(tmpDir)) fs.mkdirSync(tmpDir, { recursive: true })
    const tmpName = `edit_${Date.now()}_${path.basename(srcPath)}`
    const tmpPath = path.join(tmpDir, tmpName)
    await fs.promises.copyFile(srcPath, tmpPath)
    return tmpPath
  })

  // ── Save file to destination ─────────────────────────────────────
  ipcMain.handle('file:save', async (_, { srcPath, destPath }) => {
    await fs.promises.copyFile(srcPath, destPath)
    return destPath
  })

  ipcMain.handle('file:delete', async (_, { targetPath }) => {
    if (!targetPath) return false
    try {
      await fs.promises.unlink(targetPath)
      return true
    } catch (error) {
      if (error && error.code === 'ENOENT') return true
      throw error
    }
  })

  // ── PDF Viewer ───────────────────────────────────────────────────
  ipcMain.handle('pdf:render',   (_, p) => proxy('/pdf/render/page', p))
  ipcMain.handle('pdf:info',     (_, p) => proxy('/pdf/render/info', p))
  ipcMain.handle('pdf:text',     (_, p) => proxy('/pdf/render/text', p))
  ipcMain.handle('pdf:load',     (_, p) => fs.promises.readFile(p.path))

  // ── Annotations ─────────────────────────────────────────────────
  ipcMain.handle('pdf:annotate',      (_, p) => proxy('/pdf/annotate/', p))
  ipcMain.handle('pdf:annotate:list', (_, p) => proxy('/pdf/annotate/list', p))

  // ── Conversion ───────────────────────────────────────────────────
  ipcMain.handle('pdf:convert', (_, p) => proxy('/pdf/convert/', p, CONVERT_TIMEOUT))
  ipcMain.handle('pdf:convert:status', (_, taskId) => proxyGet(`/pdf/convert/status/${encodeURIComponent(String(taskId || ''))}`, 30000))
  ipcMain.handle('pdf:import', (_, p) => proxy('/pdf/import/', p))
  ipcMain.handle('pdf:merge', (_, p) => proxy('/pdf/import/merge', p))

  // ── Compression ──────────────────────────────────────────────────
  ipcMain.handle('pdf:compress', (_, p) => proxy('/pdf/compress/', p))

  // ── Page Operations ──────────────────────────────────────────────
  ipcMain.handle('pdf:page_ops', (_, p) => proxy('/pdf/page_ops/', p))

  // ── Search ───────────────────────────────────────────────────────
  ipcMain.handle('pdf:search', (_, p) => proxy('/pdf/search/', p))

  // OCR
  ipcMain.handle('pdf:ocr:status', () => proxyGet('/pdf/ocr/status', OCR_STATUS_TIMEOUT))
  ipcMain.handle('pdf:ocr:download', (_, p) => proxyPost('/pdf/ocr/download', p, OCR_DOWNLOAD_START_TIMEOUT))
  ipcMain.handle('pdf:ocr:formula:status', () => proxyGet('/pdf/ocr/formula/status', OCR_STATUS_TIMEOUT))
  ipcMain.handle('pdf:ocr:formula:download', (_, p) => proxyPost('/pdf/ocr/formula/download', p, OCR_DOWNLOAD_START_TIMEOUT))
  ipcMain.handle('pdf:ocr:cancel', () => proxyPost('/pdf/ocr/cancel', {}, 30000))
  ipcMain.handle('pdf:ocr:alignPage', (_, p) => proxyPost('/pdf/ocr/align/page', p, 180000))
  ipcMain.handle('pdf:ocr:apply', (_, p) => proxyPost('/pdf/ocr/apply', p, 3600000))
}
