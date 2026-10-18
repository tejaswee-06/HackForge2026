import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity, Accessibility, ArrowDownRight, ArrowRight, ArrowUpRight, Bell, BookOpen,
  BrainCircuit, Check, ChevronDown, ChevronRight, CircleHelp, Command,
  Copy, Crosshair, Database, Download, ExternalLink, Eye, FileSearch,
  Filter, Fingerprint, Gauge, GitBranch, Globe2, Hexagon, Layers3, LockKeyhole,
  Menu, Moon, MoreHorizontal, Network, Play, Radio, RefreshCw, Search,
  Minus, Plus, Volume2, Square, Contrast, Languages, Type,
  Settings2, ShieldAlert, ShieldCheck, SlidersHorizontal, Sparkles, Sun,
  Target, Timer, Wallet, X, Zap
} from 'lucide-react';
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
  BarChart, Bar
} from 'recharts';
import './styles.css';
import PixelBlast from './PixelBlast';
import PrismaticBurst from './PrismaticBurst';
import Dock from './Dock';
import { createApiAdapter } from './services/api';
import { downloadJson, downloadCsv, openReportPreview } from './services/export';
import {
  activitySeries, buildGraphView, chainLabel, chainName, evidenceForLead, fixtureModel, fmtAmount, healthView,
  humanDuration, hypothesisCards, shortAddr, sourceRows, sourceStats, toLead, txRows, utcDateTime, utcHM, utcTime
} from './services/adapters';

const astraApi = createApiAdapter();
const AstraCtx = createContext(null);
const useAstra = () => useContext(AstraCtx);
const safeStore = { get: (k) => { try { return localStorage.getItem(k); } catch { return null; } }, set: (k, v) => { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* storage unavailable */ } } };

const navGroups = [
  { label: 'WORKSPACE', items: [
    { id: 'overview', label: 'Overview', icon: Gauge },
    { id: 'graph', label: 'Investigation graph', icon: Network, badge: 'LIVE' },
    { id: 'leads', label: 'Lead queue', icon: Crosshair, count: '12' },
    { id: 'evidence', label: 'Evidence inspector', icon: Fingerprint },
  ]},
  { label: 'ANALYSIS', items: [
    { id: 'sources', label: 'OSINT sources', icon: Globe2 },
    { id: 'replay', label: 'Timeline & replay', icon: Timer },
    { id: 'hypotheses', label: 'Hypotheses', icon: BrainCircuit },
    { id: 'evaluation', label: 'Evaluation lab', icon: Activity },
  ]},
  { label: 'DELIVER', items: [
    { id: 'reports', label: 'Reports & casebook', icon: BookOpen },
    { id: 'settings', label: 'Source health', icon: Settings2 },
  ]}
];

/* ------------------------------------------------------------------ data model (backend <-> UI) */
function buildModel(bundle, graph, health, scenarios) {
  const a = bundle.analysis;
  const inc = a.meta.incident_start ? new Date(a.meta.incident_start).getTime() : null;
  const sc = (scenarios || []).find((s) => s.investigation_id === bundle.investigation_id);
  const scope = bundle.case?.scope || {};
  const title = sc ? sc.name : scope.type === 'simulation' ? `Simulation · ${scope.scenario_kind} · seed ${scope.seed}`
    : scope.type === 'wallet' ? `Wallet ${shortAddr(scope.address)}` : bundle.investigation_id;
  return {
    source: 'api', caseId: bundle.investigation_id, caseTitle: title, chain: chainLabel(bundle.case?.chain_id ?? 1),
    chainName: chainName(bundle.case?.chain_id ?? 1), dataLabel: bundle.data_label,
    dataBadge: bundle.synthetic ? 'SYNTH' : bundle.data_mode === 'cached' ? 'CACHED' : 'LIVE', synthetic: bundle.synthetic,
    version: health?.analysis_version || bundle.analysis_version, analysis: a, graph, case: bundle.case, health,
    transactions: txRows(graph, a), leads: a.leads.map((L) => toLead(L, inc)), incidentStart: inc,
    snapshot: bundle.snapshot_id, limitations: bundle.limitations,
  };
}

function useAstraModel() {
  const fixtureOnly = astraApi.mode === 'fixtures';
  const [state, setState] = useState(() => fixtureOnly ? { status: 'ready', model: fixtureModel('fixture-mode'), error: null, health: null }
    : { status: 'loading', model: null, error: null, health: null });
  const [scenarios, setScenarios] = useState([]);
  const seq = useRef(0);
  const load = useCallback(async (requestedId) => {
    if (fixtureOnly) return;
    const my = ++seq.current;
    setState((s) => ({ ...s, status: 'loading', error: null }));
    try {
      const health = await astraApi.getHealth();
      const sc = (await astraApi.getScenarios()).scenarios || [];
      setScenarios(sc);
      let id = requestedId || safeStore.get('astra-case') || sc[0]?.investigation_id;
      if (!id) throw Object.assign(new Error('The backend has no investigations or scenarios loaded.'), { code: 'NO_CASE' });
      let bundle;
      try { bundle = await astraApi.getAnalysis(id); }
      catch (e) {
        if (e.status !== 404 || !sc[0]) throw e;
        id = sc[0].investigation_id; // a remembered case disappeared (backend restart): open the default scenario
        bundle = await astraApi.getAnalysis(id);
      }
      const graph = await astraApi.getGraph(id, { max_edges: 5000 });
      if (my !== seq.current) return;
      safeStore.set('astra-case', id);
      setState({ status: 'ready', model: buildModel(bundle, graph, health, sc), error: null, health });
    } catch (error) {
      if (my !== seq.current) return;
      setState((s) => ({ status: 'error', model: s.model || fixtureModel('backend-unreachable'), error, health: s.health }));
    }
  }, [fixtureOnly]);
  useEffect(() => { load(); }, [load]);
  return { ...state, scenarios, load };
}

function App() {
  const [showLanding, setShowLanding] = useState(true);
  const [theme, setTheme] = useState(() => safeStore.get('astra-theme') || 'dark');
  const [page, setPage] = useState('overview');
  const [caseOpen, setCaseOpen] = useState(true);
  const [selectedNode, setSelectedNode] = useState(null);
  const [selectedLeadId, setSelectedLeadId] = useState(null);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('All signals');
  const [showNotifications, setShowNotifications] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  const [showDetail, setShowDetail] = useState(true);
  const [replay, setReplay] = useState(67);
  const [clueEnabled, setClueEnabled] = useState(true);
  const [sens, setSens] = useState({ status: 'idle', result: null, error: null, leadId: null, evidenceId: null });
  const [notice, setNotice] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [demoPulse, setDemoPulse] = useState(false);
  const [accessibilityOpen, setAccessibilityOpen] = useState(false);
  const [speechLanguage, setSpeechLanguage] = useState('en-US');
  const [fontScale, setFontScale] = useState(1);
  const [dyslexiaFont, setDyslexiaFont] = useState(false);
  const [contrastMode, setContrastMode] = useState(false);
  const [highContrast, setHighContrast] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const data = useAstraModel();
  const m = data.model;
  const hv = useMemo(() => (m?.source === 'api' || data.error ? healthView(data.health, m?.analysis, data.error) : null), [m, data.health, data.error]);

  const leadsList = m?.leads || [];
  const selectedLead = leadsList.find((l) => l.id === selectedLeadId) || leadsList[0] || null;
  useEffect(() => {
    // keep selections valid whenever a new investigation/analysis arrives
    if (!m) return;
    setSelectedLeadId((id) => (leadsList.some((l) => l.id === id) ? id : leadsList[0]?.id || null));
    const nodeIds = m.source === 'api' ? leadsList.map((l) => l.nodeId).concat(m.analysis?.meta?.start_node || []) : m.graphView.nodes.map((n) => n.id);
    setSelectedNode((n) => (nodeIds.includes(n) ? n : (m.source === 'api' ? leadsList[0]?.nodeId || m.analysis?.meta?.start_node : 'wallet7a')));
    setClueEnabled(true);
    setSens({ status: 'idle', result: null, error: null, leadId: null, evidenceId: null });
  }, [m]); // eslint-disable-line react-hooks/exhaustive-deps

  // Evidence-sensitivity toggle: removing the clue re-runs the backend engine (no client-side guessing).
  const clueFor = useCallback((lead) => {
    if (!lead?.raw || !m?.analysis) return null;
    const ev = m.analysis.evidence;
    const own = (id) => ev.find((e) => e.evidence_id === id);
    const temp = own(`EV-TEMP-${lead.fullAddress}`);
    if (temp && temp.hypothesis_relations.H1.direction === 'supports') return temp;
    const sup = lead.raw.support_evidence_ids.map(own).filter(Boolean);
    sup.sort((a, b) => b.reliability * b.hypothesis_relations.H1.strength - a.reliability * a.hypothesis_relations.H1.strength);
    return sup[0] || null;
  }, [m]);
  const toggleClue = useCallback(async (enabled) => {
    setClueEnabled(enabled);
    if (enabled || !m || m.source !== 'api') { setSens({ status: 'idle', result: null, error: null, leadId: null, evidenceId: null }); return; }
    const clue = clueFor(selectedLead);
    if (!clue) { setNotice('This lead has no supporting clue to remove.'); setClueEnabled(true); return; }
    setSens({ status: 'loading', result: null, error: null, leadId: selectedLead.id, evidenceId: clue.evidence_id });
    try {
      const result = await astraApi.postSensitivity(m.caseId, { remove_evidence_ids: [clue.evidence_id] });
      setSens({ status: 'ready', result, error: null, leadId: selectedLead.id, evidenceId: clue.evidence_id });
    } catch (error) {
      setSens({ status: 'error', result: null, error, leadId: selectedLead.id, evidenceId: clue.evidence_id });
      setNotice(`Sensitivity re-run failed: ${error.message}`);
    }
  }, [m, selectedLead, clueFor]);

  useEffect(() => {
    document.documentElement.style.setProperty('--astra-font-scale', String(fontScale));
    document.documentElement.classList.toggle('astra-dyslexia-font', dyslexiaFont);
    document.documentElement.classList.toggle('astra-contrast-mode', contrastMode);
    document.documentElement.classList.toggle('astra-high-contrast', highContrast);
  }, [fontScale, dyslexiaFont, contrastMode, highContrast]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    safeStore.set('astra-theme', theme);
  }, [theme]);

  useEffect(() => {
    const move = (event) => {
      document.documentElement.style.setProperty('--mouse-x', `${event.clientX}px`);
      document.documentElement.style.setProperty('--mouse-y', `${event.clientY}px`);
    };
    window.addEventListener('pointermove', move, { passive: true });
    return () => window.removeEventListener('pointermove', move);
  }, []);

  useEffect(() => {
    let timer;
    if (notice) timer = setTimeout(() => setNotice(''), 3000);
    return () => clearTimeout(timer);
  }, [notice]);

  const activeLabel = navGroups.flatMap(g => g.items).find(i => i.id === page)?.label || 'Overview';
  const leadCount = String(leadsList.length);

  const dockItems = [
    { icon: <Gauge size={20} strokeWidth={1.8} />, label: 'Overview', active: page === 'overview', onClick: () => setPage('overview') },
    { icon: <Network size={20} strokeWidth={1.8} />, label: 'Graph', active: page === 'graph', onClick: () => setPage('graph') },
    { icon: <Crosshair size={20} strokeWidth={1.8} />, label: 'Lead queue', badge: leadCount, active: page === 'leads', onClick: () => setPage('leads') },
    { icon: <Fingerprint size={20} strokeWidth={1.8} />, label: 'Evidence', active: page === 'evidence', onClick: () => setPage('evidence') },
    { icon: <Globe2 size={20} strokeWidth={1.8} />, label: 'OSINT', active: page === 'sources', onClick: () => setPage('sources') },
    { icon: <Timer size={20} strokeWidth={1.8} />, label: 'Timeline', active: page === 'replay', onClick: () => setPage('replay') },
    { icon: <BrainCircuit size={20} strokeWidth={1.8} />, label: 'Hypotheses', active: page === 'hypotheses', onClick: () => setPage('hypotheses') },
    { icon: <Activity size={20} strokeWidth={1.8} />, label: 'Evaluation lab', active: page === 'evaluation', onClick: () => setPage('evaluation') },
    { icon: <BookOpen size={20} strokeWidth={1.8} />, label: 'Casebook', active: page === 'reports', onClick: () => setPage('reports') },
    { icon: <Settings2 size={20} strokeWidth={1.8} />, label: 'Settings', active: page === 'settings', onClick: () => setPage('settings') },
  ];

  const startDemo = () => {
    setPage('graph'); setSelectedNode(m?.source === 'api' ? (m.analysis?.meta?.start_node || null) : 'exploit'); setDemoPulse(true);
    setNotice(`Investigation opened · ${m?.dataLabel || 'loading'}`);
    setTimeout(() => setDemoPulse(false), 1800);
  };
  const openLeadEvidence = (leadId) => { if (leadId) setSelectedLeadId(leadId); setPage('evidence'); };

  const exportCasebook = async () => {
    if (!m) return;
    if (m.source !== 'api') { downloadJson('astra-case-042', fixtureCasebook(selectedLead, selectedNode, clueEnabled, replay)); return; }
    try { const r = await astraApi.getReport(m.caseId); downloadJson(`astra-casebook-${m.caseId}`, r); setNotice('Casebook exported from the backend analysis.'); }
    catch (error) { setNotice(`Casebook export failed: ${error.message}`); }
  };
  const previewReport = () => {
    if (!m) return;
    if (m.source !== 'api') { const opened = openReportPreview(fixtureCasebook(selectedLead, selectedNode, clueEnabled, replay)); if (!opened) setNotice('Pop-up blocked. Allow pop-ups to preview the casebook.'); return; }
    const w = window.open(astraApi.reportHtmlUrl(m.caseId), '_blank', 'noopener');
    if (!w) setNotice('Pop-up blocked. Allow pop-ups to preview the casebook.');
  };

  if (showLanding) return <LandingPage onEnter={() => setShowLanding(false)} />;

  const ctx = { m, hv, data, astraApi, selectedLead, setSelectedLeadId, clueFor, sens, toggleClue };
  const footerMode = m?.dataLabel || (data.status === 'loading' ? 'LOADING' : 'UNAVAILABLE');

  return <AstraCtx.Provider value={ctx}><div className={`app-shell ${demoPulse ? 'pulse-demo' : ''}`}>
    <div className="ambient ambient-one" /><div className="ambient ambient-two" />
    <div className="pixel-field-wrap" aria-hidden="true">
      <PixelBlast
        variant="square"
        pixelSize={3}
        color="#0B4F7A"
        accentColor="#27A9F2"
        patternScale={2}
        patternDensity={1}
        softness={0.6}
        glow={0.55}
        twinkle={0.22}
        drift={0.16}
        speed={0.32}
        hoverEffect="reveal"
        hoverRadius={160}
        enableRipples
        rippleIntensityScale={0.8}
        edgeFade={0.32}
        transparent
      />
    </div>


    <main className="main-area">
      <header className="topbar">
        <div className="top-left"><div className="breadcrumbs"><span>ASTRA</span><ChevronRight size={14}/><strong>{activeLabel}</strong></div><span className="top-divider"/><div className="case-context"><span className="status-dot red-dot"/> {m ? m.caseId : 'NO CASE'} <span className="context-chain">/ {m ? `${m.chain} · ${m.dataBadge}` : '—'}</span></div></div>
        <div className="top-actions">
          <div className="system-status"><span className={`status-dot ${hv ? hv.dot : 'amber-dot'}`}/><span>{hv ? hv.text : 'UI fixture mode'}</span></div>
          <button className="icon-button" onClick={() => setShowNotifications(!showNotifications)} aria-label="Notifications"><Bell size={17}/><i className="notification-dot"/></button>
          <button className="theme-toggle" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-label="Toggle theme">{theme === 'dark' ? <Sun size={16}/> : <Moon size={16}/>}<span>{theme === 'dark' ? 'Light' : 'Dark'}</span></button>
          <button className="avatar top-avatar" onClick={() => setShowProfile(!showProfile)}>AI</button>
        </div>
        {showNotifications && <div className="floating-popover notification-pop"><div className="popover-title">Notifications <span className="small-tag">{(hv?.notifications || []).length} new</span></div>{(hv?.notifications || [{ title: 'UI fixture mode', sub: 'Backend data source not selected', tone: 'amber-dot' }]).map((n, i) => <div className="notification-item" key={i}><span className={`status-dot ${n.tone}`}/><div><strong>{n.title}</strong><small>{n.sub}</small></div></div>)}</div>}
        {showProfile && <div className="floating-popover profile-pop"><strong>Analyst workspace</strong><small>Local deployment · No account connected</small><button onClick={() => {setPage('settings');setShowProfile(false)}}><Settings2 size={14}/> Workspace settings</button></div>}
      </header>

      <div className="page-wrap">
        {data.status === 'loading' && <div className="hypothesis-note glass" role="status"><RefreshCw size={18}/><div><strong>Loading investigation…</strong><p>Fetching the analysis from the ASTRA backend.</p></div></div>}
        {data.error && <div className="hypothesis-note glass" role="alert"><ShieldAlert size={18}/><div><strong>{data.error.code === 'BACKEND_UNREACHABLE' ? 'ASTRA backend unreachable — showing UI FIXTURE data' : `Backend error (${data.error.code || data.error.status})`}</strong><p>{data.error.message} {m?.source === 'fixtures' ? 'The values below are the original illustrative UI fixtures, not analysis output. Start the backend and retry.' : ''}</p></div><Button variant="secondary" icon={RefreshCw} onClick={() => data.load(m?.source === 'api' ? m.caseId : undefined)}>Retry</Button></div>}
        {m && page === 'overview' && <Overview onStart={startDemo} onNavigate={setPage} onNotice={setNotice} onExport={exportCasebook} onRefresh={() => m.source === 'api' ? data.load(m.caseId) : setNotice('Fixture mode: nothing to refresh.')} />}
        {m && page === 'graph' && <GraphPage selectedNode={selectedNode} setSelectedNode={setSelectedNode} showDetail={showDetail} setShowDetail={setShowDetail} onNotice={setNotice} onOpenEvidence={openLeadEvidence} onExport={() => downloadJson(`astra-graph-${m.caseId}`, m.source === 'api' ? { caseId: m.caseId, dataLabel: m.dataLabel, graph: m.graph } : { caseId: 'CASE-042', dataMode: 'UI FIXTURE', nodes: m.graphView.nodes, edges: m.graphView.edges })} onCopy={value => { navigator.clipboard?.writeText(value)?.then(() => setNotice('Address copied.')).catch(() => setNotice('Clipboard unavailable; select and copy the address manually.')); }}/>}
        {m && page === 'leads' && <LeadsPage selectedLead={selectedLead} setSelectedLead={(l) => setSelectedLeadId(l.id)} onNavigate={setPage}/>}
        {m && page === 'evidence' && <EvidencePage clueEnabled={clueEnabled} setClueEnabled={toggleClue} onNotice={setNotice} onExport={() => downloadJson(`astra-evidence-${m.caseId}`, m.source === 'api' ? { caseId: m.caseId, dataLabel: m.dataLabel, snapshot: m.snapshot, claims: m.analysis.claims, evidence: m.analysis.evidence, findings: m.analysis.osint.findings } : { caseId: 'CASE-042', dataMode: 'UI FIXTURE', evidence: m.evidence })}/>}
        {m && page === 'sources' && <SourcesPage search={search} setSearch={setSearch} onNotice={setNotice}/>}
        {m && page === 'replay' && <ReplayPage replay={replay} setReplay={setReplay}/>}
        {m && page === 'hypotheses' && <HypothesesPage clueEnabled={clueEnabled} setClueEnabled={toggleClue}/>}
        {m && page === 'evaluation' && <EvaluationPage/>}
        {m && page === 'reports' && <ReportsPage onNotice={setNotice} onExport={exportCasebook} onPreview={previewReport}/>}
        {m && page === 'settings' && <SettingsPage theme={theme} setTheme={setTheme} onNotice={setNotice}/>}
      </div>
      <footer className="app-footer"><span><span className={`status-dot ${data.error ? 'amber-dot' : 'green-dot'}`}/> ASTRA CORE <span className="footer-sep">/</span> {m?.version || '—'}</span><span>DATA MODE: <b>{footerMode}</b><span className="footer-sep">·</span> {m?.synthetic === false ? 'Public data · verify before use' : 'Not live intelligence'}</span><span>BUILT FOR INVESTIGATORS <Sparkles size={12}/></span></footer>
    </main>
    <div className="astra-dock-host" aria-label="Quick navigation">
      <Dock
        items={dockItems}
        position="bottom"
        theme="dark"
        baseItemSize={46}
        magnification={64}
        distance={190}
        panelHeight={64}
        gap={8}
        roundness={0.55}
        bounce
        showLabels
        showIndicators
        showBadges
        accentColor="#39BDF5"
        badgeColor="#FF647C"
        className="astra-dock"
      />
    </div>

    <div className="astra-accessibility-wrap">
      {accessibilityOpen && <section className="astra-accessibility-panel" role="dialog" aria-label="Accessibility settings">
        <div className="astra-a11y-head"><div><span className="astra-a11y-kicker">ASTRA ACCESSIBILITY</span><h2>Reading & display</h2></div><button className="astra-a11y-close" onClick={() => setAccessibilityOpen(false)} aria-label="Close accessibility settings"><X size={17}/></button></div>
        <div className="astra-a11y-group"><label>Text size</label><div className="astra-a11y-row"><button onClick={() => setFontScale(v => Math.max(0.85, +(v - 0.1).toFixed(2)))} aria-label="Decrease text size"><Minus size={16}/><span>Decrease</span></button><strong>{Math.round(fontScale*100)}%</strong><button onClick={() => setFontScale(v => Math.min(1.5, +(v + 0.1).toFixed(2)))} aria-label="Increase text size"><Plus size={16}/><span>Increase</span></button></div></div>
        <div className="astra-a11y-group"><label>Reading support</label><button className={`astra-a11y-option ${dyslexiaFont?'selected':''}`} onClick={() => setDyslexiaFont(v => !v)}><Type size={17}/><span><strong>Dyslexia-friendly spacing</strong><small>Clear sans-serif, wider spacing and line height</small></span><i>{dyslexiaFont?'On':'Off'}</i></button><div className="astra-a11y-row speech-actions"><button onClick={() => speakText(document.querySelector('.page-wrap')?.innerText || '', speechLanguage, setSpeaking)}><Volume2 size={16}/><span>Read page aloud</span></button><button onClick={() => { const selected = window.getSelection()?.toString().trim(); if (selected) speakText(selected, speechLanguage, setSpeaking); else setNotice('Select some text on the page first, then choose Read selection.'); }}><Volume2 size={16}/><span>Read selection</span></button></div><div className="astra-a11y-row"><button onClick={() => { if ('speechSynthesis' in window) window.speechSynthesis.cancel(); setSpeaking(false); }}><Square size={14}/><span>Stop speech</span></button><label className="astra-language-select"><Languages size={15}/><select aria-label="Speech language" value={speechLanguage} onChange={e => setSpeechLanguage(e.target.value)}><option value="en-US">English</option><option value="hi-IN">Hindi</option><option value="fr-FR">French</option></select></label></div>{speaking && <small className="astra-speaking-state">Speech playback active · voice availability depends on your browser and operating system.</small>}</div>
        <div className="astra-a11y-group"><label>Contrast</label><button className={`astra-a11y-option ${contrastMode?'selected':''}`} onClick={() => setContrastMode(v => !v)}><Contrast size={17}/><span><strong>Contrast mode</strong><small>Reduce transparency and clarify surfaces</small></span><i>{contrastMode?'On':'Off'}</i></button><button className={`astra-a11y-option ${highContrast?'selected':''}`} onClick={() => setHighContrast(v => !v)}><Contrast size={17}/><span><strong>Increase contrast</strong><small>Stronger text, borders and focus rings</small></span><i>{highContrast?'On':'Off'}</i></button></div>
        <p className="astra-a11y-foot">Keyboard accessible · Speech uses your browser's built-in voices.</p>
      </section>}
      <button className={`astra-accessibility-button ${accessibilityOpen?'is-open':''}`} onClick={() => setAccessibilityOpen(v => !v)} aria-expanded={accessibilityOpen} aria-label="Open accessibility settings" title="Accessibility"><Accessibility size={22}/><span>Accessibility</span></button>
    </div>

    {notice && <div className="toast"><Check size={16}/>{notice}<button onClick={() => setNotice('')}><X size={14}/></button></div>}
    <div className="cursor-aura" aria-hidden="true"/>
  </div></AstraCtx.Provider>
}

function fixtureCasebook(selectedLead, selectedNode, clueEnabled, replay) {
  const fx = fixtureModel('export');
  const minute = Math.round(Number(replay) / 100 * 11);
  return { product: 'ASTRA', caseId: 'CASE-042', title: 'Fund fragmentation', generatedAt: new Date().toISOString(), dataMode: 'UI FIXTURE', liveIntelligence: false, network: 'Ethereum Mainnet (fixture)', caseWindow: '02:14–02:25 UTC (fixture)', selectedLead, selectedNode: fx.graphView.nodes.find(n => n.id === selectedNode) || fx.graphView.nodes[0], transactions: fx.transactions, graph: { nodes: fx.graphView.nodes, edges: fx.graphView.edges.map(([from,to,amount]) => ({ from: fx.graphView.nodes[from].id, to: fx.graphView.nodes[to].id, amount })) }, evidence: fx.evidence, sensitivity: { forwardingLatencyClueEnabled: clueEnabled }, replay: { cutoff: `02:${String(14 + minute).padStart(2, '0')} UTC`, laterEventsExcluded: true }, limitations: ['UI fixture; not backend analysis, live chain or OSINT data.', 'Graph proximity and timing do not establish common ownership.', 'Public indicator match requires independent source-context validation.', 'Shared-service behaviour remains an alternative explanation.'] };
}

function PageHeader({eyebrow, title, description, actions}) {
  return <div className="page-header"><div><div className="eyebrow"><span className="eyebrow-line"/>{eyebrow}</div><h1>{title}</h1><p>{description}</p></div><div className="header-actions">{actions}</div></div>
}
function Button({children, variant='primary', onClick, icon:Icon, className='', disabled}) {
  const handlePointerMove = (event) => {
    if (event.pointerType === 'touch' || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX - rect.left) / rect.width - 0.5;
    const y = (event.clientY - rect.top) / rect.height - 0.5;
    event.currentTarget.style.setProperty('--btn-rx', `${(-y * 4).toFixed(2)}deg`);
    event.currentTarget.style.setProperty('--btn-ry', `${(x * 5).toFixed(2)}deg`);
  };
  const resetPointer = (event) => {
    event.currentTarget.style.setProperty('--btn-rx', '0deg');
    event.currentTarget.style.setProperty('--btn-ry', '0deg');
  };
  return <button className={`btn btn-${variant} ${className}`} onClick={onClick} disabled={disabled} onPointerMove={handlePointerMove} onPointerLeave={resetPointer}>{Icon && <Icon size={15}/>}<span>{children}</span></button>
}
function MetricCard({label,value,change,icon:Icon,accent='cyan',sub}) {
  return <div className={`metric-card glass accent-${accent}`}><div className="metric-top"><span>{label}</span><span className="metric-icon"><Icon size={16}/></span></div><div className="metric-value">{value}</div><div className="metric-bottom"><span className={change?.startsWith('+') ? 'positive' : 'neutral'}>{change}</span><span>{sub}</span></div><div className="metric-glow"/></div>
}

function overviewNumbers(m) {
  if (m.source !== 'api') return { metrics: [['Transactions observed','18,492','+12.8%','vs previous window'],['Wallets in graph','6,204','+384','new nodes observed'],['Prioritized leads','12','4 critical','require review'],['Evidence coverage','78.4%','+6.2%','source-linked claims']], coverage: 78, exposure: '$42.0M', status: 'Under review', banner: ['Fragmented fund movement after protocol exploit', 'Suspicious transfer sequence detected across a bounded Ethereum transaction neighbourhood.'], kicker: 'CASE-042 · GHOST ATTACKS', readiness: [['Chain data provider','Healthy','ok'],['Public source registry','Healthy','ok'],['GitHub API','Rate-limited','warn']] };
  const a = m.analysis, gs = a.graph_stats;
  const topK = a.leads.slice(0, a.meta.config.top_k);
  const linked = topK.filter((L) => L.osint.supporting_finding_ids.length || L.osint.contradicting_finding_ids.length).length;
  const coverage = topK.length ? Math.round((linked / topK.length) * 1000) / 10 : 0;
  const high = a.leads.filter((L) => L.priority_tier === 'high_priority').length;
  const f = a.flow[0];
  const e0 = (m.graph?.edges || []).find((e) => e.asset_id === f?.asset_id);
  const H = { H1: 'Leading: H1 incident-related', H2: 'Leading: H2 routine service', H3: 'Leading: H3 insufficient' }[a.case_hypotheses.leading];
  return {
    metrics: [['Transactions observed', gs.edges.toLocaleString('en-US'), `${gs.duplicates_dropped_at_ingestion} duplicates dropped`, 'normalized records'],
      ['Wallets in graph', gs.nodes.toLocaleString('en-US'), `${gs.time_respecting_reachable} reachable`, 'time-ordered from origin'],
      ['Prioritized leads', String(a.leads.length), `${high} high priority`, 'transparent score'],
      ['Evidence coverage', `${coverage}%`, `${a.osint.documents.length} source docs`, 'top leads with source links']],
    coverage: Math.round(coverage), status: H,
    exposure: f ? fmtAmount(e0?.token_decimals != null ? (Number(f.initial_raw) / 10 ** e0.token_decimals).toString() : null, f.initial_raw, e0?.asset_symbol) : '—',
    banner: [m.caseTitle, `${gs.edges} observed transfers among ${gs.nodes} addresses; ${gs.time_respecting_reachable} reachable from the origin along time-ordered paths. ${m.synthetic ? 'Synthetic data.' : 'Live public data.'}`],
    kicker: `${m.caseId} · ${m.dataLabel}`, readiness: null,
  };
}

function Overview({onStart,onNavigate,onNotice,onExport,onRefresh}) {
  const { m, hv } = useAstra();
  const [range,setRange] = useState('Last 30 min');
  const [expandedActivity,setExpandedActivity] = useState(false);
  const o = overviewNumbers(m);
  const act = useMemo(() => m.source === 'api' ? activitySeries(m.transactions, range) : { data: m.flowData, peak: '02:19', total: null }, [m, range]);
  const latest = m.source === 'api' ? [...m.transactions].sort((a, b) => b.ts - a.ts).slice(0, 4) : m.transactions.slice(0, 4);
  const readiness = o.readiness || (hv?.items || []);
  return <>
    <PageHeader eyebrow="INVESTIGATION WORKSPACE / 01" title={<>See the signal.<br/><span className="title-muted">Follow the evidence.</span></>} description="A unified view of on-chain movement and public-source intelligence. Every lead is explainable. Every claim is reviewable." actions={<><Button variant="secondary" icon={Download} onClick={onExport}>Export case</Button><Button icon={Play} onClick={onStart}>Open investigation</Button></>}/>
    <div className="incident-banner glass"><div className="incident-mark"><ShieldAlert size={22}/></div><div className="incident-copy"><div className="incident-top"><span className="critical-label"><span className="status-dot red-dot"/> ACTIVE CASE</span><span className="mono muted">{o.kicker}</span></div><h2>{o.banner[0]}</h2><p>{o.banner[1]}</p></div><div className="incident-meta"><div><small>ORIGIN OUTFLOW</small><strong>{o.exposure}</strong></div><div><small>CASE STATUS</small><strong className="status-review">{o.status}</strong></div><button className="round-arrow" onClick={onStart} aria-label="Open investigation graph"><ArrowRight size={18}/></button></div></div>
    <div className="section-heading"><div><h3>Operational snapshot</h3><p>{m.source === 'api' ? 'Computed from the current investigation' : 'Illustrative UI fixture values'}</p></div><div className="select-wrap"><SlidersHorizontal size={14}/><select value={range} onChange={e=>setRange(e.target.value)}><option>Last 30 min</option><option>Last 6 hours</option><option>Last 24 hours</option></select><ChevronDown size={13}/></div></div>
    <div className="metrics-grid">
      <MetricCard label={o.metrics[0][0]} value={o.metrics[0][1]} change={o.metrics[0][2]} sub={o.metrics[0][3]} icon={Activity} accent="cyan"/>
      <MetricCard label={o.metrics[1][0]} value={o.metrics[1][1]} change={o.metrics[1][2]} sub={o.metrics[1][3]} icon={Wallet} accent="violet"/>
      <MetricCard label={o.metrics[2][0]} value={o.metrics[2][1]} change={o.metrics[2][2]} sub={o.metrics[2][3]} icon={Crosshair} accent="amber"/>
      <MetricCard label={o.metrics[3][0]} value={o.metrics[3][1]} change={o.metrics[3][2]} sub={o.metrics[3][3]} icon={ShieldCheck} accent="green"/>
    </div>
    <div className="overview-grid">
      <section className="panel glass activity-panel"><div className="panel-heading"><div><h3>Transaction activity</h3><p>Transfers per interval (latest window)</p></div><span className="live-chip"><span className="status-dot cyan-dot"/> {m.dataBadge === 'LIVE' ? 'LIVE DATA' : m.dataBadge === 'CACHED' ? 'CACHED DATA' : m.dataBadge === 'FIXTURE' ? 'UI FIXTURE' : 'SIMULATED FEED'}</span></div><div className="chart-legend"><span><i className="legend-cyan"/>Observed transfers</span><span><i className="legend-violet"/>Flagged sequence</span><span className="chart-total">{act.total ?? '1.82K'} <small>events</small></span></div><div className="chart-area">{act.data.length ? <ResponsiveContainer width="100%" height="100%"><AreaChart data={act.data} margin={{top:12,right:4,left:-22,bottom:0}}><defs><linearGradient id="activityFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#42d8e7" stopOpacity={0.26}/><stop offset="100%" stopColor="#42d8e7" stopOpacity={0}/></linearGradient></defs><CartesianGrid stroke="var(--chart-grid)" vertical={false}/><XAxis dataKey="time" axisLine={false} tickLine={false} tick={{fill:'var(--muted)',fontSize:10}} dy={10}/><YAxis axisLine={false} tickLine={false} tick={{fill:'var(--muted)',fontSize:10}} allowDecimals={false}/><Tooltip contentStyle={{background:'var(--popover)',border:'1px solid var(--border)',borderRadius:12,color:'var(--text)',fontSize:12}}/><Area type="monotone" dataKey="value" stroke="#45d9e8" strokeWidth={2.5} fill="url(#activityFill)" activeDot={{r:5,strokeWidth:0,fill:'#45d9e8'}}/></AreaChart></ResponsiveContainer> : <p className="muted">No transfers in scope.</p>}</div><div className="chart-foot"><span><span className="status-dot cyan-dot"/> Peak activity <b>{act.peak ? `${act.peak} UTC` : '—'}</b></span><span>Window: {range}</span></div></section>
      <section className="panel glass signal-panel"><div className="panel-heading"><div><h3>Priority signals</h3><p>Highest-ranked investigative leads</p></div><button className="text-link" onClick={()=>onNavigate('leads')}>View all <ArrowRight size={13}/></button></div><div className="signal-list">{m.leads.slice(0,3).map((lead,i)=><button className="signal-row" key={lead.id} onClick={()=>onNavigate('leads')}><div className={`signal-rank rank-${i+1}`}>{String(i+1).padStart(2,'0')}</div><div className="signal-copy"><strong>{lead.title}</strong><span className="mono">{lead.address}</span></div><div className="signal-score"><strong>{lead.score}</strong><span>score</span></div><ChevronRight size={15}/></button>)}{!m.leads.length && <p className="muted">No candidate leads in this investigation.</p>}</div><div className="signal-note"><ShieldCheck size={15}/><span>Scores prioritize review; they do not establish identity.</span></div></section>
    </div>
    <div className="lower-grid">
      <section className="panel glass table-panel"><div className="panel-heading"><div><h3>Latest transaction events</h3><p>Normalized on-chain records</p></div><button className="icon-button" onClick={onRefresh} aria-label="Refresh"><RefreshCw size={15}/></button></div><TransactionTable rows={latest}/><button className="panel-bottom-link" onClick={()=>onNavigate('graph')}>Explore transaction graph <ArrowRight size={14}/></button></section>
      <section className="panel glass readiness-panel"><div className="panel-heading"><div><h3>Investigation readiness</h3><p>Source and analysis health</p></div><span className="small-tag">{m.dataBadge}</span></div><div className="readiness-score"><div className="readiness-ring"><div><strong>{o.coverage}</strong><small>/100</small></div></div><div><strong>Evidence coverage</strong><p>{m.source === 'api' ? 'Share of top-ranked leads with at least one public-source finding.' : 'Most high-priority claims have linked records.'}</p><span className="positive"><ArrowUpRight size={13}/> {m.source === 'api' ? `snapshot ${m.snapshot}` : '+6.2% this run'}</span></div></div><div className="readiness-items">{readiness.map(([k, v, t]) => <div key={k}><span><i className={`mini-status ${t}`}/>{k}</span><b>{v}</b></div>)}</div></section>
    </div>
  </>;
}
function TransactionTable({rows}) {
  return <div className="table-scroll"><table className="data-table"><thead><tr><th>TRANSACTION</th><th>FLOW</th><th>VALUE</th><th>STATUS</th></tr></thead><tbody>{rows.map((t)=><tr key={t.eventId || t.id}><td><strong className="mono">{t.id}</strong><small>{t.time} UTC</small></td><td><span className="flow-cell">{t.from}<ArrowRight size={12}/>{t.to}</span><small className="mono">{t.hash}</small></td><td><strong>{t.amount}</strong><small>{t.usd}</small></td><td><StatusBadge status={t.status}/></td></tr>)}{!rows.length && <tr><td colSpan={4}><small>No transfers in scope.</small></td></tr>}</tbody></table></div>
}
function StatusBadge({status}) {
  const cls = String(status || 'Unknown').toLowerCase().replace(' ','-');
  return <span className={`status-badge ${cls}`}><i/>{status}</span>
}

function GraphPage({selectedNode,setSelectedNode,showDetail,setShowDetail,onNotice,onExport,onOpenEvidence,onCopy}) {
  const { m } = useAstra();
  const [showLabels,setShowLabels] = useState(true);
  const [showFlow,setShowFlow] = useState(true);
  const [zoom,setZoom] = useState(1);
  const [maxLeads,setMaxLeads] = useState(8);
  const [allTx,setAllTx] = useState(false);
  const view = useMemo(() => m.source === 'api' ? buildGraphView(m.analysis, m.graph, { maxLeads }) : m.graphView, [m, maxLeads]);
  const nodes = view.nodes, edges = view.edges;
  const active = nodes.find(n=>n.id===selectedNode) || nodes[0];
  const lead = active && m.source === 'api' ? m.leads.find((l) => l.nodeId === active.id) : null;
  const ev = lead ? evidenceForLead(m.analysis, lead) : null;
  const linked = ev ? [...ev.supporting, ...ev.contradicting, ...ev.context] : [];
  const labelOf = (nid) => nid === m.analysis?.meta?.start_node ? 'Incident origin' : shortAddr(nid.split(':')[1]);
  const pathNodes = lead?.raw?.path?.node_ids || [];
  const pathEvents = lead?.raw?.path?.event_ids || [];
  const evTimes = pathEvents.map((id) => m.transactions.find((t) => t.eventId === id)?.ts).filter(Boolean);
  const pathRows = m.source === 'api' ? (allTx || !pathEvents.length ? m.transactions : m.transactions.filter((t) => pathEvents.includes(t.eventId))) : m.transactions;
  const expand = () => {
    if (m.source !== 'api') { onNotice('All available fixture nodes are already visible.'); return; }
    if (view.shownLeads >= view.totalLeads) { onNotice(`All ${view.totalLeads} candidate leads are already visible.`); return; }
    setMaxLeads((n) => Math.min(view.totalLeads, n + 8)); onNotice('Graph expanded with the next-ranked leads.');
  };
  const addrText = !active ? '—' : m.source === 'api' ? shortAddr(active.address) : (active.id==='wallet7a'?'0x7a1b…91c0':active.id==='exploit'?'0xeXpl…0420':`0x${active.id.slice(0,4)}…${active.id.slice(-4)}`);
  const sig = lead?.signals || (m.source === 'api' ? { temporal: null, graph: null, source: null } : { temporal: 0.91, graph: 0.84, source: active?.id==='wallet7a'?0.62:0.36 });
  const meter = (v) => v == null ? { txt: '—', w: '0%' } : { txt: Number(v).toFixed(2), w: `${Math.round(v * 100)}%` };
  const badge = !active ? 'Observed' : active.tone === 'red' ? 'Observed' : lead?.status === 'disputed' ? 'Disputed' : lead?.leading === 'H3' ? 'Unresolved' : active.tone==='amber'?'Disputed':active.tone==='cyan'?'Inferred':'Observed';
  return <>
    <PageHeader eyebrow="INVESTIGATE / GRAPH EXPLORER" title="Follow the flow." description="Explore wallet relationships, inspect transfer paths, and keep every inference tied to its underlying evidence." actions={<><Button variant="secondary" icon={Download} onClick={onExport}>Export graph</Button><Button icon={PlusIcon} onClick={expand}>Expand graph</Button></>}/>
    <div className="case-strip glass"><div className="case-strip-icon"><Network size={18}/></div><div><strong>{m.caseId} / {m.caseTitle}</strong><span>{m.chainName} · Block window {view.blockWindow}</span></div><span className="data-mode"><i/> {m.dataLabel}</span><span className="case-strip-sep"/><span className="muted small-text">{nodes.length} of {view.totalNodes} nodes · {view.totalEdges} edges</span></div>
    <div className={`graph-workspace ${showDetail ? '' : 'detail-hidden'}`}>
      <section className="graph-panel panel glass"><div className="graph-toolbar"><div className="graph-toolbar-left"><div className="tool-chip"><span className="status-dot cyan-dot"/> Transaction graph</div><button className="tool-button" onClick={()=>setShowLabels(!showLabels)}><Eye size={14}/>{showLabels?'Labels on':'Labels off'}</button><button className="tool-button" onClick={()=>setShowFlow(!showFlow)}><GitBranch size={14}/>{showFlow?'Flow on':'Flow off'}</button></div><div className="graph-toolbar-right"><button className="icon-button small" onClick={()=>setZoom(Math.max(.7,zoom-.1))}>−</button><span className="zoom-label">{Math.round(zoom*100)}%</span><button className="icon-button small" onClick={()=>setZoom(Math.min(1.4,zoom+.1))}>+</button><button className="icon-button small" onClick={()=>setZoom(1)}><RefreshCw size={13}/></button><button className="icon-button small" onClick={()=>setShowDetail(!showDetail)}><SlidersHorizontal size={14}/></button></div></div>
        <div className="graph-canvas"><div className="graph-grid"/><div className="graph-watermark">ASTRA <span>GRAPH ENGINE</span></div><div className="graph-map" style={{transform:`scale(${zoom})`}}>
          <svg className="edge-layer" viewBox="0 0 760 410" preserveAspectRatio="none">
            <defs><linearGradient id="edgeGradient"><stop offset="0%" stopColor="#f16d7a"/><stop offset="100%" stopColor="#48d6e6"/></linearGradient><marker id="arrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 z" fill="#55d5e4"/></marker></defs>
            {edges.map((e,i)=>{const a=nodes[e[0]],b=nodes[e[1]];const fromOrigin=a.tone==='red';const toTop=b.tone==='cyan';return <g key={i} className={showFlow?'':'edge-muted'}><path d={`M ${a.x+42} ${a.y+24} C ${(a.x+b.x)/2+40} ${a.y-10}, ${(a.x+b.x)/2-40} ${b.y+65}, ${b.x+10} ${b.y+24}`} fill="none" stroke={fromOrigin?'#e77883':toTop?'#49d8e7':'var(--edge-color)'} strokeWidth={fromOrigin?2.5:1.6} strokeDasharray={e[3]?'5 5':'none'} markerEnd="url(#arrow)" opacity={fromOrigin?0.9:0.66}/>{showLabels&&<text x={(a.x+b.x)/2+12} y={(a.y+b.y)/2+13} className="edge-label">{e[2]}</text>}</g>})}
          </svg>
          {nodes.map(n=><button key={n.id} className={`graph-node node-${n.tone} ${selectedNode===n.id?'selected':''}`} style={{left:`${n.x/760*100}%`,top:`${n.y/410*100}%`}} onClick={()=>setSelectedNode(n.id)}><span className="node-orbit"/><span className="node-core">{n.tone==='red'?<ShieldAlert size={17}/>:n.tone==='cyan'?<Fingerprint size={17}/>:n.tone==='amber'?<LockKeyhole size={17}/>:<Wallet size={17}/>}</span>{showLabels&&<span className="node-label"><strong>{n.label}</strong><small>{n.sub}</small></span>}</button>)}
        </div><div className="graph-legend"><span><i className="legend-dot red-bg"/>Incident origin</span><span><i className="legend-dot cyan-bg"/>Top lead</span><span><i className="legend-dot violet-bg"/>Source-only link</span><span><i className="legend-dash"/>Unresolved path</span></div><div className="graph-controls"><button onClick={()=>setZoom(Math.min(1.4,zoom+.1))}>＋</button><button onClick={()=>setZoom(Math.max(.7,zoom-.1))}>−</button><button onClick={()=>{setZoom(1);setSelectedNode(m.source==='api'?(m.leads[0]?.nodeId||nodes[0]?.id):'wallet7a')}}><Crosshair size={14}/></button></div>{!nodes.length && <div className="graph-watermark" style={{top:'45%'}}>No observed transfers in scope</div>}</div>
        <div className="graph-bottom"><div><span className="mini-label">SELECTED PATH</span><div className="path-chain">{m.source === 'api' ? (pathNodes.length ? pathNodes.slice(0, -1).slice(-4).map((n) => <React.Fragment key={n}><span>{labelOf(n)}</span><ChevronRight size={13}/></React.Fragment>) : null) : <><span>Exploit wallet</span><ChevronRight size={13}/><span>Relay 04</span><ChevronRight size={13}/><span>Cluster B</span><ChevronRight size={13}/></>}<b>{active?.label || '—'}</b></div></div><div className="path-stats"><div><small>PATH LENGTH</small><strong>{m.source === 'api' ? (lead?.raw?.features?.hops != null ? `${lead.raw.features.hops} hop${lead.raw.features.hops === 1 ? '' : 's'}` : active?.tone === 'red' ? 'origin' : 'no path') : '3 hops'}</strong></div><div><small>TIME DELTA</small><strong>{m.source === 'api' ? (evTimes.length ? humanDuration((Math.max(...evTimes) - (m.incidentStart ?? Math.min(...evTimes))) / 1000) : '—') : '2m 39s'}</strong></div><div><small>{m.source === 'api' ? 'TRACED VALUE' : 'OBSERVED VALUE'}</small><strong>{active?.value || '—'}</strong></div></div></div>
      </section>
      {showDetail&&active&&<aside className="graph-inspector panel glass"><div className="inspector-header"><div><span className="eyebrow tiny">NODE INSPECTOR</span><h3>{active.label}</h3></div><button className="icon-button" onClick={()=>setShowDetail(false)}><X size={16}/></button></div><div className="inspector-address mono">{addrText} <button onClick={()=>onCopy(m.source==='api'?active.address:addrText)} aria-label="Copy address"><Copy size={12}/></button></div><div className="inspector-tags"><span className={`node-tag ${active.tone}`}>{active.sub}</span><StatusBadge status={badge}/></div><div className="inspector-metric"><span>{m.source === 'api' ? (active.tone === 'red' ? 'Origin observed outflow' : 'Traced value (FIFO allocation)') : 'Observed transfer value'}</span><strong>{active.value}</strong><small>Across selected case window</small></div><div className="inspector-section"><h4>Quantitative signals</h4><div className="signal-meter"><div><span>Temporal compatibility</span><b>{meter(sig.temporal).txt}</b></div><div className="meter-track"><span style={{width:meter(sig.temporal).w}}/></div></div><div className="signal-meter"><div><span>Graph relevance</span><b>{meter(sig.graph).txt}</b></div><div className="meter-track violet-meter"><span style={{width:meter(sig.graph).w}}/></div></div><div className="signal-meter"><div><span>Source corroboration</span><b>{meter(sig.source).txt}</b></div><div className="meter-track amber-meter"><span style={{width:meter(sig.source).w}}/></div></div></div><div className="inspector-section"><h4>Linked evidence <span className="small-tag">{m.source === 'api' ? linked.length : 3} records</span></h4>{m.source === 'api' ? linked.slice(0, 3).map((r) => <div className="evidence-mini" key={r.id}><span className={`evidence-mini-icon ${r.raw.kind === 'osint_indicator' ? 'violet' : ''}`}>{r.raw.kind === 'osint_indicator' ? <Globe2 size={14}/> : <FileSearch size={14}/>}</span><div><strong>{r.title.length > 48 ? `${r.title.slice(0, 46)}…` : r.title}</strong><small>{r.status} · {r.raw.source}</small></div><ChevronRight size={14}/></div>) : <><div className="evidence-mini"><span className="evidence-mini-icon"><FileSearch size={14}/></span><div><strong>Transaction sequence</strong><small>On-chain · Observed · 02:16 UTC</small></div><ChevronRight size={14}/></div><div className="evidence-mini"><span className="evidence-mini-icon violet"><Globe2 size={14}/></span><div><strong>Public indicator reference</strong><small>GitHub API · Validation pending</small></div><ChevronRight size={14}/></div></>}{m.source === 'api' && !linked.length && <small className="muted">{active.tone === 'red' ? 'Origin: see case-level evidence in the evidence inspector.' : 'No evidence records for this node.'}</small>}</div><div className="inspector-caveat"><ShieldAlert size={15}/><p>Association is a candidate lead. Graph proximity and timing do not establish common ownership.</p></div><Button className="full-btn" onClick={()=>{ if (m.source === 'api') onOpenEvidence(lead?.id); else onNotice('Evidence inspector opened for the selected node.'); }}>Open evidence inspector <ArrowRight size={14}/></Button></aside>}
    </div>
    <div className="panel glass graph-transactions"><div className="panel-heading"><div><h3>Path transactions</h3><p>{m.source === 'api' && pathEvents.length && !allTx ? 'Observed transfers on the selected lead’s time-ordered path' : 'Observed transfers related to this graph view'}</p></div><div className="inline-actions"><span className="small-tag">{pathRows.length} EDGES</span><button className="text-link" onClick={()=>{ if (m.source === 'api') setAllTx(v => !v); else onNotice('Showing all graph transactions in the fixture.'); }}>{allTx ? 'Selected path' : 'View all'} <ArrowRight size={13}/></button></div></div><TransactionTable rows={pathRows.slice(0, 200)}/></div>
  </>;
}
function PlusIcon({size=16}){return <span className="plus-icon" style={{fontSize:size}}>＋</span>}
function LeadsPage({selectedLead,setSelectedLead,onNavigate}) {
  const { m } = useAstra();
  const api = m.source === 'api';
  const [priority,setPriority] = useState('All priorities');
  const leads = m.leads;
  const filtered = leads.filter(l=>priority==='All priorities'||l.priority===priority);
  const opts = api ? ['High','Medium','Low'] : ['Critical','High','Review'];
  const cnt = (p) => String(leads.filter((l) => l.priority === p).length).padStart(2, '0');
  const linkedPct = api && leads.length ? `${Math.round(leads.filter((l) => l.raw.osint.supporting_finding_ids.length || l.raw.osint.contradicting_finding_ids.length).length / leads.length * 100)}%` : '78%';
  const alt = (l) => !api ? 'Shared service infrastructure or ordinary high-volume activity may produce similar patterns. Verify service labels and additional source evidence.'
    : l.leading === 'H1' ? 'H2 (routine service or market behaviour) remains an alternative: verify service labels and look for independent sources.'
      : l.leading === 'H2' ? 'H1 (incident-related movement) is weaker here but not excluded: check for fresh, independent incident reports.'
        : `The evidence does not separate H1 and H2. ${(l.raw.hypotheses.missing_information || []).join(' ')}`;
  const sl = selectedLead;
  return <>
    <PageHeader eyebrow="INVESTIGATE / LEAD QUEUE" title="The next best lead." description="A transparent review queue. Every ranking has reasons, counter-explanations, and a recommended verification step." actions={<Button variant="secondary" icon={Download} onClick={()=>window.print()}>Export queue</Button>}/>
    <div className="lead-summary-grid"><div className="lead-summary glass"><span className="summary-icon red-icon"><ShieldAlert size={17}/></span><div><small>{api ? 'High priority' : 'Critical'}</small><strong>{api ? cnt('High') : '04'}</strong><span>{api ? 'Score ≥ 75 · review first' : 'Immediate review'}</span></div></div><div className="lead-summary glass"><span className="summary-icon amber-icon"><Target size={17}/></span><div><small>{api ? 'Medium priority' : 'High priority'}</small><strong>{api ? cnt('Medium') : '08'}</strong><span>{api ? 'Score 45–75' : 'Review this session'}</span></div></div><div className="lead-summary glass"><span className="summary-icon cyan-icon"><Fingerprint size={17}/></span><div><small>Evidence-linked</small><strong>{linkedPct}</strong><span>{api ? 'Leads with public-source findings' : 'Of ranked claims'}</span></div></div></div>
    <div className="leads-layout"><section className="panel glass lead-list-panel"><div className="panel-heading"><div><h3>Ranked leads</h3><p>Sorted by priority score (heap top-k, deterministic ties)</p></div><div className="select-wrap"><Filter size={14}/><select value={priority} onChange={e=>setPriority(e.target.value)}><option>All priorities</option>{opts.map(o => <option key={o}>{o}</option>)}</select><ChevronDown size={13}/></div></div>{filtered.slice(0, 100).map(l=><button key={l.id} className={`lead-row ${sl?.id===l.id?'lead-active':''}`} onClick={()=>setSelectedLead(l)}><div className={`lead-score score-${(l.priorityClass || l.priority).toLowerCase()}`}><strong>{l.score}</strong><small>/100</small></div><div className="lead-row-copy"><div className="lead-title-line"><strong>{l.title}</strong><span className={`priority-pill ${(l.priorityClass || l.priority).toLowerCase()}`}>{l.priority}</span></div><span className="mono">{l.address}</span><div className="lead-reason-inline">{l.reasons[0]} · {l.age}</div></div><ChevronRight size={16}/></button>)}{!filtered.length && <p className="muted">No leads match this filter.</p>}</section>{sl ? <section className="panel glass lead-detail-panel"><div className="panel-heading"><div><span className="eyebrow tiny">LEAD DETAILS / {sl.id}</span><h3>{sl.title}</h3></div><span className={`priority-pill ${(sl.priorityClass || sl.priority).toLowerCase()}`}>{sl.priority}</span></div><div className="lead-detail-score"><div className="score-circle"><strong>{sl.score}</strong><small>priority score</small></div><div><span className="mono">{api ? sl.fullAddress : sl.address}</span><p>{sl.detail}</p></div></div><h4 className="subsection-title">Why this lead is ranked</h4><div className="reason-list">{sl.reasons.map((r,i)=><div key={r + i}><span className="reason-check"><Check size={12}/></span><span>{r}</span><span className="reason-weight">{sl.reasonWeights ? sl.reasonWeights[i] : i===0?'HIGH':'MED'}</span></div>)}</div><div className="alternative-box"><ShieldAlert size={16}/><div><strong>Alternative explanation</strong><p>{alt(sl)}</p></div></div><div className="next-step"><span className="eyebrow tiny">RECOMMENDED NEXT STEP</span><p>{api ? sl.nextCheck : 'Open the original transaction records and verify whether a source-backed indicator independently supports this lead.'}</p><Button onClick={()=>onNavigate('evidence')} icon={FileSearch}>Inspect linked evidence</Button></div></section> : <section className="panel glass lead-detail-panel"><p className="muted">No lead selected.</p></section>}</div>
  </>;
}
function EvidencePage({clueEnabled,setClueEnabled,onNotice,onExport}) {
  const { m, selectedLead, clueFor, sens } = useAstra();
  const api = m.source === 'api';
  const [tab,setTab] = useState('Supporting');
  const ev = api ? evidenceForLead(m.analysis, selectedLead) : null;
  const fixtureRows = {Supporting:[['On-chain transfer sequence','Observed transaction records','02:16:31 UTC','Observed','TX-1C7B2'],['Forwarding latency feature','ASTRA quantitative engine','Median delta: 3.2 seconds','Calculated','FEAT-TIME-09'],['Public repository indicator','GitHub public API · fixture result','Indicator match pending review','Source-reported','SRC-GH-004']],Contradicting:[['Possible shared-service behaviour','Analyst review note','Service infrastructure not ruled out','Disputed','NOTE-003']],Context:[['Known exchange-like endpoint','Public label registry','Label confidence: moderate','Source-reported','SRC-LABEL-02']]};
  const rows = api ? { Supporting: ev.supporting, Contradicting: ev.contradicting, Context: ev.context }[tab].map((r) => [r.title, r.sub, r.small, r.status, r.id, r]) : fixtureRows[tab];
  const counts = api ? [ev.supporting.length, ev.contradicting.length, ev.context.length] : [3, 1, 1];
  const claim = ev?.claim;
  const clue = api ? clueFor(selectedLead) : null;
  const sensOn = api && !clueEnabled && sens.status === 'ready' && sens.leadId === selectedLead?.id;
  const after = sensOn ? sens.result.leads.find((L) => L.lead_id === selectedLead.id) || sens.result.comparison.leads.find((r) => r.lead_id === selectedLead.id) : null;
  const rankAfter = sensOn ? (sens.result.comparison.leads.find((r) => r.lead_id === selectedLead.id)?.rank_after ?? '—') : selectedLead?.rank;
  const comps = (L) => Object.fromEntries((L?.components || []).map((c) => [c.signal, c.value]));
  const cur = api ? comps(sensOn && after?.components ? after : selectedLead?.raw) : null;
  const pct = (v) => (v == null ? null : Math.round(v * 100));
  const bars = api ? [['Temporal compatibility', pct(cur['temporal.rapid_forwarding']), ''], ['Graph relevance', pct(cur['graph.incident_path_proximity']), 'violet-meter'], ['Source corroboration', pct(cur['osint.indicator_match']), 'amber-meter']]
    : [['Temporal compatibility', clueEnabled ? 91 : null, ''], ['Graph relevance', 84, 'violet-meter'], ['Source corroboration', 62, 'amber-meter']];
  const openRecord = (r) => {
    if (!api) { onNotice(`Opened ${r[4]} details in demo mode.`); return; }
    const x = r[5];
    if (x.url && !x.synthetic) { window.open(x.url, '_blank', 'noopener,noreferrer'); return; }
    if (x.url) { onNotice(`Synthetic source (${x.url}) — reserved .invalid domain, not a real page.`); return; }
    const refs = x.raw.refs?.event_ids || [];
    onNotice(refs.length ? `${x.id}: ${refs.length} transfer reference(s), e.g. ${refs[0].slice(0, 22)}…` : `${x.id}: calculated by the ASTRA engine.`);
  };
  return <>
    <PageHeader eyebrow="VERIFY / EVIDENCE INSPECTOR" title="Every claim has a trail." description="Move from an investigative claim to the exact source record, and back again. Review contradictions before you trust a lead." actions={<Button variant="secondary" icon={Download} onClick={onExport}>Export evidence</Button>}/>
    <div className="claim-hero glass"><div className="claim-status"><Fingerprint size={18}/><span>INVESTIGATIVE CLAIM</span><StatusBadge status={api ? (claim?.status || 'Unresolved') : 'Inferred'}/></div><h2>{api ? (selectedLead ? `${selectedLead.address} ${claim?.predicate || 'is a candidate lead for'} ${claim?.object || 'the incident'}.` : 'No lead selected.') : 'Wallet 0x7a1b may share a temporal pattern with the incident flow.'}</h2><p>{api ? `${claim?.explanation || ''} This is a candidate association, not a verified identity attribution.` : 'This is a candidate association based on observed transfer timing and graph context. It is not a verified identity attribution.'}</p><div className="claim-meta"><span><small>CLAIM ID</small><b>{api ? (claim?.claim_id || '—') : 'CLM-0042'}</b></span><span><small>{api ? 'ANALYSIS AS OF' : 'LAST UPDATED'}</small><b>{api ? `${utcTime(m.analysis.meta.as_of)} UTC` : '02:24:18 UTC'}</b></span><span><small>ANALYSIS VERSION</small><b>{api ? m.analysis.meta.engine_version : 'astra-demo.0.9.4'}</b></span></div></div>
    <div className="evidence-layout"><section className="panel glass"><div className="panel-heading"><div><h3>Evidence records</h3><p>Source provenance and interpretation status</p></div><span className="small-tag">{counts[0] + counts[1] + counts[2]} RECORDS</span></div><div className="tabs"><button className={tab==='Supporting'?'active':''} onClick={()=>setTab('Supporting')}>Supporting <span>{counts[0]}</span></button><button className={tab==='Contradicting'?'active':''} onClick={()=>setTab('Contradicting')}>Contradicting <span>{counts[1]}</span></button><button className={tab==='Context'?'active':''} onClick={()=>setTab('Context')}>Context <span>{counts[2]}</span></button></div>{rows.map((r,i)=><div className="evidence-record" key={r[4]}><div className="evidence-record-icon"><FileSearch size={16}/></div><div className="evidence-record-copy"><strong>{r[0]}</strong><span>{r[1]}</span><small>{r[2]}</small><div className="record-id mono">{r[4]}</div></div><StatusBadge status={r[3]}/><button className="icon-button" onClick={()=>openRecord(r)} aria-label="Open record"><ExternalLink size={14}/></button></div>)}{!rows.length && <p className="muted">No {tab.toLowerCase()} evidence for this lead.</p>}</section><aside className="panel glass sensitivity-panel"><div className="panel-heading"><div><h3>Evidence sensitivity</h3><p>Test whether the conclusion is brittle</p></div><Sparkles size={16}/></div><div className="sensitivity-score"><div><small>Original rank</small><strong>#{api ? (selectedLead?.rank ?? '—') : 1}</strong></div><ArrowRight size={20}/><div><small>Adjusted rank</small><strong>{api ? (sens.status === 'loading' ? '…' : `#${rankAfter ?? '—'}`) : (clueEnabled?'#2':'#4')}</strong></div></div><div className="sensitivity-toggle"><div><strong>{api ? (clue ? (clue.kind === 'temporal_feature' ? 'Forwarding latency clue' : clue.title.slice(0, 44)) : 'No removable clue') : 'Forwarding latency clue'}</strong><small>{api ? (clue ? `${clue.status} · ${clue.evidence_id.slice(0, 26)}` : 'Lead has no supporting evidence') : 'Calculated · Feature FEAT-TIME-09'}</small></div><button className={`switch ${clueEnabled?'on':''}`} onClick={()=>setClueEnabled(!clueEnabled)} aria-label="Toggle latency clue" disabled={api && !clue}><span/></button></div><div className="sensitivity-explanation"><ShieldAlert size={15}/><p>{api ? (clueEnabled ? 'This clue currently contributes to the ranking. Toggle it off to re-run the ASTRA engine without it.' : sens.status === 'loading' ? 'Re-running the engine without this clue…' : sensOn ? `Engine re-run complete: score ${selectedLead.scoreExact} → ${after?.priority_score ?? '—'}, rank #${selectedLead.rank} → #${rankAfter}. ${sens.result.comparison.affected_claim_ids.length} claim(s) depended on this clue.` : 'Sensitivity re-run unavailable.') : (clueEnabled?'This clue currently contributes to the ranking. Toggle it off to see how the lead priority changes.':'With the latency clue removed, the lead drops in rank. The conclusion depends materially on this feature.')}</p></div><div className="score-breakdown">{bars.map(([label, v, cls]) => <React.Fragment key={label}><div><span>{label}</span><strong>{v == null ? '—' : `${v}%`}</strong></div><div className={`meter-track ${cls}`}><span style={{width: v == null ? '0%' : `${v}%`}}/></div></React.Fragment>)}</div><Button variant="secondary" className="full-btn" onClick={()=>setClueEnabled(true)} icon={RefreshCw}>Reset sensitivity test</Button></aside></div>
  </>;
}
function SourcesPage({search,setSearch,onNotice}) {
  const { m, astraApi: api, data } = useAstra();
  const live = m.source === 'api';
  const [sourceType,setSourceType] = useState('All sources');
  const fixtureSources = [{name:'GitHub REST API',type:'Repository',status:'Rate-limited',url:'https://docs.github.com/en/rest/search/search',detail:'Public repository metadata and permitted source references',retrieved:'02:23:41 UTC',icon:GitBranch},{name:'Ethereum RPC provider',type:'Blockchain',status:'Healthy',url:'https://ethereum.org/en/developers/docs/apis/json-rpc/',detail:'Transaction, block and receipt lookups',retrieved:'02:24:08 UTC',icon:Layers3},{name:'Public incident report registry',type:'Threat feed',status:'Healthy',url:'https://www.cisa.gov/news-events/cybersecurity-advisories',detail:'Public security advisories and incident context',retrieved:'02:21:13 UTC',icon:ShieldCheck},{name:'The Graph documentation',type:'Indexer',status:'Healthy',url:'https://thegraph.com/docs/en/',detail:'Structured blockchain data access where a relevant subgraph exists',retrieved:'02:19:04 UTC',icon:Database}];
  const iconFor = (t) => ({ Repository: GitBranch, 'Threat report': ShieldCheck, 'Threat feed': ShieldCheck, 'Label registry': Database, 'Analyst note': FileSearch, Forum: Globe2 }[t] || Layers3);
  const sources = live ? sourceRows(m.analysis).map((s) => ({ ...s, icon: iconFor(s.type) })) : fixtureSources;
  const types = live ? [...new Set(sources.map((s) => s.type))] : ['Repository','Blockchain','Threat feed','Indexer'];
  const shown = sources.filter(s=>(sourceType==='All sources'||s.type===sourceType)&&(!search||`${s.name} ${s.type} ${s.detail}`.toLowerCase().includes(search.toLowerCase())));
  const st = live ? sourceStats(m.analysis) : { registered: 5, healthy: 4, problems: 1, last: '02:24 UTC' };
  const refresh = async () => {
    if (!live) { onNotice('Source health refreshed from local demo state.'); return; }
    if (m.synthetic) { await data.load(m.caseId); onNotice('Reloaded. Live collection is disabled for synthetic cases.'); return; }
    try { const r = await api.collectOsint(m.caseId); onNotice(r.results.map((x) => `${x.term.slice(0, 10)}…: ${x.status}${x.code ? ` (${x.code})` : ''}`).join(' · ') || 'No terms collected.'); await data.load(m.caseId); }
    catch (error) { onNotice(`Collection failed: ${error.message}`); }
  };
  return <>
    <PageHeader eyebrow="INTELLIGENCE / SOURCE REGISTRY" title="Know where the signal came from." description="A transparent registry of public data inputs, extraction methods, retrieval timestamps, and provider health." actions={<Button icon={RefreshCw} onClick={refresh}>{live && !m.synthetic ? 'Collect public sources' : 'Refresh health'}</Button>}/>
    <div className="source-stats"><div className="source-stat glass"><span>Registered sources</span><strong>{String(st.registered).padStart(2,'0')}</strong></div><div className="source-stat glass"><span>Healthy providers</span><strong className="positive">{String(st.healthy).padStart(2,'0')}</strong></div><div className="source-stat glass"><span>{live ? 'Failed / limited' : 'Rate-limited'}</span><strong className="warning-text">{String(st.problems).padStart(2,'0')}</strong></div><div className="source-stat glass"><span>Last ingestion</span><strong>{st.last}</strong></div></div>
    <section className="panel glass source-table-panel"><div className="panel-heading"><div><h3>{live ? 'Collected documents' : 'Source inventory'}</h3><p>Source URLs are preserved with each indicator record{live && m.synthetic ? ' · SYNTHETIC documents on a reserved .invalid domain' : ''}</p></div><div className="source-filters"><div className="search-box"><Search size={14}/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search sources…"/></div><div className="select-wrap"><Filter size={14}/><select value={sourceType} onChange={e=>setSourceType(e.target.value)}><option>All sources</option>{types.map(t => <option key={t}>{t}</option>)}</select><ChevronDown size={13}/></div></div></div><div className="source-list">{shown.map((s, i)=>{const Icon=s.icon;return <div className="source-row" key={`${s.name}-${i}`}><div className="source-icon"><Icon size={17}/></div><div className="source-main"><strong>{s.name}</strong><span>{s.detail}</span>{s.url ? <a href={s.url} target="_blank" rel="noreferrer noopener">{s.url} <ExternalLink size={11}/></a> : null}</div><div className="source-type">{s.type}</div><div className="source-last"><small>LAST RETRIEVED</small><span>{s.retrieved}</span></div><StatusBadge status={live ? (s.status === 'Collected' ? 'Source-reported' : 'Failed') : (s.status==='Healthy'?'Observed':'Disputed')}/><button className="icon-button" onClick={()=>onNotice(live ? s.info : `${s.name}: ${s.status} (demo status)`)} aria-label="Source details"><ChevronRight size={15}/></button></div>})}{!shown.length && <p className="muted">No public-source documents for this investigation.</p>}</div><div className="source-footnote"><LockKeyhole size={14}/> Public sources only. ASTRA should respect source terms, rate limits, and data minimization requirements.</div></section>
  </>;
}
function ReplayPage({replay,setReplay}) {
  const { m, astraApi: api } = useAstra();
  const live = m.source === 'api';
  const tl = live ? m.analysis.timeline.filter((t) => t.time) : [];
  const t0 = live && tl.length ? new Date(tl[0].time).getTime() : 0;
  const t1 = live && tl.length ? new Date(tl[tl.length - 1].time).getTime() : 0;
  const cutoffMs = live ? Math.round(t0 + (t1 - t0) * Number(replay) / 100) : 0;
  const [res, setRes] = useState({ status: 'idle', data: null, error: null });
  useEffect(() => {
    if (!live || !tl.length) return;
    let cancelled = false;
    setRes((r) => ({ ...r, status: 'loading' }));
    const timer = setTimeout(async () => {
      try { const d = await api.getReplay(m.caseId, new Date(cutoffMs).toISOString().replace('.000Z', 'Z')); if (!cancelled) setRes({ status: 'ready', data: d, error: null }); }
      catch (error) { if (!cancelled) setRes({ status: 'error', data: null, error }); }
    }, 250);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [live, m, cutoffMs]); // eslint-disable-line react-hooks/exhaustive-deps
  const multiDay = live && t1 - t0 > 86400000;
  const fmt = (x) => (multiDay ? `${utcDateTime(x)}` : utcHM(x));
  const currentMinute = Math.round(replay/100*11);
  const fixtureEvents = [{m:0,time:'02:14:08',title:'Initial incident wallet identified',desc:'Exploit-associated transfer observed on-chain',kind:'observed'},{m:2,time:'02:16:31',title:'First split sequence detected',desc:'Multiple unequal outputs from Relay 04',kind:'calculated'},{m:4,time:'02:18:44',title:'Candidate temporal lead ranked',desc:'Forwarding delta feature available',kind:'inferred'},{m:7,time:'02:21:13',title:'Public incident source ingested',desc:'Source record linked to the case',kind:'source'},{m:10,time:'02:24:08',title:'Service endpoint context updated',desc:'Alternative explanation added for review',kind:'disputed'}];
  const known = live ? (res.data?.timeline || []).filter((t) => t.known_at_cutoff) : fixtureEvents.filter(e=>e.m<=currentMinute);
  const later = live ? (res.data?.timeline || []).filter((t) => !t.known_at_cutoff).length : fixtureEvents.filter(e=>e.m>currentMinute).length;
  const shown = live ? known.slice(-10).map((t) => ({ time: utcTime(t.time), title: t.title, desc: t.kind === 'transfer' ? `Observed transfer · ${t.ref.slice(0, 18)}…` : t.status === 'Unresolved' ? `Collection failed · no content · attempted ${t.retrieved_at ? utcDateTime(new Date(t.retrieved_at).getTime()) : '—'}` : `Public source · published ${t.event_time ? utcDateTime(new Date(t.event_time).getTime()) : 'unknown'} · retrieved ${t.retrieved_at ? utcDateTime(new Date(t.retrieved_at).getTime()) : '—'}`, kind: t.kind === 'transfer' ? 'observed' : t.status === 'Unresolved' ? 'unresolved' : 'source', key: t.ref })) : fixtureEvents.filter(e=>e.m<=currentMinute).map(e => ({ ...e, key: e.time }));
  const leadsAt = res.data?.leads?.length;
  return <>
    <PageHeader eyebrow="RECONSTRUCT / INVESTIGATION REPLAY" title="Rewind the investigation." description="Move the historical cutoff to see what could reasonably have been known at that point—without using future evidence." actions={<span className="data-mode"><i/> REPLAY MODE</span>}/>
    <section className="panel glass replay-panel"><div className="replay-top"><div><span className="eyebrow tiny">HISTORICAL CUTOFF</span><h2>{live ? (tl.length ? `${fmt(cutoffMs)} UTC` : '—') : `02:${String(14+currentMinute).padStart(2,'0')} UTC`}</h2><p>{live ? `Evidence known by this time only · policy: ${res.data?.policy || 'observation'}${leadsAt != null ? ` · ${leadsAt} lead(s) available` : ''}` : 'Evidence known by this time only'}</p></div><div className="replay-cutoff"><span>CASE WINDOW</span><strong>{live ? (tl.length ? `${fmt(t0)} — ${fmt(t1)} UTC` : '—') : '02:14 — 02:25 UTC'}</strong><span className="mono">snapshot: {live ? (res.data?.snapshot_id || (res.status === 'loading' ? 'computing…' : '—')) : `CASE-042-R${String(currentMinute).padStart(2,'0')}`}</span></div></div><div className="range-row"><span>{live ? (tl.length ? fmt(t0) : '—') : '02:14'}</span><input type="range" min="0" max="100" value={replay} onChange={e=>setReplay(Number(e.target.value))} aria-label="Historical cutoff"/><span>{live ? (tl.length ? fmt(t1) : '—') : '02:25'}</span></div><div className="timeline-events">{shown.map((e)=><div className="timeline-event" key={e.key}><div className={`timeline-marker ${e.kind}`}/><div className="timeline-time mono">{e.time}</div><div className="timeline-copy"><strong>{e.title}</strong><p>{e.desc}</p></div><StatusBadge status={e.kind==='observed'?'Observed':e.kind==='calculated'?'Calculated':e.kind==='inferred'?'Inferred':e.kind==='source'?'Source-reported':e.kind==='unresolved'?'Unresolved':'Disputed'}/></div>)}{live && res.status === 'error' && <p className="muted">Replay failed: {res.error.message}</p>}{live && !shown.length && res.status === 'ready' && <p className="muted">Nothing was known at this cutoff.</p>}</div><div className="replay-warning"><ShieldCheck size={16}/><div><strong>Hindsight guard enabled</strong><p>{later} later events are excluded from this replay state. {live ? `The backend recomputed the analysis using only records available at the cutoff${m.synthetic ? ' (synthetic data)' : ''}.` : 'This is a deterministic demo timeline, not live chain history.'}</p></div></div></section>
  </>;
}
function HypothesesPage({clueEnabled,setClueEnabled}) {
  const { m, sens, selectedLead } = useAstra();
  const api = m.source === 'api';
  const sensOn = api && !clueEnabled && sens.status === 'ready';
  const caseH = sensOn ? sens.result.case_hypotheses : m.analysis?.case_hypotheses;
  const hypotheses = api ? hypothesisCards(caseH) : [{id:'H1',title:'Incident-related fund movement',score:clueEnabled?78:59,desc:'Observed path and temporal signals are consistent with a possible incident-related flow.',support:'3 supporting records',against:'1 unresolved alternative',tone:'cyan',leading:true},{id:'H2',title:'Routine service / market behaviour',score:clueEnabled?42:57,desc:'Shared infrastructure or normal high-volume behaviour could explain part of the observed pattern.',support:'1 contextual record',against:'2 features need review',tone:'amber'},{id:'H3',title:'Insufficient evidence',score:clueEnabled?36:64,desc:'Current public-source corroboration is not sufficient for a confident attribution.',support:'Evidence gaps remain',against:'More verification needed',tone:'violet'}];
  // falsification row: the SELECTED lead's own leading hypothesis, before vs after removing its strongest clue
  const lh = api ? selectedLead?.raw?.hypotheses : null;
  const leadBefore = lh ? { id: lh.leading, score: Math.round(lh.hypotheses.find((x) => x.id === lh.leading).score * 100) } : null;
  const afterLead = sensOn && sens.leadId === selectedLead?.id ? sens.result.leads.find((L) => L.lead_id === selectedLead.id) : null;
  const leadAfterScore = afterLead && leadBefore ? Math.round(afterLead.hypotheses.hypotheses.find((x) => x.id === leadBefore.id).score * 100) : (leadBefore && clueEnabled ? leadBefore.score : null);
  return <>
    <PageHeader eyebrow="CHALLENGE / HYPOTHESES" title="Test the other explanation." description="Keep competing interpretations visible. A defensible investigation should show what would weaken the leading hypothesis." actions={<Button variant="secondary" icon={RefreshCw} onClick={()=>setClueEnabled(true)}>Reset comparison</Button>}/>
    <div className="hypothesis-note glass"><ShieldAlert size={18}/><div><strong>These are comparative review scores, not probabilities.</strong><p>{api ? `Computed by the ASTRA engine for ${m.caseId} (top lead and case-origin evidence). Do not use them as calibrated likelihoods or identity claims.` : 'Scores are illustrative demo values. Do not use them as calibrated likelihoods or identity claims.'}</p></div></div>
    <div className="hypothesis-grid">{hypotheses.map((h,i)=><div className={`hypothesis-card panel glass ${h.leading?'hypothesis-primary':''}`} key={h.id}><div className="hypothesis-top"><span className={`hypothesis-id ${h.tone}`}>{h.id}</span><span className="small-tag">{h.leading?'LEADING REVIEW':'ALTERNATIVE'}</span></div><h3>{h.title}</h3><p>{h.desc}</p><div className="hypothesis-score"><strong>{h.score}</strong><span>/100 review score</span></div><div className="meter-track"><span style={{width:`${h.score}%`}}/></div><div className="hypothesis-meta"><span><Check size={13}/>{h.support}</span><span><CircleHelp size={13}/>{h.against}</span></div></div>)}</div>
    <section className="panel glass falsification-panel"><div><span className="eyebrow tiny">FALSIFICATION CHECK</span><h3>What happens if the strongest clue is removed?</h3><p>{api ? `Removes the strongest supporting clue of the selected lead (${selectedLead?.address || '—'}) and re-runs the engine. ${m.analysis.case_falsification.brittle ? 'Single-evidence checks show the leading conclusion is brittle.' : 'No single supporting record flips the leading conclusion on its own.'}` : 'Disable the forwarding latency clue to test how sensitive the ranking is to one calculated feature.'}</p></div><div className="falsification-action"><div className="sensitivity-toggle"><div><strong>{api ? 'Strongest supporting clue' : 'Forwarding latency clue'}</strong><small>{api ? (sens.evidenceId ? sens.evidenceId.slice(0, 28) : 'Calculated or source-reported') : 'Calculated · FEAT-TIME-09'}</small></div><button className={`switch ${clueEnabled?'on':''}`} onClick={()=>setClueEnabled(!clueEnabled)} aria-label="Toggle clue"><span/></button></div><div className="rank-change"><span>{api ? `Selected lead · ${leadBefore?.id || '—'} review score` : 'Leading review score'}</span><strong>{api ? (leadBefore ? leadBefore.score : '—') : (clueEnabled?'78':'59')} <ArrowRight size={15}/> {api ? (sens.status === 'loading' ? '…' : (leadAfterScore ?? '—')) : (clueEnabled?'78':'59')}</strong></div></div></section>
  </>;
}
function EvaluationPage() {
  const { m, astraApi: api } = useAstra();
  const live = m.source === 'api';
  const [ev, setEv] = useState({ status: live ? 'loading' : 'idle', data: null, error: null });
  const load = useCallback(async (refresh) => {
    setEv((x) => ({ ...x, status: 'loading' }));
    try { setEv({ status: 'ready', data: await api.getEvaluation(refresh), error: null }); }
    catch (error) { setEv({ status: 'error', data: null, error }); }
  }, [api]);
  useEffect(() => { if (live) load(false); }, [live, load]);
  const a = ev.data?.aggregate;
  const p = (v) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
  const metrics = live ? (a ? [['ASTRA precision@10', p(a.astra_precision_at_10), `n=${a.incident_scenarios} incident scenarios`], ['Baseline precision@10', p(a.baseline_precision_at_10), 'BFS hop-distance baseline'], ['Service hub in top 10', `${p(a.astra_service_in_top10_rate)} vs ${p(a.baseline_service_in_top10_rate)}`, 'ASTRA vs baseline (lower is better)'], ['Case hypothesis accuracy', p(a.case_hypothesis_accuracy), `n=${a.case_hypothesis_n} scenarios`], ['Benign false-positive rate', p(a.benign_false_positive_rate), `n=${a.benign_scenarios} benign scenarios`]] : [['Evaluation', ev.status === 'loading' ? 'Running…' : '—', ev.error ? ev.error.message : 'Synthetic benchmark']])
    : [['Accuracy','97.31%','All classes'],['Illicit precision','89.72%','Predicted illicit'],['Illicit recall','47.06%','Actual illicit'],['Illicit F1','61.74%','Precision + recall'],['ROC-AUC','86.82%','Ranking quality']];
  const bars = live && a ? [['ASTRA precision@5', a.astra_precision_at_5, ''], ['Baseline precision@5', a.baseline_precision_at_5, 'violet-meter'], ['ASTRA average precision', a.astra_average_precision, 'amber-meter'], ['Baseline average precision', a.baseline_average_precision, 'green-meter']]
    : [['Accuracy', 0.9731, ''], ['Illicit precision', 0.8972, 'violet-meter'], ['Illicit recall', 0.4706, 'amber-meter'], ['ROC-AUC', 0.8682, 'green-meter']];
  return <>
    <PageHeader eyebrow="VALIDATE / EVALUATION LAB" title="Measure what actually works." description="Model results must be reproducible, time-aware, and interpreted in context—not used as a decorative confidence badge." actions={live ? <Button variant="secondary" icon={RefreshCw} onClick={() => load(true)}>Re-run benchmark</Button> : <span className="small-tag">PAST RUN · USER-PROVIDED METRICS</span>}/>
    <div className="evaluation-warning glass"><ShieldAlert size={17}/><div><strong>Evaluation caveat</strong><p>{live ? `Measured now on ${ev.data ? ev.data.scenarios.length : '…'} labelled SYNTHETIC scenarios (generator astra-sim-v1, seeds ${ev.data ? ev.data.seeds.join(', ') : '…'}). Controlled prototype evaluation — not real-world or Ethereum/DeFi attribution performance. Mean runtime ${a ? `${a.mean_runtime_s}s` : '…'} per scenario on this machine.` : 'These are the previously reported Elliptic++ Bitcoin benchmark results, not Ethereum/DeFi attribution performance. Graph features need a temporal-leakage audit.'}</p></div></div>
    <div className="metrics-grid evaluation-metrics">{metrics.map(([label,value,sub],i)=><div className="metric-card glass" key={label}><div className="metric-top"><span>{label}</span><span className="metric-icon"><Activity size={15}/></span></div><div className="metric-value">{value}</div><div className="metric-bottom"><span className={!live && i===2?'warning-text':'neutral'}>{sub}</span></div></div>)}</div>
    <div className="evaluation-grid"><section className="panel glass"><div className="panel-heading"><div><h3>{live ? 'Ranking quality vs baseline' : 'Class-level performance'}</h3><p>{live ? 'Same scenarios, same labels, same denominators' : 'Illicit class recall is the main limitation'}</p></div></div>{bars.map(([label, v, cls]) => <div className="bar-metric" key={label}><div><span>{label}</span><strong>{p(v)}</strong></div><div className={`meter-track ${cls}`}><span style={{width: v == null ? '0%' : `${(v * 100).toFixed(2)}%`}}/></div></div>)}</section><section className="panel glass"><div className="panel-heading"><div><h3>Next evaluation actions</h3><p>Improve validity before chasing a 99% headline</p></div></div><div className="checklist"><div><Check size={14}/><span>Keep chronological train / validation / test split</span></div><div><Check size={14}/><span>Audit graph features for future-edge leakage</span></div><div><Check size={14}/><span>Report precision-recall curve and class support</span></div><div><Check size={14}/><span>Compare against a simple baseline</span></div><div><Check size={14}/><span>Document threshold selection on validation only</span></div></div><div className="small-note">{live ? 'Synthetic scenarios are generated by the same team that built the engine; replace them with independently labelled real incidents before making claims.' : 'Never tune on the held-out test set. A 99% target must not be manufactured through leakage or test-set optimization.'}</div></section></div>
  </>;
}
function ReportsPage({onNotice,onExport,onPreview}) {
  const { m, astraApi: api } = useAstra();
  const live = m.source === 'api';
  const reports = live ? [[`${m.caseId} Casebook`, `Source-linked investigation report · JSON · snapshot ${m.snapshot}`, `As of ${utcTime(m.analysis.meta.as_of)} UTC`, 'JSON', 'casebook'], [`${m.caseId} Casebook (printable)`, 'HTML rendering of the same report · print / save as PDF', `As of ${utcTime(m.analysis.meta.as_of)} UTC`, 'HTML', 'html'], ['Evaluation run', 'Synthetic benchmark: metrics, seeds, baseline, runtime · JSON', 'Computed on request', 'JSON', 'evaluation']]
    : [['CASE-042 Executive Casebook','Source-linked investigation summary · PDF','Updated 02:24 UTC','PDF'],['Temporal Analysis Appendix','Feature definitions and event windows · JSON','Updated 02:21 UTC','JSON'],['Evaluation Run E-018','Metrics, split boundaries, model version · JSON','Updated 01:58 UTC','JSON']];
  const download = async (r) => {
    if (!live) { downloadJson(r[0].toLowerCase().replace(/[^a-z0-9]+/g, '-'), { title: r[0], description: r[1], updated: r[2], format: r[3], caseId: 'CASE-042', dataMode: 'UI FIXTURE', note: 'Sample artifact metadata; not a backend result.' }); return; }
    try {
      if (r[4] === 'casebook') { await onExport(); }
      else if (r[4] === 'html') { onPreview(); }
      else { downloadJson('astra-evaluation', await api.getEvaluation(false)); onNotice('Evaluation results exported.'); }
    } catch (error) { onNotice(`Download failed: ${error.message}`); }
  };
  return <>
    <PageHeader eyebrow="DELIVER / CASEBOOK" title="Turn analysis into evidence." description="Package the investigation so another analyst can reproduce the path, review the sources, and understand the limitations." actions={<Button icon={Download} onClick={onExport}>Generate casebook</Button>}/>
    <div className="report-banner glass"><div className="report-graphic"><BookOpen size={25}/><span><Check size={12}/></span></div><div><span className="eyebrow tiny">CASEBOOK BUILDER</span><h2>One defensible story. Every source attached.</h2><p>Export case scope, observed paths, claim/evidence links, contradictory clues, method version, and benchmark caveats.{live ? ` Data: ${m.dataLabel}.` : ''}</p></div><Button onClick={onPreview} icon={ArrowRight}>Preview report</Button></div>
    <section className="panel glass reports-list"><div className="panel-heading"><div><h3>{live ? 'Available exports' : 'Recent artifacts'}</h3><p>{live ? 'Generated from the same analysis shown on screen' : 'Available report fixtures for this demo case'}</p></div><span className="small-tag">{reports.length} ARTIFACTS</span></div>{reports.map(r=><div className="report-row" key={r[0]}><div className="report-icon"><FileSearch size={17}/></div><div><strong>{r[0]}</strong><span>{r[1]}</span><small>{r[2]}</small></div><span className="file-type">{r[3]}</span><button className="icon-button" onClick={() => download(r)} aria-label={`Download ${r[0]}`}><Download size={15}/></button></div>)}</section>
  </>;
}
function SettingsPage({theme,setTheme,onNotice}) {
  const { m, hv, data, astraApi: api } = useAstra();
  const live = m.source === 'api';
  const [reducedMotion,setReducedMotion] = useState(false);
  const [compact,setCompact] = useState(false);
  const [srcKind, setSrcKind] = useState('scenario');
  const [seed, setSeed] = useState('42');
  const [simKind, setSimKind] = useState('incident');
  const [wallet, setWallet] = useState('');
  const [busy, setBusy] = useState(false);
  const systems = live ? [['Ethereum data provider', hv.items[0][1], hv.items[0][2] === 'ok' ? 'green' : 'amber'], ['GitHub public API', hv.items[2][1], hv.items[2][2] === 'ok' ? 'green' : 'amber'], ['OSINT source registry', hv.items[1][1], hv.items[1][2] === 'ok' ? 'green' : 'amber'], ['ASTRA quantitative engine', data.health?.engine?.version || 'unknown', 'violet']]
    : [['Ethereum data provider','Healthy','green'],['GitHub public API','Rate-limited','amber'],['OSINT feed registry','Healthy','green'],['ASTRA quantitative engine','Demo fixture','violet']];
  const open = async () => {
    if (!live) { onNotice('Backend data source not selected (VITE_ASTRA_DATA_SOURCE=fixtures).'); return; }
    setBusy(true);
    try {
      let id;
      if (srcKind === 'scenario') id = data.scenarios[0]?.investigation_id;
      else if (srcKind === 'simulation') { const n = Number(seed); if (!Number.isInteger(n) || n < 0) throw new Error('Seed must be a non-negative integer.'); id = (await api.createSimulation({ seed: n, scenario_kind: simKind })).investigation_id; }
      else { if (!/^0x[0-9a-fA-F]{40}$/.test(wallet.trim())) throw new Error('Enter a 0x + 40 hex character address.'); id = (await api.createInvestigation({ chain_id: 1, address: wallet.trim(), max_transfers: 100 })).investigation_id; }
      await data.load(id);
      onNotice(`Opened ${id}.`);
    } catch (error) { onNotice(`${error.code ? `${error.code}: ` : ''}${error.message}`); }
    finally { setBusy(false); }
  };
  return <>
    <PageHeader eyebrow="SYSTEM / SETTINGS & SOURCE HEALTH" title="Tune your workspace." description="Configure the investigation experience and understand which parts of this build are connected versus simulated."/>
    <div className="settings-grid"><section className="panel glass settings-panel"><div className="panel-heading"><div><h3>Appearance</h3><p>Personalize your analyst console</p></div><Sun size={16}/></div><div className="setting-row"><div><strong>Interface theme</strong><small>Switch between light and dark workspaces</small></div><div className="segmented"><button className={theme==='light'?'active':''} onClick={()=>setTheme('light')}><Sun size={14}/>Light</button><button className={theme==='dark'?'active':''} onClick={()=>setTheme('dark')}><Moon size={14}/>Dark</button></div></div><div className="setting-row"><div><strong>Reduced motion</strong><small>Reduce ambient and transition animations</small></div><button className={`switch ${reducedMotion?'on':''}`} onClick={()=>{setReducedMotion(!reducedMotion);document.body.classList.toggle('reduced-motion',!reducedMotion)}}><span/></button></div><div className="setting-row"><div><strong>Compact data density</strong><small>Condense tables for high-volume review</small></div><button className={`switch ${compact?'on':''}`} onClick={()=>{setCompact(!compact);document.body.classList.toggle('compact-mode',!compact)}}><span/></button></div></section><section className="panel glass settings-panel"><div className="panel-heading"><div><h3>Connected systems</h3><p>{live ? `Backend health · ${data.health?.degraded_mode ? 'degraded (offline demo available)' : 'all configured'}` : 'Demo health status · no credentials configured'}</p></div><Radio size={16}/></div>{systems.map(s=><div className="system-row" key={s[0]}><span className={`system-dot ${s[2]}`}/><div><strong>{s[0]}</strong><small>Provider status</small></div><span className={`system-state ${s[2]}`}>{s[1]}</span></div>)}</section></div>
    <section className="panel glass settings-panel security-settings"><div className="panel-heading"><div><h3>Investigation source</h3><p>Choose the data mode. Synthetic and live data are never mixed; every view shows the active mode ({m.dataLabel}).</p></div><Database size={16}/></div><div className="setting-row"><div><strong>Data mode</strong><small>Demo scenario and simulation are SYNTHETIC; a wallet query uses the live provider (server-side key required).</small></div><div className="segmented"><button className={srcKind==='scenario'?'active':''} onClick={()=>setSrcKind('scenario')}>Demo scenario</button><button className={srcKind==='simulation'?'active':''} onClick={()=>setSrcKind('simulation')}>Simulation</button><button className={srcKind==='wallet'?'active':''} onClick={()=>setSrcKind('wallet')}>Live wallet</button></div></div>{srcKind==='simulation' && <div className="setting-row"><div><strong>Simulation</strong><small>Deterministic generator: the same seed and kind always produce the same records and results.</small></div><div className="source-filters"><div className="search-box"><Search size={14}/><input aria-label="Simulation seed" value={seed} onChange={e=>setSeed(e.target.value)} placeholder="Seed"/></div><div className="select-wrap"><Filter size={14}/><select aria-label="Scenario kind" value={simKind} onChange={e=>setSimKind(e.target.value)}><option value="incident">incident</option><option value="benign">benign</option><option value="insufficient">insufficient</option></select><ChevronDown size={13}/></div></div></div>}{srcKind==='wallet' && <div className="setting-row"><div><strong>Wallet address</strong><small>Bounded one-hop query (max 100 transfers). Without ETHERSCAN_API_KEY the backend returns an explicit provider error — never synthetic data.</small></div><div className="search-box"><Search size={14}/><input aria-label="Wallet address" value={wallet} onChange={e=>setWallet(e.target.value)} placeholder="0x…"/></div></div>}<div className="setting-row"><div><strong>Current investigation</strong><small className="mono">{m.caseId} · {m.dataLabel}</small></div><Button icon={Play} onClick={open} disabled={busy}>{busy ? 'Opening…' : 'Open investigation'}</Button></div></section>
    <section className="panel glass settings-panel security-settings"><div className="panel-heading"><div><h3>Security & data handling</h3><p>Minimum controls for a forensic intelligence product</p></div><LockKeyhole size={16}/></div><div className="security-list"><div><ShieldCheck size={16}/><span>Keep provider secrets in backend environment variables; never in browser bundles.</span></div><div><ShieldCheck size={16}/><span>Label simulated, cached, and live data modes explicitly.</span></div><div><ShieldCheck size={16}/><span>Retain source URLs, retrieval timestamps, and extraction methods.</span></div><div><ShieldCheck size={16}/><span>Use bounded queries, rate limits, and safe output encoding for source text.</span></div></div></section>
  </>;
}


function LandingPage({ onEnter }) {
  const [typed, setTyped] = useState('');
  const [launching, setLaunching] = useState(false);
  const handleEnter = () => {
    if (launching) return;
    setLaunching(true);
    window.setTimeout(() => onEnter(), 680);
  };
  const brand = 'ASTRA';
  useEffect(() => {
    let i = 0;
    const timer = window.setInterval(() => {
      i += 1;
      setTyped(brand.slice(0, i));
      if (i >= brand.length) window.clearInterval(timer);
    }, 180);
    return () => window.clearInterval(timer);
  }, []);
  return <main className="astra-landing">
    <div className="landing-burst" aria-hidden="true">
      <PrismaticBurst colors={["#7c3aed", "#a855f7", "#6366f1"]} animationType="rotate3d" intensity={2} speed={0.5} zoom={1} rayWidth={1} twist={0} mouseInteraction clickBurst intro quality="high" glow={0.3} fade={0.12} />
    </div>
    <div className="landing-vignette" aria-hidden="true" />
    <header className="landing-topbar">
      <a className="landing-brand-mark" href="#home" aria-label="ASTRA home" onClick={e=>e.preventDefault()}><span className="landing-mark"><Hexagon size={23} strokeWidth={1.6}/><span className="landing-mark-core"/></span><span className="landing-brand-word">ASTRA<span>.</span><small>INTELLIGENCE ENGINE</small></span></a>
      <div className="landing-status"><span className="status-live-dot"/> SYSTEM ONLINE <span className="landing-status-divider"/> QUANTITATIVE OSINT</div>
    </header>
    <section className="landing-hero" id="home">
      <div className="landing-kicker"><span className="landing-kicker-line"/> INTELLIGENCE, CONNECTED</div>
      <h1 className="landing-title"><span className="landing-type-line" aria-label="ASTRA">{typed}<span className="landing-caret"/></span><span className="landing-title-sub">See beyond the transaction.</span></h1>
      <p className="landing-description">Trace the movement. Connect the signals. Follow the evidence.</p>
      <p className="landing-copy">A unified intelligence workspace for blockchain investigations and public-source threat signals — designed to turn fragmented clues into explainable leads.</p>
      <button className={`landing-enter-dock${launching ? ' is-launching' : ''}`} onClick={handleEnter} aria-label="Enter ASTRA dashboard" disabled={launching}>
        <span className="landing-enter-icon"><ArrowRight size={21} strokeWidth={1.8}/></span>
        <span className="landing-enter-label"><strong>{launching ? 'Opening workspace…' : 'Enter workspace'}</strong><small>OPEN INVESTIGATION CONSOLE</small></span>
        <span className="landing-enter-shortcut">↵</span>
      </button>
      <div className="landing-footnote"><ShieldCheck size={15}/> Evidence-led intelligence <span/> No identity claims without corroboration</div>
    </section>
    <footer className="landing-footer"><span>ASTRA / GHOST ATTACKS</span><span className="landing-footer-center"><i/> OSINT <b>×</b> ON-CHAIN GRAPH</span><span>BUILT FOR INVESTIGATION</span></footer>
    <div className="landing-orbit orbit-one" aria-hidden="true"/><div className="landing-orbit orbit-two" aria-hidden="true"/>
  </main>;
}

function speakText(text, language, setSpeaking) {
  if (!('speechSynthesis' in window)) { window.alert('Text-to-speech is not supported in this browser.'); return; }
  const cleanText = String(text || '').trim();
  if (!cleanText) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(cleanText);
  utterance.lang = language;
  utterance.rate = 0.95;
  utterance.onstart = () => setSpeaking(true);
  utterance.onend = () => setSpeaking(false);
  utterance.onerror = () => setSpeaking(false);
  window.speechSynthesis.speak(utterance);
}

function CursorAura() { return null; }
createRoot(document.getElementById('root')).render(<App/>);
