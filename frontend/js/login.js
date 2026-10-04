import { get, post } from './api.js';
const $ = (s) => document.querySelector(s);
const q = new URLSearchParams(location.search);
if (q.get('expired')) $('#note').textContent = 'Your session ended. Please sign in again.';

(async () => {
  try { const me = await get('/auth/me', { quiet401: true }); if (me && me.id) { location.replace('index.html'); return; } } catch { /* not signed in */ }
  try {
    const meta = await get('/meta');
    if (meta.demo_mode) {
      const d = await get('/demo/accounts');
      $('#dpw').textContent = d.password; $('#demo').hidden = false;
      d.accounts.forEach((a) => { const b = document.createElement('button'); b.type = 'button'; b.textContent = a.name; b.title = a.description; b.onclick = () => { $('#e').value = a.email; $('#p').value = d.password; }; $('#ac').appendChild(b); });
    }
  } catch { /* demo chips are optional */ }
})();

$('#f').onsubmit = async (e) => {
  e.preventDefault(); const err = $('#err'); err.textContent = ''; const btn = $('#go'); btn.disabled = true;
  try { await post('/auth/login', { email: $('#e').value.trim(), password: $('#p').value }); location.replace('index.html'); }
  catch (ex) { err.textContent = ex.status === 429 ? 'Too many attempts. Wait a minute and try again.' : ex.status === 0 ? ex.message : ex.message; btn.disabled = false; $('#p').value = ''; $('#p').focus(); }
};
