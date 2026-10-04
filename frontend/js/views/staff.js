import { get, post, patch, qs } from '../api.js';
import { S, isStudent } from '../state.js';
import { $, $$, h, raw, mount, on, toast, empty, ago, dt, day, pill, slaPill, label, modal, confirmBox, area, select, field, check, opt, table, msgOf, load } from '../ui.js';
import { caseRoutes } from './cases.js';

const pager = (d) => h`<div class="flex sp" style="margin-top:8px"><span class="mu">${d.total} total</span><span><button class="b o sm" data-pg="${Math.max(0, d.offset - d.limit)}" ${d.offset ? '' : 'disabled'}>Previous</button> <button class="b o sm" data-pg="${d.offset + d.limit}" ${d.offset + d.limit < d.total ? '' : 'disabled'}>Next</button></span></div>`;

// ---- queue ---------------------------------------------------------------------------------------------------
const TABS = [['open', 'Needs attention'], ['mine', 'Mine'], ['unassigned', 'Unassigned'], ['breached', 'Past SLA'], ['at_risk', 'At risk'], ['pending', 'Awaiting approval'], ['all', 'All']];
const TQ = { open: { open_only: true, sort: 'sla' }, mine: { mine: true, open_only: true, sort: 'sla' }, unassigned: { unassigned: true, sort: 'oldest' }, breached: { sla: 'breached', sort: 'sla' }, at_risk: { sla: 'at_risk', sort: 'sla' }, pending: { status: 'pending_approval' }, all: {} };

async function queue(el, _, alive) {
  let tab = S.me.role === 'command' ? 'breached' : 'open', offset = 0;
  const draw = async () => {
    const d = await get('/requests' + qs({ ...TQ[tab], limit: 20, offset }));
    if (!alive()) return;
    mount(el, h`<section class="c"><div class="flex sp"><h2>${S.me.role === 'command' ? 'SLA desk' : 'Request queue'}</h2></div>
      ${S.me.role === 'command' ? h`<p class="mu">You see status, ownership and timing across departments. Request text is never shown to Command.</p>` : ''}
      <div class="tabs" role="tablist">${TABS.map(([k, t]) => h`<button role="tab" data-tab="${k}" class="${k === tab ? 'on' : ''}" aria-selected="${k === tab}">${t}</button>`)}</div>
      ${d.items.length ? table(['Ref', 'Request', 'Service', 'Where', 'Priority', 'SLA', 'Owner', 'Age'], d.items.map((r) => h`<tr class="link" data-id="${r.id}" tabindex="0"><td>${r.ref}</td><td><b>${r.title}</b>${r.is_urgent ? h` ${pill('hi', 'urgent')}` : ''}</td><td>${r.service ? r.service.name : h`<i>unrouted</i>`}<div class="mu">${r.department.name}</div></td><td>${r.location ? r.location.name : '—'}</td><td>${pill(r.priority)}</td><td>${slaPill(r.sla)}</td><td>${r.assignee ? r.assignee.name : h`<span class="mu">—</span>`}</td><td>${ago(r.created_at)}</td></tr>`)) : empty('Nothing here', 'This list is empty right now.')}
      ${d.items.length ? pager(d) : ''}</section>`);
  };
  on(el, 'click', '[data-tab]', (b) => { tab = b.dataset.tab; offset = 0; draw(); });
  on(el, 'click', '[data-pg]', (b) => { offset = +b.dataset.pg; draw(); });
  on(el, 'click', 'tr[data-id]', (r) => { location.hash = `#/request/${r.dataset.id}`; });
  on(el, 'keydown', 'tr[data-id]', (r, e) => { if (e.key === 'Enter') location.hash = `#/request/${r.dataset.id}`; });
  await draw();
}

// ---- tasks ------------------------------------------------------------------------------------------------------
async function tasks(el, _, alive) {
  const draw = async () => {
    const d = await get('/tasks' + qs({ mine: true, open_only: true, limit: 100 }));
    if (!alive()) return;
    mount(el, h`<section class="c"><h2>My tasks</h2>${d.items.length ? d.items.map((t) => h`<div class="row"><div><b>${t.title}</b><div class="mu">${t.ref} · ${label(t.parent_type)}${t.due_at ? ' · due ' + ago(t.due_at) : ''} ${t.overdue ? pill('hi', 'overdue') : ''}</div>${t.description ? h`<div class="pre mu">${t.description}</div>` : ''}</div>${pill(t.status)}
      <span>${t.status === 'todo' ? h`<button class="b o sm" data-t="${t.id}" data-s="in_progress">Start</button>` : ''} ${t.status !== 'blocked' ? h`<button class="b o sm" data-t="${t.id}" data-s="blocked">Blocked</button>` : h`<button class="b o sm" data-t="${t.id}" data-s="in_progress">Unblock</button>`} <button class="b sm" data-t="${t.id}" data-s="done" data-case="${t.parent_type === 'case'}">Done</button></span></div>`) : empty('No open tasks', 'Tasks assigned to you appear here.')}</section>`);
  };
  on(el, 'click', '[data-t]', async (b) => {
    let body = { status: b.dataset.s };
    if (b.dataset.s === 'done' && b.dataset.case === 'true') {
      const v = await modal('Outcome of this action', h`${select('outcome', 'How did it go?', [['helped', 'Helped'], ['partly', 'Partly helped'], ['no_change', 'No change']], '', 'Choose…', 'required')}${area('outcome_note', 'Note (private to the team)', '')}`, { submitText: 'Mark done', onSubmit: (x) => x });
      if (!v) return; body = { ...body, outcome: v.outcome, outcome_note: v.outcome_note || undefined };
    }
    try { await patch(`/tasks/${b.dataset.t}`, body); toast('Task updated'); await draw(); } catch (ex) { toast(msgOf(ex), true); }
  });
  await draw();
}

// ---- approvals --------------------------------------------------------------------------------------------------------
async function approvals(el, _, alive) {
  let hist = false;
  const draw = async () => {
    const list = await get('/approvals' + qs({ history: hist }));
    if (!alive()) return;
    mount(el, h`<section class="c"><div class="flex sp"><h2>Approvals</h2><button class="b o sm" data-h>${hist ? 'Show pending' : 'Show my past decisions'}</button></div>
      ${list.length ? list.map((a) => h`<div class="row"><div><b>${a.subject.ref}</b> · ${a.subject.label}<div class="mu">${a.subject.department}${a.subject.risk ? ' · risk ' + a.subject.risk : ''} · ${ago(a.created_at)}</div>${a.reason ? h`<div class="mu">Reason: ${a.reason}</div>` : ''}</div>${hist ? pill(a.status) : h`<span><button class="b sm" data-a="${a.id}" data-y="1">Approve</button> <button class="b o sm" data-a="${a.id}">Reject</button></span>`}</div>`) : empty(hist ? 'No past decisions' : 'Nothing waiting for you')}</section>`);
  };
  on(el, 'click', '[data-h]', () => { hist = !hist; draw(); });
  on(el, 'click', '[data-a]', async (b) => {
    const yes = !!b.dataset.y;
    const v = await modal(yes ? 'Approve' : 'Reject', area('reason', yes ? 'Note (optional)' : 'Reason (required, shown to the requester)', '', yes ? '' : 'required'), { submitText: yes ? 'Approve' : 'Reject', danger: !yes, onSubmit: (x) => x });
    if (!v) return;
    try { await post(`/approvals/${b.dataset.a}/decision`, { approve: yes, reason: v.reason || undefined }); toast(yes ? 'Approved' : 'Rejected'); await draw(); } catch (ex) { toast(msgOf(ex), true); }
  });
  await draw();
}

// ---- incidents ----------------------------------------------------------------------------------------------------------
async function incidents(el, _, alive) {
  const [list, cl] = await Promise.all([get('/incidents?open_only=true&limit=50'), get('/intelligence/incident-clusters')]);
  if (!alive()) return;
  mount(el, h`${cl.length ? h`<section class="c"><h3>Suggested: requests piling up</h3><p class="mu">Rule: ${cl[0].rule}. Suggestions only — you decide.</p>
    ${cl.map((c, i) => h`<div class="row"><div><b>${c.location.name}</b> — ${c.request_count} open requests<div class="mu">${c.services.join(', ')} · ${c.departments.join(', ')} · oldest ${c.oldest_open_days} d</div></div>${S.can('incident.manage') ? h`<button class="b sm" data-cl="${i}">Create incident</button>` : ''}</div>`)}</section>` : ''}
    <section class="c"><div class="flex sp"><h2>Incidents</h2>${S.can('incident.manage') ? h`<button class="b sm" id="ni">New incident</button>` : ''}</div>
      ${list.items.length ? list.items.map((i) => h`<a class="nl" href="#/incident/${i.id}"><div class="row"><div><b>${i.ref} · ${i.title}</b><div class="mu">${i.department.name} · ${i.linked_request_count} linked requests · ${ago(i.created_at)}</div></div>${pill(i.priority)} ${pill(i.status)}</div></a>`) : empty('No open incidents')}</section>`);
  on(el, 'click', '[data-cl]', async (b) => {
    const c = cl[+b.dataset.cl];
    const v = await modal('Create incident from these requests', h`${field('title', 'Title', 'text', `${c.location.name}: ${c.services.join(' / ')}`, 'required maxlength="200"')}<p class="mu">${c.request_count} requests (${c.request_numbers.map((n) => 'REQ-' + n).join(', ')}) will be linked so teams work on one shared plan.</p>`, { submitText: 'Create', onSubmit: (x) => x });
    if (!v) return;
    try { const i = await post('/incidents', { title: v.title, request_ids: c.request_ids, location_id: c.location.id }); toast(`${i.ref} created`); location.hash = `#/incident/${i.id}`; } catch (ex) { toast(msgOf(ex), true); }
  });
  const ni = $('#ni', el);
  if (ni) ni.onclick = async () => {
    const v = await modal('New incident', h`${field('title', 'Title', 'text', '', 'required maxlength="200"')}${area('description', 'What is affected?', '')}`, { submitText: 'Create', onSubmit: (x) => x });
    if (!v) return;
    try { const i = await post('/incidents', { title: v.title, description: v.description || undefined }); location.hash = `#/incident/${i.id}`; } catch (ex) { toast(msgOf(ex), true); }
  };
}

async function incident(el, id, alive) {
  const draw = async () => {
    const [i, t] = await Promise.all([get(`/incidents/${id}`), get('/tasks' + qs({ parent_type: 'incident', parent_id: id }))]);
    if (!alive()) return;
    const next = { open: ['investigating', 'mitigated', 'resolved'], investigating: ['mitigated', 'resolved'], mitigated: ['investigating', 'resolved'], resolved: ['closed', 'investigating'], closed: [] }[i.status];
    mount(el, h`<a href="#/incidents" class="mu">← Incidents</a>
      <section class="c"><div class="flex sp"><div><span class="mu">${i.ref}</span><h2>${i.title}</h2></div><div class="flex">${pill(i.priority)} ${pill(i.status)}</div></div>
        <dl class="kv"><dt>Lead department</dt><dd>${i.department.name}</dd><dt>Owner</dt><dd>${i.owner ? i.owner.name : '—'}</dd><dt>Linked requests</dt><dd>${i.linked_request_count}</dd><dt>Opened</dt><dd>${dt(i.created_at)}</dd></dl>
        ${i.description ? h`<div class="note pre">${i.description}</div>` : ''}${i.resolution_note ? h`<div class="note">Resolution: ${i.resolution_note}</div>` : ''}
        <div class="flex" style="margin-top:8px">${S.can('incident.manage') ? next.map((s) => h`<button class="b${s === 'resolved' ? '' : ' o'} sm" data-s="${s}">Mark ${label(s)}</button>`) : ''}
          ${S.can('problem.manage') && !i.problem_id ? h`<button class="b o sm" id="op">Open a problem (find the cause)</button>` : ''}${i.problem_id ? h`<a class="b o sm nl" href="#/problem/${i.problem_id}">View problem →</a>` : ''}</div></section>
      <section class="c"><div class="flex sp"><h3>Joint work order — tasks</h3>${S.can('task.manage') ? h`<button class="b o sm" id="at">Add task</button>` : ''}</div>
        ${t.items.length ? t.items.map((x) => h`<div class="row"><div><b>${x.title}</b><div class="mu">${x.ref} · ${x.assignee ? x.assignee.name : 'Unassigned'}</div></div>${pill(x.status)}</div>`) : empty('No tasks yet', 'Add a task for each team that must act.')}</section>`);
  };
  on(el, 'click', '[data-s]', async (b) => {
    const s = b.dataset.s; let body = { status: s };
    if (s === 'resolved') {
      const v = await modal('Resolve incident', h`${area('note', 'What fixed it?', '', 'required')}${check('linked', 'Also mark the linked requests as resolved and ask the students for feedback', true)}${check('force', 'Resolve even if tasks are still open')}`, { submitText: 'Resolve', onSubmit: (x) => x });
      if (!v) return; body = { status: s, note: v.note, resolve_linked_requests: !!v.linked, force: !!v.force };
    }
    try { await patch(`/incidents/${id}`, body); toast('Incident updated'); await draw(); } catch (ex) { toast(msgOf(ex), true); }
  });
  on(el, 'click', '#op', async () => {
    const v = await modal('Open a problem', field('title', 'Problem title', 'text', '', 'maxlength="200"'), { submitText: 'Open problem', onSubmit: (x) => x });
    if (!v) return;
    try { const p = await post(`/incidents/${id}/problem`, { title: v.title || undefined }); toast(`${p.ref} opened`); location.hash = `#/problem/${p.id}`; } catch (ex) { toast(msgOf(ex), true); }
  });
  on(el, 'click', '#at', async () => {
    const st = await get('/catalog/staff');
    const v = await modal('Add task', h`${field('title', 'Task', 'text', '', 'required maxlength="200"')}${select('assignee_id', 'Assign to', st.map((s) => [s.id, `${s.name} — ${s.department}`]), '', 'Unassigned')}`, { submitText: 'Add', onSubmit: (x) => x });
    if (!v) return;
    try { await post('/tasks', { parent_type: 'incident', parent_id: id, title: v.title, assignee_id: v.assignee_id || undefined }); toast('Task added'); await draw(); } catch (ex) { toast(msgOf(ex), true); }
  });
  await draw();
}

// ---- problems -----------------------------------------------------------------------------------------------------------------
async function problems(el, _, alive) {
  const [list, sug] = await Promise.all([get('/problems?open_only=true&limit=50'), get('/intelligence/problem-suggestions')]);
  if (!alive()) return;
  mount(el, h`${sug.length ? h`<section class="c"><h3>Suggested: repeat incidents</h3><p class="mu">Rule: ${sug[0].rule}.</p>${sug.map((s) => h`<div class="row"><div>${s.incident_count} incidents on the same ${s.basis} (${s.incident_numbers.map((n) => 'INC-' + n).join(', ')})</div><a class="b o sm nl" href="#/incident/${s.incident_ids[0]}">Review</a></div>`)}</section>` : ''}
    <section class="c"><h2>Problems</h2>${list.items.length ? list.items.map((p) => h`<a class="nl" href="#/problem/${p.id}"><div class="row"><div><b>${p.ref} · ${p.title}</b><div class="mu">${p.department.name} · ${p.incidents.length} incidents · ${p.changes.length} changes</div></div>${pill(p.status)}</div></a>`) : empty('No open problems', 'Open one from an incident when the cause is unclear.')}</section>`);
}

async function problem(el, id, alive) {
  const draw = async () => {
    const p = await get(`/problems/${id}`);
    if (!alive()) return;
    const next = { open: ['investigating', 'closed'], investigating: ['known_error', 'resolved', 'closed'], known_error: ['investigating', 'resolved', 'closed'], resolved: ['closed', 'investigating'], closed: [] }[p.status];
    mount(el, h`<a href="#/problems" class="mu">← Problems</a>
      <section class="c"><div class="flex sp"><div><span class="mu">${p.ref}</span><h2>${p.title}</h2></div>${pill(p.status)}</div>
        <dl class="kv"><dt>Department</dt><dd>${p.department.name}</dd><dt>Owner</dt><dd>${p.owner ? p.owner.name : '—'}</dd><dt>Root cause</dt><dd class="pre">${p.root_cause || '—'}</dd><dt>Workaround</dt><dd class="pre">${p.workaround || '—'}</dd><dt>Resolution</dt><dd class="pre">${p.resolution || '—'}</dd></dl>
        ${S.can('problem.manage') ? h`<div class="flex" style="margin-top:8px"><button class="b o sm" id="ed">Edit cause / workaround / resolution</button>${next.map((s) => h`<button class="b o sm" data-s="${s}">Mark ${label(s)}</button>`)}${S.can('change.manage') && !['resolved', 'closed'].includes(p.status) ? h`<button class="b sm" id="nc">Raise a change</button>` : ''}</div>` : ''}</section>
      <div class="g2"><section class="c"><h3>Incidents</h3>${p.incidents.length ? p.incidents.map((i) => h`<a class="nl" href="#/incident/${i.id}"><div class="row"><div>${i.ref} · ${i.title}</div>${pill(i.status)}</div></a>`) : empty('None')}</section>
      <section class="c"><h3>Changes</h3>${p.changes.length ? p.changes.map((c) => h`<a class="nl" href="#/change/${c.id}"><div class="row"><div>${c.ref} · ${c.title}</div>${pill(c.status)}</div></a>`) : empty('No change raised yet')}</section></div>`);
    el._p = p;
  };
  on(el, 'click', '[data-s]', async (b) => { try { await patch(`/problems/${id}`, { status: b.dataset.s }); toast('Updated'); await draw(); } catch (ex) { toast(msgOf(ex), true); } });
  on(el, 'click', '#ed', async () => {
    const p = el._p;
    const v = await modal('Problem analysis', h`${area('root_cause', 'Root cause', p.root_cause || '')}${area('workaround', 'Workaround', p.workaround || '')}${area('resolution', 'Resolution (needed to mark resolved)', p.resolution || '')}`, { submitText: 'Save', wide: true, onSubmit: (x) => x });
    if (!v) return;
    try { await patch(`/problems/${id}`, { root_cause: v.root_cause, workaround: v.workaround, resolution: v.resolution }); toast('Saved'); await draw(); } catch (ex) { toast(msgOf(ex), true); }
  });
  on(el, 'click', '#nc', async () => {
    const v = await modal('Raise a change', h`${field('title', 'Change', 'text', '', 'required maxlength="200"')}${select('risk', 'Risk', ['low', 'medium', 'high'], 'low')}${area('implementation_plan', 'Implementation plan (required for medium/high)', '')}${area('rollback_plan', 'Rollback plan (required for medium/high)', '')}`, { submitText: 'Create draft', wide: true, onSubmit: (x) => x });
    if (!v) return;
    try { const c = await post('/changes', { title: v.title, risk: v.risk, problem_id: id, implementation_plan: v.implementation_plan || undefined, rollback_plan: v.rollback_plan || undefined }); toast(`${c.ref} drafted`); location.hash = `#/change/${c.id}`; } catch (ex) { toast(msgOf(ex), true); }
  });
  await draw();
}

// ---- changes ------------------------------------------------------------------------------------------------------------------
async function changes(el, _, alive) {
  const list = await get('/changes?limit=50');
  if (!alive()) return;
  mount(el, h`<section class="c"><h2>Changes</h2>${list.items.length ? list.items.map((c) => h`<a class="nl" href="#/change/${c.id}"><div class="row"><div><b>${c.ref} · ${c.title}</b><div class="mu">${c.department.name} · risk ${c.risk}</div></div>${pill(c.status)}</div></a>`) : empty('No changes yet', 'Changes are raised from a problem.')}</section>`);
}

async function change(el, id, alive) {
  const draw = async () => {
    const c = await get(`/changes/${id}`);
    if (!alive()) return;
    const m = S.can('change.manage');
    mount(el, h`<a href="#/changes" class="mu">← Changes</a><section class="c"><div class="flex sp"><div><span class="mu">${c.ref}</span><h2>${c.title}</h2></div><div class="flex">${pill(c.risk, 'risk ' + c.risk)} ${pill(c.status)}</div></div>
      <dl class="kv"><dt>Department</dt><dd>${c.department.name}</dd>${c.problem_id ? h`<dt>Problem</dt><dd><a href="#/problem/${c.problem_id}">View problem</a></dd>` : ''}<dt>Implementation</dt><dd class="pre">${c.implementation_plan || '—'}</dd><dt>Rollback</dt><dd class="pre">${c.rollback_plan || '—'}</dd>
      ${c.approval ? h`<dt>Approval</dt><dd>${pill(c.approval.status)} by ${label(c.approval.approver_role)}${c.approval.reason ? ' — ' + c.approval.reason : ''}</dd>` : ''}${c.outcome ? h`<dt>Outcome</dt><dd>${pill(c.outcome === 'successful' ? 'ok' : 'hi', c.outcome)} ${c.outcome_note || ''}</dd>` : ''}</dl>
      ${m ? h`<div class="flex" style="margin-top:8px">${c.status === 'draft' ? h`<button class="b sm" data-a="submit">Submit for approval</button>` : ''}${c.status === 'approved' ? h`<button class="b sm" data-a="start">Start implementation</button>` : ''}${c.status === 'implementing' ? h`<button class="b sm" data-a="complete">Complete</button>` : ''}${['draft', 'awaiting_approval', 'approved'].includes(c.status) ? h`<button class="b o sm" data-a="cancel">Cancel change</button>` : ''}</div>` : ''}</section>`);
  };
  on(el, 'click', '[data-a]', async (b) => {
    const a = b.dataset.a; let body = {};
    if (a === 'complete') { const v = await modal('Complete change', h`${select('outcome', 'Outcome', ['successful', 'failed'], 'successful')}${area('note', 'Notes (becomes the problem resolution if successful)', '')}`, { submitText: 'Complete', onSubmit: (x) => x }); if (!v) return; body = v; }
    try { await post(`/changes/${id}/${a}`, body); toast('Change updated'); await draw(); } catch (ex) { toast(msgOf(ex), true); }
  });
  await draw();
}

export const routes = [
  { name: 'queue', title: 'Queue', perm: () => !isStudent() && S.any('request.read_department', 'request.read_all_meta'), nav: true, render: queue },
  { name: 'tasks', title: 'My tasks', perm: () => !isStudent() && S.any('task.read_own', 'task.manage'), nav: true, render: tasks },
  { name: 'approvals', title: 'Approvals', perm: 'approval.decide', nav: true, render: approvals },
  { name: 'incidents', title: 'Incidents', perm: 'incident.read', nav: true, also: ['incident'], render: incidents },
  { name: 'incident', title: 'Incident', perm: 'incident.read', render: incident },
  { name: 'problems', title: 'Problems', perm: 'problem.read', nav: true, also: ['problem'], render: problems },
  { name: 'problem', title: 'Problem', perm: 'problem.read', render: problem },
  { name: 'changes', title: 'Changes', perm: 'change.read', nav: true, also: ['change'], render: changes },
  { name: 'change', title: 'Change', perm: 'change.read', render: change },
  ...caseRoutes,
];
