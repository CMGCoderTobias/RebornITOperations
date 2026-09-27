'use strict';
const {app, BrowserWindow, Menu, Tray, nativeImage, dialog, shell, session} = require('electron');
const {spawn} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

const root = __dirname;
// Packaged distribution stays under this project's desktop/ directory.
// Managed versions live beneath the original installation. Keep settings outside them.
function installationRoot() {
  let directory = path.dirname(process.execPath);
  for (let candidate = directory; candidate !== path.dirname(candidate); candidate = path.dirname(candidate)) {
    if (fs.existsSync(path.join(candidate, '.reborn', 'active.json'))) return candidate;
  }
  return directory;
}
const dataDir = process.env.REBORN_DATA_DIR || (app.isPackaged
  ? process.platform === 'linux' ? path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'), 'reborn-it-operations') : path.resolve(installationRoot(), '../data')
  : path.join(root, 'data'));
const version = process.env.REBORN_APP_VERSION || app.getVersion();
function agentCommand(command) {
  const executable = process.env.REBORN_UPDATE_AGENT_EXE;
  if (!executable || process.env.REBORN_APP_ID !== 'rebornitoperations') return;
  const args = [command, '--app', 'rebornitoperations'];
  if (command === 'health') args.push('--version', process.env.REBORN_APP_VERSION);
  const child = spawn(executable, args, {windowsHide:true, stdio:'ignore'});
  child.on('error', error => log('Updater: '+error.message));
}
const port = process.env.REBORN_PORT || '8741';
let win, tray, quitting = false, appUrl;
fs.mkdirSync(dataDir, {recursive:true});
function log(message) { fs.appendFileSync(path.join(dataDir,'desktop.log'),new Date().toISOString()+' '+message+'\n'); }
app.setName('Reborn IT Operations');
app.setAppUserModelId('com.reborn.itoperations');
app.setPath('userData', path.join(dataDir, 'desktop-profile'));
const hasLock = app.requestSingleInstanceLock();
if (!hasLock) app.quit();
else {
  app.on('second-instance', () => showWindow());
  app.on('activate', () => showWindow());
  app.on('window-all-closed', () => {});
  app.on('before-quit', () => { quitting = true; });
  app.whenReady().then(start).catch(fail);
}

function showWindow() {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show(); win.focus();
}

function fail(error) {
  log('Startup/runtime error: '+error.message);
  dialog.showErrorBox('Reborn IT Operations could not start', `${error.message}\n\nYour inventory is unchanged. Check the server connection and SSH tunnel. Your inventory remains on the server.`);
  quitting = true; app.quit();
}

async function startBackend() {
  const connectionFile = path.join(dataDir, 'backend-connection.json');
  const savedUrl = fs.existsSync(connectionFile) ? JSON.parse(fs.readFileSync(connectionFile, 'utf8')).url : '';
  const configuredUrl = process.env.REBORN_BACKEND_URL || savedUrl;
  const url = configuredUrl || `http://127.0.0.1:${port}`;
  const parsed = new URL(url);
  if (parsed.protocol !== 'http:' || !['127.0.0.1','localhost'].includes(parsed.hostname) || parsed.username || parsed.password || parsed.pathname !== '/' || parsed.search || parsed.hash) throw new Error('Use a loopback backend URL. Remote hosting requires a secure local tunnel until remote UI authentication is implemented.');
  const origin = parsed.origin;
  async function healthy() {
    try { const response = await fetch(origin+'/api/health', {signal:AbortSignal.timeout(1500)}); const value = await response.json(); return response.ok && value.app === 'RebornITOperations'; } catch { return false; }
  }
  for(let attempt=0;attempt<30;attempt++){
    if(await healthy())return origin;
    await new Promise(resolve=>setTimeout(resolve,500));
  }
  if (configuredUrl) throw new Error('Configured backend is unavailable. Check the independent backend or secure tunnel.');
  throw new Error('Backend unavailable. Connect the VPN or check the SSH tunnel. This desktop never starts a local database.');
}

async function start() {
  log('Connecting to shared inventory backend');
  fs.mkdirSync(dataDir, {recursive:true});
  appUrl = await startBackend();
  log('Shared backend ready at '+appUrl);
  const icon = nativeImage.createFromPath(path.join(root, 'assets', 'icon.png'));
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  // The renderer only needs its own local inventory service.
  session.defaultSession.webRequest.onBeforeRequest((details, callback) => {
    callback({cancel: !(details.url === appUrl || details.url.startsWith(appUrl + '/') || details.url.startsWith('devtools://'))});
  });
  win = new BrowserWindow({width:1390,height:900,minWidth:760,minHeight:560,title:'Reborn IT Operations',icon,show:false,backgroundColor:'#f5f7f8',webPreferences:{nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true,devTools:!app.isPackaged}});
  log('Desktop window created');
  win.webContents.setWindowOpenHandler(() => ({action:'deny'}));
  win.webContents.on('will-navigate', (event, url) => { if (!(url === appUrl+'/' || url.startsWith(appUrl+'/#'))) event.preventDefault(); });
  win.on('close', event => { if (!quitting && tray) { event.preventDefault(); win.hide(); } });
  const show = () => showWindow();
  const openData = () => shell.openPath(dataDir);
  const quit = () => app.quit();
  tray = new Tray(icon.resize({width:32,height:32}));
  tray.setToolTip('Reborn IT Operations — independent backend');
  tray.setContextMenu(Menu.buildFromTemplate([{label:'Open Reborn IT Operations',click:show},{label:'Open data folder',click:openData},{type:'separator'},{label:'Quit desktop app',click:quit}]));
  tray.on('double-click', show);
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    {label:'File',submenu:[{label:'Open data folder',click:openData},{label:'Hide to tray',click:()=>win.hide()},{type:'separator'},{label:'Quit desktop app',accelerator:'Alt+F4',click:quit}]},
    {label:'Edit',submenu:[{role:'undo'},{role:'redo'},{type:'separator'},{role:'cut'},{role:'copy'},{role:'paste'},{role:'selectAll'}]},
    {label:'View',submenu:[{role:'reload'},{role:'resetZoom'},{role:'zoomIn'},{role:'zoomOut'},{role:'togglefullscreen'}]},
    {label:'Help',submenu:[{label:'Check for updates',click:()=>agentCommand('request-check')},{label:'About Reborn IT Operations',click:()=>dialog.showMessageBox(win,{type:'info',title:'Reborn IT Operations',message:'Reborn IT Operations '+version,detail:'Desktop client for the shared Reborn IT backend.\n\nThe database and monitoring run independently. Quitting this client does not stop the backend.\n\nData: '+dataDir})}]}
  ]));
  await win.loadURL(appUrl);
  log('Interface loaded');
  win.show();
  log('Desktop window shown');
  agentCommand('health');
}
