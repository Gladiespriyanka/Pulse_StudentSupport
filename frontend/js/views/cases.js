import { get, post, qs } from '../api.js';
import { S, isStudent } from '../state.js';
import { $, h, mount, on, toast, empty, ago, dt, pill, label, modal, confirmBox, area, select, field, msgOf } from '../ui.js';

const REASONS = [['known_temporary_event', 'Known temporary event'], ['already_handled', 'Already handled'], ['insufficient_evidence', 'Insufficient evidence'], ['not_a_concern', 'Not a concern']];

async function cases(el, _, alive) {
  const d = await get('/cases?limit=100');
  if (!alive()) return;
  const sug = d.items.filter((c) => c.status === 'suggested'), rest = d.items.filter((c) => c.status !== 'suggested');
  const row = (c) => h`<a class="nl" href="#/case/${c.id}"><div class="row"><div><b>${c.ref} · ${c.student ? c.student.name : c.title}</b><div class="mu">${c.title}${c.owner ? ' · coordinator ' + c.owner.name : ''} · ${ago(c.created_at)}</div></div>${pill(c.status)}</div></a>`;
  mount(el, h`<section class="c"><h2>Support cases</h2><p class="mu">You only see cases for students who chose to share with you or your department. The system suggests; a person decides.</p></section>
    ${sug.length ? h`<section class="c em"><h3><span class="tag">Awaiting your review</span></h3>${sug.map(row)}</section>` : ''}
    <section class="c"><h3>Active and past cases</h3>${rest.length ? rest.map(row) : empty('No cases', sug.length ? '' : 'Nothing has been shared with you yet.')}</section>`);
}

async function caseView(el, id, alive) {
  const student = isStudent();
  let c;
  const draw = async () => {
    c = await get(`/cases/${id}`);
    if (!alive()) return;
    const live = ['open', 'monitoring'].includes(c.status), rev = !student && S.can('case.manage'), ev = c.evidence || {};
    mount(el, h`<a href="#/${student ? 'support' : 'cases'}" class="mu">← Back</a>
      <section class="c"><div class="flex sp"><div><span class="mu">${c.ref}</span><h2>${student ? c.title : (c.student ? c.student.name : c.title)}</h2>${c.student ? h`<span class="mu">${c.student.roll_no || ''}</span>` : ''}</div><div class="flex">${pill(c.status)}${c.outcome ? pill(c.outcome) : ''}</div></div>
        <div class="note"><b>Why this case exists</b>${ev.manual ? h`<div>Opened by a person you chose to share with.</div>` : h`<div>${ev.request_count} requests across ${ev.department_count} areas (${(ev.departments || []).join(', ')}) in the last ${ev.window_days} days.${ev.request_numbers ? h` Requests: ${ev.request_numbers.map((n) => 'REQ-' + n).join(', ')}.` : ''}</div><div class="mu">Rule: ${ev.rule}, thresholds ${ev.thresholds ? ev.thresholds.min_requests + ' requests / ' + ev.thresholds.min_departments + ' areas' : ''}. This is a prompt for a human check-in, not a diagnosis. Counts only — no message text.</div>`}</div>
        ${c.status === 'suggested' && student ? h`<div class="note wn">Nobody has been asked to reach out yet. If someone you allowed reviews this and confirms, you will be told first.</div>` : ''}
        ${student && live ? h`<div class="flex"><span>Would you like them to reach out?</span><button class="b sm${c.student_response === 'accepted' ? '' : ' o'}" data-r="accepted">Yes, that's okay</button><button class="b sm${c.student_response === 'declined' ? ' dg' : ' o'}" data-r="declined">Not right now</button><button class="b o sm" id="end">End this support case</button></div>` : ''}
        ${rev && c.status === 'suggested' ? h`<div class="flex"><button class="b sm" id="cf">Confirm — I will check in</button><button class="b o sm" id="ds">Dismiss…</button></div><p class="mu">Confirming makes you the first participant and tells the student. Dismissals are recorded with a reason to measure the rule.</p>` : ''}
        ${rev && live ? h`<div class="flex"><button class="b o sm" id="ap">Add someone</button>${c.status === 'open' ? h`<button class="b o sm" id="mo">Move to monitoring</button>` : ''}<button class="b o sm" id="cl">Close case</button>${S.can('access.request') ? h`<button class="b o sm" id="ba">Ask student for extra access</button>` : ''}</div>` : ''}
        ${c.closure_note ? h`<p class="mu">Closing note: ${c.closure_note}</p>` : ''}</section>
      ${c.status === 'dismissed' ? '' : h`<div class="g2"><section class="c"><h3>Support team</h3>${c.participants.length ? c.participants.map((p) => h`<div class="row"><div><b>${p.name}</b> ${p.is_owner ? pill('in', 'coordinator') : ''}<div class="mu">${label(p.role)}${p.department ? ' · ' + p.department : ''}</div></div>${rev && live && !p.is_owner ? h`<span><button class="b o sm" data-own="${p.id}">Make coordinator</button> <button class="b o sm" data-rm="${p.id}">Remove</button></span>` : ''}</div>`) : empty('Nobody yet')}</section>
        <section class="c"><h3>Linked requests</h3>${c.requests.length ? c.requests.map((r) => h`<div class="row"><div><b>${r.ref}</b> · ${r.service || '—'} · ${r.department}${r.description ? h`<div class="pre mu">${r.description}</div>` : r.restricted && !student ? h`<div class="mu">🔒 Details restricted by policy</div>` : ''}</div>${pill(r.status)}</div>`) : empty('None')}</section></div>
      <div class="g2"><section class="c"><h3>Care thread</h3>${c.messages.length ? c.messages.map((m) => h`<div class="msg ${m.author.id === S.me.id ? 'me' : ''}"><small>${m.author.name} · ${ago(m.created_at)}</small><div class="pre">${m.body}</div></div>`) : empty('No messages yet')}
          ${live ? h`<form id="mf"><label class="sr" for="mt">Message</label><textarea class="s" id="mt" name="text" required maxlength="4000" placeholder="Write a message…"></textarea><button class="b sm" style="margin-top:8px">Send</button></form>` : ''}</section>
        <section class="c"><div class="flex sp"><h3>Support actions</h3>${!student && S.can('task.manage') && live ? h`<button class="b o sm" id="at">Add action</button>` : ''}</div>
          ${c.tasks.length ? c.tasks.map((t) => h`<div class="row"><div><b>${t.title}</b><div class="mu">${t.assignee ? t.assignee.name : ''}${t.due_at ? ' · due ' + ago(t.due_at) : ''}${t.outcome ? ' · outcome: ' + label(t.outcome) : ''}</div></div>${pill(t.status)}${student && t.status === 'done' ? h`<span><button class="b sm" data-fb="${t.id}" data-h="1">It helped</button> <button class="b o sm" data-fb="${t.id}">It didn't</button></span>` : ''}</div>`) : empty('No actions yet')}</section></div>`}`);
  };
  const run = async (fn, ok) => { try { await fn(); if (ok) toast(ok); await draw(); } catch (ex) { toast(msgOf(ex), true); } };
  on(el, 'click', '#cf', () => run(() => post(`/cases/${id}/confirm`), 'Confirmed. The student has been told.'));
  on(el, 'click', '#ds', async () => { const v = await modal('Dismiss suggestion', select('reason', 'Reason', REASONS, '', 'Choose…', 'required'), { submitText: 'Dismiss', onSubmit: (x) => x }); if (v) { try { await post(`/cases/${id}/dismiss`, v); toast('Dismissed'); location.hash = '#/cases'; } catch (ex) { toast(msgOf(ex), true); } } });
  on(el, 'click', '#mo', () => run(() => post(`/cases/${id}/status`, { status: 'monitoring' }), 'Now monitoring'));
  on(el, 'click', '#cl', async () => { const v = await modal('Close case', h`${select('outcome', 'Outcome', [['stabilised', 'Stabilised'], ['partly', 'Partly helped'], ['no_change', 'No change'], ['declined', 'Student declined']], 'stabilised')}${area('note', 'Closing note (private to the team)', '')}`, { submitText: 'Close case', onSubmit: (x) => x }); if (v) run(() => post(`/cases/${id}/close`, v), 'Case closed'); });
  on(el, 'click', '#end', async () => { if (await confirmBox('End support case?', 'Nobody will reach out. You can ask for help any time.', 'End case')) run(() => post(`/cases/${id}/close`, {}), 'Case ended'); });
  on(el, 'click', '[data-r]', (b) => run(() => post(`/cases/${id}/response`, { response: b.dataset.r }), 'Thanks — we noted your choice'));
  on(el, 'click', '[data-own]', (b) => run(() => post(`/cases/${id}/owner`, { user_id: b.dataset.own }), 'Coordinator updated'));
  on(el, 'click', '[data-rm]', (b) => run(() => fetch(`/api/v1/cases/${id}/participants/${b.dataset.rm}`, { method: 'DELETE', credentials: 'same-origin', headers: { 'X-CSRF-Token': (document.cookie.match(/pulse_csrf=([^;]*)/) || [])[1] || '' } }).then(async (r) => { if (!r.ok) throw new Error((await r.json()).error.message); }), 'Removed'));
  on(el, 'click', '[data-fb]', (b) => run(() => post(`/tasks/${b.dataset.fb}/feedback`, { helpful: !!b.dataset.h }), 'Thank you'));
  on(el, 'click', '#ap', async () => {
    const cand = await get(`/cases/${id}/candidates`);
    if (!cand.length) { toast('Nobody else is allowed by the student yet.', true); return; }
    const v = await modal('Add to the support team', h`<p class="mu">Only people the student allowed are listed.</p>${select('user_id', 'Person', cand.map((p) => [p.id, `${p.name} (${label(p.role)}${p.department ? ', ' + p.department : ''})`]), '', '', 'required')}`, { submitText: 'Add', onSubmit: (x) => x });
    if (v) run(() => post(`/cases/${id}/participants`, v), 'Added');
  });
  on(el, 'click', '#at', async () => {
    const v = await modal('Add a support action', h`${field('title', 'What will be done?', 'text', '', 'required maxlength="200"')}${select('assignee_id', 'Who', c.participants.filter((p) => p.role !== 'student').map((p) => [p.id, p.name]), S.me.id)}<p class="mu">Offers are voluntary. The student can decline outreach at any time.</p>`, { submitText: 'Add', onSubmit: (x) => x });
    if (v) run(() => post(`/cases/${id}/tasks`, { title: v.title, assignee_id: v.assignee_id }), 'Action added');
  });
  on(el, 'click', '#ba', async () => {
    const v = await modal('Ask for extra access', h`<p class="mu">The student decides. Approved access is time-limited, and every use is shown to them.</p>${area('reason', 'Why do you need it? (shown to the student)', '', 'required minlength="10"')}`, { submitText: 'Send request', onSubmit: (x) => x });
    if (v) run(() => post('/access-requests', { student_id: c.student.id, reason: v.reason }), 'Request sent to the student');
  });
  el.addEventListener('submit', (e) => { if (e.target.id !== 'mf') return; e.preventDefault(); run(() => post(`/cases/${id}/messages`, Object.fromEntries(new FormData(e.target))), 'Sent'); });
  await draw();
}

export const caseRoutes = [
  { name: 'cases', title: 'Cases', perm: () => !isStudent() && S.can('case.read'), nav: true, also: ['case'], render: cases },
  { name: 'case', title: 'Case', perm: 'case.read', render: caseView },
];
