import { get, post, patch, put, del, api, qs } from '../api.js';
import { S } from '../state.js';
import { $, $$, h, raw, mount, on, toast, empty, ago, dt, pill, label, modal, confirmBox, area, select, field, check, opt, table, msgOf } from '../ui.js';

const A = (p) => `/admin${p}`;
const asBody = (fields, v, create) => {
  const b = {};
  for (const f of fields) {
    if (f.create_only && !create) continue;
    let x = v[f.k];
    if (f.type === 'check') x = x === '1';
    else if (f.type === 'number') x = x === '' || x == null ? null : Number(x);
    else if (f.type === 'tags') x = (x || '').split(',').map((t) => t.trim()).filter(Boolean);
    else if (x === '') x = f.nullable ? null : f.type === 'select' ? null : x;
    if (x === null && !f.nullable && !create) continue;
    b[f.k] = x;
  }
  return b;
};
const input = (f, row) => {
  const v = row ? row[f.k] : f.def;
  if (f.type === 'check') return check(f.k, f.label, v ?? false);
  if (f.type === 'select') return select(f.k, f.label, f.options(), v ?? '', f.nullable ? '— none —' : null, f.req ? 'required' : '');
  if (f.type === 'area') return area(f.k, f.label, v ?? '');
  if (f.type === 'tags') return field(f.k, `${f.label} (comma separated)`, 'text', (v || []).join(', '));
  return field(f.k, f.label, f.type === 'number' ? 'number' : 'text', v ?? '', `${f.req ? 'required' : ''} ${f.create_only && row ? 'disabled' : ''} ${f.type === 'number' ? 'min="1"' : ''}`);
};

function crud(cfg) {
  return async (el, _, alive) => {
    const ctx = cfg.ctx ? await cfg.ctx() : {};
    const draw = async () => {
      const rows = await get(A(cfg.path));
      if (!alive()) return;
      mount(el, h`<section class="c"><div class="flex sp"><h2>${cfg.title}</h2><button class="b sm" id="new">New</button></div>${cfg.help ? h`<p class="mu">${cfg.help}</p>` : ''}
        ${rows.length ? table([...cfg.cols.map((c) => c[0]), ''], rows.map((r) => h`<tr>${cfg.cols.map((c) => h`<td>${c[1](r, ctx)}</td>`)}<td style="white-space:nowrap"><button class="b o sm" data-e="${r.id}">Edit</button>${cfg.extra ? cfg.extra(r) : ''}${cfg.del ? h` <button class="b o sm" data-d="${r.id}">Delete</button>` : ''}</td></tr>`)) : empty('Nothing yet')}</section>`);
      el._rows = rows;
    };
    const form = (row) => cfg.fields(ctx).map((f) => input(f, row));
    on(el, 'click', '#new', async () => { const v = await modal(`New ${cfg.one}`, h`${cfg.fields(ctx).map((f) => input(f, null))}`, { submitText: 'Create', onSubmit: (x) => post(A(cfg.path), asBody(cfg.fields(ctx), x, true)) }); if (v) { toast('Created'); await draw(); } });
    on(el, 'click', '[data-e]', async (b) => {
      const row = el._rows.find((r) => r.id === b.dataset.e);
      const v = await modal(`Edit ${cfg.one}`, h`${form(row)}`, { submitText: 'Save', onSubmit: (x) => patch(A(`${cfg.path}/${row.id}`), asBody(cfg.fields(ctx), x, false)) });
      if (v) { toast('Saved'); await draw(); }
    });
    on(el, 'click', '[data-d]', async (b) => { if (await confirmBox(`Delete this ${cfg.one}?`, 'This cannot be undone.', 'Delete', true)) { try { await del(A(`${cfg.path}/${b.dataset.d}`)); toast('Deleted'); await draw(); } catch (ex) { toast(msgOf(ex), true); } } });
    if (cfg.bind) cfg.bind(el, ctx, draw);
    await draw();
  };
}

const yn = (v) => (v ? pill('ok', 'yes') : h`<span class="mu">no</span>`);
const PRIO = ['low', 'medium', 'high', 'critical'];
const CATS = ['hostel', 'academic', 'medical', 'wellbeing', 'safety', 'finance', 'it', 'transport', 'other'];
// pickers use the everyday catalogue endpoints so department heads (asset.manage) can use the Assets page too
const deptCtx = async () => ({ depts: await get('/catalog/departments'), locs: await get('/catalog/locations'), svcs: S.can('admin.services') ? await get(A('/services')) : [] });

const departments = crud({ title: 'Departments', one: 'department', path: '/departments', help: 'The triage desk receives free-text requests that no rule could route. Only one department can be the triage desk.',
  cols: [['Code', (r) => r.code], ['Name', (r) => h`<b>${r.name}</b>`], ['Triage desk', (r) => yn(r.is_triage_desk)], ['Active', (r) => yn(r.is_active)]],
  fields: () => [{ k: 'code', label: 'Code (lowercase, no spaces)', req: true, create_only: true }, { k: 'name', label: 'Name', req: true }, { k: 'description', label: 'Description', type: 'area', nullable: true }, { k: 'is_triage_desk', label: 'This is the triage desk', type: 'check' }, { k: 'is_active', label: 'Active', type: 'check', def: true }] });
const locations = crud({ title: 'Locations', one: 'location', path: '/locations', help: 'Aliases are words students might use ("lab", "canteen"). They are matched to detect where something happened.',
  cols: [['Code', (r) => r.code], ['Name', (r) => h`<b>${r.name}</b>`], ['Kind', (r) => label(r.kind)], ['Aliases', (r) => r.aliases.join(', ')], ['Active', (r) => yn(r.is_active)]],
  fields: () => [{ k: 'code', label: 'Code', req: true, create_only: true }, { k: 'name', label: 'Name', req: true }, { k: 'kind', label: 'Kind', type: 'select', options: () => ['hostel', 'academic', 'library', 'medical', 'gate', 'mess', 'admin', 'other'], def: 'other', req: true }, { k: 'aliases', label: 'Aliases', type: 'tags' }, { k: 'is_active', label: 'Active', type: 'check', def: true }] });
const assets = crud({ title: 'Assets', one: 'asset', path: '/assets', ctx: deptCtx, help: 'When a request mentions an asset by name at a known location, it is attached automatically.',
  cols: [['Code', (r) => r.code], ['Name', (r) => h`<b>${r.name}</b>`], ['Location', (r, c) => (c.locs.find((l) => l.id === r.location_id) || {}).name || '—'], ['Status', (r) => pill(r.status === 'active' ? 'ok' : r.status === 'retired' ? '' : 'wn', r.status)]],
  fields: (c) => [{ k: 'code', label: 'Code', req: true, create_only: true }, { k: 'name', label: 'Name', req: true }, { k: 'asset_type', label: 'Type', nullable: true }, { k: 'location_id', label: 'Location', type: 'select', nullable: true, options: () => c.locs.map((l) => [l.id, l.name]) }, { k: 'department_id', label: 'Owner department', type: 'select', nullable: true, options: () => c.depts.map((d) => [d.id, d.name]) }, { k: 'status', label: 'Status', type: 'select', options: () => ['active', 'maintenance', 'retired'], def: 'active', req: true }, { k: 'notes', label: 'Notes', type: 'area', nullable: true }] });
const sla = crud({ title: 'SLA policies', one: 'policy', path: '/sla-policies', ctx: deptCtx, del: true,
  help: 'Targets are in minutes. A service-specific policy overrides the campus default for that priority. "At risk" starts at the given % of the target; a request becomes severe when overdue by the multiplier.',
  cols: [['Applies to', (r, c) => (r.service_id ? (c.svcs.find((s) => s.id === r.service_id) || {}).name : h`<b>Campus default</b>`)], ['Priority', (r) => pill(r.priority)], ['First response', (r) => `${r.response_minutes} min`], ['Resolution', (r) => `${r.resolution_minutes} min (${Math.round(r.resolution_minutes / 60)} h)`], ['At risk', (r) => r.at_risk_pct + '%'], ['Severe at', (r) => '×' + r.severe_multiplier], ['Active', (r) => yn(r.is_active)]],
  fields: (c) => [{ k: 'service_id', label: 'Service (blank = campus default)', type: 'select', nullable: true, create_only: true, options: () => c.svcs.map((s) => [s.id, s.name]) }, { k: 'priority', label: 'Priority', type: 'select', options: () => PRIO, req: true, create_only: true, def: 'medium' }, { k: 'response_minutes', label: 'First response (minutes)', type: 'number', req: true }, { k: 'resolution_minutes', label: 'Resolution (minutes)', type: 'number', req: true }, { k: 'at_risk_pct', label: 'At-risk threshold (%)', type: 'number', def: 75 }, { k: 'severe_multiplier', label: 'Severe multiplier', type: 'number', def: 2 }, { k: 'is_active', label: 'Active', type: 'check', def: true }] });
const services = crud({ title: 'Services & routing', one: 'service', path: '/services', ctx: deptCtx,
  help: 'Sensitive services keep details private unless the student shares them and are masked in small aggregates. Use "Keywords" to control how free text is routed.',
  cols: [['Service', (r) => h`<b>${r.name}</b><div class="mu">${r.code}</div>`], ['Department', (r, c) => (c.depts.find((d) => d.id === r.department_id) || {}).name], ['Priority', (r) => pill(r.default_priority)], ['Flags', (r) => h`${r.requires_approval ? pill('wn', 'approval') : ''} ${r.is_sensitive ? pill('in', 'sensitive') : ''} ${r.is_emergency ? pill('hi', 'emergency') : ''} ${r.student_visible ? '' : pill('', 'hidden')}`], ['Active', (r) => yn(r.is_active)]],
  fields: (c) => [{ k: 'code', label: 'Code', req: true, create_only: true }, { k: 'name', label: 'Name', req: true }, { k: 'description', label: 'Description', type: 'area', nullable: true }, { k: 'category', label: 'Category', type: 'select', options: () => CATS, def: 'other', req: true }, { k: 'department_id', label: 'Department', type: 'select', req: true, options: () => c.depts.map((d) => [d.id, d.name]) }, { k: 'default_priority', label: 'Default priority', type: 'select', options: () => PRIO, def: 'medium', req: true }, { k: 'requires_approval', label: 'Requires department-head approval', type: 'check' }, { k: 'is_sensitive', label: 'Sensitive (consent + masking)', type: 'check' }, { k: 'is_emergency', label: 'Emergency (used by SOS)', type: 'check' }, { k: 'student_visible', label: 'Show in student catalogue', type: 'check', def: true }, { k: 'is_active', label: 'Active', type: 'check', def: true }],
  extra: (r) => h` <button class="b o sm" data-kw="${r.id}">Keywords</button>`,
  bind: (el, ctx) => on(el, 'click', '[data-kw]', async (b) => {
    const svc = el._rows.find((r) => r.id === b.dataset.kw);
    const loop = async () => {
      const rules = (await get(A('/triage-rules'))).filter((r) => r.service_id === svc.id);
      const p = modal(`Routing keywords — ${svc.name}`, h`<p class="mu">A word matches at the start of a word ("harass" matches "harassment"). Urgent keywords make a request critical.</p>
        ${rules.length ? rules.map((r) => h`<div class="flex"><span class="grow">${r.keyword} ${r.is_urgent ? pill('hi', 'urgent') : ''} <span class="mu">weight ${r.weight}</span></span><button type="button" class="b o sm" data-rmkw="${r.id}">Remove</button></div>`) : empty('No keywords yet')}
        <hr style="border:0;border-top:1px solid var(--bd);margin:12px 0">${field('keyword', 'Add keyword', 'text', '', 'maxlength="80"')}${field('weight', 'Weight (1–10)', 'number', 1, 'min="1" max="10"')}${check('is_urgent', 'Urgent')}`,
        { submitText: 'Add keyword', onSubmit: async (x) => { if (!x.keyword) return; await post(A('/triage-rules'), { service_id: svc.id, keyword: x.keyword, weight: +x.weight || 1, is_urgent: x.is_urgent === '1' }); return 'again'; } });
      const mb = document.querySelector('.mb:last-of-type');
      mb.addEventListener('click', async (e) => { const t = e.target.closest('[data-rmkw]'); if (!t) return; e.preventDefault(); try { await del(A(`/triage-rules/${t.dataset.rmkw}`)); t.closest('.flex').remove(); toast('Removed'); } catch (ex) { toast(msgOf(ex), true); } });
      const v = await p;
      return v;
    };
    let again = await loop();
    while (again === 'again') { toast('Keyword added'); again = await loop(); }
  }) });

// ---- users ---------------------------------------------------------------------------------------------------------------
async function users(el, _, alive) {
  const [roles, depts] = await Promise.all([get(A('/roles')).catch(() => ({ roles: [] })), get(A('/departments'))]);
  let q = '', role = '', offset = 0;
  const tempShow = (title, email, pw) => modal(title, h`<p>Share this one-time password with <b>${email}</b> securely. They must change it at first sign-in. It will not be shown again.</p><div class="note"><code style="font-size:16px;user-select:all">${pw}</code></div>`);
  const draw = async () => {
    const d = await get(A('/users' + qs({ q, role, limit: 25, offset })));
    if (!alive()) return;
    mount(el, h`<section class="c"><div class="flex sp"><h2>Users</h2><button class="b sm" id="nu">New user</button></div>
      <div class="flex"><input class="s grow" id="q" type="search" placeholder="Search name or email" value="${q}" aria-label="Search users"><select class="s" id="rf" style="width:auto" aria-label="Filter by role">${opt(roles.roles.map((r) => [r.code, r.name]), role, 'All roles')}</select></div>
      ${d.items.length ? table(['Name', 'Role', 'Department', 'Status', ''], d.items.map((u) => h`<tr><td><b>${u.full_name}</b><div class="mu">${u.email}${u.roll_no ? ' · ' + u.roll_no : ''}</div></td><td>${label(u.role)}</td><td>${u.department_name || '—'}</td><td>${u.is_active ? pill('ok', 'active') : pill('', 'inactive')} ${u.locked ? pill('hi', 'locked') : ''} ${u.must_change_password ? pill('wn', 'temp password') : ''}</td>
        <td style="white-space:nowrap"><button class="b o sm" data-e="${u.id}">Edit</button> <button class="b o sm" data-r="${u.id}">Reset password</button>${u.locked ? h` <button class="b o sm" data-u="${u.id}">Unlock</button>` : ''}</td></tr>`)) : empty('No users match')}
      <div class="flex sp" style="margin-top:8px"><span class="mu">${d.total} users</span><span><button class="b o sm" data-pg="${Math.max(0, offset - 25)}" ${offset ? '' : 'disabled'}>Previous</button> <button class="b o sm" data-pg="${offset + 25}" ${offset + 25 < d.total ? '' : 'disabled'}>Next</button></span></div></section>`);
    el._u = d.items;
    const qi = $('#q', el); qi.oninput = () => { clearTimeout(el._t); el._t = setTimeout(() => { q = qi.value; offset = 0; draw().then(() => $('#q', el).focus()); }, 350); };
    $('#rf', el).onchange = (e) => { role = e.target.value; offset = 0; draw(); };
  };
  const dsel = (v) => select('department_id', 'Department (staff, heads, counsellors)', depts.map((x) => [x.id, x.name]), v, '— none —');
  on(el, 'click', '[data-pg]', (b) => { offset = +b.dataset.pg; draw(); });
  on(el, 'click', '#nu', async () => {
    const v = await modal('New user', h`${field('email', 'Email', 'email', '', 'required')}${field('full_name', 'Full name', 'text', '', 'required')}${select('role', 'Role', roles.roles.map((r) => [r.code, r.name]), 'student', null, 'required')}${dsel('')}
      ${field('roll_no', 'Roll number (students)')}${field('programme', 'Programme (students)')}${field('year', 'Year (students)', 'number', '', 'min="1" max="8"')}
      <p class="mu">A one-time temporary password is generated and shown once.</p>`, { submitText: 'Create user', wide: true,
      onSubmit: async (x) => { const b = { email: x.email, full_name: x.full_name, role: x.role }; ['department_id', 'roll_no', 'programme'].forEach((k) => { if (x[k]) b[k] = x[k]; }); if (x.year) b.year = +x.year; return post(A('/users'), b); } });
    if (v && v.temporary_password) { await tempShow('User created', v.user.email, v.temporary_password); await draw(); }
  });
  on(el, 'click', '[data-e]', async (b) => {
    const u = el._u.find((x) => x.id === b.dataset.e);
    const v = await modal('Edit user', h`${field('full_name', 'Full name', 'text', u.full_name, 'required')}${u.role === 'student' ? '' : select('role', 'Role', roles.roles.filter((r) => r.code !== 'student').map((r) => [r.code, r.name]), u.role)}${u.role === 'student' ? '' : dsel(u.department_id)}${check('is_active', 'Active (untick to sign this person out and block access)', u.is_active)}`,
      { submitText: 'Save', onSubmit: (x) => { const body = { full_name: x.full_name, is_active: x.is_active === '1' }; if (x.role && x.role !== u.role) body.role = x.role; if (u.role !== 'student') body.department_id = x.department_id || null; return patch(A(`/users/${u.id}`), body); } });
    if (v) { toast('Saved'); await draw(); }
  });
  on(el, 'click', '[data-r]', async (b) => {
    const u = el._u.find((x) => x.id === b.dataset.r);
    if (!(await confirmBox('Reset password?', `${u.full_name} will be signed out everywhere and must use a new temporary password.`, 'Reset', true))) return;
    try { const r = await post(A(`/users/${u.id}/reset-password`)); await tempShow('Temporary password', u.email, r.temporary_password); await draw(); } catch (ex) { toast(msgOf(ex), true); }
  });
  on(el, 'click', '[data-u]', async (b) => { try { await post(A(`/users/${b.dataset.u}/unlock`)); toast('Unlocked'); await draw(); } catch (ex) { toast(msgOf(ex), true); } });
  await draw();
}

// ---- roles & permissions ----------------------------------------------------------------------------------------------------
async function roles(el, _, alive) {
  const d = await get(A('/roles'));
  if (!alive()) return;
  mount(el, h`<section class="c"><h2>Roles & permissions</h2><p class="mu">Changes apply immediately, without anyone signing in again. Every change is audited. The admin role must keep user and permission management, and Command/Admin deliberately have no permission to read request text.</p></section>
    ${d.roles.map((r) => h`<section class="c"><div class="flex sp"><h3>${r.name} <span class="mu">(${r.code}, ${r.user_count} users)</span></h3><button class="b sm" data-save="${r.code}">Save ${r.name}</button></div><p class="mu">${r.description || ''}</p>
      <details><summary>${r.permissions.length} permissions</summary><div class="g2" style="gap:0 16px">${d.permissions.map((p) => h`<label class="ck" title="${p.description || ''}"><input type="checkbox" data-role="${r.code}" value="${p.code}" ${r.permissions.includes(p.code) ? 'checked' : ''}> <span><code>${p.code}</code> <span class="mu">${p.description || ''}</span></span></label>`)}</div></details></section>`)}`);
  on(el, 'click', '[data-save]', async (b) => {
    const code = b.dataset.save;
    const perms = $$(`input[data-role=${code}]:checked`, el).map((i) => i.value);
    try { await put(A(`/roles/${code}/permissions`), { permissions: perms }); toast(`${code} permissions saved`); } catch (ex) { toast(msgOf(ex), true); }
  });
}

// ---- import ---------------------------------------------------------------------------------------------------------------------
async function imports(el) {
  const kinds = [['departments', 'Departments'], ['locations', 'Locations'], ['services', 'Services'], ['assets', 'Assets'], ['users', 'Users (students & staff)']];
  mount(el, h`<section class="c"><h2>CSV import</h2><p class="mu">Always validated first. Applying is all-or-nothing: if any row has an error, nothing is saved. Existing records (same code or email) are updated, not duplicated.</p>
    <form id="imf">${select('kind', 'What are you importing?', kinds, 'users')}<p class="mu"><a id="tpl" href="#">Download the CSV template</a></p>
    <label class="f" for="file">CSV file (UTF-8, up to 5,000 rows)</label><input class="s" id="file" name="file" type="file" accept=".csv,text/csv" required>
    <div class="flex" style="margin-top:12px"><button class="b o" data-mode="check">Validate only</button><button class="b" data-mode="apply" disabled id="apply">Validate, then import…</button></div></form><div id="res" style="margin-top:12px"></div></section>`);
  const kind = () => $('[name=kind]', el).value;
  $('#tpl', el).onclick = async (e) => { e.preventDefault(); const t = await api('GET', A(`/imports/${kind()}/template`), undefined, { text: true }); const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([t], { type: 'text/csv' })); a.download = `${kind()}-template.csv`; a.click(); };
  let validated = null;
  const run = async (dry) => {
    const f = $('#file', el).files[0]; if (!f) { toast('Choose a file first', true); return null; }
    const fd = new FormData(); fd.append('file', f); fd.append('dry_run', dry ? 'true' : 'false');
    return post(A(`/imports/${kind()}`), fd);
  };
  const show = (r) => mount($('#res', el), h`<div class="note ${r.error_count ? 'hi' : ''}"><b>${r.dry_run ? 'Check complete' : r.applied ? 'Imported' : 'Nothing was imported'}:</b> ${r.rows} rows — ${r.created} to create, ${r.updated} to update, ${r.error_count} with errors.</div>
    ${r.errors.length ? table(['Row', 'Problem'], r.errors.map((e) => h`<tr><td>${e.row}</td><td>${e.message}</td></tr>`)) : ''}
    ${r.temporary_passwords.length ? h`<div class="note wn"><b>One-time temporary passwords</b> — copy them now; they are not stored or shown again.${table(['Email', 'Password'], r.temporary_passwords.map((t) => h`<tr><td>${t.email}</td><td><code>${t.temporary_password}</code></td></tr>`))}<button class="b o sm" id="dl" type="button" style="margin-top:8px">Download as CSV</button></div>` : ''}`);
  el.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-mode]'); if (!b) return; e.preventDefault();
    try {
      if (b.dataset.mode === 'check') { const r = await run(true); if (r) { show(r); validated = r.error_count === 0 && r.rows > 0; $('#apply', el).disabled = !validated; $('#apply', el).textContent = `Import ${r.rows} rows`; } }
      else {
        if (!validated) { const r = await run(true); if (!r) return; show(r); validated = r.error_count === 0 && r.rows > 0; $('#apply', el).disabled = !validated; return; }
        if (!(await confirmBox('Import now?', 'This will create and update records.', 'Import'))) return;
        const r = await run(false); show(r); el._tp = r.temporary_passwords; validated = false; $('#apply', el).disabled = true;
      }
    } catch (ex) { toast(msgOf(ex), true); }
  });
  el.addEventListener('click', (e) => { if (e.target.id !== 'dl') return; const t = 'email,temporary_password\n' + el._tp.map((x) => `${x.email},${x.temporary_password}`).join('\n'); const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([t], { type: 'text/csv' })); a.download = 'temporary-passwords.csv'; a.click(); });
  $('[name=kind]', el).onchange = () => { validated = null; $('#apply', el).disabled = true; $('#res', el).innerHTML = ''; };
}

// ---- audit ---------------------------------------------------------------------------------------------------------------------------
async function audit(el, _, alive) {
  let f = { action: '', outcome: '' }, offset = 0;
  const draw = async () => {
    const d = await get(A('/audit-logs' + qs({ ...f, limit: 50, offset })));
    if (!alive()) return;
    mount(el, h`<section class="c"><div class="flex sp"><h2>Audit log</h2><a class="b o sm nl" href="/api/v1/admin/audit-logs/export.csv${qs({ action: f.action })}">Export CSV</a></div><p class="mu">Append-only: the database rejects edits and deletions. Request text and passwords are never recorded here.</p>
      <form class="flex" id="af"><input class="s grow" name="action" placeholder="Action starts with… (e.g. request., auth., case.)" value="${f.action}" aria-label="Action filter"><select class="s" name="outcome" style="width:auto" aria-label="Outcome">${opt(['success', 'denied', 'failure'], f.outcome, 'Any outcome')}</select><button class="b sm">Filter</button></form>
      ${d.items.length ? table(['When', 'Who', 'Action', 'Target', 'Outcome', 'IP'], d.items.map((a) => h`<tr><td>${dt(a.ts)}</td><td>${a.actor.name || (a.actor_role === 'system' || !a.actor.id ? 'system / anonymous' : '')}<div class="mu">${label(a.actor.role)}</div></td><td><code>${a.action}</code>${a.details ? h`<div class="mu">${JSON.stringify(a.details)}</div>` : ''}</td><td>${a.entity_type || ''}</td><td>${pill(a.outcome === 'success' ? 'ok' : 'hi', a.outcome)}</td><td class="mu">${a.ip || ''}</td></tr>`)) : empty('No entries match')}
      <div class="flex sp" style="margin-top:8px"><span class="mu">${d.total} entries</span><span><button class="b o sm" data-pg="${Math.max(0, offset - 50)}" ${offset ? '' : 'disabled'}>Newer</button> <button class="b o sm" data-pg="${offset + 50}" ${offset + 50 < d.total ? '' : 'disabled'}>Older</button></span></div></section>`);
  };
  el.addEventListener('submit', (e) => { if (e.target.id !== 'af') return; e.preventDefault(); f = Object.fromEntries(new FormData(e.target)); offset = 0; draw(); });
  on(el, 'click', '[data-pg]', (b) => { offset = +b.dataset.pg; draw(); });
  await draw();
}

// ---- system -------------------------------------------------------------------------------------------------------------------------------
async function system(el) {
  mount(el, h`<section class="c"><h2>System</h2><p class="mu">The background worker sweeps SLAs every minute. Use this to run it immediately.</p><button class="b" id="sw">Run SLA &amp; escalation sweep now</button><div id="out" style="margin-top:10px"></div></section>
    ${S.meta.demo_mode ? h`<section class="c em"><h3><span class="tag">Demo Mode</span></h3><p class="mu">Development data only. This button exists only when Demo Mode is on and is refused on any database that is not named dev/demo/test.</p><button class="b dg" id="rs">Reset demo data</button></section>` : ''}`);
  $('#sw', el).onclick = async () => { try { const s = await post(A('/system/sla-sweep')); mount($('#out', el), h`<dl class="kv">${Object.entries(s).map(([k, v]) => h`<dt>${label(k)}</dt><dd>${v}</dd>`)}</dl>`); } catch (ex) { toast(msgOf(ex), true); } };
  const rs = $('#rs', el);
  if (rs) rs.onclick = async () => { if (!(await confirmBox('Reset demo data?', 'All demo data is replaced and everyone is signed out.', 'Reset', true))) return; try { await post('/demo/reset'); location.href = 'login.html'; } catch (ex) { toast(msgOf(ex), true); } };
}

export const routes = [
  { name: 'users', title: 'Users', perm: 'admin.users', nav: true, render: users },
  { name: 'departments', title: 'Departments', perm: 'admin.departments', nav: true, render: departments },
  { name: 'locations', title: 'Locations', perm: 'admin.departments', nav: true, render: locations },
  { name: 'services', title: 'Services', perm: 'admin.services', nav: true, render: services },
  { name: 'sla', title: 'SLA', perm: 'admin.sla', nav: true, render: sla },
  { name: 'roles', title: 'Permissions', perm: 'admin.permissions', nav: true, render: roles },
  { name: 'assets', title: 'Assets', perm: 'asset.manage', nav: true, render: assets },
  { name: 'import', title: 'Import', perm: 'admin.import', nav: true, render: imports },
  { name: 'audit', title: 'Audit', perm: 'audit.read', nav: true, render: audit },
  { name: 'system', title: 'System', perm: 'admin.system', nav: true, render: system },
];
