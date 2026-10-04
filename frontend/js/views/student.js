import { get, post, put, qs } from '../api.js';
import { S, isStudent } from '../state.js';
import { $, $$, h, raw, mount, on, toast, empty, load, ago, dt, pill, slaPill, label, esc, modal, confirmBox, area, select, check, field, opt, msgOf } from '../ui.js';
import { renderRequest } from './request.js';

const CRISIS = (list) => h`<div class="note hi"><b>If you or someone else is in danger, call now:</b><ul style="margin:6px 0 0 18px;padding:0">${list.map((x) => h`<li>${x}</li>`)}</ul></div>`;

async function home(el, _, alive) {
  const [reqs, cat, locs] = await Promise.all([get('/requests?mine=true&limit=50'), get('/catalog/services'), get('/catalog/locations')]);
  if (!alive()) return;
  const open = reqs.items.filter((r) => !['resolved', 'closed', 'cancelled', 'rejected'].includes(r.status)).length;
  const first = S.me.full_name.split(' ')[0];
  mount(el, h`
    <section class="c hero"><h1>Hi ${first}. What is going on?</h1><p class="mu">Tell us in your own words. We route it to the right people and you can follow every step.</p>
      <div class="kp" style="margin-top:12px"><div><b>${open}</b><span class="mu">open requests</span></div><div><b>${reqs.items.filter((r) => ['resolved', 'closed'].includes(r.status)).length}</b><span class="mu">resolved</span></div><div><b>${reqs.total}</b><span class="mu">in total</span></div></div></section>
    <div class="g2">
      <section class="c"><h3>Tell us what is happening</h3>
        <form id="nf"><label class="sr" for="t">Describe the problem</label>
        <textarea id="t" name="description" maxlength="4000" placeholder="e.g. The lift in Hostel B has been stuck since morning" required></textarea>
        <div class="flex" style="margin-top:8px"><div class="grow"><label class="f" for="svc">Service (optional)</label><select class="s" id="svc" name="service_id">${opt(cat.map((s) => [s.id, s.name]), '', 'Let PULSE decide')}</select></div>
        <div class="grow"><label class="f" for="loc">Where? (optional)</label><select class="s" id="loc" name="location_id">${opt(locs.map((l) => [l.id, l.name]), '', 'Not sure / detect')}</select></div></div>
        <div id="pv" class="mu" aria-live="polite" style="margin-top:8px">Start typing and we will show where it would go and why. Nothing is sent until you press Submit.</div>
        <div id="consent" hidden><div class="note wn"><b>This is a sensitive service.</b> Choose who may read the details:
          <label class="ck"><input type="radio" name="consent" value="share"> <span>Share the details with the department so they can help</span></label>
          <label class="ck"><input type="radio" name="consent" value="private"> <span>Keep private for now (you can change this later; the department cannot open it)</span></label></div></div>
        <p class="err" role="alert"></p>
        <div class="flex" style="margin-top:10px"><button class="b" id="sub">Submit</button><button type="button" class="b sos" id="sos">SOS — I need help now</button><button type="button" class="b o" id="peer">Worried about a friend?</button></div></form>
      </section>
      <section class="c"><h3>My requests</h3>${reqs.items.length ? reqs.items.map((r) => h`<a class="nl" href="#/request/${r.id}"><div class="row"><div><b>${r.title}</b><div class="mu">${r.ref} · ${r.service ? r.service.name : 'Being routed'} · ${ago(r.created_at)}</div></div>${pill(r.status)}</div></a>`) : empty('No requests yet', 'When you ask for help it appears here.')}</section>
    </div>`);
  const f = $('#nf', el), pv = $('#pv', el), err = $('.err', f);
  let state = { sensitive: false, needsChoice: false }, tm, seq = 0;
  const preview = async () => {
    const text = $('#t', el).value.trim(); const mine = ++seq;
    if (text.length < 3) { pv.textContent = 'Start typing and we will show where it would go and why. Nothing is sent until you press Submit.'; $('#consent', el).hidden = true; return; }
    try {
      const p = await post('/triage/preview', { description: text, service_id: $('#svc', el).value || undefined });
      if (mine !== seq) return;
      state = { sensitive: p.is_sensitive, needsChoice: p.needs_service_choice };
      $('#consent', el).hidden = !(p.is_sensitive && !p.urgent);
      mount(pv, h`${p.urgent ? CRISIS(p.crisis_resources) : ''}
        <div>${p.needs_service_choice ? h`<b>We could not tell where this belongs.</b> Choose a service above, or submit and the Service Desk will route it.` : h`Will go to <b>${p.department_name}</b>${p.service_name ? h` · ${p.service_name}` : ''}${p.location_name ? h` · ${p.location_name}` : ''} · priority <b>${p.priority}</b>`}</div>
        ${p.matches.length ? h`<div class="mu">Matched words: ${p.matches[0].terms.join(', ')} (${p.routed_by === 'student_choice' ? 'you chose the service' : 'routing rules'})</div>` : ''}
        ${p.articles.length ? h`<details><summary>These answers might help right now</summary>${p.articles.map((a) => h`<div class="note"><b>${a.title}</b><div class="pre">${a.body}</div></div>`)}</details>` : ''}`);
    } catch (e) { if (mine === seq) pv.textContent = ''; }
  };
  $('#t', el).oninput = () => { clearTimeout(tm); tm = setTimeout(preview, 450); };
  $('#svc', el).onchange = preview;
  f.onsubmit = async (e) => {
    e.preventDefault(); err.textContent = '';
    const v = Object.fromEntries(new FormData(f));
    const body = { description: v.description, service_id: v.service_id || undefined, location_id: v.location_id || undefined };
    if (!$('#consent', el).hidden) {
      if (!v.consent) { err.textContent = 'Please choose whether to share the details.'; return; }
      body.consent_share = v.consent === 'share';
    }
    $('#sub', el).disabled = true;
    try {
      const r = await post('/requests', body);
      if (r.crisis_resources?.length) await modal('You are not alone', h`<p>We have flagged this as urgent and alerted the team.</p>${CRISIS(r.crisis_resources)}`);
      toast(`Sent — ${r.ref}`); location.hash = `#/request/${r.id}`;
    } catch (ex) { err.textContent = msgOf(ex); $('#sub', el).disabled = false; }
  };
  $('#sos', el).onclick = async () => {
    const ok = await confirmBox('Send an SOS?', 'This immediately alerts Safety & Security with critical priority. If you are in danger, also call the emergency number.', 'Send SOS', true);
    if (!ok) return;
    try { const r = await post('/requests/sos', { location_id: $('#loc', el).value || undefined }); await modal('Help is being alerted', h`<p>Your SOS was sent (${r.ref}).</p>${CRISIS(r.crisis_resources)}`); location.hash = `#/request/${r.id}`; }
    catch (ex) { toast(msgOf(ex), true); }
  };
  $('#peer', el).onclick = () => modal('Worried about a friend?', h`<p class="mu">This is sent anonymously to counsellors. We do not record who you are, and you will not get a reply.</p>${area('description', 'What have you noticed?', '', 'required minlength="3" maxlength="2000"')}`,
    { submitText: 'Send anonymously', onSubmit: async (v) => { await post('/requests/peer-concern', { description: v.description }); toast('Sent anonymously. Thank you for caring.'); } });
}

async function privacy(el, _, alive) {
  let c;
  const draw = async () => {
    const [c2, log, ars] = await Promise.all([get('/consents/me'), get('/me/access-log'), get('/access-requests')]);
    c = c2;
    if (!alive()) return;
    const pending = ars.filter((a) => a.status === 'pending'), active = ars.filter((a) => a.status === 'approved');
    mount(el, h`
    ${c.onboarded ? '' : h`<div class="note wn"><b>Welcome.</b> Before you start, choose who may be part of your support team if you ever need extra help. You can change this at any time. Choosing nobody is fine.</div>`}
    <div class="g2"><section class="c"><h2>Who can join my support team</h2>
      <p class="mu">If several of your requests suggest you could use a check-in, PULSE only <i>suggests</i> a support case to the people you allow below. A person decides — nothing is automatic, and you can decline.</p>
      <form id="cf">${c.mentor ? check('mentor', h`My faculty mentor (${c.mentor.name})`, c.mentor_allowed) : h`<p class="mu">No faculty mentor is assigned to you.</p>`}
        <p class="mu" style="margin:10px 0 0">Departments whose staff may join:</p>
        ${c.departments.map((d) => h`<label class="ck"><input type="checkbox" name="d" value="${d.id}" ${c.department_ids.includes(d.id) ? 'checked' : ''}> <span>${d.name}</span></label>`)}
        <p class="err" role="alert"></p><button class="b">${c.onboarded ? 'Save choices' : 'Save and continue'}</button></form></section>
    <section class="c"><h2>Extra-access requests</h2><p class="mu">Staff can ask for temporary access to your records. Nothing opens unless you approve, and it ends automatically.</p>
      ${pending.map((a) => h`<div class="row"><div><b>${a.requester.name}</b> (${label(a.requester.role)})<div class="mu">${a.reason}</div></div><button class="b sm" data-ok="${a.id}">Approve</button><button class="b o sm" data-no="${a.id}">Deny</button></div>`)}
      ${active.map((a) => h`<div class="row"><div><b>${a.requester.name}</b> has access until ${dt(a.expires_at)}</div><button class="b o sm" data-rv="${a.id}">End access now</button></div>`)}
      ${pending.length || active.length ? '' : empty('No requests', 'You would be asked first.')}</section></div>
    <section class="c"><h2>Who looked at my information</h2><p class="mu">A record of staff who opened your requests or support case. Extra-access use is marked.</p>
      ${log.length ? h`<div class="sw"><table class="tbl"><thead><tr><th>When</th><th>Who</th><th>What</th></tr></thead><tbody>${log.map((x) => h`<tr><td>${dt(x.at)}</td><td>${x.by} <span class="mu">(${label(x.role)})</span></td><td>${label(x.action)} ${x.break_glass ? pill('hi', 'extra access') : ''}</td></tr>`)}</tbody></table></div>` : empty('Nobody has opened your information')}</section>`);
  };
  el.addEventListener('submit', async (e) => {
    if (e.target.id !== 'cf') return; e.preventDefault(); const f = e.target; const first = !c.onboarded;
    try {
      await put('/consents/me', { mentor: !!f.elements.mentor?.checked, department_ids: $$('input[name=d]:checked', f).map((i) => i.value) });
      toast('Your choices were saved'); S.me = await get('/auth/me');
      if (first) location.hash = '#/home'; else await draw();
    } catch (ex) { $('.err', f).textContent = msgOf(ex); }
  });
  on(el, 'click', '[data-ok],[data-no],[data-rv]', async (b) => {
    try {
      if (b.dataset.rv) await post(`/access-requests/${b.dataset.rv}/revoke`);
      else await post(`/access-requests/${b.dataset.ok || b.dataset.no}/decision`, { approve: !!b.dataset.ok });
      toast('Done'); await draw();
    } catch (ex) { toast(msgOf(ex), true); }
  });
  await draw();
}

async function support(el, _, alive) {
  const d = await get('/cases?limit=50');
  if (!alive()) return;
  mount(el, h`<div class="c"><h2>My support</h2><p class="mu">Support cases only exist for people you have chosen in Privacy & consent.</p>
    ${d.items.length ? d.items.map((c) => h`<a class="nl" href="#/case/${c.id}"><div class="row"><div><b>${c.title}</b><div class="mu">${c.ref} · ${ago(c.created_at)}</div></div>${pill(c.status)}</div></a>`) : empty('Nothing here', 'If someone suggests a check-in it will appear here and you decide what happens.')}</div>`);
}

export const routes = [
  { name: 'home', title: 'Get help', perm: () => isStudent(), nav: true, also: ['request'], render: home },
  { name: 'support', title: 'My support', perm: () => isStudent() && S.can('case.read'), nav: true, also: ['case'], render: support },
  { name: 'privacy', title: 'Privacy & consent', perm: 'consent.manage_own', nav: true, render: privacy },
  { name: 'request', title: 'Request', perm: ['request.read_own', 'request.read_department', 'request.read_all_meta'], render: renderRequest },
];
