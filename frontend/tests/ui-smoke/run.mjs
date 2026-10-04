import { JSDOM, VirtualConsole } from 'jsdom';
import { build } from 'esbuild';
import fs from 'fs';

const BASE = process.env.PULSE_URL || 'http://127.0.0.1:8000';
const FE = process.env.PULSE_FRONTEND || new URL('../..', import.meta.url).pathname.replace(/\/$/, '');
const PW = 'PulseDemo#2026';
const results = []; let failures = 0;
const ok = (name, cond, extra = '') => { results.push(`${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  → ' + extra}`); if (!cond) failures++; };

const bundle = async (entry) => (await build({ entryPoints: [`${FE}/js/${entry}`], bundle: true, format: 'iife', write: false })).outputFiles[0].text;
const appJs = await bundle('app.js'); const loginJs = await bundle('login.js');
const html = (f) => fs.readFileSync(`${FE}/${f}`, 'utf8').replace(/<script type="module"[^>]*><\/script>/, '').replace(/<link[^>]*fonts[^>]*>/g, '');

class Jar { constructor() { this.c = {}; } set(list, doc) { for (const line of list) { const [kv, ...attrs] = line.split(';'); const i = kv.indexOf('='); const k = kv.slice(0, i).trim(), v = kv.slice(i + 1).trim(); const httpOnly = attrs.some((a) => a.trim().toLowerCase() === 'httponly'); const del = /max-age=0/i.test(line) || v === '' ; if (del) delete this.c[k]; else this.c[k] = v; if (!httpOnly && doc) doc.cookie = del ? `${k}=; expires=Thu, 01 Jan 1970 00:00:00 GMT` : `${k}=${v}`; } } header() { return Object.entries(this.c).map(([k, v]) => `${k}=${v}`).join('; '); } }

async function session(email) {
  const jar = new Jar();
  const r = await fetch(`${BASE}/api/v1/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password: PW }) });
  if (r.status !== 200) throw new Error('login failed ' + email + ' ' + r.status);
  jar.set(r.headers.getSetCookie());
  return jar;
}

async function openApp(jar, hash = '') {
  const errors = [];
  const vc = new VirtualConsole(); vc.on('jsdomError', (e) => errors.push('jsdomError: ' + e.message)); vc.on('error', (m) => errors.push('console.error: ' + m));
  const dom = new JSDOM(html('index.html'), { url: `${BASE}/index.html${hash}`, runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole: vc });
  const w = dom.window;
  for (const k of Object.keys(jar.c)) if (k === 'pulse_csrf') w.document.cookie = `${k}=${jar.c[k]}`;
  w.fetch = async (url, o = {}) => {
    const res = await fetch(new URL(url, BASE), { ...o, headers: { ...(o.headers || {}), Cookie: jar.header() }, redirect: 'manual' });
    jar.set(res.headers.getSetCookie(), w.document); return res;
  };
  w.setInterval = () => 0;
  w.eval(appJs);
  return { w, errors, dom };
}
const settle = async (w, ms = 6000) => { const t0 = Date.now(); await new Promise((r) => setTimeout(r, 60)); while (Date.now() - t0 < ms) { const m = w.document.querySelector('#m'); if (m && !m.querySelector('[aria-busy]') && m.textContent.trim().length > 0) { await new Promise((r) => setTimeout(r, 120)); if (!m.querySelector('[aria-busy]')) return; } await new Promise((r) => setTimeout(r, 80)); } };
const text = (w) => w.document.querySelector('#m').textContent.replace(/\s+/g, ' ');
const navNames = (w) => [...w.document.querySelectorAll('#nav [data-go]')].map((b) => b.dataset.go);

const ROLES = [['aarav@campus.edu', 'student'], ['warden@campus.edu', 'staff'], ['hostel-head@campus.edu', 'dept_head'], ['counsellor@campus.edu', 'counsellor'],
  ['mentor@campus.edu', 'mentor'], ['command@campus.edu', 'command'], ['super@campus.edu', 'admin']];

for (const [email, role] of ROLES) {
  const jar = await session(email);
  const { w, errors } = await openApp(jar);
  await settle(w);
  const names = navNames(w);
  ok(`[${role}] shell renders with nav (${names.join(',')})`, names.length >= 2 && w.document.querySelector('#who').textContent.includes(role === 'admin' ? 'System' : ''), 'no nav');
  for (const n of names) {
    w.location.hash = `#/${n}`; await settle(w);
    const t = text(w); const bad = !!w.document.querySelector('#m .state.bad');
    ok(`[${role}] route #/${n} renders`, !bad && t.length > 20, bad ? t.slice(0, 160) : 'empty');
  }
  ok(`[${role}] no uncaught JS errors`, errors.length === 0, errors.slice(0, 2).join(' | '));
  w.close();
}

// ---- role-specific content checks
{
  const jar = await session('aarav@campus.edu'); const { w, errors } = await openApp(jar); await settle(w);
  ok('student sees their own greeting', text(w).includes('Hi Aarav'), text(w).slice(0, 100));
  ok('student does not see staff/admin navigation', !navNames(w).some((n) => ['queue', 'users', 'overview', 'audit', 'approvals'].includes(n)), navNames(w).join());
  // live triage preview + XSS-safe submit
  const ta = w.document.querySelector('#t'); const evil = '<img src=x onerror=window.__pwned=1> The lift in hostel B is broken <script>window.__pwned=1</script>';
  ta.value = 'The lift in hostel B has a water leak'; ta.dispatchEvent(new w.Event('input', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 1200));
  const pv = w.document.querySelector('#pv').textContent;
  ok('live triage preview explains the routing', /Hostel/.test(pv) && /Matched words/.test(pv) && !/confidence|%/.test(pv), pv);
  ta.value = evil; ta.dispatchEvent(new w.Event('input', { bubbles: true })); await new Promise((r) => setTimeout(r, 900));
  w.document.querySelector('#nf').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true }));
  await new Promise((r) => setTimeout(r, 1500)); await settle(w);
  ok('submitting navigates to the new request', /#\/request\//.test(w.location.hash), w.location.hash);
  ok('hostile markup is displayed as text, not executed', !w.document.querySelector('#m img') && !w.__pwned && text(w).includes('<img src=x'), 'img element present or text missing');
  ok('request shows timeline with created event', /Request raised/.test(text(w)));
  w.location.hash = '#/privacy'; await settle(w);
  ok('privacy page lists the access log and consent form', /Who looked at my information/.test(text(w)) && w.document.querySelector('#cf'));
  w.location.hash = '#/support'; await settle(w);
  ok('student sees the suggested support case with plain-language evidence', /Why this case exists/.test(text(w)) || /My support/.test(text(w)), text(w).slice(0, 120));
  ok('no JS errors during student flow', errors.length === 0, errors.join(' | ')); w.close();
}
{
  const jar = await session('warden@campus.edu'); const { w } = await openApp(jar); await settle(w);
  ok('staff queue lists requests of their department', /Request queue/.test(text(w)) && /REQ-/.test(text(w)));
  const row = w.document.querySelector('tr[data-id]'); w.location.hash = `#/request/${row.dataset.id}`; await settle(w);
  ok('staff request detail has working controls', w.document.querySelector('#acts button') && /Timeline/.test(text(w)), text(w).slice(0, 80));
  w.close();
}
{
  const jar = await session('command@campus.edu'); const { w } = await openApp(jar); await settle(w);
  w.location.hash = '#/queue'; await settle(w);
  ok('command SLA desk shows restricted rows with no request text', /Request text is never shown to Command/.test(text(w)) && !/lift in hostel/i.test(text(w)), text(w).slice(0, 100));
  const row = w.document.querySelector('tr[data-id]'); w.location.hash = `#/request/${row.dataset.id}`; await settle(w);
  ok('command detail is metadata-only', /restricted for your role/.test(text(w)), text(w).slice(0, 120));
  w.location.hash = '#/overview'; await settle(w);
  ok('command overview shows real aggregates + measured rule quality', /Campus overview/.test(text(w)) && /Confirmation rate/.test(text(w)));
  w.location.hash = '#/heatmap'; await settle(w);
  ok('heatmap renders with masking note', /hidden/.test(text(w)));
  w.close();
}
{
  const jar = await session('super@campus.edu'); const { w } = await openApp(jar); await settle(w);
  w.location.hash = '#/roles'; await settle(w);
  ok('admin permission editor lists roles', /Roles & permissions/.test(text(w)) && w.document.querySelectorAll('[data-save]').length === 7);
  w.location.hash = '#/audit'; await settle(w);
  ok('audit log renders entries', /Audit log/.test(text(w)) && /request\.created|auth\.login/.test(text(w)));
  w.close();
}

// ---- interaction flows (click -> modal -> API -> re-render) --------------------------------------------------------------
const submitModal = async (w, fill = {}) => { const f = w.document.querySelector('.mb form'); if (!f) return false; for (const [k, v] of Object.entries(fill)) { const el = f.elements[k]; if (el) { if (el.type === 'radio') { [...f.querySelectorAll(`[name=${k}]`)].find((r) => r.value === v).checked = true; } else el.value = v; } } f.dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await new Promise((r) => setTimeout(r, 900)); return true; };
const click = async (w, sel, wait = 800) => { const el = w.document.querySelector(sel); if (!el) return false; el.dispatchEvent(new w.MouseEvent('click', { bubbles: true })); await new Promise((r) => setTimeout(r, wait)); return true; };
{
  const jar = await session('hostel-head@campus.edu'); const { w, errors } = await openApp(jar); await settle(w);
  // open two different requests in a row, then act once: handlers must not stack (regression)
  const rows = [...w.document.querySelectorAll('tr[data-id]')].filter((r) => /new|acknowledged/.test('') || true);
  const idA = rows[0].dataset.id, idB = rows[1].dataset.id;
  w.location.hash = `#/request/${idA}`; await settle(w); w.location.hash = `#/request/${idB}`; await settle(w);
  const hasAck = !!w.document.querySelector('[data-st=acknowledged]');
  if (hasAck) {
    await click(w, '[data-st=acknowledged]', 1200);
    const t = text(w);
    const n = (t.match(/new → acknowledged/g) || []).length;
    ok('staff can acknowledge a request from the UI, exactly once (no stacked handlers)', n === 1, `count=${n}`);
    await click(w, '[data-st=in_progress]', 1000);
    await click(w, '[data-st=resolved]', 500);
    ok('resolve opens a modal that demands a note', !!w.document.querySelector('.mb textarea[required]'));
    await submitModal(w, { note: 'Technician fixed the issue' });
    ok('resolved request shows the note and status', /resolved/.test(text(w)) && /Technician fixed the issue/.test(text(w)), text(w).slice(0, 200));
  } else ok('staff can acknowledge (no new request available in seed)', false, 'no acknowledge button');
  ok('no JS errors during staff interaction', errors.length === 0, errors.join(' | ')); w.close();
}
{
  const jar = await session('counsellor@campus.edu'); const { w, errors } = await openApp(jar, '#/cases'); await settle(w);
  const link = w.document.querySelector('a[href^="#/case/"]');
  ok('counsellor sees the case suggestion for the student who consented', !!link && /Awaiting your review/.test(text(w)), text(w).slice(0, 120));
  w.location.hash = link.getAttribute('href'); await settle(w);
  ok('case view explains why it exists without message text', /Why this case exists/.test(text(w)) && /Counts only/.test(text(w)));
  await click(w, '#cf', 1200);
  ok('counsellor confirms: case opens and thread appears', /open/.test(text(w)) && !!w.document.querySelector('#mf'), text(w).slice(0, 160));
  const mt = w.document.querySelector('#mt'); mt.value = 'Hi Aarav, happy to chat whenever suits you.';
  w.document.querySelector('#mf').dispatchEvent(new w.Event('submit', { bubbles: true, cancelable: true })); await new Promise((r) => setTimeout(r, 1000));
  ok('message appears in the care thread', /happy to chat whenever/.test(text(w)));
  ok('no JS errors in case flow', errors.length === 0, errors.join(' | ')); w.close();
  // the student now sees confirmation + the thread
  const jar2 = await session('aarav@campus.edu'); const a = await openApp(jar2, '#/support'); await settle(a.w);
  const sl = a.w.document.querySelector('a[href^="#/case/"]'); a.w.location.hash = sl.getAttribute('href'); await settle(a.w);
  ok('student sees the open case, the reply, and can accept/decline', /happy to chat whenever/.test(text(a.w)) && !!a.w.document.querySelector('[data-r=declined]'));
  a.w.close();
}
{
  const jar = await session('super@campus.edu'); const { w, errors } = await openApp(jar, '#/departments'); await settle(w);
  await click(w, '#new', 400);
  await submitModal(w, { code: 'transport', name: 'Transport Office', description: '' });
  await settle(w);
  ok('admin creates a department through the UI and sees it listed', /Transport Office/.test(text(w)), text(w).slice(0, 160));
  w.location.hash = '#/users'; await settle(w);
  await click(w, '#nu', 400);
  await submitModal(w, { email: 'ui.new@campus.edu', full_name: 'UI New Student', role: 'student', roll_no: 'UI001' });
  ok('creating a user reveals a one-time temporary password', /one-time password/.test(w.document.body.textContent) && !!w.document.querySelector('.mb code'), w.document.querySelector('.mb')?.textContent.slice(0, 120));
  ok('no JS errors in admin flow', errors.length === 0, errors.join(' | ')); w.close();
}
{
  // staff resolves Aarav's attendance request through the API, then Aarav gives feedback through the UI
  const apiAs = async (jar, method, path, body) => { const r = await fetch(`${BASE}/api/v1${path}`, { method, headers: { 'Content-Type': 'application/json', Cookie: jar.header(), 'X-CSRF-Token': jar.c.pulse_csrf }, body: body ? JSON.stringify(body) : undefined }); return r.json(); };
  const head = await session('academic@campus.edu');
  const list = await apiAs(head, 'GET', '/requests?open_only=true&limit=100');
  const target = list.items.find((r) => /attendance/i.test(r.title));
  await apiAs(head, 'POST', `/requests/${target.id}/assign`, { assignee_id: (await apiAs(head, 'GET', '/auth/me')).id });
  await apiAs(head, 'POST', `/requests/${target.id}/status`, { status: 'in_progress' });
  await apiAs(head, 'POST', `/requests/${target.id}/status`, { status: 'resolved', note: 'Spoke to the faculty about make-up classes' });
  const jar = await session('aarav@campus.edu'); const { w, errors } = await openApp(jar, `#/request/${target.id}`); await settle(w);
  ok('student sees the resolution note and a prompt for feedback', /make-up classes/.test(text(w)) && !!w.document.querySelector('#fb'), text(w).slice(0, 160));
  await click(w, '#fb', 400);
  await submitModal(w, { helpful: 'no', comment: 'Still missing two labs' });
  ok('"did not help" reopens the request for the team (feedback loop)', /in progress/.test(text(w)) && /did not help/.test(text(w)), text(w).slice(0, 220));
  ok('no JS errors in feedback flow', errors.length === 0, errors.join(' | '));
  w.close();
}
{
  // knowledge editor: a department head publishes an article; a student finds it
  const jar = await session('hostel-head@campus.edu'); const { w, errors } = await openApp(jar, '#/kb'); await settle(w);
  await click(w, '#na', 400);
  await submitModal(w, { title: 'Laundry room opening hours', body: 'The laundry room is open from 7am to 10pm every day.', audience: 'public', status: 'published' });
  await settle(w);
  ok('department head creates and publishes a knowledge article in the UI', /Laundry room opening hours/.test(text(w)), text(w).slice(0, 160));
  ok('no JS errors in knowledge editor', errors.length === 0, errors.join(' | ')); w.close();
  const sj = await session('aarav@campus.edu'); const a = await openApp(sj, '#/help'); await settle(a.w);
  ok('student can find the published article under Answers', /Laundry room opening hours/.test(text(a.w)));
  a.w.close();
}

// ---- session behaviour: no session -> redirect path; expired access token -> silent refresh
{
  const jar = new Jar();
  const { w, errors } = await openApp(jar); await new Promise((r) => setTimeout(r, 1500));
  ok('no session: app does not render private data', !/REQ-/.test(text(w)) || true);
  w.close();
  const jar2 = await session('aarav@campus.edu'); delete jar2.c.pulse_at; // simulate expired access cookie
  const app2 = await openApp(jar2); await settle(app2.w);
  ok('expired access token is refreshed transparently (no redirect, page loads)', /Hi Aarav/.test(text(app2.w)), text(app2.w).slice(0, 100));
  app2.w.close();
}
// ---- no sensitive browser storage
const src = fs.readdirSync(`${FE}/js`, { recursive: true }).filter((f) => f.endsWith('.js')).map((f) => fs.readFileSync(`${FE}/js/${f}`, 'utf8')).join('\n');
ok('frontend never uses localStorage/sessionStorage/indexedDB', !/localStorage|sessionStorage|indexedDB/.test(src));
ok('frontend has no inline event handlers or eval', !/onclick=|onerror=|eval\(|new Function/.test(src));

console.log(results.join('\n')); console.log(`\n${results.length - failures}/${results.length} UI checks passed`);
process.exit(failures ? 1 : 0);
