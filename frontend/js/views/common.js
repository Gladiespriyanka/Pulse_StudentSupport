import { get, post, qs } from '../api.js';
import { S, isStudent } from '../state.js';
import { $, h, mount, on, toast, empty, loading, load, ago, label, esc, table, field, msgOf, modal, area, select, check } from '../ui.js';
import { patch } from '../api.js';

async function notifications(el) {
  const draw = async () => {
    const d = await get('/notifications?limit=50');
    mount(el, h`<div class="c"><div class="flex sp"><h2>Notifications</h2><button class="b o sm" data-act="all" ${d.unread ? '' : 'disabled'}>Mark all read</button></div>
      ${d.items.length ? d.items.map((n) => h`<div class="row" ${n.read ? '' : h`style="font-weight:600"`}><div><span class="${n.read ? 'mu' : ''}">${n.title}</span>${n.body ? h`<div class="mu">${n.body}</div>` : ''}</div><span class="mu">${ago(n.created_at)}</span>${n.entity_type === 'request' ? h`<a href="#/request/${n.entity_id}" data-read="${n.id}">Open</a>` : n.entity_type === 'case' ? h`<a href="#/case/${n.entity_id}" data-read="${n.id}">Open</a>` : n.entity_type === 'change' ? h`<a href="#/changes" data-read="${n.id}">Open</a>` : ''}</div>`) : empty('You are all caught up', 'New updates about your requests appear here.')}</div>`);
    window.dispatchEvent(new Event('pulse:unread'));
  };
  {
    on(el, 'click', '[data-act=all]', async () => { await post('/notifications/read', {}); await draw(); });
    on(el, 'click', '[data-read]', (a) => { post('/notifications/read', { ids: [a.dataset.read] }).then(() => window.dispatchEvent(new Event('pulse:unread'))); });
  }
  await draw();
}

async function help(el) {
  const mgr = S.can('knowledge.manage');
  mount(el, h`<div class="c"><div class="flex sp"><h2>${isStudent() ? 'Answers' : 'Knowledge base'}</h2>${mgr ? h`<button class="b sm" id="na">New article</button>` : ''}</div><p class="mu">Search articles written by campus teams${isStudent() ? '' : ', including drafts created from resolved problems'}.</p>
    <input class="s" id="q" type="search" placeholder="Search…" aria-label="Search knowledge base"><div id="kb" style="margin-top:10px"></div></div>`);
  const kb = $('#kb', el);
  let t, items = [];
  const form = (a) => h`${field('title', 'Title', 'text', a?.title || '', 'required maxlength="200"')}${area('body', 'Body', a?.body || '', 'required rows="8"')}${select('audience', 'Who can read it', [['public', 'Everyone, including students'], ['staff', 'Staff only']], a?.audience || 'staff')}${select('status', 'Status', ['draft', 'published', 'archived'], a?.status || 'draft')}`;
  const run = async () => {
    const q = $('#q', el).value.trim();
    mount(kb, loading(2));
    try {
      const d = await get('/knowledge' + qs({ q, limit: 50 })); items = d.items;
      mount(kb, d.items.length ? h`${d.items.map((a) => h`<details class="row"><summary><b>${a.title}</b> ${mgr ? h`<span class="pill">${a.status}</span> <span class="pill">${a.audience}</span>` : ''}</summary><div class="pre mu">${a.body}</div>${mgr ? h`<button class="b o sm" data-ed="${a.id}">Edit</button>` : ''}</details>`)}` : empty('Nothing found', q ? 'Try different words.' : 'No articles yet.'));
    } catch (e) { mount(kb, h`<div class="state bad" role="alert">${e.message}</div>`); }
  };
  $('#q', el).oninput = () => { clearTimeout(t); t = setTimeout(run, 300); };
  const na = $('#na', el);
  if (na) na.onclick = async () => { const v = await modal('New article', form(null), { submitText: 'Save', wide: true, onSubmit: (x) => post('/knowledge', x) }); if (v) { toast('Article saved'); await run(); } };
  on(el, 'click', '[data-ed]', async (b) => { const a = items.find((x) => x.id === b.dataset.ed); const v = await modal('Edit article', form(a), { submitText: 'Save', wide: true, onSubmit: (x) => patch(`/knowledge/${a.id}`, x) }); if (v) { toast('Article saved'); await run(); } });
  await run();
}

export const routes = [
  { name: 'notifications', title: 'Notifications', perm: 'notification.read_own', render: notifications },
  { name: 'help', title: 'Answers', perm: () => isStudent() && S.can('knowledge.read'), nav: true, render: help },
  { name: 'kb', title: 'Knowledge', perm: () => !isStudent() && S.can('knowledge.read'), nav: true, render: help },
];
