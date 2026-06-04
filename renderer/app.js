/**
 * PDFusion 閳?Renderer Process Script
 * Uses PDF.js for rendering with text selection support
 */

import { getDocument, GlobalWorkerOptions, TextLayer } from './pdf.min.mjs'
GlobalWorkerOptions.workerSrc = './pdf.worker.min.mjs'

// 閳光偓閳光偓 State 閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓
const state = {
  tabs: [],
  activeTab: null,
  lastNonSearchPanel: 'convert',
  thumbsLoaded: {},
  renderToken: 0,
  thumbRenderToken: 0,
  isDragging: false,
  startY: 0,
  scrollTop: 0,
  clipboardPages: [],
  clipboardTabId: null,
  draggedThumbIndex: null,
  dragOverThumbIndex: null,
  dragInsertAfter: false,
  isReorderingThumbs: false,
  isRotatingPages: false,
  pendingThumbDrag: null,
  dragPointerClientY: null,
  suppressThumbClick: false,
  selectedThumbIndexes: [],
  lastSelectedThumbIndex: null,
  pastedThumbIndexes: [],
  pastedTabId: null,
  mergeTitleCounter: 1,
  isRenderingPages: false,
  zoomOpSeq: 0,
  zoomFinalizeTimer: null,
  isAppCloseInProgress: false,
  ocrStatusPollTimer: null,
  ocrApplyPollTimer: null,
  ocrDownloadInProgress: false,
  ocrApplyInProgress: false,
  ocrCancelRequested: false,
  convertInProgress: false,
  convertProgressTaskId: null,
  convertProgressTimer: null,
  convertProgressValue: 0,
  fileLoadSession: null,
  fileLoadCancelRequested: false,
  loadingActionHandler: null,
  loadingOverlayHideTimer: null,
  confirmOverlayHideTimer: null,
  activeRightPanel: 'convert',
  pageRenderScheduleTimer: null,
  pendingViewportRenderForce: false,
}
const UNDO_STACK_LIMIT = 30

// 閳光偓閳光偓 DOM refs 閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓
const $ = id => document.getElementById(id)
const tabBar = $('tab-bar')
const emptyState = $('empty-state')
const workspace = $('workspace')
const pageInput = $('page-input')
const pageTotal = $('page-total')
const zoomSelect = $('zoom-select')
const thumbList = $('thumb-list')
const viewerWrap = $('viewer-wrap')
const pagesContainer = $('pages-container')
const loadingOverlay = $('loading-overlay')
const loadingTitle = $('loading-title')
const loadingDetail = $('loading-detail')
const loadingFill = $('loading-fill')
const loadingPercent = $('loading-percent')
const loadingStage = $('loading-stage')
const loadingActions = $('loading-actions')
const loadingActionBtn = $('btn-loading-action')
const importDropZone = $('import-drop-zone')
const mergeDropZone = $('merge-drop-zone')
const confirmOverlay = $('confirm-overlay')
const confirmTitle = $('confirm-title')
const confirmMessage = $('confirm-message')
const confirmCloseBtn = $('confirm-close')
const confirmSaveBtn = $('confirm-save')
const confirmCancelBtn = $('confirm-cancel')
const confirmDiscardBtn = $('confirm-discard')

const IMAGE_EXTENSIONS = new Set(['.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff'])
const WORD_EXTENSIONS = new Set(['.doc', '.docx'])
const PDF_EXTENSIONS = new Set(['.pdf'])
const MERGE_EXTENSIONS = new Set([...PDF_EXTENSIONS, ...WORD_EXTENSIONS, ...IMAGE_EXTENSIONS])
const MIN_THUMB_RENDER_CONCURRENCY = 2
const MAX_THUMB_RENDER_CONCURRENCY = 8
const PAGE_RENDER_PREFETCH_RADIUS = 2
const PAGE_RENDER_CONCURRENCY = 2
const PAGE_RENDER_SCROLL_DEBOUNCE_MS = 70
const MAX_PAGE_OUTPUT_SCALE = 1.5
const FILE_LOAD_CANCELLED_MESSAGE = '文件加载已取消'
let activeCustomSelectCtx = null

function closeActiveCustomSelect({ restoreFocus = false } = {}) {
  if (!activeCustomSelectCtx) return
  const ctx = activeCustomSelectCtx
  activeCustomSelectCtx = null
  ctx.trigger.classList.remove('open')
  ctx.dropdown.classList.remove('open')
  ctx.dropdown.dataset.openUp = '0'
  ctx.trigger.setAttribute('aria-expanded', 'false')
  if (restoreFocus) ctx.trigger.focus()
}

function buildCustomSelectOptions(ctx) {
  const { select, dropdown } = ctx
  dropdown.innerHTML = ''
  const options = Array.from(select.options || [])
  options.forEach((opt, index) => {
    const optionBtn = document.createElement('button')
    optionBtn.type = 'button'
    optionBtn.className = 'ui-select-option'
    optionBtn.textContent = opt.textContent || ''
    optionBtn.disabled = !!opt.disabled
    optionBtn.dataset.index = String(index)
    if (opt.selected) optionBtn.classList.add('is-selected')
    optionBtn.addEventListener('click', ev => {
      ev.preventDefault()
      ev.stopPropagation()
      if (opt.disabled) return
      if (select.selectedIndex !== index) {
        select.selectedIndex = index
      }
      select.dispatchEvent(new Event('change', { bubbles: true }))
      syncCustomSelect(select)
      closeActiveCustomSelect()
    })
    dropdown.appendChild(optionBtn)
  })
}

function positionCustomSelectDropdown(ctx) {
  const rect = ctx.trigger.getBoundingClientRect()
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight
  ctx.dropdown.style.width = `${Math.round(rect.width)}px`
  ctx.dropdown.style.maxHeight = `${Math.max(140, Math.min(260, Math.floor(viewportHeight * 0.5)))}px`
  const dropdownHeight = Math.max(48, ctx.dropdown.offsetHeight || 0)
  const spaceBelow = viewportHeight - rect.bottom - 10
  const spaceAbove = rect.top - 10
  const openUp = spaceBelow < Math.min(220, dropdownHeight) && spaceAbove > spaceBelow
  ctx.dropdown.dataset.openUp = openUp ? '1' : '0'
  const top = openUp
    ? Math.max(8, rect.top - dropdownHeight - 6)
    : Math.min(viewportHeight - dropdownHeight - 8, rect.bottom + 6)
  const left = Math.max(8, Math.min(rect.left, viewportWidth - rect.width - 8))
  ctx.dropdown.style.left = `${Math.round(left)}px`
  ctx.dropdown.style.top = `${Math.round(top)}px`
}

function syncCustomSelect(select) {
  const ctx = select?._customSelectCtx
  if (!ctx) return
  const selectedOption = select.selectedOptions?.[0] || Array.from(select.options || [])[0] || null
  ctx.label.textContent = selectedOption ? selectedOption.textContent || '' : ''
  ctx.trigger.disabled = !!select.disabled
  const shouldHide = select.classList.contains('hidden')
  ctx.wrap.classList.toggle('hidden', shouldHide)
  if (shouldHide && activeCustomSelectCtx === ctx) {
    closeActiveCustomSelect()
  }
  buildCustomSelectOptions(ctx)
}

function openCustomSelect(ctx) {
  if (!ctx || ctx.trigger.disabled) return
  if (activeCustomSelectCtx && activeCustomSelectCtx !== ctx) {
    closeActiveCustomSelect()
  }
  syncCustomSelect(ctx.select)
  positionCustomSelectDropdown(ctx)
  ctx.trigger.classList.add('open')
  ctx.dropdown.classList.add('open')
  ctx.trigger.setAttribute('aria-expanded', 'true')
  activeCustomSelectCtx = ctx
}

function enhanceSelect(select, shape = 'toolbar') {
  if (!select || select._customSelectCtx) return
  const wrap = document.createElement('div')
  wrap.className = 'ui-select-wrap'
  wrap.dataset.uiShape = shape
  if (shape === 'panel') wrap.classList.add('select-fluid')
  if (shape === 'toolbar') {
    const computed = getComputedStyle(select)
    if (computed.minWidth && computed.minWidth !== '0px') {
      wrap.style.minWidth = computed.minWidth
    }
    const rectWidth = Math.round(select.getBoundingClientRect().width)
    if (rectWidth > 0) {
      wrap.style.width = `${rectWidth}px`
    }
  }

  const parent = select.parentNode
  if (!parent) return
  parent.insertBefore(wrap, select)
  wrap.appendChild(select)
  select.classList.add('ui-native-select')

  const trigger = document.createElement('button')
  trigger.type = 'button'
  trigger.className = 'ui-select-trigger'
  trigger.setAttribute('aria-haspopup', 'listbox')
  trigger.setAttribute('aria-expanded', 'false')

  const label = document.createElement('span')
  label.className = 'ui-select-label'
  const caret = document.createElement('span')
  caret.className = 'ui-select-caret'
  trigger.appendChild(label)
  trigger.appendChild(caret)
  wrap.appendChild(trigger)

  const dropdown = document.createElement('div')
  dropdown.className = 'ui-select-dropdown'
  document.body.appendChild(dropdown)

  const ctx = { select, wrap, trigger, label, dropdown }
  select._customSelectCtx = ctx
  select._uiSelectWrap = wrap

  trigger.addEventListener('click', ev => {
    ev.preventDefault()
    ev.stopPropagation()
    if (activeCustomSelectCtx === ctx) {
      closeActiveCustomSelect({ restoreFocus: true })
      return
    }
    openCustomSelect(ctx)
  })

  trigger.addEventListener('keydown', ev => {
    if (ev.key === 'Enter' || ev.key === ' ') {
      ev.preventDefault()
      if (activeCustomSelectCtx === ctx) closeActiveCustomSelect({ restoreFocus: true })
      else openCustomSelect(ctx)
      return
    }
    if (ev.key === 'Escape') {
      closeActiveCustomSelect({ restoreFocus: true })
      return
    }
    if (ev.key === 'ArrowDown' && activeCustomSelectCtx !== ctx) {
      ev.preventDefault()
      openCustomSelect(ctx)
    }
  })

  select.addEventListener('change', () => syncCustomSelect(select))
  select.addEventListener('input', () => syncCustomSelect(select))

  const selectObserver = new MutationObserver(() => {
    syncCustomSelect(select)
  })
  selectObserver.observe(select, {
    attributes: true,
    childList: true,
    subtree: true,
  })

  syncCustomSelect(select)
}

function initCustomSelects() {
  const toolbarIds = ['zoom-select', 'normalize-size-select']
  toolbarIds.forEach(id => enhanceSelect($(id), 'toolbar'))
  document.querySelectorAll('.panel-select').forEach(el => {
    enhanceSelect(el, 'panel')
  })
}

function setLoadingProgress({
  visible = true,
  title = '正在加载文档',
  detail = '正在准备文件，请稍候...',
  stage = '处理中',
  progress = 0,
  action = null,
}) {
  if (!loadingOverlay) return
  if (!visible) {
    hideLoadingProgress()
    return
  }

  if (state.loadingOverlayHideTimer) {
    clearTimeout(state.loadingOverlayHideTimer)
    state.loadingOverlayHideTimer = null
  }
  loadingOverlay.classList.remove('hidden', 'overlay-hiding')
  if (!loadingOverlay.classList.contains('overlay-visible')) {
    requestAnimationFrame(() => loadingOverlay.classList.add('overlay-visible'))
  }

  loadingTitle.textContent = title
  loadingDetail.textContent = detail
  loadingStage.textContent = stage
  const safeProgress = Math.max(0, Math.min(100, Math.round(progress)))
  loadingFill.style.width = `${safeProgress}%`
  loadingPercent.textContent = `${safeProgress}%`
  const hasAction = visible && action && typeof action.onClick === 'function'
  if (loadingActions && loadingActionBtn) {
    if (hasAction) {
      state.loadingActionHandler = action.onClick
      loadingActionBtn.textContent = String(action.label || '取消')
      loadingActionBtn.disabled = !!action.disabled
      loadingActions.classList.remove('hidden')
    } else {
      state.loadingActionHandler = null
      loadingActionBtn.disabled = false
      loadingActions.classList.add('hidden')
    }
  }
  document.body.classList.add('busy')
}

function hideLoadingProgress() {
  if (!loadingOverlay) return
  if (state.loadingOverlayHideTimer) {
    clearTimeout(state.loadingOverlayHideTimer)
    state.loadingOverlayHideTimer = null
  }
  state.loadingActionHandler = null
  if (loadingActions && loadingActionBtn) {
    loadingActionBtn.disabled = false
    loadingActions.classList.add('hidden')
  }
  loadingOverlay.classList.remove('overlay-visible')
  loadingOverlay.classList.add('overlay-hiding')
  state.loadingOverlayHideTimer = setTimeout(() => {
    loadingOverlay.classList.add('hidden')
    loadingOverlay.classList.remove('overlay-hiding')
    state.loadingOverlayHideTimer = null
  }, 180)
  document.body.classList.remove('busy')
}

function showConfirmOverlay() {
  if (!confirmOverlay) return
  if (state.confirmOverlayHideTimer) {
    clearTimeout(state.confirmOverlayHideTimer)
    state.confirmOverlayHideTimer = null
  }
  confirmOverlay.classList.remove('hidden', 'overlay-hiding')
  if (!confirmOverlay.classList.contains('overlay-visible')) {
    requestAnimationFrame(() => confirmOverlay.classList.add('overlay-visible'))
  }
}

function hideConfirmOverlay() {
  if (!confirmOverlay) return
  if (state.confirmOverlayHideTimer) {
    clearTimeout(state.confirmOverlayHideTimer)
    state.confirmOverlayHideTimer = null
  }
  confirmOverlay.classList.remove('overlay-visible')
  confirmOverlay.classList.add('overlay-hiding')
  state.confirmOverlayHideTimer = setTimeout(() => {
    confirmOverlay.classList.add('hidden')
    confirmOverlay.classList.remove('overlay-hiding')
    state.confirmOverlayHideTimer = null
  }, 160)
}

if (loadingActionBtn) {
  loadingActionBtn.addEventListener('click', async () => {
    if (loadingActionBtn.disabled) return
    const handler = state.loadingActionHandler
    if (typeof handler !== 'function') return
    try {
      await handler()
    } catch (e) {
      console.error('loading action failed', e)
    }
  })
}

function createFileLoadCancelledError() {
  const error = new Error(FILE_LOAD_CANCELLED_MESSAGE)
  error.code = 'FILE_LOAD_CANCELLED'
  return error
}

function isFileLoadCancelledError(error) {
  if (error?.code === 'FILE_LOAD_CANCELLED') return true
  const text = String(error?.message || error || '')
  return text.includes(FILE_LOAD_CANCELLED_MESSAGE)
}

function beginFileLoadSession() {
  let resolveCancel = null
  const cancelPromise = new Promise(resolve => {
    resolveCancel = resolve
  })
  const session = {
    cancelled: false,
    cancelPromise,
    resolveCancel,
    loadingTask: null,
  }
  state.fileLoadSession = session
  state.fileLoadCancelRequested = false
  return session
}

function endFileLoadSession(session) {
  if (state.fileLoadSession !== session) return
  state.fileLoadSession = null
  state.fileLoadCancelRequested = false
}

function ensureFileLoadNotCancelled(session) {
  if (!session) return
  if (session.cancelled) {
    throw createFileLoadCancelledError()
  }
}

function buildFileLoadAction() {
  if (!state.fileLoadSession) return null
  return {
    label: state.fileLoadCancelRequested ? '正在取消...' : '取消加载',
    disabled: !!state.fileLoadCancelRequested,
    onClick: requestFileLoadCancel,
  }
}

function setFileLoadProgress(session, payload) {
  ensureFileLoadNotCancelled(session)
  setLoadingProgress({
    ...payload,
    action: buildFileLoadAction(),
  })
}

async function awaitFileLoad(promise, session, { onCancel } = {}) {
  if (!session) return promise
  ensureFileLoadNotCancelled(session)
  return Promise.race([
    promise,
    session.cancelPromise.then(async () => {
      if (typeof onCancel === 'function') {
        try {
          await onCancel()
        } catch (_) {}
      }
      throw createFileLoadCancelledError()
    }),
  ])
}

async function requestFileLoadCancel() {
  const session = state.fileLoadSession
  if (!session || session.cancelled) return

  session.cancelled = true
  state.fileLoadCancelRequested = true
  state.renderToken += 1
  state.thumbRenderToken += 1
  clearScheduledPageRender()
  state.pendingViewportRenderForce = false

  try {
    const task = session.loadingTask
    if (task && typeof task.destroy === 'function') {
      await task.destroy()
    }
  } catch (_) {}

  if (typeof session.resolveCancel === 'function') {
    session.resolveCancel()
  }

  setLoadingProgress({
    title: '正在取消加载',
    detail: '已发送取消请求，正在停止当前读取任务...',
    stage: '取消中',
    progress: Math.max(8, getLoadingProgressValue()),
    action: buildFileLoadAction(),
  })
}

function getThumbRenderConcurrency() {
  const cores = Number(window.navigator?.hardwareConcurrency) || 4
  const suggested = Math.floor(cores / 2)
  return Math.max(MIN_THUMB_RENDER_CONCURRENCY, Math.min(MAX_THUMB_RENDER_CONCURRENCY, suggested || MIN_THUMB_RENDER_CONCURRENCY))
}

function getFileExtension(filePath) {
  const match = /\.([^.]+)$/.exec(filePath)
  return match ? `.${match[1].toLowerCase()}` : ''
}

function getBaseName(filePath) {
  return (filePath || '').split(/[\\/]/).pop() || ''
}

function replaceExtension(filePath, newExt) {
  return filePath.replace(/\.[^.]+$/, newExt)
}

function getSuggestedPdfPath(paths) {
  if (paths.length === 1) return replaceExtension(paths[0], '.pdf')
  return replaceExtension(paths[0], '_images.pdf')
}

function getSuggestedMergedPdfPath(paths) {
  const first = paths[0]
  if (/\.pdf$/i.test(first)) return first.replace(/\.pdf$/i, '_merged.pdf')
  const withPdf = /\.([^.]+)$/.test(first) ? replaceExtension(first, '.pdf') : `${first}.pdf`
  return withPdf.replace(/\.pdf$/i, '_merged.pdf')
}

function isSupportedImportPath(filePath) {
  const ext = getFileExtension(filePath)
  return PDF_EXTENSIONS.has(ext) || WORD_EXTENSIONS.has(ext) || IMAGE_EXTENSIONS.has(ext)
}

function isSupportedMergePath(filePath) {
  const ext = getFileExtension(filePath)
  return MERGE_EXTENSIONS.has(ext)
}

function extractDropPaths(event, { forMerge = false } = {}) {
  const files = Array.from(event.dataTransfer?.files || [])
  const supported = files.filter(file => {
    const ext = getFileExtension(file.name || '')
    return forMerge ? MERGE_EXTENSIONS.has(ext) : isSupportedImportPath(file.name || '')
  })
  return supported.map(file => window.pdfAPI.getPathForFile(file)).filter(Boolean)
}

function getNextMergedTitle() {
  const title = `合并${state.mergeTitleCounter}`
  state.mergeTitleCounter += 1
  return title
}

async function openPreparedPdf(tempPath, savePathHint, options = {}, loadSession = null) {
  ensureFileLoadNotCancelled(loadSession)
  if (loadSession) {
    setFileLoadProgress(loadSession, {
      title: '正在准备文档',
      detail: '正在读取文件信息并创建工作标签页...',
      stage: '打开中',
      progress: options.progress ?? 72,
    })
  } else {
    setLoadingProgress({
      title: '正在准备文档',
      detail: '正在读取文件信息并创建工作标签页...',
      stage: '打开中',
      progress: options.progress ?? 72,
    })
  }

  let tab = null
  try {
    tab = createTab(tempPath, savePathHint, options.displayTitle)
    const info = await awaitFileLoad(window.pdfAPI.getInfo({ path: tempPath }), loadSession)
    ensureFileLoadNotCancelled(loadSession)

    tab.pageCount = info.page_count
    tab.title = options.displayTitle || getBaseName(savePathHint) || tab.title
    if (options.markUnsaved) {
      tab.unsaved = true
    }
    renderTabBar()
    pageTotal.textContent = `/ ${tab.pageCount}`

    if (loadSession) {
      setFileLoadProgress(loadSession, {
        title: '正在渲染页面',
        detail: '正在生成阅读视图和缩略图...',
        stage: '渲染中',
        progress: Math.max(options.progress ?? 72, 88),
      })
    } else {
      setLoadingProgress({
        title: '正在渲染页面',
        detail: '正在生成阅读视图和缩略图...',
        stage: '渲染中',
        progress: Math.max(options.progress ?? 72, 88),
      })
    }

    await loadPDFDocument(tempPath, {}, loadSession)
    refreshOcrStatusForCurrentPanel()
  } catch (e) {
    if (tab && isFileLoadCancelledError(e)) {
      const removedTab = removeTab(tab.id)
      await deleteManagedTempPdf(removedTab)
    }
    throw e
  }
}

async function importSelectedPaths(paths, loadSession = null) {
  ensureFileLoadNotCancelled(loadSession)
  const showProgress = payload => {
    if (loadSession) {
      setFileLoadProgress(loadSession, payload)
      return
    }
    setLoadingProgress(payload)
  }

  const pdfPaths = []
  const wordPaths = []
  const imagePaths = []

  for (const inputPath of paths) {
    const ext = getFileExtension(inputPath)
    if (PDF_EXTENSIONS.has(ext)) pdfPaths.push(inputPath)
    else if (WORD_EXTENSIONS.has(ext)) wordPaths.push(inputPath)
    else if (IMAGE_EXTENSIONS.has(ext)) imagePaths.push(inputPath)
  }

  if (pdfPaths.length === 0 && wordPaths.length === 0 && imagePaths.length === 0) {
    throw new Error('No supported PDF, Word, or image files were selected.')
  }

  const totalUnits = pdfPaths.length + wordPaths.length + (imagePaths.length > 0 ? 1 : 0)
  let completedUnits = 0
  const unitProgress = (base, span = 52) => base + (totalUnits === 0 ? 0 : (completedUnits / totalUnits) * span)

  for (const originalPath of pdfPaths) {
    ensureFileLoadNotCancelled(loadSession)
    showProgress({
      title: '正在打开 PDF',
      detail: `正在准备 ${getBaseName(originalPath)} 的编辑副本...`,
      stage: '复制 PDF',
      progress: unitProgress(8),
    })
    const tempPath = await awaitFileLoad(window.pdfAPI.createTemp({ srcPath: originalPath }), loadSession)
    completedUnits += 1
    await openPreparedPdf(tempPath, originalPath, {
      progress: unitProgress(16),
      displayTitle: getBaseName(originalPath),
    }, loadSession)
  }

  for (const originalPath of wordPaths) {
    ensureFileLoadNotCancelled(loadSession)
    showProgress({
      title: '正在转换 Word 文档',
      detail: `正在将 ${getBaseName(originalPath)} 转换为 PDF...`,
      stage: '转换 Word',
      progress: unitProgress(8),
    })
    const res = await awaitFileLoad(window.pdfAPI.importFiles({ paths: [originalPath] }), loadSession)
    completedUnits += 1
    await openPreparedPdf(res.temp_path, replaceExtension(originalPath, '.pdf'), {
      markUnsaved: true,
      progress: unitProgress(16),
      displayTitle: getBaseName(originalPath),
    }, loadSession)
  }

  if (imagePaths.length > 0) {
    ensureFileLoadNotCancelled(loadSession)
    showProgress({
      title: '正在合并图片',
      detail: `正在将 ${imagePaths.length} 张图片合并为 PDF...`,
      stage: '生成 PDF',
      progress: unitProgress(8),
    })
    const res = await awaitFileLoad(window.pdfAPI.importFiles({ paths: imagePaths }), loadSession)
    completedUnits += 1
    await openPreparedPdf(res.temp_path, getSuggestedPdfPath(imagePaths), {
      markUnsaved: true,
      progress: unitProgress(16),
      displayTitle: getBaseName(imagePaths[0]),
    }, loadSession)
  }
}

async function mergeSelectedPaths(paths, loadSession = null) {
  ensureFileLoadNotCancelled(loadSession)
  const showProgress = payload => {
    if (loadSession) {
      setFileLoadProgress(loadSession, payload)
      return
    }
    setLoadingProgress(payload)
  }
  const supportedPaths = paths.filter(isSupportedMergePath)
  if (supportedPaths.length < 2) {
    throw new Error('合并至少需要 2 个 PDF / Word / 图片文件。')
  }

  showProgress({
    title: '正在合并文档',
    detail: `已选择 ${supportedPaths.length} 个文件，正在合并...`,
    stage: '合并中',
    progress: 10,
  })

  const res = await awaitFileLoad(window.pdfAPI.mergeFiles({ paths: supportedPaths }), loadSession)
  const mergedTitle = getNextMergedTitle()
  await openPreparedPdf(res.temp_path, getSuggestedMergedPdfPath(supportedPaths), {
    markUnsaved: true,
    progress: 84,
    displayTitle: mergedTitle,
  }, loadSession)
}

// Tab helpers 閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓
function getTab() { return state.tabs.find(t => t.id === state.activeTab) }

function sortPageIndexes(indexes) {
  return [...new Set(indexes)].sort((a, b) => a - b)
}

function getCopiedThumbIndexes() {
  const tab = getTab()
  if (!tab || state.clipboardTabId !== tab.id) return []
  return sortPageIndexes(state.clipboardPages)
}

function getPastedThumbIndexes() {
  const tab = getTab()
  if (!tab || state.pastedTabId !== tab.id) return []
  return sortPageIndexes(state.pastedThumbIndexes)
}

function remapIndexesAfterDelete(indexes, deletedIndexes, pageCountBefore) {
  const deleted = sortPageIndexes((deletedIndexes || []).filter(
    index => Number.isInteger(index) && index >= 0 && index < pageCountBefore
  ))
  if (deleted.length === 0) return sortPageIndexes(indexes || [])

  const deletedSet = new Set(deleted)
  const source = sortPageIndexes((indexes || []).filter(index => Number.isInteger(index)))
  const remapped = []
  for (const index of source) {
    if (index < 0 || index >= pageCountBefore) continue
    if (deletedSet.has(index)) continue
    let shift = 0
    for (const deletedIndex of deleted) {
      if (deletedIndex < index) shift += 1
      else break
    }
    remapped.push(index - shift)
  }
  return sortPageIndexes(remapped)
}

function syncPastedIndexesAfterDelete(tabId, deletedIndexes, pageCountBefore) {
  if (state.pastedTabId !== tabId) return
  state.pastedThumbIndexes = remapIndexesAfterDelete(
    state.pastedThumbIndexes,
    deletedIndexes,
    pageCountBefore
  )
  if (state.pastedThumbIndexes.length === 0) {
    state.pastedTabId = null
  }
}

function remapIndexesAfterReorder(indexes, newOrder, pageCountBefore) {
  if (!Array.isArray(newOrder) || newOrder.length !== pageCountBefore) {
    return sortPageIndexes(indexes || [])
  }
  const source = sortPageIndexes((indexes || []).filter(
    index => Number.isInteger(index) && index >= 0 && index < pageCountBefore
  ))
  const oldToNew = new Map()
  newOrder.forEach((oldIndex, newIndex) => {
    if (Number.isInteger(oldIndex)) oldToNew.set(oldIndex, newIndex)
  })
  const remapped = []
  for (const index of source) {
    if (oldToNew.has(index)) remapped.push(oldToNew.get(index))
  }
  return sortPageIndexes(remapped)
}

function remapIndexAfterReorder(index, newOrder, pageCountBefore) {
  if (!Number.isInteger(index) || index < 0 || index >= pageCountBefore) return null
  if (!Array.isArray(newOrder) || newOrder.length !== pageCountBefore) return index
  return newOrder.indexOf(index)
}

function buildReorderPlan(pageCount, fromIndex, toInsertIndex) {
  if (!Number.isInteger(pageCount) || pageCount <= 0) return null
  if (!Number.isInteger(fromIndex) || fromIndex < 0 || fromIndex >= pageCount) return null
  const order = Array.from({ length: pageCount }, (_, i) => i)
  const [moved] = order.splice(fromIndex, 1)
  const clampedInsert = Math.max(0, Math.min(toInsertIndex, pageCount))
  const adjustedInsert = clampedInsert > fromIndex ? clampedInsert - 1 : clampedInsert
  order.splice(adjustedInsert, 0, moved)
  return order
}

function clearThumbDragVisualState() {
  state.dragOverThumbIndex = null
  state.dragInsertAfter = false
  thumbList.querySelectorAll('.thumb-item').forEach(item => {
    item.classList.remove('drag-over', 'drag-over-before', 'drag-over-after', 'dragging')
  })
}

function applyThumbDropIndicator(targetIndex, insertAfter) {
  thumbList.querySelectorAll('.thumb-item').forEach((item, index) => {
    item.classList.remove('drag-over', 'drag-over-before', 'drag-over-after')
    if (index !== targetIndex || index === state.draggedThumbIndex) return
    item.classList.add('drag-over')
    item.classList.add(insertAfter ? 'drag-over-after' : 'drag-over-before')
  })
}

function getThumbDropTargetByClientY(clientY) {
  const items = Array.from(thumbList.querySelectorAll('.thumb-item'))
  if (items.length === 0) return null

  const firstRect = items[0].getBoundingClientRect()
  if (clientY <= firstRect.top) {
    return { index: 0, insertAfter: false }
  }

  for (let i = 0; i < items.length; i += 1) {
    const rect = items[i].getBoundingClientRect()
    const middle = rect.top + rect.height / 2
    if (clientY < middle) return { index: i, insertAfter: false }
    if (clientY <= rect.bottom) return { index: i, insertAfter: true }

    const next = items[i + 1]
    if (next) {
      const nextTop = next.getBoundingClientRect().top
      if (clientY < nextTop) return { index: i, insertAfter: true }
    }
  }

  return { index: items.length - 1, insertAfter: true }
}

function autoScrollThumbListOnDrag(clientY) {
  const rect = thumbList.getBoundingClientRect()
  const edge = 36
  const maxStep = 26

  if (clientY < rect.top + edge) {
    const ratio = Math.min(1, (rect.top + edge - clientY) / edge)
    thumbList.scrollTop -= Math.max(2, Math.round(maxStep * ratio))
  } else if (clientY > rect.bottom - edge) {
    const ratio = Math.min(1, (clientY - (rect.bottom - edge)) / edge)
    thumbList.scrollTop += Math.max(2, Math.round(maxStep * ratio))
  }
}

function updateThumbDragTargetByPointer(clientY) {
  const target = getThumbDropTargetByClientY(clientY)
  if (!target) return
  autoScrollThumbListOnDrag(clientY)
  if (
    state.dragOverThumbIndex === target.index
    && state.dragInsertAfter === target.insertAfter
  ) {
    return
  }
  state.dragOverThumbIndex = target.index
  state.dragInsertAfter = target.insertAfter
  applyThumbDropIndicator(target.index, target.insertAfter)
}

function updateThumbSelectionStates() {
  const selected = new Set(state.selectedThumbIndexes)
  const pasted = new Set(getPastedThumbIndexes())
  thumbList.querySelectorAll('.thumb-item').forEach((item, index) => {
    item.classList.toggle('selected', selected.has(index))
    item.classList.remove('copied')
    item.classList.toggle('pasted', pasted.has(index))
    const badge = item.querySelector('.thumb-badge')
    if (!badge) return
    if (selected.has(index)) {
      badge.textContent = '已选择'
      badge.className = 'thumb-badge selected'
      badge.classList.remove('hidden')
    } else if (pasted.has(index)) {
      badge.textContent = '已粘贴'
      badge.className = 'thumb-badge pasted'
      badge.classList.remove('hidden')
    } else {
      badge.className = 'thumb-badge hidden'
      badge.textContent = ''
    }
  })
}

function setSelectedThumbIndexes(indexes) {
  state.selectedThumbIndexes = sortPageIndexes(indexes)
  updateThumbSelectionStates()
}

function clearSelectedThumbIndexes() {
  state.selectedThumbIndexes = []
  state.lastSelectedThumbIndex = null
  updateThumbSelectionStates()
}

async function clearTabUndoSnapshots(tab) {
  if (!tab || !Array.isArray(tab.undoStack) || tab.undoStack.length === 0) return
  const snapshots = [...tab.undoStack]
  tab.undoStack = []
  for (const item of snapshots) {
    if (!item || !item.path) continue
    try {
      await window.pdfAPI.deleteFile({ targetPath: item.path })
    } catch (_) {}
  }
}

async function pushUndoSnapshot(tab, reason = '') {
  if (!tab || !tab.path) return false
  if (!window.pdfAPI?.createTemp) return false
  try {
    const snapshotPath = await window.pdfAPI.createTemp({ srcPath: tab.path })
    if (!Array.isArray(tab.undoStack)) tab.undoStack = []
    tab.undoStack.push({ path: snapshotPath, reason, at: Date.now() })
    while (tab.undoStack.length > UNDO_STACK_LIMIT) {
      const removed = tab.undoStack.shift()
      if (removed?.path) {
        try {
          await window.pdfAPI.deleteFile({ targetPath: removed.path })
        } catch (_) {}
      }
    }
    return true
  } catch (e) {
    console.warn('pushUndoSnapshot failed', reason, e)
    return false
  }
}

async function undoLastEdit() {
  const tab = getTab()
  if (!tab || !Array.isArray(tab.undoStack) || tab.undoStack.length === 0) return false
  const snapshot = tab.undoStack.pop()
  if (!snapshot?.path) return false
  const currentPage = getNearestPageIndexInViewport(tab)
  try {
    await window.pdfAPI.saveFileTo({ srcPath: snapshot.path, destPath: tab.path })
    tab.unsaved = true
    renderTabBar()
    tab.pdfDoc = null
    state.thumbsLoaded = {}
    const info = await window.pdfAPI.getInfo({ path: tab.path })
    tab.pageCount = info.page_count
    tab.page = Math.max(0, Math.min(currentPage, tab.pageCount - 1))
    pageTotal.textContent = `/ ${tab.pageCount}`
    await loadPDFDocument(tab.path, { preservePageIndex: tab.page })
    updateThumbSelectionStates()
    return true
  } catch (e) {
    console.error('Undo failed', e)
    alert('撤销失败：' + (e.message || e))
    return false
  } finally {
    try {
      await window.pdfAPI.deleteFile({ targetPath: snapshot.path })
    } catch (_) {}
  }
}

function handleThumbSelection(pageIndex, event = null) {
  if (state.suppressThumbClick) {
    state.suppressThumbClick = false
    return
  }
  const isRangeSelect = !!(event && event.shiftKey && state.lastSelectedThumbIndex !== null)
  const isToggleSelect = !!(event && (event.ctrlKey || event.metaKey))

  if (isRangeSelect) {
    const start = Math.min(state.lastSelectedThumbIndex, pageIndex)
    const end = Math.max(state.lastSelectedThumbIndex, pageIndex)
    const range = []
    for (let i = start; i <= end; i += 1) range.push(i)
    setSelectedThumbIndexes(range)
  } else if (isToggleSelect) {
    const selected = new Set(state.selectedThumbIndexes)
    if (selected.has(pageIndex)) selected.delete(pageIndex)
    else selected.add(pageIndex)
    setSelectedThumbIndexes([...selected])
    state.lastSelectedThumbIndex = pageIndex
  } else {
    setSelectedThumbIndexes([pageIndex])
    state.lastSelectedThumbIndex = pageIndex
    scrollToPage(pageIndex)
  }
}

function startThumbManualDrag(event, pageIndex) {
  if (event.button !== 0) return
  const tab = getTab()
  if (!tab || tab.pageCount <= 1 || state.isReorderingThumbs) return
  state.pendingThumbDrag = {
    pageIndex,
    startX: event.clientX,
    startY: event.clientY,
  }
  state.dragPointerClientY = event.clientY
  document.addEventListener('mousemove', handleThumbManualDragMove, true)
  document.addEventListener('mouseup', handleThumbManualDragEnd, true)
}

function stopThumbManualDragListeners() {
  document.removeEventListener('mousemove', handleThumbManualDragMove, true)
  document.removeEventListener('mouseup', handleThumbManualDragEnd, true)
}

function activateThumbManualDrag(pageIndex) {
  state.draggedThumbIndex = pageIndex
  state.dragOverThumbIndex = null
  state.dragInsertAfter = false
  const srcItem = thumbList.children[pageIndex]
  if (srcItem) srcItem.classList.add('dragging')
}

function handleThumbManualDragMove(event) {
  if (!state.pendingThumbDrag) return
  state.dragPointerClientY = event.clientY
  if ((event.buttons & 1) === 0) {
    handleThumbManualDragEnd(event)
    return
  }
  const { pageIndex, startX, startY } = state.pendingThumbDrag
  const moveDistance = Math.abs(event.clientX - startX) + Math.abs(event.clientY - startY)
  if (state.draggedThumbIndex === null) {
    if (moveDistance < 5) return
    activateThumbManualDrag(pageIndex)
  }
  event.preventDefault()
  event.stopPropagation()
  updateThumbDragTargetByPointer(event.clientY)
}

async function handleThumbManualDragEnd(event) {
  stopThumbManualDragListeners()
  const fromIndex = state.draggedThumbIndex
  const targetIndex = state.dragOverThumbIndex
  const insertAfter = state.dragInsertAfter
  const hadDrag = Number.isInteger(fromIndex)

  state.pendingThumbDrag = null
  state.dragPointerClientY = null
  state.draggedThumbIndex = null
  clearThumbDragVisualState()

  if (!hadDrag) return
  if (event) {
    event.preventDefault()
    event.stopPropagation()
  }
  state.suppressThumbClick = true

  if (!Number.isInteger(targetIndex)) return
  const tab = getTab()
  if (!tab) return
  const toInsertIndex = targetIndex + (insertAfter ? 1 : 0)
  await reorderPagesByDrag(tab, fromIndex, toInsertIndex)
}

function isManagedTempPdf(filePath) {
  if (!filePath) return false
  const normalized = filePath.replace(/\\/g, '/').toLowerCase()
  return normalized.includes('/pdfusion/') && normalized.endsWith('.pdf')
}

async function deleteManagedTempPdf(tab) {
  if (!tab || !isManagedTempPdf(tab.path)) return
  try {
    await window.pdfAPI.deleteFile({ targetPath: tab.path })
  } catch (e) {
    console.error('Delete temp PDF failed', e)
  }
}

function removeTab(id) {
  const idx = state.tabs.findIndex(t => t.id === id)
  if (idx === -1) return null

  const [removedTab] = state.tabs.splice(idx, 1)
  if (state.activeTab === id) {
    const next = state.tabs[idx] || state.tabs[idx - 1]
    setActiveTab(next ? next.id : null)
  } else {
    renderTabBar()
  }
  return removedTab
}

function showUnsavedCloseDialog(tab) {
  return new Promise(resolve => {
    confirmTitle.textContent = '保存更改'
    confirmMessage.textContent = `《${tab.title}》已被修改。选择“保存”将先选择保存位置并写出 PDF；选择“不保存”将关闭当前临时 PDF 并删除它。`
    showConfirmOverlay()

    const cleanup = (result) => {
      hideConfirmOverlay()
      confirmSaveBtn.removeEventListener('click', onSave)
      confirmCancelBtn.removeEventListener('click', onCancel)
      confirmDiscardBtn.removeEventListener('click', onDiscard)
      confirmCloseBtn.removeEventListener('click', onCancel)
      confirmOverlay.removeEventListener('click', onOverlayClick)
      document.removeEventListener('keydown', onKeydown)
      resolve(result)
    }

    const onSave = () => cleanup('save')
    const onCancel = () => cleanup('cancel')
    const onDiscard = () => cleanup('discard')
    const onOverlayClick = e => {
      if (e.target === confirmOverlay) cleanup('cancel')
    }
    const onKeydown = e => {
      if (e.key === 'Escape') cleanup('cancel')
    }

    confirmSaveBtn.addEventListener('click', onSave)
    confirmCancelBtn.addEventListener('click', onCancel)
    confirmDiscardBtn.addEventListener('click', onDiscard)
    confirmCloseBtn.addEventListener('click', onCancel)
    confirmOverlay.addEventListener('click', onOverlayClick)
    document.addEventListener('keydown', onKeydown)
  })
}

function createTab(path, originalPath = null, displayTitle = '') {
  const id = Date.now() + Math.random()
  const title = displayTitle || getBaseName(originalPath || path)
  const tab = { 
    id, path, title, pageCount: 1, page: 0, zoom: 1.0, liveZoom: 1.0, renderedZoom: 1.0, rotate: 0, pdfDoc: null,
    unsaved: false, originalPath: originalPath || path,
    searchQuery: '',
    searchResults: [],
    searchOpSeq: 0,
    searchStatus: '',
    undoStack: [],
  }
  state.tabs.push(tab)
  renderTabBar()
  setActiveTab(id)
  return tab
}

function closeTab(id) {
  const tab = state.tabs.find(t => t.id === id)
  if (tab && tab.unsaved) {
    return
  }
  removeTab(id)
}

async function requestCloseTab(id) {
  const tab = state.tabs.find(t => t.id === id)
  if (!tab) return

  if (tab.unsaved) {
    const action = await showUnsavedCloseDialog(tab)
    if (action === 'cancel') return
    if (action === 'save') {
      const saved = await saveCurrentPDF(tab)
      if (!saved) return
      const removedTab = removeTab(id)
      await clearTabUndoSnapshots(removedTab)
      await deleteManagedTempPdf(removedTab)
      return
    }
    const removedTab = removeTab(id)
    await clearTabUndoSnapshots(removedTab)
    await deleteManagedTempPdf(removedTab)
    return
  }

  const removedTab = removeTab(id)
  await clearTabUndoSnapshots(removedTab)
  await deleteManagedTempPdf(removedTab)
}

async function handleAppCloseRequest() {
  if (state.isAppCloseInProgress) return false
  state.isAppCloseInProgress = true
  try {
    const unsavedTabs = state.tabs.filter(tab => tab.unsaved)
    for (const pendingTab of unsavedTabs) {
      const tab = state.tabs.find(item => item.id === pendingTab.id)
      if (!tab || !tab.unsaved) continue

      setActiveTab(tab.id)
      const action = await showUnsavedCloseDialog(tab)
      if (action === 'cancel') return false
      if (action === 'save') {
        const saved = await saveCurrentPDF(tab)
        if (!saved) return false
      }
    }

    for (const tab of [...state.tabs]) {
      await clearTabUndoSnapshots(tab)
      await deleteManagedTempPdf(tab)
    }
    return true
  } catch (e) {
    console.error('Handle app close request failed', e)
    return false
  } finally {
    state.isAppCloseInProgress = false
  }
}

function setActiveTab(id) {
  clearZoomFinalizeTimer()
  clearScheduledPageRender()
  state.pendingViewportRenderForce = false
  state.activeTab = id
  state.selectedThumbIndexes = []
  state.lastSelectedThumbIndex = null
  state.pastedThumbIndexes = []
  state.pastedTabId = null
  renderTabBar()
  if (id) {
    emptyState.classList.add('hidden')
    workspace.classList.remove('hidden')
    renderAllPages()
    loadThumbnails()
  } else {
    emptyState.classList.remove('hidden')
    workspace.classList.add('hidden')
  }
  syncSearchUiForActiveTab()
  refreshOcrStatusForCurrentPanel()
}

function renderTabBar() {
  tabBar.querySelectorAll('.tab').forEach(el => el.remove())
  state.tabs.forEach(tab => {
    const el = document.createElement('div')
    el.className = 'tab' + (tab.id === state.activeTab ? ' active' : '')
    const closeBtnClass = 'close-btn' + (tab.unsaved ? ' unsaved' : '')
    el.innerHTML = `<span class="tab-title" title="${tab.title}">${tab.title}</span><span class="${closeBtnClass}" aria-label="Close tab">&times;</span>`
    el.addEventListener('click', async e => {
      if (e.target.classList.contains('close-btn')) { await requestCloseTab(tab.id); return }
      setActiveTab(tab.id)
    })
    tabBar.insertBefore(el, $('btn-open'))
  })
}


function showThumbContextMenu(e, pageIndex) {
  e.preventDefault()
  const tab = getTab()
  if (!tab) return
  if (!state.selectedThumbIndexes.includes(pageIndex)) {
    setSelectedThumbIndexes([pageIndex])
    state.lastSelectedThumbIndex = pageIndex
  }

  let menu = document.getElementById('thumb-context-menu')
  if (menu) menu.remove()

  menu = document.createElement('div')
  menu.id = 'thumb-context-menu'
  menu.style.cssText = `
    position: fixed; left: ${e.clientX}px; top: ${e.clientY}px;
    background: var(--bg3); border: 1px solid var(--border);
    border-radius: 6px; padding: 4px 0; z-index: 1000;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3); min-width: 120px;
  `

  const createMenuItem = (text, action, { closeOnClick = true, enabled = true } = {}) => {
    const item = document.createElement('div')
    item.textContent = text
    item.style.cssText = `
      padding: 6px 12px; cursor: pointer; font-size: 12px; color: var(--text);
    `
    if (!enabled) {
      item.style.opacity = '0.5'
      item.style.cursor = 'not-allowed'
    }
    item.addEventListener('mouseenter', () => {
      if (!enabled) return
      item.style.background = 'var(--accent)'
    })
    item.addEventListener('mouseleave', () => {
      item.style.background = 'transparent'
    })
    item.addEventListener('click', ev => {
      ev.stopPropagation()
      if (!enabled) return
      if (typeof action === 'function') action()
      if (closeOnClick) menu.remove()
    })
    return item
  }

  const selectedPages = state.selectedThumbIndexes.length > 0
    ? state.selectedThumbIndexes
    : [pageIndex]

  const hasSelectedPages = state.selectedThumbIndexes.length > 0
  const hasCurrentPageSelected = state.selectedThumbIndexes.includes(pageIndex)
  const totalPages = tab.pageCount || 0
  const isAllPagesSelected = totalPages > 0 && state.selectedThumbIndexes.length === totalPages

  const selectItem = createMenuItem('页面选择', null, { closeOnClick: false })
  selectItem.style.position = 'relative'
  selectItem.style.paddingRight = '24px'
  const selectArrow = document.createElement('span')
  selectArrow.textContent = '▶'
  selectArrow.style.cssText = 'position:absolute; right:8px; top:50%; transform:translateY(-50%); opacity:0.8;'
  selectItem.appendChild(selectArrow)

  const selectSubmenu = document.createElement('div')
  selectSubmenu.style.cssText = `
    display:none; position:absolute; left:100%; top:-4px; margin-left:4px;
    background: var(--bg3); border: 1px solid var(--border);
    border-radius: 6px; padding: 4px 0; min-width: 170px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3); z-index: 1001;
  `
  const createSelectSubmenuItem = (text, action, { enabled = true } = {}) => {
    const subItem = document.createElement('div')
    subItem.textContent = text
    subItem.style.cssText = 'padding: 6px 12px; cursor: pointer; font-size: 12px; color: var(--text);'
    if (!enabled) {
      subItem.style.opacity = '0.5'
      subItem.style.cursor = 'not-allowed'
    }
    subItem.addEventListener('mouseenter', () => {
      if (!enabled) return
      subItem.style.background = 'var(--accent)'
    })
    subItem.addEventListener('mouseleave', () => subItem.style.background = 'transparent')
    subItem.addEventListener('click', ev => {
      ev.stopPropagation()
      if (!enabled) return
      action()
      menu.remove()
    })
    return subItem
  }
  selectSubmenu.appendChild(createSelectSubmenuItem(
    isAllPagesSelected ? '取消选择所有页面' : '全选所有页面',
    () => {
      if (isAllPagesSelected) {
        clearSelectedThumbIndexes()
        return
      }
      const allPages = Array.from({ length: totalPages }, (_, idx) => idx)
      if (allPages.length === 0) return
      setSelectedThumbIndexes(allPages)
      state.lastSelectedThumbIndex = allPages[allPages.length - 1]
    },
    { enabled: totalPages > 0 }
  ))
  selectSubmenu.appendChild(createSelectSubmenuItem(
    hasCurrentPageSelected ? '取消选择当前页' : '选择当前页',
    () => {
      const selected = new Set(state.selectedThumbIndexes)
      if (selected.has(pageIndex)) selected.delete(pageIndex)
      else selected.add(pageIndex)
      const nextSelected = [...selected]
      if (nextSelected.length === 0) {
        clearSelectedThumbIndexes()
        return
      }
      setSelectedThumbIndexes(nextSelected)
      state.lastSelectedThumbIndex = pageIndex
    }
  ))
  selectSubmenu.appendChild(createSelectSubmenuItem(
    '取消选择所有页面',
    () => {
      clearSelectedThumbIndexes()
    },
    { enabled: hasSelectedPages }
  ))
  selectItem.appendChild(selectSubmenu)

  let hideSelectSubmenuTimer = null
  const showSelectSubmenu = () => {
    if (hideSelectSubmenuTimer) clearTimeout(hideSelectSubmenuTimer)
    selectSubmenu.style.display = 'block'
    selectItem.style.background = 'var(--accent)'
  }
  const hideSelectSubmenu = () => {
    if (hideSelectSubmenuTimer) clearTimeout(hideSelectSubmenuTimer)
    hideSelectSubmenuTimer = setTimeout(() => {
      selectSubmenu.style.display = 'none'
      selectItem.style.background = 'transparent'
    }, 120)
  }
  selectItem.addEventListener('mouseenter', showSelectSubmenu)
  selectItem.addEventListener('mouseleave', hideSelectSubmenu)
  selectSubmenu.addEventListener('mouseenter', showSelectSubmenu)
  selectSubmenu.addEventListener('mouseleave', hideSelectSubmenu)
  menu.appendChild(selectItem)

  menu.appendChild(createMenuItem(selectedPages.length > 1 ? '复制所选页面' : '复制页面', () => {
    state.clipboardPages = [...selectedPages]
    state.clipboardTabId = tab.id
    updateThumbSelectionStates()
    console.log(`Copied pages ${selectedPages.map(page => page + 1).join(', ')}`)
  }))

  menu.appendChild(createMenuItem(
    selectedPages.length > 1 ? '拆分所选为新PDF' : '拆分当前页为新PDF',
    () => { splitPagesToNewPdf(tab, selectedPages) }
  ))

  const canPaste = state.clipboardPages.length > 0
  const pasteItem = createMenuItem('粘贴到当前页', null, { closeOnClick: false })
  pasteItem.style.position = 'relative'
  pasteItem.style.paddingRight = '24px'
  const pasteArrow = document.createElement('span')
  pasteArrow.textContent = '▶'
  pasteArrow.style.cssText = 'position:absolute; right:8px; top:50%; transform:translateY(-50%); opacity:0.8;'
  pasteItem.appendChild(pasteArrow)

  const pasteSubmenu = document.createElement('div')
  pasteSubmenu.style.cssText = `
    display:none; position:absolute; left:100%; top:-4px; margin-left:4px;
    background: var(--bg3); border: 1px solid var(--border);
    border-radius: 6px; padding: 4px 0; min-width: 130px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3); z-index: 1001;
  `
  const createSubmenuItem = (text, action) => {
    const subItem = document.createElement('div')
    subItem.textContent = text
    subItem.style.cssText = 'padding: 6px 12px; cursor: pointer; font-size: 12px; color: var(--text);'
    subItem.addEventListener('mouseenter', () => subItem.style.background = 'var(--accent)')
    subItem.addEventListener('mouseleave', () => subItem.style.background = 'transparent')
    subItem.addEventListener('click', ev => {
      ev.stopPropagation()
      action()
      menu.remove()
    })
    return subItem
  }
  pasteSubmenu.appendChild(createSubmenuItem('粘贴到当前页前', () => {
    insertPage(tab, pageIndex, state.clipboardPages, state.clipboardTabId)
  }))
  pasteSubmenu.appendChild(createSubmenuItem('粘贴到当前页后', () => {
    insertPage(tab, pageIndex + 1, state.clipboardPages, state.clipboardTabId)
  }))
  pasteItem.appendChild(pasteSubmenu)

  let hidePasteSubmenuTimer = null
  const showPasteSubmenu = () => {
    if (!canPaste) return
    if (hidePasteSubmenuTimer) clearTimeout(hidePasteSubmenuTimer)
    pasteSubmenu.style.display = 'block'
    pasteItem.style.background = 'var(--accent)'
  }
  const hidePasteSubmenu = () => {
    if (hidePasteSubmenuTimer) clearTimeout(hidePasteSubmenuTimer)
    hidePasteSubmenuTimer = setTimeout(() => {
      pasteSubmenu.style.display = 'none'
      pasteItem.style.background = 'transparent'
    }, 120)
  }
  pasteItem.addEventListener('mouseenter', showPasteSubmenu)
  pasteItem.addEventListener('mouseleave', hidePasteSubmenu)
  pasteSubmenu.addEventListener('mouseenter', showPasteSubmenu)
  pasteSubmenu.addEventListener('mouseleave', hidePasteSubmenu)

  if (!canPaste) {
    pasteItem.style.opacity = '0.5'
    pasteItem.style.pointerEvents = 'none'
    pasteArrow.style.display = 'none'
  }
  menu.appendChild(pasteItem)

  menu.appendChild(createMenuItem('删除页面', async () => {
    if (tab.pageCount <= 1) return
    await deletePage(tab, pageIndex)
  }))

  document.body.appendChild(menu)

  const closeMenu = (ev) => {
    if (!menu.contains(ev.target)) {
      menu.remove()
      document.removeEventListener('click', closeMenu)
    }
  }
  setTimeout(() => document.addEventListener('click', closeMenu), 10)
}

async function insertPage(tab, insertAt, srcPageIndexes, srcTabId) {
  const srcTab = state.tabs.find(t => t.id === srcTabId)
  const srcPath = srcTab ? srcTab.path : tab.path
  const srcPages = Array.isArray(srcPageIndexes) ? srcPageIndexes : [srcPageIndexes]

  try {
    await pushUndoSnapshot(tab, 'insert_page')
    await window.pdfAPI.pageOps({
      action: 'cross_copy',
      paths: [tab.path],
      out_path: tab.path,
      src_path: srcPath,
      src_pages: srcPages,
      insert_at: insertAt
    })

    tab.unsaved = true
    state.pastedThumbIndexes = srcPages.map((_, offset) => insertAt + offset)
    state.pastedTabId = tab.id
    renderTabBar()
    
    tab.pdfDoc = null
    const info = await window.pdfAPI.getInfo({ path: tab.path })
    tab.pageCount = info.page_count
    pageTotal.textContent = `/ ${tab.pageCount}`
    await loadPDFDocument(tab.path)
    setSelectedThumbIndexes(state.pastedThumbIndexes)
  } catch (e) {
    console.error('Insert page failed', e)
    alert('插入页面失败：' + (e.message || e))
  }
}

async function reorderPagesByDrag(tab, fromIndex, toInsertIndex) {
  if (!tab || state.isReorderingThumbs) return
  const pageCountBefore = tab.pageCount
  const newOrder = buildReorderPlan(pageCountBefore, fromIndex, toInsertIndex)
  if (!newOrder) return
  const isNoop = newOrder.every((oldIndex, newIndex) => oldIndex === newIndex)
  if (isNoop) return

  state.isReorderingThumbs = true
  try {
    await pushUndoSnapshot(tab, 'reorder_pages')
    await window.pdfAPI.pageOps({
      action: 'reorder',
      paths: [tab.path],
      out_path: tab.path,
      new_order: newOrder,
    })

    tab.unsaved = true
    renderTabBar()

    state.selectedThumbIndexes = remapIndexesAfterReorder(
      state.selectedThumbIndexes,
      newOrder,
      pageCountBefore
    )
    state.lastSelectedThumbIndex = remapIndexAfterReorder(
      state.lastSelectedThumbIndex,
      newOrder,
      pageCountBefore
    )
    if (state.pastedTabId === tab.id) {
      state.pastedThumbIndexes = remapIndexesAfterReorder(
        state.pastedThumbIndexes,
        newOrder,
        pageCountBefore
      )
      if (state.pastedThumbIndexes.length === 0) state.pastedTabId = null
    }
    const remappedPage = remapIndexAfterReorder(tab.page || 0, newOrder, pageCountBefore)
    tab.page = Number.isInteger(remappedPage) ? remappedPage : 0

    tab.pdfDoc = null
    const info = await window.pdfAPI.getInfo({ path: tab.path })
    tab.pageCount = info.page_count
    pageTotal.textContent = `/ ${tab.pageCount}`
    await loadPDFDocument(tab.path)
    setSelectedThumbIndexes(state.selectedThumbIndexes)
    scrollToPage(tab.page || 0)
  } catch (e) {
    console.error('Reorder pages failed', e)
    alert('调整页面顺序失败：' + (e.message || e))
  } finally {
    state.isReorderingThumbs = false
  }
}

async function deletePage(tab, pageIndex) {
  if (tab.pageCount <= 1) return

  try {
    const pageCountBefore = tab.pageCount
    await pushUndoSnapshot(tab, 'delete_page')
    await window.pdfAPI.pageOps({
      action: 'delete_pages',
      paths: [tab.path],
      out_path: tab.path,
      pages: [pageIndex]
    })

    syncPastedIndexesAfterDelete(tab.id, [pageIndex], pageCountBefore)
    tab.unsaved = true
    renderTabBar()
    
    tab.pdfDoc = null
    if (tab.page >= tab.pageCount - 1) tab.page = Math.max(0, tab.pageCount - 2)
    const info = await window.pdfAPI.getInfo({ path: tab.path })
    tab.pageCount = info.page_count
    pageTotal.textContent = `/ ${tab.pageCount}`
    await loadPDFDocument(tab.path)
  } catch (e) {
    console.error('Delete page failed', e)
  }
}

async function splitPagesToNewPdf(tab, pageIndexes) {
  const pages = sortPageIndexes(pageIndexes || [])
  if (!tab || pages.length === 0) return

  const sourceForHint = tab.originalPath || tab.title
  const suggestedPath = /\.pdf$/i.test(sourceForHint)
    ? sourceForHint.replace(/\.pdf$/i, `_split_${pages.length}p.pdf`)
    : `${sourceForHint}_split_${pages.length}p.pdf`

  try {
    setLoadingProgress({
      title: '正在拆分页面',
      detail: `正在生成包含 ${pages.length} 页的新PDF…`,
      stage: '拆分PDF',
      progress: 26,
    })

    const tempOutPath = await window.pdfAPI.createTemp({ srcPath: tab.path })

    await window.pdfAPI.pageOps({
      action: 'extract_pages',
      paths: [tab.path],
      out_path: tempOutPath,
      pages,
    })

    setLoadingProgress({
      title: '拆分完成',
      detail: '正在打开拆分后的新文档…',
      stage: '打开文档',
      progress: 82,
    })

    await openPreparedPdf(tempOutPath, suggestedPath, {
      markUnsaved: true,
      progress: 92,
    })
  } catch (e) {
    console.error('Split pages failed', e)
    alert('拆分页面失败: ' + (e.message || e))
  } finally {
    setTimeout(hideLoadingProgress, 180)
  }
}

async function normalizeAllPagesByMaxSize(mode) {
  const tab = getTab()
  if (!tab) return

  const isByWidth = mode === 'max_width'
  const modeLabel = isByWidth ? '最大页宽' : '最大页高'
  const action = isByWidth ? 'normalize_size_by_max_width' : 'normalize_size_by_max_height'
  const confirmed = confirm(`将按${modeLabel}对当前文档所有页面做等比例缩放。\n此操作会修改当前文档（可另存为）。\n是否继续？`)
  if (!confirmed) return

  try {
    await pushUndoSnapshot(tab, 'normalize_pages')
    setLoadingProgress({
      title: '正在统一页面尺寸',
      detail: `正在按${modeLabel}等比调整所有页面...`,
      stage: '处理中',
      progress: 18,
    })

    await window.pdfAPI.pageOps({
      action,
      paths: [tab.path],
      out_path: tab.path,
    })

    tab.unsaved = true
    renderTabBar()

    tab.pdfDoc = null
    const info = await window.pdfAPI.getInfo({ path: tab.path })
    tab.pageCount = info.page_count
    if ((tab.page || 0) >= tab.pageCount) tab.page = Math.max(0, tab.pageCount - 1)
    pageTotal.textContent = `/ ${tab.pageCount}`
    await loadPDFDocument(tab.path)

    setLoadingProgress({
      title: '页面尺寸已统一',
      detail: `已按${modeLabel}完成全部页面等比缩放。`,
      stage: '完成',
      progress: 100,
    })
  } catch (e) {
    console.error('Normalize page size failed', e)
    alert('统一页面尺寸失败：' + (e.message || e))
  } finally {
    setTimeout(hideLoadingProgress, 180)
  }
}

async function loadThumb(tab, i) {
  const thumbRenderToken = state.thumbRenderToken
  const item = thumbList.children[i]
  if (!item || state.thumbsLoaded[i]) return
  
  try {
    if (thumbRenderToken !== state.thumbRenderToken) return
    // Use backend rendering for thumbnails so concurrent requests can run in
    // server thread-pool (true multi-threaded rendering off the UI thread).
    const res = await window.pdfAPI.render({ path: tab.path, page: i, dpi: 48, rotate: tab.rotate })
    if (thumbRenderToken !== state.thumbRenderToken) return
    const placeholder = item.querySelector('.thumb-placeholder')
    if (placeholder) {
      const img = document.createElement('img')
      img.src = res.image
      img.style.width = '100%'
      img.style.height = '120px'
      img.style.objectFit = 'contain'
      img.style.display = 'block'
      img.style.opacity = '0'
      img.style.transition = 'opacity 0.18s ease'
      img.addEventListener('load', () => {
        requestAnimationFrame(() => {
          img.style.opacity = '1'
        })
      }, { once: true })
      placeholder.replaceChildren(img)
    }
    state.thumbsLoaded[i] = true
  } catch (e) {
    console.error('loadThumb failed', i, e)
  }
}

// 閳光偓閳光偓 Right panel tabs 閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓
function parseCssDurationToMs(rawValue, fallbackMs = 220) {
  const value = String(rawValue || '').trim().toLowerCase()
  if (!value) return fallbackMs
  if (value.endsWith('ms')) {
    const num = Number(value.slice(0, -2))
    return Number.isFinite(num) && num >= 0 ? num : fallbackMs
  }
  if (value.endsWith('s')) {
    const num = Number(value.slice(0, -1))
    return Number.isFinite(num) && num >= 0 ? Math.round(num * 1000) : fallbackMs
  }
  const direct = Number(value)
  return Number.isFinite(direct) && direct >= 0 ? direct : fallbackMs
}

function getPanelSwitchDurationMs() {
  const rootStyle = getComputedStyle(document.documentElement)
  return parseCssDurationToMs(rootStyle.getPropertyValue('--panel-switch-duration'), 220)
}

function setActiveRightPanel(panel) {
  const targetPanel = panel || 'convert'
  const targetPanelEl = $(`panel-${targetPanel}`)
  if (!targetPanelEl) return

  const isSamePanel = state.activeRightPanel === targetPanel
  const isPanelAlreadyVisible = targetPanelEl.classList.contains('panel-active')
  const panelSwitchDurationMs = getPanelSwitchDurationMs()
  state.activeRightPanel = targetPanel

  document.querySelectorAll('.rt-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.panel === targetPanel)
  })

  if (isSamePanel && isPanelAlreadyVisible) {
    if (targetPanel === 'ocr') {
      refreshOcrStatus()
    }
    if (targetPanel !== 'search-results') {
      state.lastNonSearchPanel = targetPanel
    }
    return
  }

  const panels = Array.from(document.querySelectorAll('#right-content > div'))
  panels.forEach(panelEl => {
    if (panelEl._panelHideTimer) {
      clearTimeout(panelEl._panelHideTimer)
      panelEl._panelHideTimer = null
    }

    if (panelEl === targetPanelEl) {
      panelEl.classList.remove('panel-fade-out')
      panelEl.classList.add('panel-active')
      return
    }

    if (!panelEl.classList.contains('panel-active') && !panelEl.classList.contains('panel-fade-out')) {
      return
    }

    panelEl.classList.remove('panel-active')
    panelEl.classList.add('panel-fade-out')
    panelEl._panelHideTimer = setTimeout(() => {
      panelEl.classList.remove('panel-fade-out')
      panelEl._panelHideTimer = null
    }, panelSwitchDurationMs)
  })

  if (targetPanel === 'ocr') {
    refreshOcrStatus()
  }
  if (targetPanel !== 'search-results') {
    state.lastNonSearchPanel = targetPanel
  }
}

document.querySelectorAll('.rt-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    setActiveRightPanel(tab.dataset.panel)
  })
})

setActiveRightPanel(document.querySelector('.rt-tab.active')?.dataset?.panel || 'convert')

// 閳光偓閳光偓 Conversion 閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓
function exportFormatNeedsDpi(fmt) {
  return fmt === 'jpg_single' || fmt === 'jpg_long'
}

function validateLongImageExport(tab, fmt) {
  if (fmt !== 'jpg_long') return
  if ((tab.pageCount || 0) > 5) {
    throw new Error('长图导出仅支持页数小于等于 5 页的 PDF')
  }
}

function updateConvertOptions() {
  const dpiSelect = $('cv-dpi')
  if (!dpiSelect) return
  const fmt = $('cv-fmt').value
  const showDpi = exportFormatNeedsDpi(fmt)
  dpiSelect.classList.toggle('hidden', !showDpi)
  const dpiWrap = dpiSelect._uiSelectWrap || dpiSelect.parentElement
  const dpiLabel = dpiWrap ? dpiWrap.previousElementSibling : dpiSelect.previousElementSibling
  if (dpiLabel) {
    dpiLabel.classList.toggle('hidden', !showDpi)
  }
  const formulaOptions = $('formula-options')
  if (formulaOptions) {
    formulaOptions.classList.toggle('hidden', fmt !== 'docx')
    if (fmt === 'docx') setFormulaStatus('公式将以截图保留，不调用识别模型')
  }
}

function stopConvertProgressPolling() {
  if (!state.convertProgressTimer) return
  clearTimeout(state.convertProgressTimer)
  state.convertProgressTimer = null
}

function setConvertActionEnabled(enabled) {
  const btn = $('btn-convert')
  if (btn) btn.disabled = !enabled
}

function hideConvertProgress() {
  const wrap = $('convert-progress')
  const fill = $('convert-progress-fill')
  const label = $('convert-progress-label')
  const percent = $('convert-progress-percent')
  if (wrap) {
    wrap.classList.add('hidden')
    wrap.setAttribute('aria-hidden', 'true')
  }
  if (fill) fill.style.width = '0%'
  if (label) label.textContent = '准备转换'
  if (percent) percent.textContent = '0%'
  state.convertProgressValue = 0
}

function showConvertProgress(progress, label) {
  const wrap = $('convert-progress')
  const fill = $('convert-progress-fill')
  const labelEl = $('convert-progress-label')
  const percent = $('convert-progress-percent')
  const nextValue = Math.max(state.convertProgressValue, Math.max(0, Math.min(100, Math.round(progress))))
  state.convertProgressValue = nextValue
  if (wrap) {
    wrap.classList.remove('hidden')
    wrap.setAttribute('aria-hidden', 'false')
  }
  if (fill) fill.style.width = `${nextValue}%`
  if (labelEl) labelEl.textContent = label || '正在转换'
  if (percent) percent.textContent = `${nextValue}%`
}

async function pollConvertProgress(taskId) {
  if (!taskId || state.convertProgressTaskId !== taskId || !state.convertInProgress) return
  try {
    const status = await window.pdfAPI.convertStatus(taskId)
    if (state.convertProgressTaskId !== taskId || !state.convertInProgress) return
    const statusText = String(status?.status || '')
    const stage = String(status?.stage || '正在转换')
    const rawProgress = Number(status?.progress)
    if (Number.isFinite(rawProgress)) {
      showConvertProgress(rawProgress, stage)
    }
    if (statusText === 'done') {
      showConvertProgress(100, '转换完成')
      return
    }
    if (statusText === 'error') {
      return
    }
  } catch (_) {
    if (state.convertProgressTaskId !== taskId || !state.convertInProgress) return
    const nextValue = state.convertProgressValue < 92
      ? state.convertProgressValue + Math.max(1, Math.round((92 - state.convertProgressValue) * 0.08))
      : state.convertProgressValue
    showConvertProgress(nextValue, '正在转换')
  }
  if (state.convertProgressTaskId !== taskId || !state.convertInProgress) return
  stopConvertProgressPolling()
  state.convertProgressTimer = setTimeout(() => {
    pollConvertProgress(taskId)
  }, 700)
}

$('btn-convert').addEventListener('click', async () => {
  const tab = getTab(); if (!tab) return
  if (state.convertInProgress) return
  const fmt = $('cv-fmt').value
  try {
    validateLongImageExport(tab, fmt)
  } catch (e) {
    $('cv-status').textContent = '转换失败：' + (e.message || e)
    return
  }
  const ext = fmt === 'txt' ? 'txt' : fmt === 'html' ? 'html' : fmt === 'markdown' ? 'md' : fmt === 'docx' ? 'docx' : 'jpg'
  const filters = fmt === 'txt'
    ? [{ name: 'Text', extensions: ['txt'] }]
    : fmt === 'html'
      ? [{ name: 'HTML', extensions: ['html'] }]
      : fmt === 'markdown'
        ? [{ name: 'Markdown', extensions: ['md'] }]
        : fmt === 'docx'
          ? [{ name: 'Word Document', extensions: ['docx'] }]
          : fmt === 'jpg_long'
            ? [{ name: 'JPEG', extensions: ['jpg'] }]
            : null

  let outPath
  if (filters) {
    outPath = await window.pdfAPI.saveFile({ defaultPath: tab.title.replace('.pdf', `.${ext}`), filters })
  } else {
    outPath = await window.pdfAPI.openDir()
  }
  if (!outPath) return

  const taskId = `convert_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
  $('cv-status').textContent = '正在转换...'
  state.convertInProgress = true
  state.convertProgressTaskId = taskId
  state.convertProgressValue = 0
  setConvertActionEnabled(false)
  showConvertProgress(2, '正在准备转换')
  stopConvertProgressPolling()
  pollConvertProgress(taskId)
  try {
    const payload = { path: tab.path, out_path: outPath, fmt, task_id: taskId }
    if (exportFormatNeedsDpi(fmt)) {
      payload.dpi = parseInt($('cv-dpi').value, 10)
    }
    if (fmt === 'docx') {
      payload.formula_ocr = false
      setFormulaStatus('正在以截图保留公式')
    }
    const res = await window.pdfAPI.convert(payload)
    stopConvertProgressPolling()
    showConvertProgress(100, '转换完成')
    $('cv-status').textContent = `已完成：${res.out_path || outPath}`
  } catch (e) {
    stopConvertProgressPolling()
    $('cv-status').textContent = '转换失败：' + (e.message || e)
  } finally {
    state.convertInProgress = false
    state.convertProgressTaskId = null
    setConvertActionEnabled(true)
    setTimeout(() => {
      if (!state.convertInProgress) hideConvertProgress()
    }, 1200)
  }
})

$('cv-fmt').addEventListener('change', updateConvertOptions)

function clampInt(value, fallback, min, max) {
  const parsed = parseInt(value, 10)
  const safe = Number.isFinite(parsed) ? parsed : fallback
  return Math.max(min, Math.min(max, safe))
}

function formatFileSize(bytes) {
  const n = Number(bytes)
  if (!Number.isFinite(n) || n < 0) return ''
  if (n < 1024) return `${Math.round(n)} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(2)} MB`
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function buildCompressSummary(res) {
  const originalSize = Number(res?.original_size)
  const compressedSize = Number(res?.compressed_size)
  let ratio = Number(res?.ratio)

  if (Number.isFinite(originalSize) && originalSize > 0 && Number.isFinite(compressedSize) && compressedSize >= 0) {
    ratio = compressedSize / originalSize
  }

  const lines = []
  if (Number.isFinite(originalSize) && originalSize >= 0) {
    lines.push(`原始大小：${formatFileSize(originalSize)}`)
  }
  if (Number.isFinite(compressedSize) && compressedSize >= 0) {
    lines.push(`结果大小：${formatFileSize(compressedSize)}`)
  }
  if (Number.isFinite(ratio) && ratio >= 0) {
    const reduction = (1 - ratio) * 100
    if (reduction >= 0) {
      lines.push(`压缩率：${reduction.toFixed(2)}%`)
    } else {
      lines.push(`体积变化：+${Math.abs(reduction).toFixed(2)}%`)
    }
  }

  return { lines, ratio }
}

function getCompressParams({ commit = false } = {}) {
  const rawMode = $('cp-mode')?.value || 'compress'
  const mode = rawMode === 'high'
    ? 'compress'
    : rawMode === 'low'
      ? 'rasterize'
      : rawMode
  const qualityInput = $('cp-quality')
  const dpiInput = $('cp-dpi')
  const quality = clampInt(qualityInput?.value, 60, 10, 95)
  const dpi = clampInt(dpiInput?.value, 120, 72, 300)

  if (commit) {
    if (qualityInput) qualityInput.value = String(quality)
    if (dpiInput) dpiInput.value = String(dpi)
  }

  return { mode, quality, dpi }
}

function updateCompressHint() {
  const { mode, quality, dpi } = getCompressParams()
  const qualityInput = $('cp-quality')
  const dpiInput = $('cp-dpi')
  const hint = $('cp-hint')
  if (!qualityInput || !dpiInput || !hint) return

  if (mode === 'compress') {
    qualityInput.disabled = false
    dpiInput.disabled = true
    hint.textContent = `压缩：保留文字/矢量内容，仅重压内部图片。当前 JPEG 质量=${quality}。`
  } else {
    qualityInput.disabled = true
    dpiInput.disabled = false
    hint.textContent = `转页面为图片：整页转为图片，按 DPI=${dpi} 处理。该模式下文件可能比原 PDF 更大。`
  }
}

$('cp-mode').addEventListener('change', updateCompressHint)
$('cp-quality').addEventListener('input', updateCompressHint)
$('cp-dpi').addEventListener('input', updateCompressHint)
$('cp-quality').addEventListener('blur', () => { getCompressParams({ commit: true }); updateCompressHint() })
$('cp-dpi').addEventListener('blur', () => { getCompressParams({ commit: true }); updateCompressHint() })
updateCompressHint()

// Compression 閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓
$('btn-compress').addEventListener('click', async () => {
  const tab = getTab(); if (!tab) return
  const outPath = await window.pdfAPI.saveFile({ defaultPath: tab.title.replace('.pdf', '_compressed.pdf'), filters: [{ name: 'PDF', extensions: ['pdf'] }] })
  if (!outPath) return

  let currentMode = 'compress'
  try {
    const { mode, quality, dpi } = getCompressParams({ commit: true })
    currentMode = mode
    $('cp-status').textContent = mode === 'compress' ? '正在压缩...' : '正在转页面为图片...'
    const payload = {
      path: tab.path,
      out_path: outPath,
      mode,
    }
    if (mode === 'compress') payload.quality = quality
    else payload.dpi = dpi

    const res = await window.pdfAPI.compress(payload)
    const { lines: summaryLines, ratio } = buildCompressSummary(res)
    const donePrefix = mode === 'compress' ? '压缩完成' : '转换完成'
    const statusLines = [
      donePrefix,
      `输出文件：${res.out_path || outPath}`,
      ...summaryLines,
    ]
    if (mode === 'rasterize' && Number.isFinite(Number(res.used_dpi))) {
      statusLines.push(`实际 DPI：${res.used_dpi}`)
    }
    if (mode === 'rasterize' && Number.isFinite(ratio) && ratio > 1) {
      statusLines.push('提示：该模式下文件可能比原 PDF 更大')
    }
    if (res.message) statusLines.push(`提示：${res.message}`)
    $('cp-status').textContent = statusLines.join('\n')
  } catch (e) {
    const failPrefix = currentMode === 'compress' ? '压缩失败：' : '转换失败：'
    $('cp-status').textContent = failPrefix + (e.message || e)
  }
})

// OCR
function setOcrStatus(message) {
  if ($('ocr-status')) $('ocr-status').textContent = message
}

function refreshOcrStatusForCurrentPanel() {
  const activePanel = document.querySelector('.rt-tab.active')?.dataset?.panel
  if (activePanel !== 'ocr') return
  // Keep progress/status polling as source of truth during active OCR jobs.
  if (state.ocrApplyPollTimer) return
  refreshOcrStatus()
}

function setOcrActionButtonsEnabled(enabled, { applying = state.ocrApplyInProgress } = {}) {
  const downloadBtn = $('btn-ocr-download')
  const runBtn = $('btn-ocr-run')
  if (downloadBtn) downloadBtn.disabled = !enabled || applying
  if (runBtn) runBtn.disabled = !enabled || applying
}

function normalizeOcrRuntimeMessage(errorText) {
  const text = String(errorText || '')
  if (/likely\s+slim|no module named ['"]cv2['"]|opencv-python-headless/i.test(text)) {
    return '当前安装包未包含 OCR 运行库（Slim 版）。请安装 Full 版后使用 OCR。'
  }
  return text
}

function simplifyOcrError(error) {
  const text = String(error || '').replace(/\s+/g, ' ').trim()
  if (!text) return ''
  const hintIdx = text.indexOf('| hint:')
  if (hintIdx >= 0) {
    return text.slice(hintIdx + 7).trim()
  }
  const primary = text.split('| caused by:')[0].trim()
  return primary.length > 220 ? primary.slice(0, 220) + '...' : primary
}

function isOcrCancelError(error) {
  const text = String(error?.message || error || '').toLowerCase()
  return /ocr apply cancelled by user|ocr 已取消|用户取消|已取消/.test(text)
}

function getLoadingProgressValue() {
  const text = String(loadingPercent?.textContent || '').trim()
  const value = Number.parseInt(text, 10)
  return Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0
}

function buildOcrLoadingAction({ cancelRequested = state.ocrCancelRequested } = {}) {
  if (!state.ocrApplyInProgress || !window.pdfAPI?.ocrCancel) return null
  return {
    label: cancelRequested ? '正在取消...' : '取消 OCR',
    disabled: !!cancelRequested,
    onClick: requestOcrCancel,
  }
}

async function requestOcrCancel() {
  if (!window.pdfAPI?.ocrCancel) return
  if (!state.ocrApplyInProgress || state.ocrCancelRequested) return
  state.ocrCancelRequested = true
  setOcrStatus('正在取消 OCR，请稍候...')
  setLoadingProgress({
    title: '正在取消 OCR',
    detail: '已发送取消请求，等待当前页识别结束...',
    stage: '取消中',
    progress: Math.max(18, getLoadingProgressValue()),
    action: buildOcrLoadingAction({ cancelRequested: true }),
  })
  try {
    await window.pdfAPI.ocrCancel()
  } catch (e) {
    state.ocrCancelRequested = false
    setOcrStatus('取消 OCR 失败：' + (e.message || e))
    setLoadingProgress({
      title: '正在执行 OCR',
      detail: '取消请求失败，可重试取消。',
      stage: 'OCR 处理中',
      progress: Math.max(18, getLoadingProgressValue()),
      action: buildOcrLoadingAction({ cancelRequested: false }),
    })
    await refreshOcrStatus()
  }
}

function stopOcrStatusPolling() {
  if (!state.ocrStatusPollTimer) return
  clearInterval(state.ocrStatusPollTimer)
  state.ocrStatusPollTimer = null
}

function stopOcrApplyPolling() {
  if (!state.ocrApplyPollTimer) return
  clearInterval(state.ocrApplyPollTimer)
  state.ocrApplyPollTimer = null
}

function updateOcrApplyProgress(status) {
  const applyWorkers = Math.max(1, Number(status?.apply_workers) || 1)
  const totalPages = Math.max(0, Number(status?.apply_total_pages) || 0)
  const donePages = Math.max(0, Number(status?.apply_done_pages) || 0)
  const currentPage = Math.max(0, Number(status?.apply_current_page) || 0)
  const recognizedBlocks = Math.max(0, Number(status?.apply_recognized_blocks) || 0)
  const cancelRequested = !!status?.apply_cancel_requested
  const isCancelling = cancelRequested || state.ocrCancelRequested
  if (totalPages <= 0) return

  const safeDone = Math.min(totalPages, donePages)
  const currentStep = Math.min(totalPages, Math.max(1, safeDone + (safeDone < totalPages ? 1 : 0)))
  const progress = 18 + Math.round((safeDone / totalPages) * 76)
  const isParallel = applyWorkers > 1
  if (cancelRequested) state.ocrCancelRequested = true
  const detailText = isCancelling
    ? `正在取消 OCR：已完成 ${safeDone}/${totalPages} 页，请稍候...`
    : isParallel
      ? `并行识别中：已完成 ${safeDone}/${totalPages} 页（并行进程=${applyWorkers}）`
      : `正在识别第 ${currentStep}/${totalPages} 页（PDF 第 ${currentPage || currentStep} 页）`
  setLoadingProgress({
    title: isCancelling ? '正在取消 OCR' : '正在执行 OCR',
    detail: detailText,
    stage: isCancelling ? '取消中' : 'OCR 处理中',
    progress: Math.min(96, progress),
    action: buildOcrLoadingAction({ cancelRequested: isCancelling }),
  })
  if (isCancelling) {
    setOcrStatus(`正在取消 OCR：已完成 ${safeDone}/${totalPages} 页，请稍候...`)
  } else if (isParallel) {
    setOcrStatus(`OCR 并行识别中：已完成 ${safeDone}/${totalPages} 页，累计 ${recognizedBlocks} 段`)
  } else {
    setOcrStatus(`OCR 识别中：第 ${currentStep}/${totalPages} 页，累计 ${recognizedBlocks} 段`)
  }
}

function startOcrApplyPolling() {
  stopOcrApplyPolling()
  if (!window.pdfAPI?.ocrStatus) return
  state.ocrApplyPollTimer = setInterval(async () => {
    try {
      const status = await window.pdfAPI.ocrStatus()
      if (status?.applying) {
        state.ocrApplyInProgress = true
        setOcrActionButtonsEnabled(true, { applying: true })
        updateOcrApplyProgress(status)
      }
    } catch (_) {}
  }, 300)
}

function startOcrStatusPolling() {
  stopOcrStatusPolling()
  state.ocrStatusPollTimer = setInterval(async () => {
    const ready = await refreshOcrStatus()
    if (ready || !state.ocrDownloadInProgress) {
      stopOcrStatusPolling()
    }
  }, 1200)
}

function setFormulaStatus(message) {
  if ($('formula-status')) $('formula-status').textContent = message
}

async function refreshOcrStatus() {
  if (!window.pdfAPI.ocrStatus) return false
  try {
    const status = await window.pdfAPI.ocrStatus()
    const runtimeReady = !!status.runtime_ready
    const modelsReady = !!status.models_ready
    const downloading = !!status.downloading
    const applying = !!status.applying
    const cancelRequested = !!status.apply_cancel_requested
    const error = status.error || ''
    const message = status.message || ''
    state.ocrApplyInProgress = applying
    if (!applying) {
      state.ocrCancelRequested = false
    } else if (cancelRequested) {
      state.ocrCancelRequested = true
    }

    if (!runtimeReady) {
      state.ocrDownloadInProgress = false
      setOcrActionButtonsEnabled(false, { applying: false })
      setOcrStatus(normalizeOcrRuntimeMessage(error) || 'OCR 运行库未就绪，请先安装 paddleocr / paddlepaddle')
      return false
    }
    if (applying) {
      setOcrActionButtonsEnabled(true, { applying: true })
      if (cancelRequested) {
        setOcrStatus(message || '正在取消 OCR，请稍候...')
      } else {
        setOcrStatus(message || 'OCR 识别中...')
      }
      return false
    }

    setOcrActionButtonsEnabled(true, { applying: false })
    if (downloading) {
      setOcrStatus(message || '模型下载中...')
      return false
    }
    if (modelsReady) {
      state.ocrDownloadInProgress = false
      setOcrStatus('OCR 模型已就绪')
      return true
    }
    state.ocrDownloadInProgress = false
    if (error) {
      const detail = simplifyOcrError(error)
      setOcrStatus((message || 'OCR 模型未下载') + (detail ? `：${detail}` : ''))
      return false
    }
    setOcrStatus(message || 'OCR 模型未下载')
    return false
  } catch (e) {
    state.ocrDownloadInProgress = false
    setOcrStatus('OCR 状态获取失败：' + (e.message || e))
    return false
  }
}

$('btn-ocr-download').addEventListener('click', async () => {
  if (!window.pdfAPI.ocrDownload) return
  if ($('btn-ocr-download')?.disabled) {
    await refreshOcrStatus()
    return
  }
  setOcrStatus('正在请求下载 OCR 模型...')
  state.ocrDownloadInProgress = true
  try {
    await window.pdfAPI.ocrDownload({ lang: $('ocr-lang').value || 'ch' })
    startOcrStatusPolling()
    await refreshOcrStatus()
  } catch (e) {
    state.ocrDownloadInProgress = false
    stopOcrStatusPolling()
    setOcrStatus('模型下载启动失败：' + (e.message || e))
    await refreshOcrStatus()
  }
})

$('btn-ocr-run').addEventListener('click', async () => {
  const tab = getTab()
  if (!tab) return
  if (!window.pdfAPI.ocrApply) return
  if ($('btn-ocr-run')?.disabled) {
    await refreshOcrStatus()
    return
  }

  const currentPage = getNearestPageIndexInViewport(tab)
  const dpi = parseInt($('ocr-dpi').value, 10) || 200
  const lang = ($('ocr-lang').value || 'ch').trim() || 'ch'
  let shouldRefreshStatusOnError = false

  state.ocrCancelRequested = false
  const undoSnapshotPushed = await pushUndoSnapshot(tab, 'ocr_apply')
  state.ocrApplyInProgress = true
  setOcrActionButtonsEnabled(true, { applying: true })
  setLoadingProgress({
    title: '正在执行 OCR',
    detail: '正在准备 OCR 引擎并开始逐页识别，请稍候...',
    stage: 'OCR 处理中',
    progress: 18,
    action: buildOcrLoadingAction({ cancelRequested: false }),
  })
  setOcrStatus('OCR 识别中...')
  startOcrApplyPolling()
  try {
    const res = await window.pdfAPI.ocrApply({
      path: tab.path,
      out_path: tab.path,
      dpi,
      lang,
    })
    tab.unsaved = true
    renderTabBar()
    tab.pdfDoc = null
    state.thumbsLoaded = {}
    await loadPDFDocument(tab.path, { preservePageIndex: currentPage })
    const usedWorkers = Number.isFinite(Number(res.used_workers)) ? Number(res.used_workers) : 1
    setOcrStatus(`OCR 完成：识别 ${res.recognized_blocks || 0} 段文字（并行进程=${usedWorkers}）`)
    setLoadingProgress({
      title: 'OCR 完成',
      detail: `已识别 ${res.recognized_blocks || 0} 段文字并回写 PDF。`,
      stage: '完成',
      progress: 100,
      action: null,
    })
  } catch (e) {
    if (undoSnapshotPushed && Array.isArray(tab.undoStack) && tab.undoStack.length > 0) {
      const latest = tab.undoStack[tab.undoStack.length - 1]
      if (latest?.reason === 'ocr_apply') {
        tab.undoStack.pop()
        try {
          await window.pdfAPI.deleteFile({ targetPath: latest.path })
        } catch (_) {}
      }
    }
    if (isOcrCancelError(e)) {
      setOcrStatus('OCR 已取消')
      setLoadingProgress({
        title: 'OCR 已取消',
        detail: '当前 OCR 任务已取消，未写回修改。',
        stage: '已取消',
        progress: 0,
        action: null,
      })
    } else {
      shouldRefreshStatusOnError = true
      console.error('OCR apply failed', e)
      setOcrStatus('OCR 失败：' + (e.message || e))
      alert('OCR 失败：' + (e.message || e))
    }
  } finally {
    state.ocrApplyInProgress = false
    state.ocrCancelRequested = false
    stopOcrApplyPolling()
    setOcrActionButtonsEnabled(true, { applying: false })
    if (shouldRefreshStatusOnError) {
      await refreshOcrStatus()
    }
    setTimeout(hideLoadingProgress, 180)
  }
})

// Search 閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓
function ensureTabSearchState(tab) {
  if (!tab) return
  if (!Array.isArray(tab.searchResults)) tab.searchResults = []
  if (typeof tab.searchQuery !== 'string') tab.searchQuery = ''
  if (!Number.isInteger(tab.searchOpSeq)) tab.searchOpSeq = 0
  if (typeof tab.searchStatus !== 'string') tab.searchStatus = ''
}

function getTabSearchResults(tab) {
  if (!tab || !Array.isArray(tab.searchResults)) return []
  return tab.searchResults
}

function renderSearchPanelForTab(tab) {
  const countEl = $('search-count')
  const listEl = $('search-result-list')
  if (!countEl || !listEl) return

  listEl.innerHTML = ''
  if (!tab) {
    countEl.textContent = ''
    return
  }

  ensureTabSearchState(tab)
  if (tab.searchStatus) {
    countEl.textContent = tab.searchStatus
    return
  }

  const results = getTabSearchResults(tab)
  if (results.length === 0) {
    countEl.textContent = tab.searchQuery ? '当前文档暂无搜索结果' : ''
    return
  }

  countEl.textContent = `找到 ${results.length} 条结果`
  const fallbackText = tab.searchQuery || ''
  results.forEach(r => {
    const pageIndex = Number.parseInt(r?.page, 10)
    const safePageIndex = Number.isInteger(pageIndex) && pageIndex >= 0 ? pageIndex : 0
    const item = document.createElement('div')
    item.className = 'search-result-item'

    const pg = document.createElement('div')
    pg.className = 'pg'
    pg.textContent = `Page ${safePageIndex + 1}`
    item.appendChild(pg)

    const snippet = document.createElement('div')
    snippet.textContent = r.snippet || fallbackText
    item.appendChild(snippet)

    item.addEventListener('click', () => {
      scrollToPage(safePageIndex)
    })

    listEl.appendChild(item)
  })
}

function syncSearchUiForActiveTab() {
  const tab = getTab()
  const inputEl = $('search-input')
  if (inputEl) {
    inputEl.value = tab?.searchQuery || ''
  }
  renderSearchPanelForTab(tab)
}

async function clearSearchMode({ clearInput = true, exitSearchPanel = true } = {}) {
  const tab = getTab()
  if (!tab) {
    renderSearchPanelForTab(null)
    if (clearInput && $('search-input')) $('search-input').value = ''
    if (exitSearchPanel) {
      setActiveRightPanel(state.lastNonSearchPanel || 'convert')
    }
    return
  }

  ensureTabSearchState(tab)
  tab.searchOpSeq += 1
  const hadResults = getTabSearchResults(tab).length > 0
  tab.searchResults = []
  tab.searchStatus = ''

  const currentInputValue = $('search-input')?.value?.trim() || ''
  if (clearInput) {
    tab.searchQuery = ''
  } else {
    tab.searchQuery = currentInputValue
  }

  syncSearchUiForActiveTab()
  if (exitSearchPanel) {
    setActiveRightPanel(state.lastNonSearchPanel || 'convert')
  }
  if (hadResults && state.activeTab === tab.id) {
    await renderAllPages()
  }
}

$('btn-search').addEventListener('click', runSearch)
$('btn-search-cancel').addEventListener('click', () => {
  clearSearchMode({ clearInput: true, exitSearchPanel: true }).catch(e => {
    console.error('cancel search failed', e)
  })
})
$('search-input').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    runSearch()
    return
  }
  if (e.key === 'Escape') {
    clearSearchMode({ clearInput: true, exitSearchPanel: true }).catch(err => {
      console.error('cancel search failed', err)
    })
  }
})
$('search-input').addEventListener('input', e => {
  if (!e.target.value.trim()) {
    clearSearchMode({ clearInput: false, exitSearchPanel: true }).catch(err => {
      console.error('clear search on input failed', err)
    })
  }
})
if ($('normalize-size-select')) {
  $('normalize-size-select').addEventListener('change', async e => {
    const mode = e.target.value
    if (!mode) return
    await normalizeAllPagesByMaxSize(mode)
    e.target.value = ''
    syncCustomSelect(e.target)
  })
}

async function runSearch() {
  const tab = getTab(); if (!tab) return
  ensureTabSearchState(tab)
  const searchSeq = ++tab.searchOpSeq
  const query = $('search-input').value.trim()
  if (!query) {
    await clearSearchMode({ clearInput: false, exitSearchPanel: true })
    return
  }

  tab.searchQuery = query
  tab.searchStatus = '正在搜索...'
  setActiveRightPanel('search-results')
  renderSearchPanelForTab(tab)

  try {
    const res = await window.pdfAPI.search({ path: tab.path, query })
    const targetTab = state.tabs.find(item => item.id === tab.id)
    if (!targetTab) return
    ensureTabSearchState(targetTab)
    if (searchSeq !== targetTab.searchOpSeq) return

    targetTab.searchQuery = query
    targetTab.searchStatus = ''
    targetTab.searchResults = Array.isArray(res?.results) ? res.results : []

    if (state.activeTab === targetTab.id) {
      renderSearchPanelForTab(targetTab)
      await renderAllPages()
      if (targetTab.searchResults.length > 0) {
        scrollToPage(targetTab.searchResults[0].page)
      }
    }
  } catch (e) {
    const targetTab = state.tabs.find(item => item.id === tab.id)
    if (!targetTab) return
    ensureTabSearchState(targetTab)
    if (searchSeq !== targetTab.searchOpSeq) return

    targetTab.searchResults = []
    targetTab.searchStatus = '搜索失败：' + (e.message || e)
    if (state.activeTab === targetTab.id) {
      renderSearchPanelForTab(targetTab)
      await renderAllPages()
    }
  }
}

function handleDragOver(e) {
  e.preventDefault()
  e.stopPropagation()
  if (!e.dataTransfer) return
  const types = Array.from(e.dataTransfer.types || [])
  const isThumbDrag = types.includes('application/x-pdftool-thumb')
  e.dataTransfer.dropEffect = isThumbDrag ? 'move' : 'copy'
}

const handleImportDrop = async e => {
  e.preventDefault()
  e.stopPropagation()
  const types = Array.from(e.dataTransfer?.types || [])
  if (types.includes('application/x-pdftool-thumb')) return

  let loadSession = null
  try {
    const paths = extractDropPaths(e, { forMerge: false })
    if (paths.length === 0) {
      alert('仅支持导入 PDF、Word 和图片文件。')
      return
    }

    loadSession = beginFileLoadSession()
    setFileLoadProgress(loadSession, {
      title: '正在接收拖拽文件',
      detail: `已接收 ${paths.length} 个文件，正在准备导入...`,
      stage: '分析中',
      progress: 4,
    })
    await importSelectedPaths(paths, loadSession)
    ensureFileLoadNotCancelled(loadSession)
    setLoadingProgress({
      title: '导入完成',
      detail: '文档已准备就绪。',
      stage: '完成',
      progress: 100,
      action: null,
    })
  } catch (e) {
    if (isFileLoadCancelledError(e)) {
      setLoadingProgress({
        title: '导入已取消',
        detail: '拖拽文件读取已取消。',
        stage: '已取消',
        progress: 0,
        action: null,
      })
    } else {
      console.error('Drag drop failed', e)
      alert('拖拽导入失败：' + (e?.message || e))
    }
  } finally {
    if (loadSession) endFileLoadSession(loadSession)
    setTimeout(hideLoadingProgress, 180)
  }
}

const handleMergeDrop = async e => {
  e.preventDefault()
  e.stopPropagation()
  const types = Array.from(e.dataTransfer?.types || [])
  if (types.includes('application/x-pdftool-thumb')) return

  let loadSession = null
  try {
    const paths = extractDropPaths(e, { forMerge: true })
    if (paths.length < 2) {
      alert('合并至少需要 2 个 PDF / Word / 图片文件。')
      return
    }

    loadSession = beginFileLoadSession()
    setFileLoadProgress(loadSession, {
      title: '正在接收合并文件',
      detail: `已接收 ${paths.length} 个文件，正在准备合并...`,
      stage: '分析中',
      progress: 4,
    })
    await mergeSelectedPaths(paths, loadSession)
    ensureFileLoadNotCancelled(loadSession)
    setLoadingProgress({
      title: '合并完成',
      detail: '合并结果已准备就绪。',
      stage: '完成',
      progress: 100,
      action: null,
    })
  } catch (e) {
    if (isFileLoadCancelledError(e)) {
      setLoadingProgress({
        title: '合并已取消',
        detail: '拖拽文件读取已取消。',
        stage: '已取消',
        progress: 0,
        action: null,
      })
    } else {
      console.error('Merge drop failed', e)
      alert('拖拽合并失败：' + (e?.message || e))
    }
  } finally {
    if (loadSession) endFileLoadSession(loadSession)
    setTimeout(hideLoadingProgress, 180)
  }
}

document.addEventListener('dragover', handleDragOver)
document.addEventListener('drop', handleImportDrop)
workspace.addEventListener('dragover', handleDragOver)
workspace.addEventListener('drop', handleImportDrop)
viewerWrap.addEventListener('dragover', handleDragOver)
viewerWrap.addEventListener('drop', handleImportDrop)
emptyState.addEventListener('dragover', handleDragOver)
emptyState.addEventListener('drop', handleImportDrop)
if (importDropZone) {
  importDropZone.addEventListener('dragover', handleDragOver)
  importDropZone.addEventListener('drop', handleImportDrop)
}
if (mergeDropZone) {
  mergeDropZone.addEventListener('dragover', handleDragOver)
  mergeDropZone.addEventListener('drop', handleMergeDrop)
}

// 閳光偓閳光偓 Save functionality 閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓閳光偓
async function saveCurrentPDF(targetTab = null) {
  const tab = targetTab || getTab()
  if (!tab) return false
  
  try {
    const savePath = await window.pdfAPI.saveFile({
      defaultPath: tab.originalPath,
      filters: [{ name: 'PDF', extensions: ['pdf'] }]
    })
    if (!savePath) return false

    await window.pdfAPI.saveFileTo({ srcPath: tab.path, destPath: savePath })

    tab.originalPath = savePath
    tab.unsaved = false
    await clearTabUndoSnapshots(tab)
    tab.title = savePath.split(/[\\/]/).pop()
    renderTabBar()
    console.log('Saved to', savePath)
    return true
  } catch (e) {
    console.error('Save failed', e)
    return false
  }
}

document.addEventListener('click', ev => {
  if (!activeCustomSelectCtx) return
  const ctx = activeCustomSelectCtx
  if (ctx.wrap.contains(ev.target) || ctx.dropdown.contains(ev.target)) return
  closeActiveCustomSelect()
}, true)

window.addEventListener('resize', () => closeActiveCustomSelect())
window.addEventListener('blur', () => closeActiveCustomSelect())
document.addEventListener('scroll', () => closeActiveCustomSelect(), true)

document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === 'z') {
    const targetTag = (e.target?.tagName || '').toLowerCase()
    const isTypingTarget = targetTag === 'input' || targetTag === 'textarea' || !!e.target?.isContentEditable
    if (isTypingTarget && e.target !== pageInput) {
      return
    }
    e.preventDefault()
    undoLastEdit()
    return
  }
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault()
    saveCurrentPDF()
  }
})

updateConvertOptions()

// Init
function normalizeZoomValue(value) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return Math.max(0.25, Math.min(4, value))
  }

  if (typeof value === 'string') {
    const cleaned = value.trim().replace('%', '')
    const parsed = parseFloat(cleaned)
    if (Number.isFinite(parsed)) {
      return parsed > 10 ? Math.max(0.25, Math.min(4, parsed / 100)) : Math.max(0.25, Math.min(4, parsed))
    }
  }

  return 1
}

function updateZoomSelectDisplay(zoom) {
  const safeZoom = normalizeZoomValue(zoom)
  const roundedZoom = Math.round(safeZoom * 100) / 100
  const options = Array.from(zoomSelect.options || [])
  const matched = options.find(option => Math.abs(normalizeZoomValue(option.value) - roundedZoom) < 0.0001)
  const existingDynamic = zoomSelect.querySelector('option[data-dynamic-zoom="1"]')

  if (matched && !matched.dataset.dynamicZoom) {
    if (existingDynamic) existingDynamic.remove()
    zoomSelect.value = matched.value
    syncCustomSelect(zoomSelect)
    return
  }

  let dynamicOption = existingDynamic
  if (!dynamicOption) {
    dynamicOption = document.createElement('option')
    dynamicOption.dataset.dynamicZoom = '1'
    zoomSelect.appendChild(dynamicOption)
  }
  dynamicOption.value = String(roundedZoom)
  dynamicOption.textContent = `${Math.round(roundedZoom * 100)}%`
  zoomSelect.value = dynamicOption.value
  syncCustomSelect(zoomSelect)
}

function updateToolbarState(tab = getTab()) {
  if (!tab) {
    pageInput.value = 1
    pageTotal.textContent = '/ 0'
    updateZoomSelectDisplay(1)
    return
  }

  pageInput.value = (tab.page || 0) + 1
  pageTotal.textContent = `/ ${tab.pageCount || 0}`
  updateZoomSelectDisplay(tab.liveZoom || tab.zoom || 1)
}

function clearScheduledPageRender() {
  if (!state.pageRenderScheduleTimer) return
  clearTimeout(state.pageRenderScheduleTimer)
  state.pageRenderScheduleTimer = null
}

function isRenderContextValid(tab, renderToken) {
  if (!tab || !tab.pdfDoc) return false
  if (renderToken !== state.renderToken) return false
  const active = getTab()
  return !!active && active.id === tab.id
}

function getPageRenderOutputScale() {
  const dpr = Number(window.devicePixelRatio) || 1
  return Math.max(1, Math.min(MAX_PAGE_OUTPUT_SCALE, dpr))
}

function getDefaultPageBaseMetric() {
  return {
    pageView: [0, 0, 595, 842],
    userUnit: 1,
    baseWidth: 595,
    baseHeight: 842,
  }
}

function getScaledPageDimensions(baseMetric, zoom) {
  const scale = Number.isFinite(Number(zoom)) ? Number(zoom) : 1
  const baseWidth = Number(baseMetric?.baseWidth) || 595
  const baseHeight = Number(baseMetric?.baseHeight) || 842
  return {
    width: Math.max(1, baseWidth * scale),
    height: Math.max(1, baseHeight * scale),
  }
}

async function getPageBaseMetric(tab, pageIndex) {
  if (!tab?.pdfDoc || !Number.isInteger(pageIndex) || pageIndex < 0 || pageIndex >= tab.pageCount) {
    return null
  }

  if (!Array.isArray(tab.pageBaseMetrics) || tab.pageBaseMetrics.length !== tab.pageCount) {
    tab.pageBaseMetrics = Array.from({ length: tab.pageCount }, () => null)
  }
  const cached = tab.pageBaseMetrics[pageIndex]
  if (cached) return cached

  if (!tab.pageMetricPromises) tab.pageMetricPromises = new Map()
  const pending = tab.pageMetricPromises.get(pageIndex)
  if (pending) return pending

  const promise = (async () => {
    const page = await tab.pdfDoc.getPage(pageIndex + 1)
    const baseRotation = Number.isFinite(page.rotate) ? page.rotate : 0
    const viewRotation = Number.isFinite(tab.rotate) ? tab.rotate : 0
    const viewportRotation = (((baseRotation + viewRotation) % 360) + 360) % 360
    const baseViewport = page.getViewport({ scale: 1, rotation: viewportRotation })
    const pageView = Array.isArray(page.view) ? page.view : [0, 0, baseViewport.width, baseViewport.height]
    const userUnit = Number.isFinite(page.userUnit) ? page.userUnit : 1
    const metric = {
      pageView,
      userUnit,
      baseWidth: baseViewport.width,
      baseHeight: baseViewport.height,
    }
    tab.pageBaseMetrics[pageIndex] = metric
    return metric
  })().finally(() => {
    if (tab.pageMetricPromises) tab.pageMetricPromises.delete(pageIndex)
  })

  tab.pageMetricPromises.set(pageIndex, promise)
  return promise
}

function createPagePlaceholder(tab, pageIndex, baseMetric) {
  const dims = getScaledPageDimensions(baseMetric, tab.zoom || 1)
  const safeUserUnit = Number(baseMetric?.userUnit) || 1
  const container = document.createElement('div')
  container.className = 'page-container'
  container.dataset.page = String(pageIndex)
  container.dataset.rendered = '0'
  container.dataset.renderWidth = String(dims.width)
  container.dataset.renderHeight = String(dims.height)
  container.style.width = `${dims.width}px`
  container.style.height = `${dims.height}px`
  container.style.setProperty('--scale-factor', `${tab.zoom || 1}`)
  container.style.setProperty('--user-unit', `${safeUserUnit}`)
  container.style.setProperty('--total-scale-factor', `${(tab.zoom || 1) * safeUserUnit}`)
  container.style.setProperty('--scale-round-x', '1px')
  container.style.setProperty('--scale-round-y', '1px')

  const placeholder = document.createElement('div')
  placeholder.className = 'page-placeholder'
  placeholder.style.position = 'absolute'
  placeholder.style.inset = '0'
  placeholder.style.borderRadius = '2px'
  placeholder.style.background = 'linear-gradient(180deg, rgba(255,255,255,0.94), rgba(245,239,230,0.88))'
  placeholder.style.zIndex = '1'
  container.appendChild(placeholder)
  return container
}

function getPageContainerByIndex(pageIndex) {
  return pagesContainer.querySelector(`.page-container[data-page="${pageIndex}"]`)
}

function getVisiblePageIndexes(tab) {
  const pageEls = Array.from(pagesContainer.querySelectorAll('.page-container'))
  if (pageEls.length === 0) return []

  const wrapRect = viewerWrap.getBoundingClientRect()
  const margin = Math.max(180, Math.min(640, wrapRect.height * 0.6))
  const visible = []
  for (const el of pageEls) {
    const rect = el.getBoundingClientRect()
    if (rect.bottom < wrapRect.top - margin || rect.top > wrapRect.bottom + margin) {
      continue
    }
    const index = Number.parseInt(el.dataset.page || '', 10)
    if (Number.isInteger(index) && index >= 0 && index < tab.pageCount) {
      visible.push(index)
    }
  }
  if (visible.length > 0) return sortPageIndexes(visible)
  return [getNearestPageIndexInViewport(tab)]
}

function buildViewportTargetIndexes(tab) {
  const visible = getVisiblePageIndexes(tab)
  const visibleSet = new Set(visible)
  const targets = new Set()
  for (const index of visible) {
    for (let delta = -PAGE_RENDER_PREFETCH_RADIUS; delta <= PAGE_RENDER_PREFETCH_RADIUS; delta += 1) {
      const next = index + delta
      if (next >= 0 && next < tab.pageCount) targets.add(next)
    }
  }
  const center = Math.max(0, Math.min(tab.page || 0, tab.pageCount - 1))
  return [...targets].sort((a, b) => {
    const aVisible = visibleSet.has(a) ? 0 : 1
    const bVisible = visibleSet.has(b) ? 0 : 1
    if (aVisible !== bVisible) return aVisible - bVisible
    const dist = Math.abs(a - center) - Math.abs(b - center)
    if (dist !== 0) return dist
    return a - b
  })
}

async function renderPageIntoContainer(tab, pageIndex, renderToken, force = false) {
  if (!isRenderContextValid(tab, renderToken)) return
  const existing = getPageContainerByIndex(pageIndex)
  if (!existing) return
  if (!force && existing.dataset.rendered === '1') return

  if (!tab.pageRenderPromises) tab.pageRenderPromises = new Map()
  const pending = tab.pageRenderPromises.get(pageIndex)
  if (pending) {
    await pending
    return
  }

  const promise = (async () => {
    const pageEl = await renderPage(tab, pageIndex)
    if (!isRenderContextValid(tab, renderToken)) return
    const latest = getPageContainerByIndex(pageIndex)
    if (!latest) return
    pageEl.dataset.rendered = '1'
    latest.replaceWith(pageEl)
  })().finally(() => {
    if (tab.pageRenderPromises) tab.pageRenderPromises.delete(pageIndex)
  })

  tab.pageRenderPromises.set(pageIndex, promise)
  await promise
}

async function renderPagesForViewport(tab, renderToken, { force = false } = {}) {
  if (!isRenderContextValid(tab, renderToken)) return
  const targets = buildViewportTargetIndexes(tab)
  if (targets.length === 0) return

  const queue = force
    ? targets
    : targets.filter(index => {
      const el = getPageContainerByIndex(index)
      return !el || el.dataset.rendered !== '1'
    })
  if (queue.length === 0) return

  const workerCount = Math.min(PAGE_RENDER_CONCURRENCY, queue.length)
  let cursor = 0
  const workers = Array.from({ length: workerCount }, async () => {
    while (true) {
      if (!isRenderContextValid(tab, renderToken)) return
      const nextIdx = queue[cursor]
      cursor += 1
      if (!Number.isInteger(nextIdx)) return
      await renderPageIntoContainer(tab, nextIdx, renderToken, force)
    }
  })
  await Promise.all(workers)
}

function scheduleViewportRender({ immediate = false, force = false } = {}) {
  const tab = getTab()
  if (!tab?.pdfDoc) return

  state.pendingViewportRenderForce = state.pendingViewportRenderForce || force
  const renderToken = state.renderToken
  const run = async () => {
    state.pageRenderScheduleTimer = null
    const forceRun = !!state.pendingViewportRenderForce
    state.pendingViewportRenderForce = false
    try {
      await renderPagesForViewport(tab, renderToken, { force: forceRun })
    } catch (e) {
      console.error('Viewport render failed', e)
    }
  }

  if (immediate) {
    clearScheduledPageRender()
    run()
    return
  }
  if (state.pageRenderScheduleTimer) return
  state.pageRenderScheduleTimer = setTimeout(run, PAGE_RENDER_SCROLL_DEBOUNCE_MS)
}

async function loadPDFDocument(path, options = {}, loadSession = null) {
  const tab = getTab()
  if (!tab || tab.path !== path) return
  const preservePageIndex = Number.isInteger(options.preservePageIndex) ? options.preservePageIndex : null
  const shouldPreservePage = preservePageIndex !== null

  ensureFileLoadNotCancelled(loadSession)
  const pdfBytes = await awaitFileLoad(window.pdfAPI.loadPDF({ path }), loadSession)
  ensureFileLoadNotCancelled(loadSession)

  const loadingTask = getDocument({ data: pdfBytes })
  if (loadSession) {
    loadSession.loadingTask = loadingTask
  }
  try {
    tab.pdfDoc = await awaitFileLoad(loadingTask.promise, loadSession, {
      onCancel: async () => {
        try {
          await loadingTask.destroy()
        } catch (_) {}
      },
    })
  } finally {
    if (loadSession && loadSession.loadingTask === loadingTask) {
      loadSession.loadingTask = null
    }
  }
  ensureFileLoadNotCancelled(loadSession)

  tab.pageCount = tab.pdfDoc.numPages
  tab.pageBaseMetrics = Array.from({ length: tab.pageCount }, () => null)
  tab.pageMetricPromises = new Map()
  tab.pageRenderPromises = new Map()
  tab.defaultPageBaseMetric = getDefaultPageBaseMetric()
  if (tab.pageCount > 0) {
    try {
      const firstMetric = await awaitFileLoad(getPageBaseMetric(tab, 0), loadSession)
      if (firstMetric) tab.defaultPageBaseMetric = firstMetric
    } catch (_) {}
  }
  ensureFileLoadNotCancelled(loadSession)
  state.thumbsLoaded = {}
  clearScheduledPageRender()
  state.pendingViewportRenderForce = false
  if (!shouldPreservePage) {
    viewerWrap.scrollTop = 0
    viewerWrap.scrollLeft = 0
  }
  updateToolbarState(tab)
  loadThumbnails()
  await awaitFileLoad(renderAllPages(), loadSession)
  ensureFileLoadNotCancelled(loadSession)

  if (shouldPreservePage && tab.pageCount > 0) {
    const safeIndex = Math.max(0, Math.min(preservePageIndex, tab.pageCount - 1))
    tab.page = safeIndex
    updateToolbarState(tab)

    const target = pagesContainer.querySelector(`.page-container[data-page="${safeIndex}"]`)
    if (target) {
      viewerWrap.scrollTop = Math.max(0, target.offsetTop - 12)
      viewerWrap.scrollLeft = 0
    }

    thumbList.querySelectorAll('.thumb-item').forEach((item, index) => {
      item.classList.toggle('active', index === safeIndex)
    })
    syncThumbListToPage(safeIndex)
    scheduleViewportRender({ immediate: true, force: false })
  }
}

function buildSearchHighlights(tab, pageIndex, viewport, pageView) {
  const layer = document.createElement('div')
  layer.className = 'search-highlight-layer'

  const tabResults = getTabSearchResults(tab)
  const results = tabResults.filter(result => result.page === pageIndex)
  const [viewX0, viewY0, viewX1, viewY1] = pageView || [0, 0, viewport.width, viewport.height]
  const pageHeight = viewY1 - viewY0
  for (const result of results) {
    const [x0, y0, x1, y1] = result.rect
    const [left, top, right, bottom] = viewport.convertToViewportRectangle([
      viewX0 + x0,
      viewY0 + (pageHeight - y1),
      viewX0 + x1,
      viewY0 + (pageHeight - y0),
    ])
    const highlight = document.createElement('div')
    highlight.className = 'search-highlight'
    highlight.style.left = `${Math.min(left, right)}px`
    highlight.style.top = `${Math.min(top, bottom)}px`
    highlight.style.width = `${Math.abs(right - left)}px`
    highlight.style.height = `${Math.abs(bottom - top)}px`
    layer.appendChild(highlight)
  }

  return layer
}

function buildFallbackTextLayer(words, viewport, pageView) {
  const layer = document.createElement('div')
  layer.className = 'ocr-fallback-layer'
  layer.style.width = `${viewport.width}px`
  layer.style.height = `${viewport.height}px`

  const [viewX0, viewY0, viewX1, viewY1] = pageView || [0, 0, viewport.width, viewport.height]
  const pageHeight = viewY1 - viewY0
  const entries = []
  const dedupe = new Set()
  for (const item of words || []) {
    const rect = Array.isArray(item?.rect) ? item.rect : null
    const textValue = String(item?.text || '').trim()
    if (!rect || rect.length < 4 || !textValue) continue

    const x0 = Number(rect[0])
    const y0 = Number(rect[1])
    const x1 = Number(rect[2])
    const y1 = Number(rect[3])
    if (![x0, y0, x1, y1].every(Number.isFinite)) continue

    const [left, top, right, bottom] = viewport.convertToViewportRectangle([
      viewX0 + x0,
      viewY0 + (pageHeight - y1),
      viewX0 + x1,
      viewY0 + (pageHeight - y0),
    ])
    const width = Math.abs(right - left)
    const height = Math.abs(bottom - top)
    if (width < 0.5 || height < 0.5) continue

    const normalizedLeft = Math.min(left, right)
    const normalizedTop = Math.min(top, bottom)
    const dedupeKey = `${textValue}\t${Math.round(normalizedLeft * 2) / 2}\t${Math.round(normalizedTop * 2) / 2}\t${Math.round(width * 2) / 2}\t${Math.round(height * 2) / 2}`
    if (dedupe.has(dedupeKey)) continue
    dedupe.add(dedupeKey)

    entries.push({
      text: textValue,
      left: normalizedLeft,
      top: normalizedTop,
      width,
      height,
      centerY: normalizedTop + (height / 2),
      block: Number.isFinite(Number(item?.block)) ? Number(item.block) : -1,
      line: Number.isFinite(Number(item?.line)) ? Number(item.line) : -1,
      word: Number.isFinite(Number(item?.word)) ? Number(item.word) : -1,
    })
  }

  if (entries.length === 0) return layer

  // Keep DOM order close to visual reading order to avoid cross-line jump when selecting.
  entries.sort((a, b) => {
    const aHasIdx = a.block >= 0 && a.line >= 0 && a.word >= 0
    const bHasIdx = b.block >= 0 && b.line >= 0 && b.word >= 0
    if (aHasIdx && bHasIdx) {
      if (a.block !== b.block) return a.block - b.block
      if (a.line !== b.line) return a.line - b.line
      if (a.word !== b.word) return a.word - b.word
    }
    if (Math.abs(a.centerY - b.centerY) > 1.8) return a.centerY - b.centerY
    return a.left - b.left
  })

  // Avoid secondary line clustering here:
  // aggressive y-tolerance can merge adjacent lines and cause cross-line auto selection.
  const sortedEntries = entries

  const measureCanvas = document.createElement('canvas')
  const measureCtx = measureCanvas.getContext('2d')
  for (const entry of sortedEntries) {
    const fontSize = Math.max(8, Math.min(48, entry.height * 0.9))
    const hitPadX = Math.max(0.4, Math.min(1.8, entry.height * 0.05))
    let scaleX = 1
    if (measureCtx) {
      measureCtx.font = `${fontSize}px sans-serif`
      const measuredWidth = measureCtx.measureText(entry.text).width
      if (Number.isFinite(measuredWidth) && measuredWidth > 0.1) {
        const desiredWidth = Math.max(1, entry.width)
        scaleX = Math.max(0.75, Math.min(1.8, desiredWidth / measuredWidth))
      }
    }

    const span = document.createElement('span')
    span.className = 'ocr-fallback-word'
    span.textContent = entry.text
    span.style.left = `${entry.left - hitPadX}px`
    span.style.top = `${entry.top}px`
    span.style.width = `${Math.max(1, entry.width + (hitPadX * 2))}px`
    span.style.height = `${Math.max(1, entry.height)}px`
    span.style.fontSize = `${fontSize}px`
    span.style.lineHeight = `${Math.max(1, entry.height)}px`
    span.style.transform = `scaleX(${scaleX})`
    span.style.fontFamily = 'sans-serif'
    span.style.letterSpacing = '0px'
    layer.appendChild(span)
  }

  return layer
}

async function renderPage(tab, pageIndex) {
  const page = await tab.pdfDoc.getPage(pageIndex + 1)
  const baseRotation = Number.isFinite(page.rotate) ? page.rotate : 0
  const viewRotation = Number.isFinite(tab.rotate) ? tab.rotate : 0
  const viewportRotation = (((baseRotation + viewRotation) % 360) + 360) % 360
  const viewport = page.getViewport({ scale: tab.zoom || 1, rotation: viewportRotation })
  const pageView = Array.isArray(page.view) ? page.view : [0, 0, viewport.width, viewport.height]
  const userUnit = Number.isFinite(page.userUnit) ? page.userUnit : 1
  const totalScaleFactor = viewport.scale * userUnit
  if (Array.isArray(tab.pageBaseMetrics) && pageIndex >= 0 && pageIndex < tab.pageBaseMetrics.length) {
    const safeScale = Math.max(0.001, Number(tab.zoom) || 1)
    tab.pageBaseMetrics[pageIndex] = {
      pageView,
      userUnit,
      baseWidth: viewport.width / safeScale,
      baseHeight: viewport.height / safeScale,
    }
  }

  const container = document.createElement('div')
  container.className = 'page-container'
  container.dataset.page = String(pageIndex)
  container.dataset.rendered = '1'
  container.dataset.renderWidth = String(viewport.width)
  container.dataset.renderHeight = String(viewport.height)
  container.style.width = `${viewport.width}px`
  container.style.height = `${viewport.height}px`
  container.style.setProperty('--scale-factor', `${viewport.scale}`)
  container.style.setProperty('--user-unit', `${userUnit}`)
  container.style.setProperty('--total-scale-factor', `${totalScaleFactor}`)
  container.style.setProperty('--scale-round-x', '1px')
  container.style.setProperty('--scale-round-y', '1px')

  const canvas = document.createElement('canvas')
  canvas.className = 'page-canvas'
  const outputScale = getPageRenderOutputScale()
  canvas.width = Math.ceil(viewport.width * outputScale)
  canvas.height = Math.ceil(viewport.height * outputScale)
  canvas.style.width = `${viewport.width}px`
  canvas.style.height = `${viewport.height}px`
  container.appendChild(canvas)

  const ctx = canvas.getContext('2d')
  const transform = outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0]
  await page.render({ canvasContext: ctx, viewport, transform }).promise

  const textLayerDiv = document.createElement('div')
  textLayerDiv.className = 'text-layer'
  textLayerDiv.style.width = `${viewport.width}px`
  textLayerDiv.style.height = `${viewport.height}px`
  container.appendChild(textLayerDiv)

  let hasTextLayer = false
  try {
    const textContent = await page.getTextContent()
    const items = Array.isArray(textContent?.items) ? textContent.items : []
    if (items.length > 0) {
      const textLayer = new TextLayer({
        textContentSource: textContent,
        container: textLayerDiv,
        viewport,
      })
      await textLayer.render()
      hasTextLayer = textLayerDiv.childElementCount > 0
    }
  } catch (e) {
    console.error('Text layer render failed', pageIndex, e)
  }

  if (!hasTextLayer && window.pdfAPI?.getPageText) {
    try {
      const textRes = await window.pdfAPI.getPageText({ path: tab.path, page: pageIndex })
      if (Array.isArray(textRes?.words) && textRes.words.length > 0) {
        const fallbackLayer = buildFallbackTextLayer(textRes.words, viewport, pageView)
        if (fallbackLayer.childElementCount > 0) {
          textLayerDiv.remove()
          container.appendChild(fallbackLayer)
        }
      }
    } catch (e) {
      console.error('Fallback text layer failed', pageIndex, e)
    }
  }

  if (getTabSearchResults(tab).length > 0) {
    container.appendChild(buildSearchHighlights(tab, pageIndex, viewport, pageView))
  }

  return container
}

async function renderAllPages() {
  const tab = getTab()
  if (!tab || !tab.pdfDoc) {
    pagesContainer.innerHTML = ''
    state.isRenderingPages = false
    clearScheduledPageRender()
    state.pendingViewportRenderForce = false
    return
  }

  state.isRenderingPages = true
  const renderToken = ++state.renderToken
  tab.pageRenderPromises = new Map()
  clearScheduledPageRender()
  state.pendingViewportRenderForce = false
  updateToolbarState(tab)

  try {
    const fallbackMetric = tab.defaultPageBaseMetric || getDefaultPageBaseMetric()
    const placeholders = document.createDocumentFragment()
    for (let i = 0; i < tab.pageCount; i += 1) {
      const metric = (Array.isArray(tab.pageBaseMetrics) ? tab.pageBaseMetrics[i] : null) || fallbackMetric
      placeholders.appendChild(createPagePlaceholder(tab, i, metric))
    }
    if (!isRenderContextValid(tab, renderToken)) return
    pagesContainer.replaceChildren(placeholders)
    if ((tab.pageCount || 0) === 0) {
      pagesContainer.innerHTML = ''
    }
    tab.renderedZoom = tab.zoom || 1
    tab.liveZoom = tab.renderedZoom
    await renderPagesForViewport(tab, renderToken, { force: true })
  } finally {
    if (renderToken === state.renderToken) {
      state.isRenderingPages = false
    }
  }
}

function clearZoomFinalizeTimer() {
  if (!state.zoomFinalizeTimer) return
  clearTimeout(state.zoomFinalizeTimer)
  state.zoomFinalizeTimer = null
}

function applyLiveZoomPreview(tab, targetZoom) {
  const renderedZoom = tab.renderedZoom || 1
  const previewScale = targetZoom / renderedZoom
  const pageEls = Array.from(pagesContainer.querySelectorAll('.page-container'))
  if (pageEls.length === 0) return false

  pageEls.forEach(pageEl => {
    const baseWidth = parseFloat(pageEl.dataset.renderWidth || pageEl.style.width || '0')
    const baseHeight = parseFloat(pageEl.dataset.renderHeight || pageEl.style.height || '0')
    if (baseWidth > 0) pageEl.style.width = `${baseWidth * previewScale}px`
    if (baseHeight > 0) pageEl.style.height = `${baseHeight * previewScale}px`

    pageEl.querySelectorAll('.page-canvas, .text-layer, .search-highlight-layer').forEach(layer => {
      layer.style.transformOrigin = 'top left'
      layer.style.transform = `scale(${previewScale})`
    })
  })

  return true
}

function syncCurrentPageVisualState(tab) {
  const syncedIndex = getNearestPageIndexInViewport(tab)
  tab.page = syncedIndex
  updateToolbarState(tab)
  thumbList.querySelectorAll('.thumb-item').forEach((item, index) => {
    item.classList.toggle('active', index === syncedIndex)
  })
  syncThumbListToPage(syncedIndex)
}

function scheduleZoomFinalize(tab, zoomOpSeq) {
  clearZoomFinalizeTimer()
  state.zoomFinalizeTimer = setTimeout(() => {
    state.zoomFinalizeTimer = null
    if (zoomOpSeq !== state.zoomOpSeq) return
    const currentTab = getTab()
    if (!currentTab || currentTab.id !== tab.id) return

    const scrollTopBefore = viewerWrap.scrollTop
    const scrollLeftBefore = viewerWrap.scrollLeft
    renderAllPages().then(() => {
      if (zoomOpSeq !== state.zoomOpSeq) return
      const activeTab = getTab()
      if (!activeTab || activeTab.id !== tab.id) return
      viewerWrap.scrollTop = scrollTopBefore
      viewerWrap.scrollLeft = scrollLeftBefore
      requestAnimationFrame(() => {
        if (zoomOpSeq !== state.zoomOpSeq) return
        viewerWrap.scrollTop = scrollTopBefore
        viewerWrap.scrollLeft = scrollLeftBefore
        syncCurrentPageVisualState(tab)
      })
    }).catch(e => {
      console.error('Finalize zoom render failed', e)
    })
  }, 120)
}

function getNearestPageIndexInViewport(tab = getTab()) {
  if (!tab) return 0
  const pageEls = Array.from(pagesContainer.querySelectorAll('.page-container'))
  if (pageEls.length === 0) return Math.max(0, Math.min(tab.page || 0, (tab.pageCount || 1) - 1))

  const viewerTop = viewerWrap.getBoundingClientRect().top
  let bestIndex = tab.page || 0
  let bestDistance = Number.POSITIVE_INFINITY
  pageEls.forEach((el, index) => {
    const distance = Math.abs(el.getBoundingClientRect().top - viewerTop - 24)
    if (distance < bestDistance) {
      bestDistance = distance
      bestIndex = index
    }
  })
  return Math.max(0, Math.min(bestIndex, (tab.pageCount || 1) - 1))
}

function scrollToPage(pageIndex) {
  const tab = getTab()
  if (!tab) return

  const safeIndex = Math.max(0, Math.min(pageIndex, (tab.pageCount || 1) - 1))
  const target = pagesContainer.querySelector(`.page-container[data-page="${safeIndex}"]`)
  tab.page = safeIndex
  updateToolbarState(tab)
  if (target) {
    const viewerRect = viewerWrap.getBoundingClientRect()
    const targetRect = target.getBoundingClientRect()
    const top = viewerWrap.scrollTop + (targetRect.top - viewerRect.top) - 12
    viewerWrap.scrollTo({
      top: Math.max(0, top),
      left: viewerWrap.scrollLeft,
      behavior: 'smooth',
    })
  }

  thumbList.querySelectorAll('.thumb-item').forEach((item, index) => {
    item.classList.toggle('active', index === safeIndex)
  })
  syncThumbListToPage(safeIndex, { behavior: 'smooth' })
}

function syncThumbListToPage(pageIndex, { behavior = 'auto', force = false } = {}) {
  const item = thumbList.children[pageIndex]
  if (!item) return

  const listTop = thumbList.scrollTop
  const listBottom = listTop + thumbList.clientHeight
  const itemTop = item.offsetTop
  const itemBottom = itemTop + item.offsetHeight
  const edgePadding = 10
  const shouldScroll = force
    || itemTop < (listTop + edgePadding)
    || itemBottom > (listBottom - edgePadding)

  if (!shouldScroll) return

  const centeredTop = itemTop - Math.max(0, (thumbList.clientHeight - item.offsetHeight) / 2)
  thumbList.scrollTo({
    top: Math.max(0, centeredTop),
    behavior,
  })
}

function loadThumbnails() {
  const tab = getTab()
  if (!tab) {
    thumbList.innerHTML = ''
    return
  }

  state.thumbRenderToken += 1
  state.thumbsLoaded = {}
  thumbList.innerHTML = ''
  stopThumbManualDragListeners()
  state.pendingThumbDrag = null
  state.dragPointerClientY = null
  state.draggedThumbIndex = null
  clearThumbDragVisualState()

  for (let i = 0; i < tab.pageCount; i += 1) {
    const item = document.createElement('div')
    item.className = 'thumb-item' + (i === (tab.page || 0) ? ' active' : '')
    item.innerHTML = `
      <div class="thumb-badge hidden"></div>
      <div class="thumb-placeholder"></div>
      <div class="thumb-num">Page ${i + 1}</div>
    `
    item.addEventListener('click', e => handleThumbSelection(i, e))
    item.addEventListener('contextmenu', e => showThumbContextMenu(e, i))
    item.addEventListener('mousedown', e => startThumbManualDrag(e, i))
    thumbList.appendChild(item)
  }
  updateThumbSelectionStates()
  syncThumbListToPage(tab.page || 0, { force: true })

  const preferredIndexes = sortPageIndexes([
    ...(state.pastedTabId === tab.id ? state.pastedThumbIndexes : []),
    ...(state.selectedThumbIndexes || []),
    tab.page || 0,
    Math.max(0, (tab.page || 0) - 1),
    Math.min(tab.pageCount - 1, (tab.page || 0) + 1),
  ].filter(index => index >= 0 && index < tab.pageCount))

  const remainingIndexes = []
  for (let i = 0; i < tab.pageCount; i += 1) {
    if (!preferredIndexes.includes(i)) remainingIndexes.push(i)
  }

  const queue = [...preferredIndexes, ...remainingIndexes]
  const currentThumbToken = state.thumbRenderToken
  const runQueue = async () => {
    const total = queue.length
    if (total === 0) return

    const workerCount = Math.min(getThumbRenderConcurrency(), total)
    let cursor = 0
    const workers = Array.from({ length: workerCount }, async () => {
      while (true) {
        if (state.activeTab !== tab.id || currentThumbToken !== state.thumbRenderToken) return
        const next = cursor
        cursor += 1
        if (next >= total) return
        await loadThumb(tab, queue[next])
      }
    })
    try {
      await Promise.all(workers)
    } catch (e) {
      console.error('Parallel thumbnail render failed', e)
    }
  }

  runQueue()
}

function applyZoom(nextZoom, anchor = null) {
  const tab = getTab()
  if (!tab) return

  const currentVisualZoom = tab.liveZoom || tab.zoom || 1
  const clampedZoom = Math.max(0.25, Math.min(4, nextZoom))
  const zoomChanged = Math.abs(clampedZoom - currentVisualZoom) > 0.0001
  if (!zoomChanged) return

  const zoomOpSeq = ++state.zoomOpSeq
  const rect = viewerWrap.getBoundingClientRect()
  const pointerAnchorState = anchor
    ? {
      clientX: anchor.clientX - rect.left,
      clientY: anchor.clientY - rect.top,
      x: anchor.clientX - rect.left + viewerWrap.scrollLeft,
      y: anchor.clientY - rect.top + viewerWrap.scrollTop,
    }
    : null

  tab.zoom = Math.round(clampedZoom * 100) / 100
  tab.liveZoom = tab.zoom
  const ratio = tab.zoom / currentVisualZoom
  updateToolbarState(tab)

  const previewApplied = applyLiveZoomPreview(tab, tab.zoom)

  if (pointerAnchorState) {
    viewerWrap.scrollLeft = Math.max(0, pointerAnchorState.x * ratio - pointerAnchorState.clientX)
    viewerWrap.scrollTop = Math.max(0, pointerAnchorState.y * ratio - pointerAnchorState.clientY)
  } else {
    const centerX = rect.width / 2
    const centerY = rect.height / 2
    viewerWrap.scrollLeft = Math.max(0, (viewerWrap.scrollLeft + centerX) * ratio - centerX)
    viewerWrap.scrollTop = Math.max(0, (viewerWrap.scrollTop + centerY) * ratio - centerY)
  }

  if (previewApplied) {
    syncCurrentPageVisualState(tab)
    scheduleZoomFinalize(tab, zoomOpSeq)
    return
  }

  renderAllPages().then(() => {
    if (zoomOpSeq !== state.zoomOpSeq) return
    const currentTab = getTab()
    if (!currentTab || currentTab.id !== tab.id) return
    syncCurrentPageVisualState(tab)
  }).catch(e => {
    console.error('Apply zoom render failed', e)
  })
}

function adjustZoom(delta, anchor = null) {
  const tab = getTab()
  if (!tab) return

  applyZoom((tab.liveZoom || tab.zoom || 1) + delta, anchor)
}

async function rotateCurrent(delta) {
  const tab = getTab()
  if (!tab || state.isRotatingPages) return

  const normalizedDelta = (((delta % 360) + 360) % 360)
  if (!normalizedDelta) return

  const targetPage = getNearestPageIndexInViewport(tab)
  state.isRotatingPages = true

  try {
    await pushUndoSnapshot(tab, 'rotate_pages')
    await window.pdfAPI.pageOps({
      action: 'rotate_pages',
      paths: [tab.path],
      out_path: tab.path,
      rotate: normalizedDelta,
    })

    tab.rotate = 0
    tab.unsaved = true
    renderTabBar()

    tab.pdfDoc = null
    state.thumbsLoaded = {}
    await loadPDFDocument(tab.path, { preservePageIndex: targetPage })
  } catch (e) {
    console.error('Rotate pages failed', e)
    alert('旋转页面失败：' + (e.message || e))
  } finally {
    state.isRotatingPages = false
  }
}

async function openFilesFromDialog() {
  let loadSession = null
  try {
    const paths = await window.pdfAPI.openFiles()
    if (!paths || paths.length === 0) return

    loadSession = beginFileLoadSession()
    setFileLoadProgress(loadSession, {
      title: '正在准备导入',
      detail: `已选择 ${paths.length} 个文件。`,
      stage: '分析中',
      progress: 4,
    })
    await importSelectedPaths(paths, loadSession)
    ensureFileLoadNotCancelled(loadSession)
    setLoadingProgress({
      title: '导入完成',
      detail: '文档已准备就绪。',
      stage: '完成',
      progress: 100,
      action: null,
    })
  } catch (e) {
    if (isFileLoadCancelledError(e)) {
      setLoadingProgress({
        title: '导入已取消',
        detail: '文件读取已取消。',
        stage: '已取消',
        progress: 0,
        action: null,
      })
    } else {
      console.error('Open files failed', e)
      alert('导入失败：' + (e?.message || e))
    }
  } finally {
    if (loadSession) endFileLoadSession(loadSession)
    setTimeout(hideLoadingProgress, 180)
  }
}

async function openMergeFromDialog() {
  let loadSession = null
  try {
    const paths = await window.pdfAPI.openFiles()
    if (!paths || paths.length === 0) return

    loadSession = beginFileLoadSession()
    setFileLoadProgress(loadSession, {
      title: '正在准备合并',
      detail: `已选择 ${paths.length} 个文件。`,
      stage: '分析中',
      progress: 4,
    })
    await mergeSelectedPaths(paths, loadSession)
    ensureFileLoadNotCancelled(loadSession)
    setLoadingProgress({
      title: '合并完成',
      detail: '合并结果已准备就绪。',
      stage: '完成',
      progress: 100,
      action: null,
    })
  } catch (e) {
    if (isFileLoadCancelledError(e)) {
      setLoadingProgress({
        title: '合并已取消',
        detail: '文件读取已取消。',
        stage: '已取消',
        progress: 0,
        action: null,
      })
    } else {
      console.error('Open merge files failed', e)
      alert('合并失败：' + (e?.message || e))
    }
  } finally {
    if (loadSession) endFileLoadSession(loadSession)
    setTimeout(hideLoadingProgress, 180)
  }
}

initCustomSelects()
$('btn-open').addEventListener('click', openFilesFromDialog)
if ($('btn-open2')) $('btn-open2').addEventListener('click', openFilesFromDialog)
if ($('btn-merge')) $('btn-merge').addEventListener('click', openMergeFromDialog)
if ($('btn-merge2')) $('btn-merge2').addEventListener('click', openMergeFromDialog)
$('btn-prev').addEventListener('click', () => {
  const tab = getTab()
  if (!tab) return
  scrollToPage((tab.page || 0) - 1)
})
$('btn-next').addEventListener('click', () => {
  const tab = getTab()
  if (!tab) return
  scrollToPage((tab.page || 0) + 1)
})
$('btn-zoom-in').addEventListener('click', () => adjustZoom(0.1))
$('btn-zoom-out').addEventListener('click', () => adjustZoom(-0.1))
$('btn-rotate-l').addEventListener('click', () => rotateCurrent(-90))
$('btn-rotate-r').addEventListener('click', () => rotateCurrent(90))

pageInput.addEventListener('change', () => {
  const page = Math.max(1, parseInt(pageInput.value, 10) || 1)
  scrollToPage(page - 1)
})

zoomSelect.addEventListener('change', () => {
  const tab = getTab()
  if (!tab) return

  applyZoom(normalizeZoomValue(zoomSelect.value))
})

viewerWrap.addEventListener('wheel', e => {
  if (!(e.ctrlKey || e.metaKey)) return

  const tab = getTab()
  if (!tab || !tab.pdfDoc) return

  e.preventDefault()
  const delta = e.deltaY < 0 ? 0.1 : -0.1
  adjustZoom(delta, { clientX: e.clientX, clientY: e.clientY })
}, { passive: false })

viewerWrap.addEventListener('scroll', () => {
  const tab = getTab()
  if (!tab) return
  if (state.isRenderingPages) return

  const bestIndex = getNearestPageIndexInViewport(tab)

  if (bestIndex !== tab.page) {
    tab.page = bestIndex
    updateToolbarState(tab)
    thumbList.querySelectorAll('.thumb-item').forEach((item, index) => {
      item.classList.toggle('active', index === bestIndex)
    })
    syncThumbListToPage(bestIndex)
  }
  scheduleViewportRender({ immediate: false, force: false })
})

thumbList.addEventListener('wheel', e => {
  if (e.ctrlKey || e.metaKey) return
  if (thumbList.scrollHeight <= thumbList.clientHeight) return
  e.preventDefault()
  e.stopPropagation()
  thumbList.scrollTop += e.deltaY
  if (state.draggedThumbIndex !== null && Number.isFinite(state.dragPointerClientY)) {
    updateThumbDragTargetByPointer(state.dragPointerClientY)
  }
}, { passive: false })

if (typeof window.pdfAPI?.onAppCloseRequest === 'function' && typeof window.pdfAPI?.replyAppClose === 'function') {
  window.pdfAPI.onAppCloseRequest(async () => {
    const allowClose = await handleAppCloseRequest()
    window.pdfAPI.replyAppClose({ allow: allowClose })
  })
}

if (window.pdfAPI?.ocrStatus) {
  refreshOcrStatus()
}

setActiveTab(null)
hideLoadingProgress()
