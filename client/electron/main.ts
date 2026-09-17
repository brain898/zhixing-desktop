import { app, BrowserWindow, ipcMain } from 'electron';
import path from 'path';
import fs from 'fs';
import http from 'http';
import { spawn, ChildProcess } from 'child_process';

app.disableHardwareAcceleration();

let mainWindow: BrowserWindow | null = null;
let backendProcess: ChildProcess | null = null;
let backendOwnedByThisProcess = false;

function checkBackendAlive(): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get('http://127.0.0.1:8766/api/system/status', (res) => {
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.setTimeout(600, () => {
      req.destroy();
      resolve(false);
    });
  });
}

async function ensureBackendRunning() {
  const isAlive = await checkBackendAlive();
  if (isAlive) {
    return;
  }

  const serverDir = path.resolve(__dirname, '../../server');
  const serverScript = path.join(serverDir, 'main.py');

  if (fs.existsSync(serverScript)) {
    try {
      backendProcess = spawn('python', [serverScript], {
        cwd: serverDir,
        stdio: 'ignore',
        windowsHide: true,
      });
      backendOwnedByThisProcess = true;

      backendProcess.on('error', (err) => {
        console.error('Failed to spawn backend process:', err);
      });
      backendProcess.on('exit', () => {
        backendProcess = null;
        backendOwnedByThisProcess = false;
      });

      // 轮询等待后台服务就绪
      for (let i = 0; i < 25; i++) {
        await new Promise((r) => setTimeout(r, 200));
        if (await checkBackendAlive()) {
          break;
        }
      }
    } catch (e) {
      console.error('Error starting backend process:', e);
    }
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    title: '知行有策',
    width: 1440,
    height: 900,
    minWidth: 1200,
    minHeight: 760,
    center: true,
    skipTaskbar: false,
    frame: true,
    show: true,
    backgroundColor: '#FFFFFF',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
    },
  });

  const distHtmlPath = path.join(__dirname, '../dist/index.html');

  if (fs.existsSync(distHtmlPath)) {
    mainWindow.loadFile(distHtmlPath);
  } else {
    mainWindow.loadURL('http://localhost:5173');
  }

  mainWindow.show();
  mainWindow.focus();

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

// 窗口控制 IPC
ipcMain.on('window-minimize', () => {
  if (mainWindow) mainWindow.minimize();
});

ipcMain.on('window-maximize', () => {
  if (mainWindow) {
    if (mainWindow.isMaximized()) {
      mainWindow.unmaximize();
    } else {
      mainWindow.maximize();
    }
  }
});

ipcMain.on('window-close', () => {
  if (mainWindow) mainWindow.close();
});

ipcMain.handle('window-is-maximized', () => {
  return mainWindow ? mainWindow.isMaximized() : false;
});

const gotTheLock = app.requestSingleInstanceLock();

if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
  });

  app.whenReady().then(async () => {
    await ensureBackendRunning();
    createWindow();

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
      }
    });
  });
}

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  if (!backendOwnedByThisProcess) return;

  try {
    const req = http.request(
      {
        hostname: '127.0.0.1',
        port: 8766,
        path: '/api/system/shutdown',
        method: 'POST',
        timeout: 500,
      },
      () => {}
    );
    req.on('error', () => {});
    req.end();
  } catch (e) {
    // 忽略异常
  }

  if (backendProcess) {
    try {
      backendProcess.kill();
    } catch (e) {}
    backendProcess = null;
  }
});
