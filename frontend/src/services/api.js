/** ASTRA frontend API client.
 *
 * Data source (VITE_ASTRA_DATA_SOURCE):
 *   'api'      (default) - the integrated ASTRA backend. VITE_ASTRA_API_BASE_URL may point at it; when
 *                           empty, requests are same-origin and the Vite dev/preview proxy forwards
 *                           /api and /health to http://127.0.0.1:8000 (see vite.config.js).
 *   'fixtures'           - the original deterministic UI fixtures, labelled "UI FIXTURE".
 * When the backend is selected but unreachable the UI says so explicitly; it never silently shows
 * fixture data as backend data. Provider secrets stay on the server (never in VITE_* variables).
 */
const API_BASE = (import.meta.env?.VITE_ASTRA_API_BASE_URL || '').replace(/\/$/, '');
const DATA_SOURCE = (import.meta.env?.VITE_ASTRA_DATA_SOURCE || 'api').toLowerCase() === 'fixtures' ? 'fixtures' : 'api';
const TIMEOUT_MS = 30000;

export class AstraApiError extends Error {
  constructor(status, body, fallback) {
    const e = body?.error || {};
    super(e.message || fallback || `ASTRA API ${status}`);
    this.status = status;
    this.code = e.code || (status === 0 ? 'BACKEND_UNREACHABLE' : 'HTTP_ERROR');
    this.retryable = Boolean(e.retryable);
    this.degraded = Boolean(e.degraded_mode);
    this.details = e.details;
  }
}

async function request(path, options = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options, signal: options.signal || ctrl.signal,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    });
  } catch (error) {
    throw new AstraApiError(0, null, error?.name === 'AbortError' ? 'Backend request timed out.' : 'ASTRA backend is unreachable.');
  } finally { clearTimeout(timer); }
  const text = await response.text().catch(() => '');
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = null; }
  if (!response.ok) {
    // A 5xx without ASTRA's error envelope comes from a proxy/gateway, i.e. the backend itself is down.
    if (response.status >= 500 && !body?.error) {
      const e = new AstraApiError(response.status, null, `ASTRA backend is unreachable (proxy returned HTTP ${response.status}).`);
      e.code = 'BACKEND_UNREACHABLE';
      throw e;
    }
    throw new AstraApiError(response.status, body, `ASTRA API ${response.status}: ${response.statusText}`);
  }
  return body;
}

const q = (params) => {
  const s = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') s.set(k, String(v)); });
  const str = s.toString();
  return str ? `?${str}` : '';
};
const enc = encodeURIComponent;

export function createApiAdapter() {
  return {
    mode: DATA_SOURCE,
    getHealth: () => request('/health'),
    getScenarios: () => request('/api/scenarios'),
    listInvestigations: () => request('/api/investigations'),
    getInvestigation: (id) => request(`/api/investigations/${enc(id)}`),
    getAnalysis: (id, params) => request(`/api/investigations/${enc(id)}/analysis${q(params)}`),
    getGraph: (id, params) => request(`/api/investigations/${enc(id)}/graph${q(params)}`),
    getLeads: (id, params) => request(`/api/investigations/${enc(id)}/leads${q(params)}`),
    getEvidence: (id, params) => request(`/api/investigations/${enc(id)}/evidence${q(params)}`),
    getReplay: (id, cutoff, policy = 'observation') => request(`/api/investigations/${enc(id)}/replay${q({ cutoff, policy })}`),
    postSensitivity: (id, body) => request(`/api/investigations/${enc(id)}/sensitivity`, { method: 'POST', body: JSON.stringify(body || {}) }),
    getReport: (id) => request(`/api/investigations/${enc(id)}/report`),
    reportHtmlUrl: (id) => `${API_BASE}/api/investigations/${enc(id)}/report?format=html`,
    getEvaluation: (refresh = false) => request(`/api/evaluation${q({ refresh: refresh ? 'true' : '' })}`),
    createInvestigation: (payload) => request('/api/investigations', { method: 'POST', body: JSON.stringify(payload) }),
    createSimulation: (payload) => request('/api/simulations', { method: 'POST', body: JSON.stringify(payload) }),
    collectOsint: (id, terms) => request(`/api/investigations/${enc(id)}/osint/collect`, { method: 'POST', body: JSON.stringify(terms || null) }),
  };
}

export function getApiBaseUrl() { return API_BASE || null; }
export function getDataSource() { return DATA_SOURCE; }
