const { contextBridge, ipcRenderer, webUtils } = require('electron')

contextBridge.exposeInMainWorld('pdfAPI', {
  getPathForFile: (file) => webUtils.getPathForFile(file),

  // File dialogs
  openFiles:  ()     => ipcRenderer.invoke('dialog:open'),
  saveFile:   (opts) => ipcRenderer.invoke('dialog:save', opts),
  openDir:    ()     => ipcRenderer.invoke('dialog:openDir'),
  createTemp: (opts) => ipcRenderer.invoke('file:createTemp', opts),
  saveFileTo: (opts) => ipcRenderer.invoke('file:save', opts),
  deleteFile: (opts) => ipcRenderer.invoke('file:delete', opts),

  // Viewer
  render:     (p) => ipcRenderer.invoke('pdf:render', p),
  getInfo:    (p) => ipcRenderer.invoke('pdf:info', p),
  getPageText:(p) => ipcRenderer.invoke('pdf:text', p),
  loadPDF:    (p) => ipcRenderer.invoke('pdf:load', p),

  // Annotations
  annotate:       (p) => ipcRenderer.invoke('pdf:annotate', p),
  listAnnotations:(p) => ipcRenderer.invoke('pdf:annotate:list', p),

  // Translation
  translate: (p) => ipcRenderer.invoke('pdf:translate', p),

  // Conversion
  convert: (p) => ipcRenderer.invoke('pdf:convert', p),
  convertStatus: (taskId) => ipcRenderer.invoke('pdf:convert:status', taskId),
  importFiles: (p) => ipcRenderer.invoke('pdf:import', p),
  mergeFiles: (p) => ipcRenderer.invoke('pdf:merge', p),

  // Compression
  compress: (p) => ipcRenderer.invoke('pdf:compress', p),

  // Page operations
  pageOps: (p) => ipcRenderer.invoke('pdf:page_ops', p),

  // Search
  search: (p) => ipcRenderer.invoke('pdf:search', p),

  // OCR
  ocrStatus: () => ipcRenderer.invoke('pdf:ocr:status'),
  ocrDownload: (p) => ipcRenderer.invoke('pdf:ocr:download', p),
  formulaStatus: () => ipcRenderer.invoke('pdf:ocr:formula:status'),
  formulaDownload: (p) => ipcRenderer.invoke('pdf:ocr:formula:download', p),
  ocrCancel: () => ipcRenderer.invoke('pdf:ocr:cancel'),
  ocrAlignPage: (p) => ipcRenderer.invoke('pdf:ocr:alignPage', p),
  ocrApply: (p) => ipcRenderer.invoke('pdf:ocr:apply', p),

  // App close handshake
  onAppCloseRequest: (handler) => {
    if (typeof handler !== 'function') return () => {}
    const wrapped = () => handler()
    ipcRenderer.on('app:request-close', wrapped)
    return () => ipcRenderer.removeListener('app:request-close', wrapped)
  },
  replyAppClose: (payload) => ipcRenderer.send('app:close-response', payload),
})
