// API client. No tokens are ever visible to JavaScript: sessions are httpOnly cookies; only the CSRF token is read.
export class ApiError extends Error {
  constructor(status, code, message, details, requestId) {
    super(message); this.status = status; this.code = code; this.details = details; this.requestId = requestId;
  }
}
const csrf = () => (document.cookie.match(/(?:^|; )pulse_csrf=([^;]*)/) || [])[1] || '';
let refreshing = null;
let onExpired = () => { location.href = 'login.html?expired=1'; };
export const setExpiredHandler = (f) => { onExpired = f; };

async function refresh() {
  if (!refreshing) {
    refreshing = fetch('/api/v1/auth/refresh', { method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf() } })
      .then((r) => r.ok).catch(() => false).finally(() => { setTimeout(() => { refreshing = null; }, 0); });
  }
  return refreshing;
}

export async function api(method, path, body, opts = {}) {
  const headers = { Accept: 'application/json', 'X-CSRF-Token': csrf() };
  let payload;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  let res;
  try {
    res = await fetch('/api/v1' + path, { method, headers, body: payload, credentials: 'same-origin' });
  } catch (e) {
    throw new ApiError(0, 'network', 'Cannot reach the server. Check your connection and try again.');
  }
  if (res.status === 401 && !opts.retried && !path.startsWith('/auth/login')) {
    if (!path.startsWith('/auth/refresh') && (await refresh())) return api(method, path, body, { ...opts, retried: true });
    if (!opts.quiet401) onExpired();
  }
  const ct = res.headers.get('content-type') || '';
  if (opts.text || (res.ok && !ct.includes('json'))) {
    if (!res.ok) throw new ApiError(res.status, 'http_error', 'Request failed');
    return res.text();
  }
  const data = ct.includes('json') ? await res.json().catch(() => null) : null;
  if (!res.ok) {
    const e = (data && data.error) || {};
    throw new ApiError(res.status, e.code || 'http_error', e.message || 'Something went wrong.', e.details, e.request_id);
  }
  return data;
}
export const get = (p, o) => api('GET', p, undefined, o);
export const post = (p, b = {}, o) => api('POST', p, b, o);
export const patch = (p, b = {}, o) => api('PATCH', p, b, o);
export const put = (p, b = {}, o) => api('PUT', p, b, o);
export const del = (p, o) => api('DELETE', p, undefined, o);
export const qs = (o) => { const p = new URLSearchParams(); Object.entries(o).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '' && v !== false) p.set(k, v === true ? 'true' : v); }); const s = p.toString(); return s ? '?' + s : ''; };
