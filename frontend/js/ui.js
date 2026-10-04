// UI helpers. Everything interpolated through h`` is HTML-escaped unless it is already Safe markup.
class Safe { constructor(s) { this.s = s; } toString() { return this.s; } }
export const raw = (s) => new Safe(String(s));
export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const r = (v) => (v instanceof Safe ? v.s : Array.isArray(v) ? v.map(r).join('') : v === false || v == null ? '' : esc(v));
export const h = (s, ...v) => new Safe(s.reduce((a, x, i) => a + x + (i < v.length ? r(v[i]) : ''), ''));
export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const mount = (el, safe) => { el.innerHTML = safe instanceof Safe ? safe.s : esc(safe); return el; };

// event delegation: on(root,'click','[data-act]', (el, ev) => ...)
export function on(root, type, sel, fn) {
  root.addEventListener(type, (ev) => { const t = ev.target.closest(sel); if (t && root.contains(t)) fn(t, ev); });
}

export function toast(msg, bad = false) {
  const t = $('#toast'); t.textContent = msg; t.style.opacity = 1; t.style.background = bad ? 'var(--hi)' : '';
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.style.opacity = 0; }, 3800);
}

// ---- states -------------------------------------------------------------------------------------
export const loading = (n = 3) => h`<div class="c" aria-busy="true" aria-label="Loading">${Array.from({ length: n }, () => h`<div class="skel"></div>`)}</div>`;
export const empty = (msg, sub = '') => h`<div class="state"><b>${msg}</b>${sub ? h`<div class="mu">${sub}</div>` : ''}</div>`;
export const errorBox = (e) => h`<div class="state bad" role="alert"><b>${e.message || 'Something went wrong'}</b>${e.requestId ? h`<div class="mu">Reference ${e.requestId}</div>` : ''}<div><button class="b o" data-act="retry">Try again</button></div></div>`;
export const msgOf = (e) => (e && e.details && Array.isArray(e.details) && e.details.length ? `${e.message} ${e.details.map((d) => (typeof d === 'string' ? d : d.message)).join('; ')}` : e.message || 'Something went wrong');

// ---- formatting ---------------------------------------------------------------------------------
export const dt = (v) => (v ? new Date(v).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—');
export const day = (v) => (v ? new Date(v).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '—');
export function ago(v) {
  if (!v) return '—'; const s = (Date.now() - new Date(v)) / 1000; const a = Math.abs(s);
  const u = a < 3600 ? [Math.round(a / 60), 'min'] : a < 86400 ? [Math.round(a / 3600), 'h'] : [Math.round(a / 86400), 'd'];
  return s >= 0 ? `${u[0]} ${u[1]} ago` : `in ${u[0]} ${u[1]}`;
}
export const mins = (m) => (m == null ? '—' : Math.abs(m) < 120 ? `${Math.abs(m)} min` : Math.abs(m) < 2880 ? `${Math.round(Math.abs(m) / 60)} h` : `${Math.round(Math.abs(m) / 1440)} d`);
const lab = (s) => String(s || '').replace(/_/g, ' ');
export const label = lab;
const TONE = { critical: 'hi', high: 'wn', medium: 'in', low: '', breached: 'hi', at_risk: 'wn', on_track: 'ok', met: 'ok', met_late: 'wn', paused: '', 'n/a': '',
  new: 'in', pending_approval: 'wn', acknowledged: 'in', in_progress: 'in', resolved: 'ok', closed: '', cancelled: '', rejected: 'hi',
  open: 'in', investigating: 'in', mitigated: 'wn', known_error: 'wn', draft: '', awaiting_approval: 'wn', approved: 'ok', implementing: 'in', completed: 'ok',
  suggested: 'wn', monitoring: 'in', dismissed: '', todo: '', blocked: 'hi', done: 'ok', pending: 'wn', denied: 'hi', revoked: '', expired: '' };
export const pill = (v, text) => h`<span class="pill ${TONE[v] ?? ''}">${text ?? lab(v)}</span>`;
export const slaPill = (s) => {
  if (!s) return '';
  const st = s.resolution;
  if (['breached', 'at_risk'].includes(st)) return pill(st, `${lab(st)}${s.minutes_to_resolution_due != null ? ' · ' + mins(s.minutes_to_resolution_due) + (s.minutes_to_resolution_due < 0 ? ' over' : ' left') : ''}`);
  return pill(st);
};

// ---- modal --------------------------------------------------------------------------------------
export function modal(title, body, { onSubmit, submitText = 'Save', danger = false, wide = false } = {}) {
  return new Promise((resolve) => {
    const wrap = document.createElement('div'); wrap.className = 'mb';
    wrap.innerHTML = `<form class="md" role="dialog" aria-modal="true" aria-label="${esc(title)}" ${wide ? 'style="max-width:760px"' : ''}><h3>${esc(title)}</h3><div class="body"></div><p class="err" role="alert"></p><div class="flex" style="justify-content:flex-end;margin-top:12px"><button type="button" class="b o" data-x>Cancel</button>${onSubmit ? `<button class="b ${danger ? 'dg' : ''}">${esc(submitText)}</button>` : ''}</div></form>`;
    mount($('.body', wrap), body);
    document.body.appendChild(wrap);
    const form = $('form', wrap), err = $('.err', wrap);
    const close = (v) => { wrap.remove(); document.removeEventListener('keydown', esck); resolve(v); };
    const esck = (e) => { if (e.key === 'Escape') close(null); };
    document.addEventListener('keydown', esck);
    $('[data-x]', wrap).onclick = () => close(null);
    wrap.addEventListener('mousedown', (e) => { if (e.target === wrap) close(null); });
    const first = $('input,select,textarea', form); if (first) first.focus();
    form.onsubmit = async (e) => {
      e.preventDefault(); if (!onSubmit) return;
      const btn = $('button:not([data-x])', form); btn.disabled = true; err.textContent = '';
      try { const out = await onSubmit(Object.fromEntries(new FormData(form)), form); close(out === undefined ? true : out); }
      catch (ex) { err.textContent = msgOf(ex); btn.disabled = false; }
    };
  });
}
export const confirmBox = (title, text, okText = 'Confirm', danger = false) => modal(title, h`<p>${text}</p>`, { onSubmit: () => true, submitText: okText, danger });

// ---- form field builders ---------------------------------------------------------------------------------
export const opt = (items, sel, blank) => [blank != null ? h`<option value="">${blank}</option>` : '', items.map((i) => { const [v, t] = Array.isArray(i) ? i : [i, lab(i)]; return h`<option value="${v}" ${String(v) === String(sel ?? '') ? 'selected' : ''}>${t}</option>`; })];
export const field = (name, labelText, type = 'text', val = '', extra = '') => h`<label class="f" for="f_${name}">${labelText}</label><input class="s" id="f_${name}" name="${name}" type="${type}" value="${val ?? ''}" ${raw(extra)}>`;
export const area = (name, labelText, val = '', extra = '') => h`<label class="f" for="f_${name}">${labelText}</label><textarea class="s" id="f_${name}" name="${name}" ${raw(extra)}>${val ?? ''}</textarea>`;
export const select = (name, labelText, items, sel, blank, extra = '') => h`<label class="f" for="f_${name}">${labelText}</label><select class="s" id="f_${name}" name="${name}" ${raw(extra)}>${opt(items, sel, blank)}</select>`;
export const check = (name, text, on = false) => h`<label class="ck"><input type="checkbox" name="${name}" value="1" ${on ? 'checked' : ''}> <span>${text}</span></label>`;
export const table = (cols, rows, { link = false } = {}) => h`<div class="sw"><table class="tbl"><thead><tr>${cols.map((c) => h`<th>${c}</th>`)}</tr></thead><tbody>${rows}</tbody></table></div>`;

// ---- generic async view wrapper --------------------------------------------------------------------------------------
export async function load(el, fn) {
  mount(el, loading());
  try { const out = await fn(); return out; }
  catch (e) { mount(el, errorBox(e)); const b = $('[data-act=retry]', el); if (b) b.onclick = () => load(el, fn); return null; }
}
