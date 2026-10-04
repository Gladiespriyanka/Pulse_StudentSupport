import { get, post, qs } from '../api.js';
import { S, isStudent } from '../state.js';
import { $, $$, h, mount, on, toast, empty, ago, dt, pill, slaPill, label, modal, confirmBox, area, select, field, check, opt, msgOf } from '../ui.js';

const OPEN = ['new', 'acknowledged', 'in_progress'];

export async function renderRequest(el, id, alive) {
  let r = await get(`/requests/${id}`);
  const mine = () => isStudent();
  let tasks = [];
  const loadTasks = async () => { tasks = S.any('task.manage', 'task.read_own') && !r.restricted ? (await get('/tasks' + qs({ parent_type: 'request', parent_id: id }))).items : []; };
  await loadTasks();
  if (!alive()) return;

  const draw = () => {
    const staffAct = !mine() && !r.restricted && S.can('request.manage');
    const hasFeedback = (r.events || []).some((e) => e.kind === 'feedback');
    mount(el, h`
      <a href="#/${mine() ? 'home' : 'queue'}" class="mu">← Back</a>
      <section class="c"><div class="flex sp"><div><span class="mu">${r.ref}</span><h2>${r.title}</h2></div><div class="flex">${pill(r.status)} ${pill(r.priority)} ${r.is_urgent ? pill('hi', 'urgent') : ''} ${slaPill(r.sla)}</div></div>
        <dl class="kv" style="margin-top:8px"><dt>Service</dt><dd>${r.service ? r.service.name : 'Awaiting routing'}</dd><dt>Department</dt><dd>${r.department.name}</dd>
          <dt>Location</dt><dd>${r.location ? r.location.name : '—'}</dd>${r.asset ? h`<dt>Asset</dt><dd>${r.asset.name}</dd>` : ''}
          <dt>Handled by</dt><dd>${r.assignee ? r.assignee.name : 'Not yet assigned'}</dd><dt>Raised</dt><dd>${dt(r.created_at)} (${ago(r.created_at)})</dd>
          ${r.requester ? h`<dt>Student</dt><dd>${r.requester.name} · ${r.requester.roll_no || ''}</dd>` : ''}
          ${r.sla?.resolution_due_at && OPEN.includes(r.status) ? h`<dt>Target</dt><dd>${dt(r.sla.resolution_due_at)}</dd>` : ''}</dl>
        ${r.restricted ? h`<div class="note wn">Details are restricted for your role. You can see status, ownership and timing only.</div>` : h`<div class="note pre">${r.description}</div>`}
        ${r.triage && r.triage.routed_by ? h`<p class="mu">Why it went here: ${r.triage.routed_by === 'rules' ? 'routing rules matched ' + (r.triage.matches?.[0]?.terms || []).join(', ') : r.triage.routed_by === 'student_choice' ? 'the student chose this service' : r.triage.routed_by === 'triage_desk' ? 'no rule matched, so the Service Desk will route it' : label(r.triage.routed_by)}.</p>` : ''}
        ${mine() && r.service?.is_sensitive ? h`<label class="ck"><input type="checkbox" id="cs" ${r.consent_share ? 'checked' : ''}> <span>Department can read the details of this request</span></label>` : ''}
        ${r.status === 'pending_approval' ? h`<div class="note">Waiting for approval. The clock starts once it is approved.</div>` : ''}
        <div class="flex" style="margin-top:8px" id="acts">${actions(staffAct, hasFeedback)}</div></section>
      <div class="g2">
        <section class="c"><h3>Timeline</h3>${r.restricted ? empty('Not available for your role') : h`<ul class="tl">${(r.events || []).map((e) => h`<li class="${e.kind === 'escalated' ? 'h' : ''}"><b>${evTitle(e)}</b> <span class="mu">${e.actor ? '· ' + e.actor.name : ''} · ${ago(e.created_at)}</span>${e.internal ? h` ${pill('wn', 'internal')}` : ''}${e.note ? h`<div class="pre">${e.note}</div>` : ''}</li>`)}</ul>`}
          ${!r.restricted && !['closed', 'cancelled', 'rejected'].includes(r.status) ? h`<form id="cm"><label class="sr" for="ct">Message</label>${area('text', mine() ? 'Message the team' : 'Reply', '', 'required maxlength="4000" id="ct"')}${staffAct ? check('internal', 'Internal note (the student will not see it)') : ''}<button class="b sm" style="margin-top:8px">Send</button></form>` : ''}</section>
        ${!mine() && !r.restricted ? h`<section class="c"><div class="flex sp"><h3>Tasks</h3>${S.can('task.manage') ? h`<button class="b o sm" id="nt">Add task</button>` : ''}</div>${tasks.length ? tasks.map((t) => h`<div class="row"><div><b>${t.title}</b><div class="mu">${t.ref} · ${t.assignee ? t.assignee.name : 'Unassigned'}</div></div>${pill(t.status)}</div>`) : empty('No tasks')}</section>` : ''}
      </div>`);
  };

  function evTitle(e) {
    if (e.kind === 'status_changed') return `${label(e.from_status)} → ${label(e.to_status)}`;
    if (e.kind === 'created') return 'Request raised';
    if (e.kind === 'assigned') return e.meta?.auto ? 'Assigned automatically' : 'Assigned';
    if (e.kind === 'rerouted') return `Re-routed to ${e.meta?.to_department || 'another team'}`;
    if (e.kind === 'comment') return 'Message';
    if (e.kind === 'escalated') return e.meta?.level ? `Escalated (level ${e.meta.level})` : 'Escalated';
    if (e.kind === 'feedback') return e.meta?.helpful ? 'Student: this helped' : 'Student: this did not help';
    return label(e.kind);
  }
  function actions(staffAct, hasFeedback) {
    const b = [];
    if (mine()) {
      if (['new', 'pending_approval', 'acknowledged', 'in_progress'].includes(r.status)) b.push(h`<button class="b o sm" data-st="cancelled">Cancel request</button>`);
      if (r.status === 'resolved') b.push(h`<button class="b o sm" data-st="in_progress">Reopen</button>`);
      if (['resolved', 'closed'].includes(r.status) && !hasFeedback) b.push(h`<button class="b sm" id="fb">Did this help?</button>`);
    } else if (staffAct) {
      if (r.status === 'new') b.push(h`<button class="b sm" data-st="acknowledged">Acknowledge</button><button class="b o sm" data-st="in_progress">Start work</button>`);
      if (r.status === 'acknowledged') b.push(h`<button class="b sm" data-st="in_progress">Start work</button><button class="b o sm" data-st="resolved">Resolve</button>`);
      if (r.status === 'in_progress') b.push(h`<button class="b sm" data-st="resolved">Resolve</button>`);
    }
    if (!mine() && S.any('request.assign') && OPEN.includes(r.status)) b.push(h`<button class="b o sm" id="as">Assign</button><button class="b o sm" id="rr">Re-route</button>`);
    else if (staffAct && OPEN.includes(r.status)) b.push(h`<button class="b o sm" id="tk">Take it</button>`);
    if (!mine() && S.can('request.escalate') && OPEN.includes(r.status)) b.push(h`<button class="b o sm" id="es">Escalate</button>`);
    return b;
  }

  const refresh = async (p) => { r = p || await get(`/requests/${id}`); await loadTasks(); draw(); };
  const act = async (fn, ok) => { try { const p = await fn(); toast(ok || 'Updated'); await refresh(p); window.dispatchEvent(new Event('pulse:unread')); } catch (ex) { toast(msgOf(ex), true); } };

  draw();
  {
    on(el, 'click', '[data-st]', async (bt) => {
      const to = bt.dataset.st; let note;
      if (to === 'resolved') { const v = await modal('Resolve request', area('note', 'What was done? (shown to the student)', '', 'required'), { submitText: 'Resolve', onSubmit: (x) => x }); if (!v) return; note = v.note; }
      else if (to === 'in_progress' && mine()) { const v = await modal('Reopen request', area('note', 'What is still wrong?', '', 'required'), { submitText: 'Reopen', onSubmit: (x) => x }); if (!v) return; note = v.note; }
      else if (to === 'cancelled') { if (!(await confirmBox('Cancel this request?', 'The team will stop working on it.', 'Cancel request', true))) return; }
      act(() => post(`/requests/${id}/status`, { status: to, note }), 'Request updated');
    });
    on(el, 'click', '#fb', async () => {
      const v = await modal('Did this help?', h`<label class="ck"><input type="radio" name="helpful" value="yes" required> <span>Yes, it helped</span></label><label class="ck"><input type="radio" name="helpful" value="no"> <span>No, I still need help</span></label>${area('comment', 'Anything to add? (optional)', '')}`, { submitText: 'Send feedback', onSubmit: (x) => x });
      if (v) act(() => post(`/requests/${id}/feedback`, { helpful: v.helpful === 'yes', comment: v.comment || undefined }), 'Thank you for the feedback');
    });
    on(el, 'click', '#as', async () => {
      const st = await get('/catalog/staff' + qs({ department_id: r.department.id }));
      const v = await modal('Assign', select('assignee_id', 'Assign to', st.map((s) => [s.id, `${s.name} (${label(s.role)})`]), r.assignee?.id, 'Unassigned'), { submitText: 'Assign', onSubmit: (x) => x });
      if (v) act(() => post(`/requests/${id}/assign`, { assignee_id: v.assignee_id || null }), 'Assignment saved');
    });
    on(el, 'click', '#tk', () => act(() => post(`/requests/${id}/assign`, { assignee_id: S.me.id }), 'You took this request'));
    on(el, 'click', '#rr', async () => {
      const cat = await get('/catalog/services');
      const v = await modal('Re-route to another service', select('service_id', 'Service', cat.map((s) => [s.id, s.name]), r.service?.id), { submitText: 'Re-route', onSubmit: (x) => x });
      if (v) act(() => post(`/requests/${id}/reroute`, { service_id: v.service_id }), 'Re-routed');
    });
    on(el, 'click', '#es', async () => { const v = await modal('Escalate', area('note', 'Why? (internal)', ''), { submitText: 'Escalate', onSubmit: (x) => x }); if (v) act(() => post(`/requests/${id}/escalate`, { note: v.note || undefined }), 'Escalated'); });
    on(el, 'click', '#nt', async () => {
      const st = await get('/catalog/staff' + qs({ department_id: r.department.id }));
      const v = await modal('Add task', h`${field('title', 'Task', 'text', '', 'required maxlength="200"')}${select('assignee_id', 'Assign to', st.map((s) => [s.id, s.name]), S.me.id, 'Unassigned')}`, { submitText: 'Add', onSubmit: (x) => x });
      if (v) act(async () => { await post('/tasks', { parent_type: 'request', parent_id: id, title: v.title, assignee_id: v.assignee_id || undefined }); return null; }, 'Task added');
    });
    el.addEventListener('submit', (e) => {
      if (e.target.id !== 'cm') return; e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target));
      act(() => post(`/requests/${id}/comments`, { text: f.text, internal: !!f.internal }), 'Sent');
    });
    el.addEventListener('change', (e) => { if (e.target.id === 'cs') act(() => post(`/requests/${id}/consent`, { share: e.target.checked }), e.target.checked ? 'Shared with the department' : 'Now private'); });
  }
}
