/** Map ASTRA backend responses onto the data shapes the existing UI components already render.
 * Pure functions only; no values are invented here - anything unknown is shown as unknown ("—"). */
import * as F from './fixtures';

const pad = (n) => String(n).padStart(2, '0');
export const utcTime = (iso) => { if (!iso) return '—'; const d = new Date(iso); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`; };
export const utcHM = (ms) => { const d = new Date(ms); return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; };
export const utcDateTime = (ms) => { const d = new Date(ms); return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${utcHM(ms)}`; };
export const shortAddr = (a) => (a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '—');
export const shortHash = (h) => (h ? `${h.slice(0, 6)}…${h.slice(-4)}` : '—');
export const chainLabel = (id) => ({ 1: 'ETH MAINNET' }[id] || `CHAIN ${id}`);
export const chainName = (id) => ({ 1: 'Ethereum Mainnet' }[id] || `Chain ${id}`);
const ms = (iso) => (iso ? new Date(iso).getTime() : null);

export function fmtAmount(normalized, raw, symbol) {
  if (normalized == null) return raw != null ? `${raw} raw units` : '—';
  const n = Number(normalized);
  const txt = Number.isFinite(n) && Math.abs(n) < 1e15 ? n.toLocaleString('en-US', { maximumFractionDigits: 4 }) : normalized;
  return `${txt}${symbol ? ` ${symbol}` : ''}`;
}

export function humanDuration(sec) {
  if (sec == null || !Number.isFinite(sec)) return '—';
  const s = Math.max(0, Math.round(sec));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${pad(s % 60)}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${pad(Math.floor((s % 3600) / 60))}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

export function priorityOf(tier) {
  if (tier === 'high_priority') return { label: 'High', cls: 'high' };
  if (tier === 'medium_priority') return { label: 'Medium', cls: 'review' };
  return { label: 'Low', cls: 'review' };
}

const H_TITLES = { H1: 'Incident-related fund movement', H2: 'Routine service / market behaviour', H3: 'Insufficient evidence' };
const H_DESC = {
  H1: 'Observed paths, timing and public-source signals are consistent with a possible incident-related flow.',
  H2: 'Exchange, service or other routine behaviour could explain the observed pattern.',
  H3: 'The available evidence does not distinguish the alternatives; more verification is needed.',
};

function signal(L, path) {
  const [g, k] = path.split('.');
  const v = L.signals?.[g]?.[k];
  return v == null ? null : v;
}

export function toLead(L, incidentStart) {
  const p = priorityOf(L.priority_tier);
  const arr = ms(L.features?.earliest_arrival);
  const age = arr != null && incidentStart != null ? `+${humanDuration((arr - incidentStart) / 1000)} after origin` : 'no observed path';
  const statusLine = L.status === 'disputed' ? 'Public sources disagree about this address.'
    : L.status === 'unresolved' ? 'The evidence is insufficient to distinguish the alternatives.'
      : `Candidate lead from observed transfers${L.osint?.supporting_finding_ids?.length ? ' and public-source indicators' : ''}.`;
  return {
    id: L.lead_id, rank: L.rank, title: L.title, address: shortAddr(L.address), fullAddress: L.address, nodeId: L.node_id,
    score: Math.round(L.priority_score), scoreExact: L.priority_score, priority: p.label, priorityClass: p.cls, age,
    reasons: L.reasons.length ? L.reasons : ['No positive contributing signals'],
    reasonWeights: L.top_contributors.map((c) => (Math.abs(c.points) >= 10 ? 'HIGH' : Math.abs(c.points) >= 4 ? 'MED' : 'LOW')),
    detail: `${statusLine} Leading review hypothesis: ${L.hypotheses.leading} (${H_TITLES[L.hypotheses.leading]}). ${L.interpretation}`,
    nextCheck: L.next_check, status: L.status, leading: L.hypotheses.leading,
    signals: { temporal: signal(L, 'temporal.rapid_forwarding'), graph: signal(L, 'graph.incident_path_proximity'), source: signal(L, 'osint.indicator_match') },
    raw: L,
  };
}

function bestTraced(L) {
  const t = Object.values(L?.features?.traced_by_asset || {}).filter((v) => Number(v.traced_in_raw) > 0);
  if (!t.length) return null;
  t.sort((a, b) => (b.share_of_initial || 0) - (a.share_of_initial || 0));
  return t[0];
}

/** Bounded graph view: origin + top leads + the nodes on their observed paths, laid out by hop distance. */
export function buildGraphView(analysis, graph, { maxLeads = 8 } = {}) {
  const start = analysis.meta.start_node;
  const leadsAll = analysis.leads;
  const shown = leadsAll.slice(0, maxLeads);
  const byNode = Object.fromEntries(leadsAll.map((L) => [L.node_id, L]));
  const hop = {};
  const order = [];
  const add = (n, h) => { if (!n) return; if (!(n in hop)) order.push(n); hop[n] = Math.min(hop[n] ?? 99, h); };
  if (analysis.meta.start_in_graph) add(start, 0);
  shown.forEach((L) => { (L.path?.node_ids || []).forEach((n, i) => add(n, i)); add(L.node_id, L.features.hops ?? 99); });
  const finite = Object.values(hop).filter((h) => h < 99);
  const maxHop = finite.length ? Math.max(...finite) : 0;
  Object.keys(hop).forEach((n) => { if (hop[n] >= 99) hop[n] = maxHop + 1; });
  const maxCol = Math.max(1, ...Object.values(hop));
  const cols = {};
  order.forEach((n) => { (cols[hop[n]] ||= []).push(n); });
  const pos = {};
  Object.entries(cols).forEach(([c, list]) => {
    list.sort((a, b) => (byNode[a]?.rank ?? 0) - (byNode[b]?.rank ?? 0));
    // spread a column over the full canvas height; stagger crowded columns so node cards never overlap
    const crowded = list.length >= 3;
    const x0 = 95 + (Number(c) * 555) / maxCol;
    list.forEach((n, i) => {
      const y = list.length === 1 ? 175 : 40 + (i * 300) / (list.length - 1);
      const x = crowded ? x0 + (i % 2 ? 34 : -34) : x0;
      pos[n] = { x: Math.round(Math.min(670, Math.max(70, x))), y: Math.round(y) };
    });
  });
  const flowInit = analysis.flow?.[0];
  const nodes = order.map((n) => {
    const L = byNode[n];
    const addr = n.split(':')[1];
    if (n === start) {
      const sym = (graph?.edges || []).find((e) => e.source === n)?.asset_symbol;
      const dec = (graph?.edges || []).find((e) => e.source === n)?.token_decimals;
      return { id: n, nodeId: n, address: addr, ...pos[n], label: 'Incident origin', sub: 'Origin · observed', tone: 'red',
        value: flowInit ? fmtAmount(dec != null ? normalize(flowInit.initial_raw, dec) : null, flowInit.initial_raw, sym) : '—', leadId: null };
    }
    const boundary = (analysis.boundaries || []).some((b) => b.type === 'privacy_boundary' && (b.node_ids || []).includes(n));
    const tone = L?.rank === 1 ? 'cyan' : (L?.status === 'disputed' || boundary) ? 'amber' : (L && !L.features.time_respecting_reachable) ? 'purple' : 'blue';
    const sub = !L ? 'Path node' : boundary ? 'Unresolved boundary' : L.status === 'disputed' ? `Disputed · ${Math.round(L.priority_score)}/100` : `Lead #${L.rank} · ${Math.round(L.priority_score)}/100`;
    const t = bestTraced(L);
    return { id: n, nodeId: n, address: addr, ...pos[n], label: shortAddr(addr), sub, tone, value: t ? fmtAmount(t.traced_in, t.traced_in_raw, t.symbol) : '—', leadId: L?.lead_id || null };
  });
  const idx = Object.fromEntries(nodes.map((nd, i) => [nd.id, i]));
  const agg = {};
  (graph?.edges || []).forEach((e) => {
    if (!(e.source in idx) || !(e.target in idx) || e.source === e.target) return;
    const k = `${e.source}|${e.target}`;
    (agg[k] ||= []).push(e);
  });
  const edges = Object.entries(agg).map(([k, list]) => {
    const [s, t] = k.split('|');
    const label = list.length === 1 ? fmtAmount(list[0].normalized_value, list[0].raw_value, list[0].asset_symbol) : `${list.length} transfers`;
    return [idx[s], idx[t], label, nodes[idx[t]].tone === 'amber'];
  });
  const blocks = (graph?.edges || []).map((e) => e.block_number).filter((b) => b != null);
  return { nodes, edges, totalNodes: graph?.nodes?.length ?? 0, totalEdges: graph?.edges?.length ?? 0,
    blockWindow: blocks.length ? `${Math.min(...blocks).toLocaleString('en-US')}–${Math.max(...blocks).toLocaleString('en-US')}` : '—',
    shownLeads: shown.length, totalLeads: leadsAll.length };
}

function normalize(raw, dec) {
  const s = String(raw).padStart(dec + 1, '0');
  const whole = s.slice(0, s.length - dec) || '0';
  const frac = s.slice(s.length - dec).replace(/0+$/, '');
  return frac ? `${whole}.${frac}` : whole;
}

export function txRows(graph, analysis) {
  const start = analysis?.meta?.start_node;
  const label = (n) => (n === start ? 'Incident origin' : shortAddr(n.split(':')[1]));
  return (graph?.edges || []).map((e) => ({
    id: `TX-${e.transaction_hash.slice(2, 7).toUpperCase()}`, eventId: e.edge_id, from: label(e.source), to: label(e.target),
    amount: fmtAmount(e.normalized_value, e.raw_value, e.asset_symbol), usd: e.block_number != null ? `block ${e.block_number.toLocaleString('en-US')}` : 'block —',
    time: utcTime(e.timestamp), ts: ms(e.timestamp), status: 'Observed', hash: shortHash(e.transaction_hash), type: e.event_type,
    source: e.source, target: e.target,
  }));
}

export function activitySeries(rows, rangeLabel) {
  if (!rows.length) return { data: [], peak: null, total: 0 };
  const maxT = Math.max(...rows.map((r) => r.ts));
  const minAll = Math.min(...rows.map((r) => r.ts));
  const span = { 'Last 30 min': 30, 'Last 6 hours': 360, 'Last 24 hours': 1440 }[rangeLabel] || 30;
  const minT = Math.max(minAll, maxT - span * 60000);
  const inRange = rows.filter((r) => r.ts >= minT);
  const buckets = 12;
  const width = Math.max(60000, Math.ceil((maxT - minT + 1) / buckets / 60000) * 60000);
  const data = Array.from({ length: buckets }, (_, i) => ({ time: utcHM(minT + i * width), value: 0, t: minT + i * width }));
  inRange.forEach((r) => { const i = Math.min(buckets - 1, Math.floor((r.ts - minT) / width)); data[i].value += 1; });
  const peak = data.reduce((a, b) => (b.value > a.value ? b : a), data[0]);
  return { data, peak: peak?.value ? peak.time : null, total: inRange.length };
}

export function evidenceForLead(analysis, lead) {
  if (!lead?.raw) return { supporting: [], contradicting: [], context: [], claim: null };
  const L = lead.raw;
  const ev = Object.fromEntries(analysis.evidence.map((e) => [e.evidence_id, e]));
  const caseSup = analysis.evidence.filter((e) => (e.provenance?.case_level || e.kind === 'origin_pattern') && e.hypothesis_relations.H1.direction === 'supports').map((e) => e.evidence_id);
  const caseCon = analysis.evidence.filter((e) => (e.provenance?.case_level || e.kind === 'origin_pattern') && e.hypothesis_relations.H1.direction === 'contradicts').map((e) => e.evidence_id);
  const row = (id) => {
    const e = ev[id];
    if (!e) return null;
    const url = e.provenance?.source_url || null;
    const host = url ? (() => { try { return new URL(url).host; } catch { return ''; } })() : '';
    return { title: e.title, sub: `${e.source}${host ? ` · ${host}` : ''}${e.provenance?.case_level ? ' · case origin' : ''}`,
      small: e.interpretation.length > 170 ? `${e.interpretation.slice(0, 167)}…` : e.interpretation,
      status: e.status === 'Context' ? 'Context' : e.status, id: e.evidence_id, url, synthetic: e.provenance?.data_mode === 'synthetic' || analysis.meta.synthetic, raw: e };
  };
  const claim = analysis.claims.find((c) => c.claim_id === `CLM-${L.lead_id}-OSINT`) || analysis.claims.find((c) => c.claim_id === `CLM-${L.lead_id}-PRIORITY`);
  return {
    supporting: [...new Set([...L.support_evidence_ids, ...caseSup])].map(row).filter(Boolean),
    contradicting: [...new Set([...L.contradiction_evidence_ids, ...caseCon])].map(row).filter(Boolean),
    context: L.context_evidence_ids.map(row).filter(Boolean),
    claim,
  };
}

export function hypothesisCards(h) {
  if (!h) return [];
  const tone = { H1: 'cyan', H2: 'amber', H3: 'violet' };
  return h.hypotheses.map((x) => ({
    id: x.id, title: H_TITLES[x.id], score: Math.round(x.score * 100), desc: H_DESC[x.id], tone: tone[x.id], leading: h.leading === x.id,
    support: x.id === 'H3' ? (h.missing_information?.length ? `${h.missing_information.length} evidence gap(s)` : 'No major gaps listed')
      : `${x.supporting_evidence_ids.length} supporting record${x.supporting_evidence_ids.length === 1 ? '' : 's'}`,
    against: x.id === 'H3' ? `decisiveness ${x.decisiveness}` : `${x.contradicting_evidence_ids.length} contradicting record${x.contradicting_evidence_ids.length === 1 ? '' : 's'}`,
  }));
}

const TYPE_LABEL = { threat_report: 'Threat report', repository: 'Repository', label_registry: 'Label registry', analyst_note: 'Analyst note', forum: 'Forum', threat_feed: 'Threat feed', other: 'Other' };
export const sourceTypeLabel = (t) => TYPE_LABEL[t] || t;

export function sourceRows(analysis) {
  const ind = {};
  (analysis.osint.indicators || []).forEach((i) => { ind[i.doc_id] = (ind[i.doc_id] || 0) + 1; });
  const matched = {};
  (analysis.osint.findings || []).forEach((f) => { if (f.match_type !== 'no_match') matched[f.doc_id] = (matched[f.doc_id] || 0) + 1; });
  const docs = (analysis.osint.documents || []).map((d) => ({
    name: d.title || d.source_name, type: sourceTypeLabel(d.source_type), status: 'Collected', url: d.url,
    detail: `${d.source_name} · ${ind[d.doc_id] || 0} indicator(s), ${matched[d.doc_id] || 0} matched · reliability rubric ${d.reliability} · published ${d.published_at ? utcDateTime(ms(d.published_at)) + ' UTC' : 'unknown'}`,
    retrieved: d.retrieved_at ? `${utcDateTime(ms(d.retrieved_at))} UTC` : '—', doc: d,
    info: `${d.doc_id} · ${d.extraction_method} · ${d.content_hash?.slice(0, 19)}… · ${d.data_mode}`,
  }));
  const failed = (analysis.osint.documents_unusable || []).map((u) => {
    const s = (analysis.osint.sources || []).find((x) => x.error && x.status === 'failed');
    return { name: s?.name || u.doc_id, type: sourceTypeLabel(s?.type), status: 'Failed', url: s?.url || '', detail: `Collection failed: ${u.error || u.status}`, retrieved: s?.last_retrieved_at ? `${utcDateTime(ms(s.last_retrieved_at))} UTC` : '—', info: u.doc_id };
  });
  return [...docs, ...failed];
}

export function sourceStats(analysis) {
  const s = analysis.osint.sources || [];
  const last = Math.max(0, ...s.map((x) => ms(x.last_retrieved_at) || 0));
  return { registered: s.length, healthy: s.filter((x) => x.status === 'ok').length,
    problems: s.filter((x) => x.status !== 'ok').length, last: last ? `${utcHM(last)} UTC` : '—' };
}

export function healthView(health, analysis, error) {
  if (error) return { text: 'Backend unreachable', dot: 'red-dot', items: [], notifications: [{ title: 'Backend unreachable', sub: error.message, tone: 'amber-dot' }] };
  const prov = health?.providers || {};
  const eth = prov.etherscan || {};
  const gh = prov.github_issues_search || {};
  const fail = (analysis?.osint?.sources || []).filter((s) => s.status !== 'ok');
  const items = [
    ['Chain data provider', eth.configured ? (eth.last_error_code ? `Error ${eth.last_error_code}` : 'Configured') : 'Not configured (offline demo)', eth.configured && !eth.last_error_code ? 'ok' : 'warn'],
    ['Public source registry', fail.length ? `${fail.length} source issue(s)` : 'Healthy', fail.length ? 'warn' : 'ok'],
    ['GitHub API', gh.enabled ? (gh.last_error_code ? gh.last_error_code : 'Enabled') : 'Disabled', gh.enabled && !gh.last_error_code ? 'ok' : 'warn'],
  ];
  const notifications = [];
  fail.forEach((s) => notifications.push({ title: `Source issue: ${s.name}`, sub: `${s.error || s.status}`, tone: 'amber-dot' }));
  if (health?.degraded_mode) notifications.push({ title: 'Live provider not configured', sub: health.degraded_reason || 'Degraded mode', tone: 'amber-dot' });
  if (analysis) notifications.push({ title: 'Analysis complete', sub: `${analysis.meta.investigation_id} · snapshot ${analysis.meta.snapshot_id}`, tone: 'green-dot' });
  return { text: health?.status !== 'ok' ? 'Backend degraded' : health?.degraded_mode ? 'Offline demo mode' : 'Systems nominal',
    dot: health?.status !== 'ok' ? 'red-dot' : health?.degraded_mode ? 'amber-dot' : 'green-dot', items, notifications, version: health?.analysis_version };
}

/** UI model for the original fixtures (explicit fixture mode or backend unreachable). */
export function fixtureModel(reason) {
  return {
    source: 'fixtures', reason, caseId: 'CASE-042', caseTitle: 'Fund fragmentation', chain: 'ETH MAINNET', chainName: 'Ethereum Mainnet (fixture)',
    dataLabel: 'UI FIXTURE', dataBadge: 'FIXTURE', synthetic: true, version: 'v0.9.4-demo',
    transactions: F.transactions, leads: F.leads, flowData: F.flowData, graphView: { nodes: F.nodes, edges: F.edges.map((e, i) => [...e, i === 4]), totalNodes: 6, totalEdges: 6, blockWindow: '19,482,100–19,483,020', shownLeads: 4, totalLeads: 4 },
    evidence: F.evidence, analysis: null, graph: null,
  };
}
