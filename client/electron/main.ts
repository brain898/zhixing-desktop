import { app, BrowserWindow, Menu } from 'electron';
import path from 'path';
import fs from 'fs';
import http from 'http';
import { spawn, ChildProcess } from 'child_process';

app.disableHardwareAcceleration();
if (process.platform === 'win32') {
  // 让任务栏使用应用自身图标分组，而不是 Electron 默认图标
  app.setAppUserModelId('com.zhixing.desktop');
}

let mainWindow: BrowserWindow | null = null;
let backendProcess: ChildProcess | null = null;
let backendOwnedByThisProcess = false;
const backendUrl = (process.env.ZHIXING_BACKEND_URL || 'http://127.0.0.1:8766').replace(/\/$/, '');

// 过滤敏感密钥信息与 Ring Buffer 环形错误日志缓冲（最大 4KB）
class StderrRingBuffer {
  private buffer: string[] = [];
  private totalLength = 0;
  private readonly maxBytes: number;

  constructor(maxBytes = 4096) {
    this.maxBytes = maxBytes;
  }

  append(chunk: string) {
    // 过滤 API Key、Token 等敏感鉴权凭据
    const sanitized = chunk
      .replace(/sk-[a-zA-Z0-9_-]+/g, 'sk-***')
      .replace(/DEEPSEEK_API_KEY\s*[:=]\s*['"]?[a-zA-Z0-9_-]+['"]?/gi, 'DEEPSEEK_API_KEY=***')
      .replace(/bearer\s+[a-zA-Z0-9._-]+/gi, 'Bearer ***');

    this.buffer.push(sanitized);
    this.totalLength += Buffer.byteLength(sanitized, 'utf8');

    while (this.totalLength > this.maxBytes && this.buffer.length > 0) {
      const removed = this.buffer.shift()!;
      this.totalLength -= Buffer.byteLength(removed, 'utf8');
    }
  }

  clear() {
    this.buffer = [];
    this.totalLength = 0;
  }

  getText(): string {
    return this.buffer.join('');
  }
}

const stderrRingBuffer = new StderrRingBuffer(4096);

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// 与 public/icon.svg 一致的品牌标识，内联到启动页（data URL 页面无法引用本地资源）
const LOGO_SVG = `<svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="zx-tile" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3D7A62"/><stop offset="1" stop-color="#17382D"/></linearGradient></defs><rect width="64" height="64" rx="15" fill="url(#zx-tile)"/><path d="M10.5 26C18.5 22.8 26.3 23.2 31 27.8V48.5C26.3 44.4 18.5 43.9 10.5 46.5Z" fill="#FFFFFF"/><path d="M53.5 26C45.5 22.8 37.7 23.2 33 27.8V48.5C37.7 44.4 45.5 43.9 53.5 46.5Z" fill="#FFFFFF" fill-opacity="0.8"/><path d="M32 5.5Q33.5 13.2 40.5 14.8Q33.5 16.4 32 24.1Q30.5 16.4 23.5 14.8Q30.5 13.2 32 5.5Z" fill="#E8B64C"/></svg>`;

function resolveAppIcon(): string | undefined {
  // 生产构建时 Vite 把 public/ 复制到 dist/；开发模式直接读 public/
  const candidates = process.platform === 'win32' ? ['icon.ico', 'icon.png'] : ['icon.png'];
  for (const dir of ['../dist', '../public']) {
    for (const name of candidates) {
      const iconPath = path.join(__dirname, dir, name);
      if (fs.existsSync(iconPath)) return iconPath;
    }
  }
  return undefined;
}

function renderStartupHtml(state: 'starting' | 'failed', errorLog?: string): string {
  const isFailed = state === 'failed';
  const cleanLog = errorLog ? escapeHtml(errorLog.slice(-1500)) : '';

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>知行有策 - 服务启动</title>
  <style>
    :root {
      --brand-accent: #285C49;
      --brand-accent-hover: #204B3C;
      --bg-color: #F6F7F6;
      --card-bg: #FFFFFF;
      --text-primary: #1B211E;
      --text-secondary: #5B665F;
      --border-color: #E4E7E5;
      --danger-color: #B42318;
      --danger-bg: #FDF1F0;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: "Microsoft YaHei UI", "Segoe UI Variable", "Noto Sans SC", "Microsoft YaHei", "Segoe UI", sans-serif;
      background-color: var(--bg-color);
      background-image: radial-gradient(circle at 50% 38%, rgba(61, 122, 98, 0.08), transparent 55%);
      color: var(--text-primary);
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      padding: 24px;
      user-select: none;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--border-color);
      border-radius: 16px;
      padding: 40px 40px 36px;
      width: 520px;
      max-width: 90vw;
      box-shadow: 0 16px 48px rgba(20, 32, 26, 0.10), 0 2px 8px rgba(20, 32, 26, 0.04);
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
    }
    .logo-badge {
      width: 56px;
      height: 56px;
      border-radius: 14px;
      display: flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 20px;
      font-size: 24px;
      font-weight: 700;
      box-shadow: 0 8px 20px rgba(23, 56, 45, 0.22);
    }
    .logo-badge svg { width: 56px; height: 56px; display: block; }
    .logo-badge.failed {
      background: var(--danger-bg);
      color: var(--danger-color);
      box-shadow: none;
    }
    .title {
      font-size: 18px;
      font-weight: 600;
      margin-bottom: 8px;
    }
    .subtitle {
      font-size: 13px;
      color: var(--text-secondary);
      line-height: 1.6;
      margin-bottom: 24px;
    }
    .spinner {
      width: 28px;
      height: 28px;
      border: 3px solid rgba(40, 92, 73, 0.15);
      border-top-color: var(--brand-accent);
      border-radius: 50%;
      animation: spin 0.8s linear infinite;
      margin-bottom: 12px;
    }
    @keyframes spin {
      to { transform: rotate(360deg); }
    }
    .log-box {
      width: 100%;
      background: #17201C;
      color: #E3EDE7;
      border-radius: 6px;
      padding: 12px;
      font-family: Consolas, Monaco, "Courier New", monospace;
      font-size: 11px;
      line-height: 1.45;
      text-align: left;
      white-space: pre-wrap;
      word-break: break-all;
      max-height: 140px;
      overflow-y: auto;
      margin-bottom: 20px;
      user-select: text;
    }
    .retry-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      height: 38px;
      padding: 0 20px;
      border-radius: 6px;
      background-color: var(--brand-accent);
      color: #FFFFFF;
      font-size: 13px;
      font-weight: 600;
      text-decoration: none;
      border: none;
      cursor: pointer;
      transition: background-color 0.15s ease;
    }
    .retry-btn:hover {
      background-color: var(--brand-accent-hover);
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo-badge ${isFailed ? 'failed' : ''}">
      ${isFailed ? '!' : LOGO_SVG}
    </div>
    <div class="title">
      ${isFailed ? '后端服务启动失败' : '知行有策 服务启动中...'}
    </div>
    <div class="subtitle">
      ${isFailed 
        ? '本地后台服务未能按预期就绪。请检查以下诊断日志或点击重试。' 
        : '正在初始化本地数据库与业务运行时，请稍候...'}
    </div>
    ${isFailed ? '' : '<div class="spinner"></div>'}
    ${isFailed && cleanLog ? `<div class="log-box">${cleanLog}</div>` : ''}
    ${isFailed ? '<a href="action:retry" class="retry-btn">重试启动</a>' : ''}
  </div>
</body>
</html>`;
}

function checkBackendAlive(): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get(`${backendUrl}/api/system/status`, (res) => {
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.setTimeout(600, () => {
      req.destroy();
      resolve(false);
    });
  });
}

function loadStartupView(state: 'starting' | 'failed', errorLog?: string) {
  if (!mainWindow) return;
  const html = renderStartupHtml(state, errorLog);
  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
}

function loadMainApp() {
  if (!mainWindow) return;
  const distHtmlPath = path.join(__dirname, '../dist/index.html');
  if (fs.existsSync(distHtmlPath)) {
    mainWindow.loadFile(distHtmlPath);
  } else {
    mainWindow.loadURL('http://localhost:5173');
  }
}

async function stopOwnedBackend(timeoutMs = 4000): Promise<boolean> {
  if (!backendOwnedByThisProcess || !backendProcess) {
    backendProcess = null;
    backendOwnedByThisProcess = false;
    return true;
  }

  const proc = backendProcess;
  return new Promise((resolve) => {
    let settled = false;

    const finalize = (success: boolean) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (backendProcess === proc) {
        backendProcess = null;
        backendOwnedByThisProcess = false;
      }
      resolve(success);
    };

    const timer = setTimeout(() => {
      finalize(false);
    }, timeoutMs);

    if (proc.killed || proc.exitCode !== null) {
      finalize(true);
      return;
    }

    proc.once('exit', () => {
      finalize(true);
    });

    try {
      proc.kill();
    } catch {
      finalize(true);
    }
  });
}

interface BackendRuntimeInfo {
  pythonExe: string;
  serverDir: string;
  serverScript: string;
  portableRoot: string;
  isPackaged: boolean;
}

function resolveBackendRuntime(): BackendRuntimeInfo {
  // 1. 优先检测便携包打包运行结构：
  const portableRootFromResources = path.resolve(process.resourcesPath, '..');
  const bundledPyFromResources = path.join(portableRootFromResources, 'backend', 'python', 'python.exe');
  const bundledServerFromResources = path.join(portableRootFromResources, 'backend', 'server');
  const bundledScriptFromResources = path.join(bundledServerFromResources, 'main.py');

  if (fs.existsSync(bundledPyFromResources) && fs.existsSync(bundledScriptFromResources)) {
    return {
      pythonExe: bundledPyFromResources,
      serverDir: bundledServerFromResources,
      serverScript: bundledScriptFromResources,
      portableRoot: portableRootFromResources,
      isPackaged: true,
    };
  }

  // 2. 备用检测：基于 app.getAppPath() 向上寻找便携包根目录
  try {
    const appDir = app.getAppPath();
    const candidateRoot = path.resolve(appDir, '../..');
    const candPy = path.join(candidateRoot, 'backend', 'python', 'python.exe');
    const candServer = path.join(candidateRoot, 'backend', 'server');
    const candScript = path.join(candServer, 'main.py');
    if (fs.existsSync(candPy) && fs.existsSync(candScript)) {
      return {
        pythonExe: candPy,
        serverDir: candServer,
        serverScript: candScript,
        portableRoot: candidateRoot,
        isPackaged: true,
      };
    }
  } catch {}

  // 3. 源码开发模式：直接使用当前项目的 server/main.py
  const devServerDir = path.resolve(__dirname, '../../server');
  const devScript = path.join(devServerDir, 'main.py');
  const devPython = process.env.ZHIXING_PYTHON || 'python';
  return {
    pythonExe: devPython,
    serverDir: devServerDir,
    serverScript: devScript,
    portableRoot: path.resolve(__dirname, '../..'),
    isPackaged: false,
  };
}

async function ensureBackendRunning(): Promise<boolean> {
  const isAlive = await checkBackendAlive();
  if (isAlive) {
    return true;
  }

  const runtime = resolveBackendRuntime();

  if (!fs.existsSync(runtime.serverScript)) {
    stderrRingBuffer.append(`未找到后端入口脚本: ${runtime.serverScript}\n`);
    return false;
  }

  try {
    // 若此前已由本实例启动过后端，必须先确认旧实例退出，避免多进程冲突与失去管理
    if (backendOwnedByThisProcess && backendProcess) {
      const stopped = await stopOwnedBackend(4000);
      if (!stopped) {
        stderrRingBuffer.append('无法确认旧后端进程退出，终止重试以防止多实例所有权竞争\n');
        return false;
      }
    }

    const spawnEnv: NodeJS.ProcessEnv = {
      ...process.env,
      PYTHONIOENCODING: 'utf-8',
    };

    if (runtime.isPackaged) {
      spawnEnv.ZHIXING_PORTABLE_ROOT = runtime.portableRoot;
      const pyDir = path.dirname(runtime.pythonExe);
      spawnEnv.PATH = `${pyDir};${process.env.PATH || ''}`;
      delete spawnEnv.PYTHONHOME;
      delete spawnEnv.PYTHONPATH;
    }

    const currentProc = spawn(runtime.pythonExe, [runtime.serverScript], {
      cwd: runtime.serverDir,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
      env: spawnEnv,
    });

    backendProcess = currentProc;
    backendOwnedByThisProcess = true;

    currentProc.stderr?.on('data', (data) => {
      const text = data.toString('utf8');
      stderrRingBuffer.append(text);
    });

    let hasExitedEarly = false;
    currentProc.on('error', (err) => {
      // 仅当触发回调的进程仍是当前管理的子进程时，才更新全局状态
      if (backendProcess === currentProc) {
        stderrRingBuffer.append(`启动失败: ${err.message}\n`);
        backendProcess = null;
        backendOwnedByThisProcess = false;
      }
    });

    currentProc.on('exit', (code, signal) => {
      // 仅当触发回调的进程仍是当前管理的子进程时，才更新全局状态
      if (backendProcess === currentProc) {
        if (backendOwnedByThisProcess) {
          stderrRingBuffer.append(`服务异常退出 (code: ${code}, signal: ${signal})\n`);
        }
        backendProcess = null;
        backendOwnedByThisProcess = false;
      }
      hasExitedEarly = true;
    });

    // 轮询等待后台服务就绪 (50次 * 200ms = 10秒，兼容冷启动与模型载入)
    for (let i = 0; i < 50; i++) {
      if (hasExitedEarly) {
        break;
      }
      await new Promise((r) => setTimeout(r, 200));
      if (await checkBackendAlive()) {
        return true;
      }
    }

    return await checkBackendAlive();
  } catch (e: any) {
    stderrRingBuffer.append(`启动异常: ${e.message}\n`);
    return false;
  }
}

let activeStartupPromise: Promise<boolean> | null = null;

function ensureBackendRunningSerialized(): Promise<boolean> {
  if (activeStartupPromise) {
    return activeStartupPromise;
  }
  activeStartupPromise = (async () => {
    try {
      return await ensureBackendRunning();
    } finally {
      activeStartupPromise = null;
    }
  })();
  return activeStartupPromise;
}

let isRetrying = false;
async function handleRetry() {
  if (isRetrying) return;
  isRetrying = true;
  try {
    const isAlive = await checkBackendAlive();
    if (isAlive) {
      loadMainApp();
      return;
    }

    stderrRingBuffer.clear();
    loadStartupView('starting');

    const ok = await ensureBackendRunningSerialized();
    if (ok) {
      loadMainApp();
    } else {
      const log = stderrRingBuffer.getText() || '服务重试启动超时，未能成功建立连接 (http://127.0.0.1:8766)';
      loadStartupView('failed', log);
    }
  } finally {
    isRetrying = false;
  }
}

function createWindow() {
  Menu.setApplicationMenu(null);

  mainWindow = new BrowserWindow({
    title: '知行有策',
    autoHideMenuBar: true,
    width: 1440,
    height: 900,
    minWidth: 1200,
    minHeight: 760,
    center: true,
    skipTaskbar: false,
    frame: true,
    show: true,
    backgroundColor: '#F6F7F6',
    icon: resolveAppIcon(),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: false,
    },
  });

  // 拦截应用内重试点击
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (url === 'action:retry' || url.startsWith('action:retry')) {
      event.preventDefault();
      handleRetry();
    }
  });

  mainWindow.show();
  mainWindow.focus();

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

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
    createWindow();

    const isAlive = await checkBackendAlive();
    if (isAlive) {
      loadMainApp();
    } else {
      loadStartupView('starting');
      const ok = await ensureBackendRunningSerialized();
      if (ok) {
        loadMainApp();
      } else {
        const log = stderrRingBuffer.getText() || '后台服务初始化超时，未能成功建立连接 (http://127.0.0.1:8766)';
        loadStartupView('failed', log);
      }
    }

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
        checkBackendAlive().then((alive) => {
          if (alive) loadMainApp();
          else loadStartupView('failed', stderrRingBuffer.getText());
        });
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
  if (backendProcess) {
    try {
      backendProcess.kill();
    } catch (e) {}
    backendProcess = null;
  }
});
