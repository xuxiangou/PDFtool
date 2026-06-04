const { app, BrowserWindow, ipcMain, dialog, Menu } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const fs = require('fs')
const axios = require('axios')

const PYTHON_PORT = 18765
let pythonProcess = null
let mainWindow = null
let isCloseApproved = false
let isCloseRequestPending = false
let closeIpcReady = false

const net = require('net')

const singleInstanceLock = app.requestSingleInstanceLock()
if (!singleInstanceLock) {
  app.quit()
}

// 检测端口是否可用
function findAvailablePort(startPort) {
  return new Promise((resolve) => {
    const server = net.createServer()
    server.listen(startPort, '127.0.0.1', () => {
      const port = server.address().port
      server.close(() => resolve(port))
    })
    server.on('error', () => resolve(findAvailablePort(startPort + 1)))
  })
}

let mainWindowPort = PYTHON_PORT

app.on('second-instance', () => {
  if (!mainWindow || mainWindow.isDestroyed()) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.focus()
})

app.whenReady().then(async () => {
  Menu.setApplicationMenu(null)

  const port = await findAvailablePort(18765)
  mainWindowPort = port

  startPythonServer(port)

  const waitForPython = setInterval(async () => {
    try {
      await axios.get(`http://127.0.0.1:${port}/health`, { timeout: 500 })
      clearInterval(waitForPython)
      createWindow(port)
    } catch (_) {}
  }, 300)

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow(port)
  })
})

function startPythonServer(port) {
  const isPackaged = app.isPackaged
  const pyExe = isPackaged
    ? resolvePackagedBackendExecutable()
    : (process.platform === 'win32' ? 'python' : 'python3')

  const script = path.join(__dirname, '../python/main.py')

  const args = isPackaged
    ? ['--port', String(port)]
    : [script, '--port', String(port)]

  console.log('[Main] Starting Python:', pyExe, args)

  pythonProcess = spawn(pyExe, args, {
    env: {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      PYTHONIOENCODING: 'utf-8',
      PYTHONUTF8: '1',
    }
  })

  pythonProcess.stdout.on('data', d => console.log('[Python]', d.toString().trim()))
  pythonProcess.stderr.on('data', d => console.error('[Python]', d.toString().trim()))
  pythonProcess.on('close', code => console.log(`[Python] exited: ${code}`))
}

function resolvePackagedBackendExecutable() {
  if (process.platform !== 'win32') {
    return path.join(process.resourcesPath, 'python', 'pdf_backend')
  }
  const candidates = [
    path.join(process.resourcesPath, 'python', 'pdf_backend.exe'),
    path.join(process.resourcesPath, 'python', 'pdf_backend_slim.exe'),
  ]
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate
    }
  }
  return candidates[0]
}

function createWindow(port) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.focus()
    return mainWindow
  }

  const win = new BrowserWindow({
    width: 1600, height: 960,
    icon: path.join(__dirname, '../logo/logo.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--python-port=${port}`]
    }
  })
  win.setMenuBarVisibility(false)
  if (typeof win.removeMenu === 'function') {
    win.removeMenu()
  }
  mainWindow = win
  win.loadFile(path.join(__dirname, '../renderer/index.html'))
  if (!closeIpcReady) {
    closeIpcReady = true
    ipcMain.on('app:close-response', (_, payload) => {
      isCloseRequestPending = false
      if (!payload || !payload.allow) return
      if (!mainWindow || mainWindow.isDestroyed()) {
        app.quit()
        return
      }
      isCloseApproved = true
      mainWindow.close()
    })
  }
  win.on('close', event => {
    if (isCloseApproved) return
    if (!win.webContents || win.webContents.isDestroyed()) return
    event.preventDefault()
    if (isCloseRequestPending) return
    isCloseRequestPending = true
    try {
      win.webContents.send('app:request-close')
    } catch (_) {
      isCloseRequestPending = false
      isCloseApproved = true
      win.close()
    }
  })
  win.on('closed', () => {
    if (mainWindow === win) mainWindow = null
    isCloseApproved = false
    isCloseRequestPending = false
  })
  require('./ipc_router')(ipcMain, port)
  return win
}

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  if (pythonProcess) pythonProcess.kill()
})
