// Windows process helpers: list processes with command lines (PowerShell/CIM), kill a process tree (taskkill).
'use strict';
const { spawn, spawnSync } = require('child_process');

function runPowerShell(script, timeoutMs = 20000) {
  return new Promise((resolve) => {
    let out = '', err = '';
    let child;
    try {
      child = spawn('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', script], { windowsHide: true });
    } catch (e) {
      return resolve({ code: -1, out, err: String(e) });
    }
    child.stdout.on('data', (d) => { out += d; });
    child.stderr.on('data', (d) => { err += d; });
    const t = setTimeout(() => { try { child.kill(); } catch (_) { /* ignore */ } }, timeoutMs);
    child.on('close', (code) => { clearTimeout(t); resolve({ code, out, err }); });
    child.on('error', (e) => { clearTimeout(t); resolve({ code: -1, out, err: String(e) }); });
  });
}

// Every process: { pid, ppid, name, cmd }.
async function listProcesses() {
  const script = "Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CommandLine | ConvertTo-Json -Compress";
  const r = await runPowerShell(script);
  if (!r.out.trim()) return [];
  try {
    const j = JSON.parse(r.out);
    const arr = Array.isArray(j) ? j : [j];
    return arr.map((p) => ({ pid: p.ProcessId, ppid: p.ParentProcessId, name: p.Name || '', cmd: p.CommandLine || '' }));
  } catch (_) {
    return [];
  }
}

// Processes whose image name matches and whose command line contains `match` (case-insensitive).
function filterProcesses(all, match, name) {
  const m = match.toLowerCase();
  return all.filter((p) => (!name || p.name.toLowerCase() === name.toLowerCase()) && p.cmd.toLowerCase().includes(m));
}

async function findProcesses(match, name) {
  return filterProcesses(await listProcesses(), match, name);
}

// True when `pid` is `rootPid` or has `rootPid` among its ancestors (within the given process list).
function isDescendant(all, pid, rootPid) {
  const byPid = new Map(all.map((p) => [p.pid, p]));
  let cur = pid;
  for (let i = 0; i < 64 && cur; i++) {
    if (cur === rootPid) return true;
    const p = byPid.get(cur);
    if (!p) return false;
    cur = p.ppid;
  }
  return false;
}

function killTree(pid) {
  if (!pid) return false;
  const r = spawnSync('taskkill.exe', ['/PID', String(pid), '/T', '/F'], { windowsHide: true, encoding: 'utf8' });
  return r.status === 0;
}

function isAlive(pid) {
  if (!pid) return false;
  try { process.kill(pid, 0); return true; } catch (_) { return false; }
}

module.exports = { runPowerShell, listProcesses, filterProcesses, findProcesses, isDescendant, killTree, isAlive };
