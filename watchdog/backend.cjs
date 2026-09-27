// Watchdog's managed runner uses Node. Keep Python attached through stdin so
// even an abrupt wrapper exit closes the pipe and shuts down the backend.
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

const root = path.resolve(__dirname, '..');
const python = process.env.REBORN_PYTHON || path.join(root, '.venv', 'bin', 'python');
const db = process.env.REBORN_DB || path.join(root, 'data', 'inventory.sqlite3');
const port = process.env.REBORN_PORT || '8741';
if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
  throw new Error('REBORN_PORT must be a port number from 1 to 65535');
}
if (!fs.existsSync(python)) throw new Error('Create the backend .venv or set REBORN_PYTHON to an absolute Python executable path');
const child = spawn(python, [path.join(root, 'backend_runner.py'), '--desktop', '--db', db, '--port', port], {
  cwd: root, windowsHide: true, stdio: ['pipe', 'ignore', 'ignore'],
});
const children = [child];
if (process.env.REBORN_HEARTBEAT_BIND) {
  children.push(spawn(python, [path.join(root, 'scripts', 'heartbeat-relay.py'), '--managed'], {
    cwd: root, windowsHide: true, stdio: ['pipe', 'ignore', 'ignore'],
  }));
}
let stopping = false;
let exitCode = 0;
let timer;
let healthTimer;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  clearInterval(healthTimer);
  exitCode = code;
  for (const process of children) process.stdin.end();
  timer = setTimeout(() => { for (const process of children) process.kill('SIGTERM'); }, 1000);
}
process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());
process.on('message', message => { if (message === 'shutdown') stop(); });
let remaining = children.length;
for (const child of children) {
  child.stdin.on('error', () => {});
  child.on('error', error => {
    console.error('Backend launch failed:', error.code || 'unknown error');
    stop(1);
  });
  child.on('close', () => {
    remaining -= 1;
    if (!stopping) stop(1);
    if (!remaining) { clearTimeout(timer); process.exit(exitCode); }
  });
}

// Hot-added modules in older Watchdog versions do not get a health timer until
// Protocol restarts. Probe here too so this deployment needs no global restart.
let healthFailures = 0;
let checking = false;
async function checkHealth() {
  if (stopping || checking) return;
  checking = true;
  let healthy = false;
  try {
    const response = await fetch(`http://127.0.0.1:${port}/api/health`, {signal: AbortSignal.timeout(5000)});
    healthy = response.ok && (await response.json()).app === 'RebornITOperations';
    if (healthy && process.env.REBORN_HEARTBEAT_BIND) {
      const relay = await fetch(`http://${process.env.REBORN_HEARTBEAT_BIND}:8742/api/health`, {signal: AbortSignal.timeout(5000)});
      healthy = relay.status === 404;
    }
  } catch {}
  healthFailures = healthy ? 0 : healthFailures + 1;
  try {
    await fetch('http://127.0.0.1:49159/notify', {method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({service:'RebornITOperations',action:'status',status:healthy?'running':'unhealthy'}),
      signal:AbortSignal.timeout(3000)});
  } catch {} // A Control Center/Protocol outage must not kill a healthy backend.
  checking = false;
  if (healthFailures >= 3) stop(1); // PM2 restarts this app only.
}
healthTimer = setInterval(checkHealth, 30000);
setTimeout(checkHealth, 3000).unref();
