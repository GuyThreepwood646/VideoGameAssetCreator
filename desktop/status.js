'use strict';
const list = document.getElementById('services');
  const summary = document.getElementById('summary');
  const build = document.getElementById('build');
  const buildlog = document.getElementById('buildlog');
  const openApp = document.getElementById('openApp');
  const hint = document.getElementById('hint');

  function render(snap) {
    list.replaceChildren(...snap.services.map((s) => {
      const li = document.createElement('li');
      const dot = document.createElement('span'); dot.className = 'dot ' + s.state;
      const body = document.createElement('div');
      const name = document.createElement('div'); name.className = 'name';
      name.textContent = s.name;
      const url = document.createElement('span'); url.className = 'url'; url.textContent = s.url;
      name.appendChild(url);
      const msg = document.createElement('div'); msg.className = 'msg' + (s.state === 'error' ? ' error' : s.state === 'off' || / Off in the app/.test(s.message) ? ' off' : '');
      msg.textContent = s.message + (s.owned && s.pid ? ` (PID ${s.pid})` : '');
      body.append(name, msg);
      const actions = document.createElement('div'); actions.className = 'actions';
      for (const a of s.actions || []) {
        const b = document.createElement('button'); b.textContent = a.label; b.dataset.action = a.id;
        actions.appendChild(b);
      }
      li.append(dot, body, actions);
      return li;
    }));
    // A service whose graphics card is turned off in the app counts as fine; the app carries on with the other card.
    const up = snap.services.filter((s) => s.state === 'up' || s.state === 'off').length;
    const off = snap.services.filter((s) => s.state === 'off' || / Off in the app/.test(s.message)).length;
    const offText = off ? ` (${off} graphics card${off === 1 ? '' : 's'} off in the app)` : '';
    summary.textContent = (snap.allUp ? 'All services up' : `${up} of ${snap.services.length} services up`) + offText;
    build.classList.toggle('show', snap.build.active || (snap.build.lines.length > 0 && !snap.allUp));
    const text = snap.build.lines.join('\n');
    if (buildlog.textContent !== text) { buildlog.textContent = text; buildlog.scrollTop = buildlog.scrollHeight; }
    openApp.className = snap.allUp ? 'primary' : '';
    openApp.textContent = snap.allUp ? 'Open app' : 'Open app anyway';
    const backendUp = snap.services.find((s) => s.key === 'backend');
    openApp.disabled = !(backendUp && backendUp.state === 'up');
    hint.textContent = snap.allUp ? '' : (backendUp && backendUp.state === 'up' ? 'Some services are still down' : 'Waiting for the backend');
  }

  document.body.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-action]');
    if (!b) return;
    b.disabled = true;
    window.vgac.action(b.dataset.action).finally(() => { b.disabled = false; });
  });

  window.vgac.onState(render);
  window.vgac.requestState().then(render);
