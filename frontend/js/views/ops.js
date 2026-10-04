import { get, qs } from '../api.js';
import { S } from '../state.js';
import { $, h, mount, on, toast, empty, ago, pill, label, msgOf, raw } from '../ui.js';

const bar = (v, max) => h`<i style="width:${max ? Math.max(2, Math.round((v / max) * 100)) : 2}%"></i>`;
const num = (v, suf = '') => (v == null ? '—' : v + suf);

function kpis(s) {
  const r = s.requests;
  return h`<div class="kp"><div><b>${r.created}</b><span class="mu">requests (30 d)</span></div><div><b>${r.resolved}</b><span class="mu">resolved</span></div><div><b>${r.open_total}</b><span class="mu">open now</span></div>
    <div><b class="${r.breached_open ? 's3' : ''}">${r.breached_open}</b><span class="mu">past SLA</span></div><div><b>${num(s.sla.resolution_compliance_pct, '%')}</b><span class="mu">on time (n=${s.sla.resolution_sample})</span></div>
    <div><b>${num(s.timings.avg_first_response_minutes)}</b><span class="mu">avg first reply, min</span></div><div><b>${num(s.feedback.helpful_pct, '%')}</b><span class="mu">said it helped (n=${s.feedback.responses})</span></div></div>`;
}

async function overview(el, _, alive) {
  const [s, br] = await Promise.all([get('/reports/summary'), get('/reports/briefing')]);
  if (!alive()) return;
  const max = Math.max(1, ...s.by_department.map((d) => d.created));
  const care = s.care, itsm = s.itsm;
  mount(el, h`<section class="c hero"><h1>Campus overview</h1><p class="mu">Aggregates from live data. No individual is named here and request text is never shown.</p>${kpis(s)}</section>
    <div class="g2"><section class="c"><h3>By department (30 days)</h3>${s.by_department.length ? s.by_department.map((d) => h`<div class="bar"><span style="width:150px">${d.department}</span>${bar(d.created, max)}<span>${d.created} · ${d.open} open${d.breached ? h` · <b class="s3">${d.breached} late</b>` : ''}</span></div>`) : empty('No requests in this period')}<p class="mu"><a href="${'/api/v1/reports/summary.csv'}">Download CSV</a></p></section>
    <section class="c"><h3>Incidents · problems · changes</h3><dl class="kv"><dt>Open incidents</dt><dd>${itsm.incidents_open}</dd><dt>Open problems</dt><dd>${itsm.problems_open}</dd><dt>Changes in flight</dt><dd>${itsm.changes_in_flight}</dd><dt>Changes done</dt><dd>${itsm.changes_successful} successful · ${itsm.changes_failed} failed</dd></dl>
      ${br.clusters.length ? h`<div class="note wn"><b>Piling up:</b> ${br.clusters.map((c) => `${c.location.name} (${c.request_count})`).join(', ')} — <a href="#/incidents">review</a></div>` : ''}</section></div>
    <div class="g2"><section class="c"><h3>How well is the case rule working?</h3><p class="mu">Measured from human decisions, not estimated.</p>
      <dl class="kv"><dt>Awaiting review</dt><dd>${care.rule_suggestions.suggested_pending}</dd><dt>Confirmed by a person</dt><dd>${care.rule_suggestions.confirmed}</dd><dt>Dismissed</dt><dd>${care.rule_suggestions.dismissed}</dd><dt>Confirmation rate</dt><dd>${care.rule_suggestions.confirmation_rate_pct == null ? 'not enough decisions yet' : care.rule_suggestions.confirmation_rate_pct + '% of ' + care.rule_suggestions.decided}</dd></dl>
      <h3 style="margin-top:12px">Support actions: did they help?</h3>${care.interventions.masked ? h`<p class="mu">Hidden: ${care.interventions.reason}, to protect individuals.</p>` : h`<dl class="kv">${Object.entries(care.interventions.outcomes).map(([k, v]) => h`<dt>${label(k)}</dt><dd>${v}</dd>`)}</dl>`}</section>
    <section class="c"><h3>Weekly briefing</h3><pre class="pre" style="font:inherit;margin:0">${br.text}</pre></section></div>`);
}

async function heatmap(el, _, alive) {
  const d = await get('/reports/heatmap');
  if (!alive()) return;
  const locs = [...new Set(d.cells.map((c) => c.location))], cats = [...new Set(d.cells.map((c) => c.category))];
  const max = Math.max(1, ...d.cells.map((c) => c.count || 0));
  const cell = (loc, cat) => {
    const cs = d.cells.filter((c) => c.location === loc && c.category === cat);
    if (!cs.length) return h`<td></td>`;
    const tot = cs.reduce((a, c) => a + (c.count || 0), 0), masked = cs.some((c) => c.masked);
    const a = Math.min(0.9, 0.12 + (tot / max) * 0.78);
    return h`<td title="${cs.map((c) => `${c.time_of_day}: ${c.masked ? 'hidden' : c.count}`).join(' · ')}" style="background:rgba(214,59,91,${a.toFixed(2)})">${masked && !tot ? '·' : tot}${masked && tot ? '+' : ''}</td>`;
  };
  mount(el, h`<section class="c"><h2>Where and what</h2><p class="mu">Requests by location and category, last 30 days (${d.timezone} time). ${d.note} Hover a cell for time of day.</p>
    ${locs.length ? h`<div class="sw"><table class="mx"><thead><tr><th></th>${cats.map((c) => h`<th>${label(c)}</th>`)}</tr></thead><tbody>${locs.map((l) => h`<tr><th>${l}</th>${cats.map((c) => cell(l, c))}</tr>`)}</tbody></table></div><p class="mu">${d.masked_cells} cell(s) hidden (small counts in sensitive categories).</p>` : empty('No located requests yet')}</section>`);
}

async function reports(el, _, alive) {
  const s = await get('/reports/summary');
  if (!alive()) return;
  mount(el, h`<section class="c hero"><h1>${s.scope === 'department' ? 'Your department' : 'Campus'} — last 30 days</h1>${kpis(s)}</section>
    <section class="c"><h3>By department</h3>${s.by_department.length ? h`<div class="sw"><table class="tbl"><thead><tr><th>Department</th><th>Created</th><th>Resolved</th><th>Open</th><th>Past SLA</th></tr></thead><tbody>${s.by_department.map((d) => h`<tr><td>${d.department}</td><td>${d.created}</td><td>${d.resolved}</td><td>${d.open}</td><td>${d.breached}</td></tr>`)}</tbody></table></div>` : empty('No data in this period')}
    <p class="mu">Priority mix: ${Object.entries(s.requests.by_priority).map(([k, v]) => `${k} ${v}`).join(' · ') || '—'} · <a href="/api/v1/reports/summary.csv">Download CSV</a></p></section>`);
}

export const routes = [
  { name: 'overview', title: 'Overview', perm: 'aggregate.read', nav: true, render: overview },
  { name: 'heatmap', title: 'Heatmap', perm: 'aggregate.read', nav: true, render: heatmap },
  { name: 'reports', title: 'Reports', perm: () => S.can('report.read') && S.me.role !== 'command' && S.me.role !== 'admin', nav: true, render: reports },
];
