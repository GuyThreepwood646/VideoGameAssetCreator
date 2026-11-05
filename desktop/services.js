// Service supervisor: probes the four services, starts the ones the launcher may own (ComfyUI B, backend),
// builds the frontend when frontend/dist is missing, and stops only what it started.
//
// Graphics cards turned off in the app (GET /api/gpus, or <projectRoot>/library/prefs.json before the backend
// answers): while card "b" is off the launcher does not start ComfyUI B, stops the ComfyUI B it started (once), and
// shows "Off (turned off in the app)" with a "Start anyway" action that turns the card back on through
// PUT /api/gpus/b. ComfyUI A belongs to Comfy Desktop and is never stopped; when "a" is off it is only marked.
//
// The backend also starts ComfyUI A and B on demand (backend/vgac/instances.py) and reports it in GET /api/gpus
// (state running | starting | stopped | failed | off, owned). The launcher does not fight it: a ComfyUI B the
// launcher did not start is foreign and left alone (never stopped on quit or when card b is off), and the
// launcher's own one-time auto-start of B is skipped while the backend reports b as "starting".
'use strict';
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const EventEmitter = require('events');
const procs = require('./procs');

const PROBE_TIMEOUT_MS = 3000;

async function probe(url) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), PROBE_TIMEOUT_MS);
  try {
    const res = await fetch(url, { signal: ctl.signal, redirect: 'manual', headers: { accept: '*/*' } });
    const text = await res.text();
    return { ok: res.ok, status: res.status, contentType: res.headers.get('content-type') || '', text };
  } catch (e) {
    const code = e && e.cause && e.cause.code;
    return { ok: false, status: 0, error: e.name === 'AbortError' ? 'timeout' : (code || e.message) };
  } finally {
    clearTimeout(t);
  }
}

class Supervisor extends EventEmitter {
  constructor(cfg, log) {
    super();
    this.cfg = cfg;
    this.log = log || (() => {});
    this.children = { comfyB: null, backend: null, build: null };
    this.startedAt = {};
    this.autoStarted = { comfyB: false, backend: false, build: false, backendRestart: false };
    this.distExists = false;
    this.buildLines = [];
    this.ticking = false;
    this.timer = null;
    this.allUp = false;
    this.lastUp = {};
    // Graphics cards from the backend: { a: { enabled, state, owned, canStart }, b: ... }, or null before the first
    // answer (state, owned and canStart are only known from the backend, not from prefs.json).
    this.gpus = null;
    this.gpuSource = null; // 'backend' | 'prefs'
    this.stoppedForOff = false; // ComfyUI B was stopped once because card b was turned off
    this.pendingEnable = new Set(); // cards to turn back on once the backend answers ("Start anyway" while it was down)
    this.services = {
      comfyA: this._svc('comfyA', cfg.comfyA.name, cfg.comfyA.url),
      comfyB: this._svc('comfyB', cfg.comfyB.name, cfg.comfyB.url),
      backend: this._svc('backend', cfg.backend.name, cfg.backend.url + cfg.backend.healthPath),
      ui: this._svc('ui', cfg.frontend.name, cfg.backend.url + '/'),
    };
  }

  _svc(key, name, url) {
    return { key, name, url, state: 'unknown', message: 'Checking', owned: false, pid: null, actions: [] };
  }

  resolve(p) { return this.cfg.resolve(p); }

  start() {
    this.tick();
    this.timer = setInterval(() => this.tick(), this.cfg.pollIntervalMs || 2000);
  }

  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  isOwned(key) {
    const c = this.children[key];
    return !!(c && c.pid && procs.isAlive(c.pid));
  }

  snapshot() {
    const services = ['comfyA', 'comfyB', 'backend', 'ui'].map((k) => {
      const s = this.services[k];
      return { key: s.key, name: s.name, url: s.url, state: s.state, message: s.message, owned: s.owned, pid: s.pid, actions: s.actions };
    });
    return { services, allUp: this.allUp, build: { active: !!this.children.build, lines: this.buildLines.slice(-40) } };
  }

  async tick() {
    if (this.ticking) return;
    this.ticking = true;
    try {
      const cfg = this.cfg;
      const [a, b, h, u] = await Promise.all([
        probe(cfg.comfyA.url + '/system_stats'),
        probe(cfg.comfyB.url + '/system_stats'),
        probe(cfg.backend.url + cfg.backend.healthPath),
        probe(cfg.backend.url + '/'),
      ]);
      this.distExists = fs.existsSync(path.join(this.resolve(cfg.frontend.dist), 'index.html'));
      await this._readGpus(h);
      this._applyComfyA(a);
      if (this.gpuOff('b')) await this._applyComfyBOff(b);
      else await this._applyStartable('comfyB', b, () => this.startComfyB());
      await this._applyStartable('backend', h, () => this.startBackend(), (r) => {
        try { return r.ok && JSON.parse(r.text).ok === true; } catch (_) { return false; }
      });
      await this._applyUi(h, u);
      // A card turned off in the app counts as fine: the app carries on with the other one.
      const allUp = ['comfyA', 'comfyB', 'backend', 'ui'].every((k) => this.services[k].state === 'up' || this.services[k].state === 'off');
      const wasAllUp = this.allUp;
      this.allUp = allUp;
      for (const k of ['comfyA', 'comfyB', 'backend', 'ui']) {
        const up = this.services[k].state === 'up';
        if (up && this.lastUp[k] === false) this.emit('service-up', k);
        this.lastUp[k] = up;
      }
      this.emit('state', this.snapshot());
      if (allUp && !wasAllUp) this.emit('all-up');
    } catch (e) {
      this.log('tick error: ' + (e && e.stack || e));
    } finally {
      this.ticking = false;
    }
  }

  _set(key, state, message, actions) {
    const s = this.services[key];
    const changed = s.state !== state || s.message !== message;
    s.state = state;
    s.message = message;
    s.actions = actions || [];
    s.owned = key === 'comfyB' || key === 'backend' ? this.isOwned(key) : false;
    s.pid = s.owned ? this.children[key].pid : null;
    if (changed) this.log(`${key}: ${state} - ${message}`);
  }

  _applyComfyA(r) {
    // Never stopped by the launcher, even when card a is off in the app: Comfy Desktop owns it.
    const off = this.gpuOff('a') ? '. Off in the app' : '';
    const eng = this.backendEngine('a');
    if (r.ok) return this._set('comfyA', 'up', (eng.owned ? 'Running (started by the app when a step needed it)' : 'Running (started by ComfyUI Desktop)') + off);
    if (eng.state === 'starting') return this._set('comfyA', 'starting', 'The app is starting it (about a minute)');
    const exe = this.cfg.comfyA.desktopExe;
    const actions = exe && fs.existsSync(exe) ? [{ id: 'openComfyDesktop', label: 'Open ComfyUI Desktop' }] : [];
    this._set('comfyA', 'down', (this.gpuOff('a') ? 'Not running' : 'Open ComfyUI Desktop and start the GameDevComfy instance') + off, this.gpuOff('a') ? [] : actions);
  }

  // ---- graphics cards turned off in the app ------------------------------------------------------------------

  gpuOff(instance) {
    if (this.pendingEnable.has(instance)) return false;
    return !!(this.gpus && this.gpus[instance] && this.gpus[instance].enabled === false);
  }

  // What the backend says about the ComfyUI of a card: 'starting' while it is starting it, owned when it started it.
  backendEngine(instance) {
    const g = this.gpus && this.gpus[instance];
    if (!g || this.gpuSource !== 'backend' || !this.backendUp) return { state: null, owned: false, canStart: false };
    return { state: g.state || null, owned: !!g.owned, canStart: !!g.canStart };
  }

  _prefsPath() {
    return this.resolve(this.cfg.prefsFile || 'library/prefs.json');
  }

  // Card state from GET /api/gpus when the backend answers; before that, from prefs.json (so a card that is off is
  // not started at launch only to be stopped a moment later).
  async _readGpus(health) {
    const wasOffB = this.gpuOff('b');
    let backendOk = false;
    try { backendOk = health.ok && JSON.parse(health.text).ok === true; } catch (_) { backendOk = false; }
    this.backendUp = backendOk;
    if (backendOk) {
      for (const inst of Array.from(this.pendingEnable)) {
        if (await this.setGpuEnabled(inst, true)) this.pendingEnable.delete(inst);
      }
      const r = await probe(this.cfg.backend.url + '/api/gpus');
      if (r.ok) {
        try {
          const rows = JSON.parse(r.text);
          const next = {};
          for (const g of rows) {
            next[g.instance] = { enabled: g.enabled !== false, state: g.state || null, owned: !!g.owned, canStart: !!g.can_start };
          }
          this.gpus = next;
          this.gpuSource = 'backend';
        } catch (e) {
          this.log('could not read /api/gpus: ' + e.message);
        }
      }
    } else if (this.gpuSource !== 'backend') {
      try {
        const prefs = JSON.parse(fs.readFileSync(this._prefsPath(), 'utf8'));
        const off = Array.isArray(prefs.disabled_instances) ? prefs.disabled_instances : [];
        this.gpus = { a: { enabled: !off.includes('a') }, b: { enabled: !off.includes('b') } };
        this.gpuSource = 'prefs';
      } catch (_) {
        if (this.gpuSource === null) this.gpus = null; // no file yet: every card on
      }
    }
    const offB = this.gpuOff('b');
    if (wasOffB && !offB) {
      // Card b is back on: resume the normal auto-start.
      this.log('card b turned back on in the app; ComfyUI B may start again');
      this.autoStarted.comfyB = false;
      this.stoppedForOff = false;
      if (this.services.comfyB.state === 'off') this.services.comfyB.state = 'down';
    }
  }

  async _applyComfyBOff(r) {
    const actions = [{ id: 'startComfyB', label: 'Start anyway' }];
    if (this.isOwned('comfyB') && !this.stoppedForOff) {
      const child = this.children.comfyB;
      this.stoppedForOff = true;
      this.children.comfyB = null; // so the exit handler does not flag an error
      this.log(`comfyB: card b turned off in the app; stopping ComfyUI B (PID ${child.pid}) to free the card`);
      procs.killTree(child.pid);
      return this._set('comfyB', 'off', 'Off (turned off in the app)', actions);
    }
    if (this.isOwned('comfyB')) return this._set('comfyB', 'off', 'Off (turned off in the app), stopping', actions);
    if (r.ok) {
      // Started outside this launcher: left alone. The app sends it no work while the card is off.
      return this._set('comfyB', 'off', 'Off in the app (still running, started outside this launcher, left alone)',
        [{ id: 'startComfyB', label: 'Turn on in the app' }]);
    }
    this._set('comfyB', 'off', 'Off (turned off in the app)', actions);
  }

  // PUT /api/gpus/{instance} {enabled}. Returns whether the backend took it.
  async setGpuEnabled(instance, enabled) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), PROBE_TIMEOUT_MS);
    try {
      const res = await fetch(`${this.cfg.backend.url}/api/gpus/${encodeURIComponent(instance)}`, {
        method: 'PUT', signal: ctl.signal, headers: { 'content-type': 'application/json' }, body: JSON.stringify({ enabled }),
      });
      this.log(`card ${instance}: set ${enabled ? 'on' : 'off'} in the app -> HTTP ${res.status}`);
      return res.ok;
    } catch (e) {
      this.log(`card ${instance}: could not reach the backend to turn it ${enabled ? 'on' : 'off'}: ${e.message}`);
      return false;
    } finally {
      clearTimeout(t);
    }
  }

  async _applyStartable(key, r, startFn, okFn) {
    const s = this.services[key];
    const cfg = this.cfg[key];
    const ok = okFn ? okFn(r) : r.ok;
    const eng = key === 'comfyB' ? this.backendEngine('b') : { state: null, owned: false, canStart: false };
    if (ok) {
      const owned = this.isOwned(key);
      const outside = eng.owned ? 'Running (started by the app when a step needed it)' : 'Running (started outside this launcher, left alone on quit)';
      this._set(key, 'up', owned ? 'Running (started by this launcher)' : outside,
        [{ id: key === 'backend' ? 'restartBackend' : 'restartComfyB', label: 'Restart' }]);
      return;
    }
    // Not answering. Are we waiting on a process we started?
    if (this.isOwned(key)) {
      const elapsed = (Date.now() - (this.startedAt[key] || Date.now())) / 1000;
      if (elapsed > (cfg.startTimeoutSeconds || 300)) {
        this._set(key, 'error', `Started ${Math.round(elapsed)} s ago and still not answering; see ${path.basename(this.resolve(cfg.log))}`,
          [{ id: key === 'backend' ? 'restartBackend' : 'restartComfyB', label: 'Restart' }]);
      } else {
        this._set(key, 'starting', `Starting (${Math.round(elapsed)} s)`);
      }
      return;
    }
    if (eng.state === 'starting' && !this.isOwned(key)) {
      // The backend is starting it for a step: do not start a second copy.
      this._set(key, 'starting', 'The app is starting it (about a minute)');
      return;
    }
    if (s.state === 'error' && s.message.startsWith('Exited')) return; // wait for the user
    if (key === 'backend' && !this.distExists) {
      // Build first: the backend mounts frontend/dist at import time.
      this._set('backend', 'down', 'Waiting for the UI build before starting');
      return;
    }
    if (key === 'backend' && this.children.build) {
      this._set('backend', 'down', 'Waiting for the UI build before starting');
      return;
    }
    // Something not ours may be starting on that port: do not start a duplicate.
    const external = await procs.findProcesses(cfg.processMatch, cfg.processName);
    if (external.length) {
      this._set(key, 'starting', `Found a process started outside this launcher (PID ${external[0].pid}); waiting for it to answer`,
        [{ id: key === 'backend' ? 'restartBackend' : 'restartComfyB', label: 'Restart' }]);
      return;
    }
    if (!this.autoStarted[key]) {
      this.autoStarted[key] = true;
      startFn();
      return;
    }
    if (s.state !== 'error') {
      const why = eng.canStart ? 'Not running. The app starts it by itself when a step needs it' : 'Not running';
      this._set(key, 'down', why, [{ id: key === 'backend' ? 'startBackend' : 'startComfyB', label: 'Start' }]);
    }
  }

  async _applyUi(h, u) {
    const backendUp = this.services.backend.state === 'up';
    const isHtml = u.ok && /text\/html/i.test(u.contentType);
    if (backendUp && isHtml) return this._set('ui', 'up', 'Served by the backend');
    if (this.children.build) {
      return this._set('ui', 'building', 'Building the frontend (npm run build)');
    }
    if (!this.distExists) {
      if (!this.autoStarted.build) {
        this.autoStarted.build = true;
        this.startBuild();
        return this._set('ui', 'building', 'frontend/dist missing; building the frontend (npm run build)');
      }
      if (this.services.ui.state === 'error') return; // build failed; wait for the user
      return this._set('ui', 'down', 'frontend/dist missing', [{ id: 'retryBuild', label: 'Build' }]);
    }
    if (!backendUp) return this._set('ui', 'down', 'Waiting for the backend');
    // Backend up, dist exists, but / is not HTML: the backend started before the build.
    if (this.isOwned('backend') && !this.autoStarted.backendRestart) {
      this.autoStarted.backendRestart = true;
      this._set('ui', 'down', 'Backend started before the UI build; restarting it');
      this.restart('backend', { force: true }).catch((e) => this.log('auto restart failed: ' + e));
      return;
    }
    this._set('ui', 'down', 'The backend started before the UI build; restart it to serve the UI', [{ id: 'restartBackend', label: 'Restart backend' }]);
  }

  _openLog(p) {
    fs.mkdirSync(path.dirname(p), { recursive: true });
    try {
      return { fd: fs.openSync(p, 'a'), path: p };
    } catch (e) {
      // Another process (for example a PowerShell Start-Process redirect) holds the file. Use a sibling.
      const alt = p.replace(/(\.[^.\\/]+)?$/, '.launcher$1');
      this.log(`cannot open ${p} (${e.code}); logging to ${alt}`);
      return { fd: fs.openSync(alt, 'a'), path: alt };
    }
  }

  startComfyB() {
    if (this.gpuOff('b')) {
      // "Start anyway" (or a restart) while card b is off in the app: turn it back on there first. If the backend
      // is down this is sent as soon as it answers; until then the card counts as on here.
      this.pendingEnable.add('b');
      this.stoppedForOff = false;
      if (this.gpus && this.gpus.b) this.gpus.b.enabled = true;
      this.setGpuEnabled('b', true).then((ok) => { if (ok) this.pendingEnable.delete('b'); });
      if (this.services.comfyB.state === 'off' && /still running/.test(this.services.comfyB.message)) {
        this.services.comfyB.state = 'down';
        return; // already running outside the launcher: turning the card on is all it needs
      }
    }
    const cfg = this.cfg.comfyB;
    const script = this.resolve(cfg.startScript);
    if (!fs.existsSync(script)) return this._set('comfyB', 'error', `Start script not found: ${script}`);
    const log = this._openLog(this.resolve(cfg.log));
    this._spawnOwned('comfyB', 'powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', script],
      { cwd: path.dirname(script), log });
  }

  startBackend() {
    const cfg = this.cfg.backend;
    const python = this.resolve(cfg.python);
    if (!fs.existsSync(python)) return this._set('backend', 'error', `Python not found: ${python}`);
    const log = this._openLog(this.resolve(cfg.log));
    this._spawnOwned('backend', python, cfg.args, { cwd: this.resolve(cfg.cwd), log, env: { PYTHONUNBUFFERED: '1' } });
  }

  _spawnOwned(key, exe, args, opts) {
    const stamp = new Date().toISOString();
    fs.writeSync(opts.log.fd, `\n[${stamp}] VGAC launcher: ${exe} ${args.join(' ')}\n`);
    let child;
    try {
      child = spawn(exe, args, {
        cwd: opts.cwd,
        windowsHide: true,
        stdio: ['ignore', opts.log.fd, opts.log.fd],
        env: Object.assign({}, process.env, opts.env || {}),
      });
    } catch (e) {
      fs.closeSync(opts.log.fd);
      return this._set(key, 'error', `Could not start: ${e.message}`);
    }
    this.children[key] = child;
    this.startedAt[key] = Date.now();
    this.log(`${key}: started PID ${child.pid}: ${exe} ${args.join(' ')} (log ${opts.log.path})`);
    this._set(key, 'starting', 'Starting (0 s)');
    child.on('error', (e) => this.log(`${key}: spawn error ${e.message}`));
    child.on('exit', (code, signal) => {
      try { fs.closeSync(opts.log.fd); } catch (_) { /* ignore */ }
      if (this.children[key] !== child) return;
      this.children[key] = null;
      this.log(`${key}: PID ${child.pid} exited (code ${code}, signal ${signal})`);
      if (this.services[key].state === 'starting' || this.services[key].state === 'up') {
        this._set(key, 'error', `Exited with code ${code}; see ${path.basename(opts.log.path)}`,
          [{ id: key === 'backend' ? 'startBackend' : 'startComfyB', label: 'Start again' }]);
        this.emit('state', this.snapshot());
      }
    });
  }

  startBuild() {
    if (this.children.build) return;
    const cfg = this.cfg.frontend;
    const dir = this.resolve(cfg.dir);
    const logPath = this.resolve(cfg.buildLog);
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    const logFd = fs.openSync(logPath, 'a');
    this.buildLines = [`$ ${cfg.buildCommand}  (in ${dir})`];
    const child = spawn(process.env.ComSpec || 'cmd.exe', ['/d', '/s', '/c', cfg.buildCommand], { cwd: dir, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    this.children.build = child;
    this.log(`build: started PID ${child.pid}`);
    const onData = (d) => {
      const text = d.toString();
      fs.writeSync(logFd, text);
      for (const line of text.split(/\r?\n/)) {
        if (line.trim()) this.buildLines.push(line.replace(/\x1b\[[0-9;]*m/g, ''));
      }
      if (this.buildLines.length > 200) this.buildLines = this.buildLines.slice(-200);
      this.emit('state', this.snapshot());
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);
    child.on('error', (e) => onData(Buffer.from(`spawn error: ${e.message}\n`)));
    child.on('exit', (code) => {
      fs.closeSync(logFd);
      this.children.build = null;
      this.log(`build: exited with code ${code}`);
      this.buildLines.push(`build finished with exit code ${code}`);
      if (code === 0) {
        this.autoStarted.backend = false; // let the backend start (or be restarted) on the next tick
        this.services.ui.state = 'down';
      } else {
        this._set('ui', 'error', `Build failed (exit code ${code}); see ${path.basename(logPath)}`, [{ id: 'retryBuild', label: 'Build again' }]);
      }
      this.emit('state', this.snapshot());
      this.tick();
    });
  }

  retryBuild() {
    this.autoStarted.build = true;
    this.startBuild();
    this._set('ui', 'building', 'Building the frontend (npm run build)');
    this.emit('state', this.snapshot());
  }

  // Processes matching the service that this launcher did not start.
  async foreignProcesses(key) {
    const cfg = this.cfg[key];
    const all = await procs.listProcesses();
    const matching = procs.filterProcesses(all, cfg.processMatch, cfg.processName);
    const own = this.children[key] && this.children[key].pid;
    return matching.filter((p) => !(own && procs.isDescendant(all, p.pid, own)));
  }

  // Stop every process of the service (own tree and, when `force`, foreign ones too), then start it again.
  // Returns 'started' | 'needs-confirm' | 'no-foreign'.
  async restart(key, opts = {}) {
    const foreign = await this.foreignProcesses(key);
    if (foreign.length && !opts.force) return { result: 'needs-confirm', foreign };
    const own = this.children[key];
    if (own && own.pid) {
      this.children[key] = null; // so the exit handler does not flag an error
      procs.killTree(own.pid);
    }
    for (const p of foreign) procs.killTree(p.pid);
    this.log(`${key}: restart; killed own ${own ? own.pid : '-'} and foreign [${foreign.map((p) => p.pid).join(', ')}]`);
    // Wait for the port to stop answering so the new process can bind.
    const cfg = this.cfg[key];
    const url = key === 'backend' ? cfg.url + cfg.healthPath : cfg.url + '/system_stats';
    for (let i = 0; i < 30; i++) {
      const r = await probe(url);
      if (!r.ok) break;
      await new Promise((res) => setTimeout(res, 500));
    }
    this.autoStarted[key] = true;
    this.services[key].state = 'down';
    if (key === 'backend') this.startBackend(); else this.startComfyB();
    this.emit('state', this.snapshot());
    return { result: 'started' };
  }

  // Quit: stop only what we started. ComfyUI A is never touched.
  stopOwned() {
    const stopped = [];
    for (const key of ['comfyB', 'backend', 'build']) {
      const c = this.children[key];
      if (c && c.pid && procs.isAlive(c.pid)) {
        this.children[key] = null;
        procs.killTree(c.pid);
        stopped.push(`${key} (PID ${c.pid})`);
      }
    }
    if (stopped.length) this.log('stopped on quit: ' + stopped.join(', '));
    return stopped;
  }
}

module.exports = { Supervisor, probe };
