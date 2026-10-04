import { get, post, setExpiredHandler } from './api.js';
import { S } from './state.js';
import { $, $$, h, mount, on, toast, loading, errorBox, field, msgOf, esc } from './ui.js';
import { routes as student } from './views/student.js';
import { routes as staff } from './views/staff.js';
import { routes as ops } from './views/ops.js';
import { routes as admin } from './views/admin.js';
import { routes as common } from './views/common.js';

const ROUTES = [...student, ...staff, ...ops, ...admin, ...common];
const root = () => $('#m');

function home() {
  const r = S.me.role;
  return r === 'student' ? 'home' : r === 'command' ? 'overview' : r === 'admin' ? 'users' : r === 'mentor' ? 'cases' : 'queue';
}

function allowed(rt) { return !rt.perm || (typeof rt.perm === 'function' ? rt.perm() : S.any(...[].concat(rt.perm))); }

function nav() {
  const cur = location.hash.split('/')[1] || home();
  mount($('#nav'), h`${ROUTES.filter((r) => r.nav && allowed(r)).map((r) => h`<button data-go="${r.name}" class="${(cur === r.name || r.also?.includes(cur)) ? 'on' : ''}">${r.title}</button>`)}`);
}

let seq = 0;
async function route() {
  if (!S.me) return;
  const [, name = home(), ...rest] = location.hash.replace(/^#\/?/, '#/').split('/');
  const arg = rest.join('/');
  if (S.me.must_change_password) return renderPassword(true);
  let rt = ROUTES.find((r) => r.name === name);
  if (S.me.role === 'student' && S.me.consent_onboarded === false && name !== 'privacy') { location.hash = '#/privacy'; return; }
  if (!rt || !allowed(rt)) { rt = ROUTES.find((r) => r.name === home()); }
  const mine = ++seq;
  nav();
  document.title = `${rt.title} — PULSE`;
  // every view renders into a fresh container, so listeners from the previous view are discarded with it
  const view = document.createElement('div');
  view.className = 'view';
  root().replaceChildren(view);
  mount(view, loading(4));
  try { await rt.render(view, arg, () => mine === seq); }
  catch (e) { if (mine === seq) { mount(view, errorBox(e)); const b = $('[data-act=retry]', view); if (b) b.onclick = route; } }
}

export const go = (name, arg) => { location.hash = `#/${name}${arg ? '/' + arg : ''}`; };

function renderPassword(forced) {
  nav();
  const el = root();
  mount(el, h`<div class="c" style="max-width:460px"><h2>${forced ? 'Choose a new password' : 'Change password'}</h2>
    <p class="mu">${forced ? 'Your account uses a temporary password. Set your own to continue.' : 'You will be signed out on other devices.'}</p>
    <form id="pw">${field('current_password', 'Current password', 'password', '', 'autocomplete="current-password" required')}
    ${field('new_password', `New password (at least ${S.meta.password_min_length} characters)`, 'password', '', 'autocomplete="new-password" required')}
    <p class="err" role="alert"></p><button class="b" style="margin-top:12px">Save password</button></form></div>`);
  $('#pw').onsubmit = async (e) => {
    e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); const err = $('.err', e.target);
    try { await post('/auth/change-password', f); toast('Password changed'); S.me = await get('/auth/me'); location.hash = ''; route(); }
    catch (ex) { err.textContent = msgOf(ex); }
  };
}

async function unread() {
  try {
    const { unread: n } = await get('/notifications/unread-count', { quiet401: true });
    const b = $('#bell i'); if (b) { b.textContent = n > 99 ? '99+' : n; b.hidden = !n; }
  } catch { /* the bell is best-effort */ }
}

async function logout() {
  try { await post('/auth/logout', {}, { quiet401: true }); } catch { /* sign out locally regardless */ }
  location.href = 'login.html';
}

async function boot() {
  setExpiredHandler(() => { location.href = 'login.html?expired=1'; });
  try { S.meta = await get('/meta'); } catch (e) { mount(root(), errorBox(e)); return; }
  try { S.me = await get('/auth/me', { quiet401: true }); }
  catch (e) { if (e.status === 401) { location.href = 'login.html'; return; } mount(root(), errorBox(e)); $('[data-act=retry]').onclick = () => location.reload(); return; }

  if (S.meta.demo_mode) $('#demo').innerHTML = '<div class="demo-banner">Demo Mode — development data only. Never enabled in production.</div>';
  mount($('#who'), h`<button id="umb" aria-haspopup="menu" aria-expanded="false">${S.me.full_name} · ${S.me.role_name}</button>
    <div class="dd" hidden role="menu"><button data-act="pw" role="menuitem">Change password</button><button data-act="out" role="menuitem">Sign out</button></div>`);
  const dd = $('#who .dd');
  on($('#who'), 'click', 'button', (b) => {
    if (b.id === 'umb') { dd.hidden = !dd.hidden; b.setAttribute('aria-expanded', String(!dd.hidden)); return; }
    dd.hidden = true; if (b.dataset.act === 'out') logout(); if (b.dataset.act === 'pw') renderPassword(false);
  });
  document.addEventListener('click', (e) => { if (!e.target.closest('#who')) dd.hidden = true; });
  $('#bell').onclick = () => go('notifications');
  on($('#nav'), 'click', '[data-go]', (b) => go(b.dataset.go));
  window.addEventListener('hashchange', route);
  if (!S.me.permissions.includes('notification.read_own')) $('#bell').hidden = true;
  unread(); setInterval(() => { if (!document.hidden) unread(); }, 60000);
  window.addEventListener('pulse:unread', unread);
  route();
}
boot();
