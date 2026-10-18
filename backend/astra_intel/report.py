"""Casebook / report export built ONLY from an engine analysis (the same object the UI shows).

JSON is the canonical export; HTML is a printable rendering in which every dynamic value is
HTML-escaped (source excerpts are untrusted content). No scores, labels or conclusions are added here.
"""
from __future__ import annotations

import html
from typing import Any

REPORT_VERSION = "astra-casebook-1.0.0"


def build_report(analysis: dict, case: dict, *, generated_at: str, benchmark: dict | None = None) -> dict[str, Any]:
    m = analysis["meta"]
    leads = analysis["leads"][: m["config"]["top_k"]]
    ev = {e["evidence_id"]: e for e in analysis["evidence"]}
    claims = analysis["claims"]
    return {
        "report_version": REPORT_VERSION, "product": "ASTRA", "generated_at": generated_at,
        "data_label": m["data_label"], "synthetic": m["synthetic"],
        "notice": ("SYNTHETIC / SIMULATED DATA - this casebook demonstrates the method and is not real-world intelligence."
                   if m["synthetic"] else "Contains live/cached public data. Candidate leads are not identity attribution."),
        "case": case,
        "analysis": {"snapshot_id": m["snapshot_id"], "engine_version": m["engine_version"], "method_versions": m["method_versions"],
                     "config": m["config"], "config_hash": m["config_hash"], "data_mode": m["data_mode"],
                     "cutoff": m["cutoff"], "as_of": m["as_of"], "replay_policy": m["replay_policy"],
                     "dataset_ref": m["dataset_ref"], "overrides": m["overrides"]},
        "executive_summary": _summary(analysis),
        "graph_stats": analysis["graph_stats"],
        "case_hypotheses": analysis["case_hypotheses"], "case_falsification": analysis["case_falsification"],
        "leads": [{k: L[k] for k in ("rank", "lead_id", "address", "title", "priority_score", "priority_tier", "status",
                                     "reasons", "missing_signals", "not_applicable_signals", "next_check",
                                     "support_evidence_ids", "contradiction_evidence_ids", "path", "fastest_route")}
                  | {"leading_hypothesis": L["hypotheses"]["leading"],
                     "components": [c for c in L["components"] if c["points"]]} for L in leads],
        "observed_paths": [{"lead_id": L["lead_id"], **(L["path"] or {})} for L in leads if L["path"]],
        "claims": claims,
        "evidence": [ev[i] for i in sorted({i for c in claims for k in ("support_evidence_ids", "contradiction_evidence_ids",
                                                                      "context_evidence_ids") for i in c[k]} | {
                         e["evidence_id"] for e in analysis["evidence"] if e["provenance"].get("case_level")}) if i in ev],
        "contradictions": [f for f in analysis["osint"]["findings"] if f["contradictory"]],
        "unresolved_boundaries": analysis["boundaries"],
        "source_register": analysis["osint"]["documents"],
        "sources_status": analysis["osint"]["sources"],
        "flow": analysis["flow"],
        "benchmark": benchmark,
        "limitations": analysis["limitations"],
    }


def _summary(a: dict) -> list[str]:
    m, gs, h = a["meta"], a["graph_stats"], a["case_hypotheses"]
    out = [f"Data: {m['data_label']}; {gs['edges']} observed transfers among {gs['nodes']} addresses "
           f"({gs['time_respecting_reachable']} reachable from the origin along time-ordered paths).",
           f"Leading case hypothesis: {h['leading']} ({next(x['title'] for x in h['hypotheses'] if x['id'] == h['leading'])}); "
           "scores are review scores, not probabilities."]
    if a["leads"]:
        L = a["leads"][0]
        out.append(f"Top candidate lead {L['lead_id']} ({L['address']}): priority {L['priority_score']}/100, "
                   f"status {L['status']}. Next check: {L['next_check']}")
    c = sum(1 for f in a["osint"]["findings"] if f["contradictory"])
    out.append(f"{len(a['osint']['documents'])} public-source documents analysed; {c} contradictory finding(s); "
               f"{len(a['boundaries'])} unresolved boundary item(s).")
    if m["cutoff"]:
        out.append(f"Historical replay at cutoff {m['cutoff']} (policy: {m['replay_policy']}); later records excluded.")
    return out


def render_html(report: dict) -> str:
    e = lambda v: html.escape("" if v is None else str(v), quote=True)
    rows = lambda items: "".join(items)
    lead_rows = rows(f"<tr><td>{e(L['rank'])}</td><td><code>{e(L['address'])}</code><br><small>{e(L['title'])}</small></td>"
                     f"<td>{e(L['priority_score'])}</td><td>{e(L['leading_hypothesis'])}</td><td>{e(L['status'])}</td>"
                     f"<td><ul>{rows(f'<li>{e(r)}</li>' for r in L['reasons'])}</ul></td><td>{e(L['next_check'])}</td></tr>"
                     for L in report["leads"])
    claim_rows = rows(f"<tr><td>{e(c['claim_id'])}</td><td>{e(c['subject'])} {e(c['predicate'])} {e(c['object'])}</td>"
                      f"<td>{e(c['status'])}</td><td>{e(', '.join(c['support_evidence_ids']))}</td>"
                      f"<td>{e(', '.join(c['contradiction_evidence_ids']))}</td></tr>" for c in report["claims"])
    ev_rows = rows(f"<tr><td>{e(x['evidence_id'])}</td><td>{e(x['status'])}</td><td>{e(x['title'])}<br><small>{e(x['interpretation'])}</small>"
                   f"{'<br><small>Source: <a href=' + chr(34) + e(x['provenance'].get('source_url')) + chr(34) + ' rel=' + chr(34) + 'noopener noreferrer' + chr(34) + '>' + e(x['provenance'].get('source_url')) + '</a></small>' if x['provenance'].get('source_url') else ''}"
                   f"{'<br><small>Excerpt: <q>' + e(x['provenance'].get('excerpt')) + '</q></small>' if x['provenance'].get('excerpt') else ''}"
                   f"</td><td>{e(x['reliability'])}</td></tr>" for x in report["evidence"])
    src_rows = rows(f"<tr><td>{e(d['source_name'])}</td><td>{e(d['source_type'])}</td><td>{e(d['url'])}</td>"
                    f"<td>{e(d['published_at'] or 'unknown')}</td><td>{e(d['retrieved_at'])}</td><td>{e(d['data_mode'])}</td>"
                    f"<td>{e(d['content_hash'])}</td></tr>" for d in report["source_register"])
    hyp = report["case_hypotheses"]
    hyp_rows = rows(f"<tr><td>{e(h['id'])}</td><td>{e(h['title'])}</td><td>{e(h['score'])}</td>"
                    f"<td>{e(', '.join(h['supporting_evidence_ids']))}</td><td>{e(', '.join(h['contradicting_evidence_ids']))}</td></tr>"
                    for h in hyp["hypotheses"])
    bnd = rows(f"<li>{e(b['type'])}: {e(b.get('detail'))}</li>" for b in report["unresolved_boundaries"])
    lim = rows(f"<li>{e(x)}</li>" for x in report["limitations"])
    summ = rows(f"<li>{e(x)}</li>" for x in report["executive_summary"])
    a = report["analysis"]
    bench = ""
    if report.get("benchmark"):
        b = report["benchmark"]
        bench = (f"<h2>Benchmark (controlled synthetic evaluation)</h2><p>{e(b.get('summary'))}</p>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ASTRA Casebook {e(report['case'].get('investigation_id'))}</title>
<style>body{{font:14px/1.55 system-ui,sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;color:#172536}}
table{{border-collapse:collapse;width:100%;margin:8px 0 20px}}td,th{{border:1px solid #d5dde5;padding:6px;vertical-align:top;text-align:left}}
th{{background:#f1f4f7}}code{{word-break:break-all}}.flag{{display:inline-block;padding:6px 10px;border:2px solid #b45309;color:#b45309;font-weight:700;letter-spacing:.06em}}
small{{color:#55657a}}</style></head><body>
<span class="flag">{e(report['data_label'])}</span>
<h1>ASTRA Casebook</h1><p>{e(report['notice'])}</p>
<p>Investigation <b>{e(report['case'].get('investigation_id'))}</b> · chain {e(report['case'].get('chain_id'))} ·
generated {e(report['generated_at'])} · snapshot {e(a['snapshot_id'])} · engine {e(a['engine_version'])} ·
cutoff {e(a['cutoff'] or 'none (all available data)')} · as of {e(a['as_of'])} · config {e(a['config_hash'])}</p>
<h2>Executive summary</h2><ul>{summ}</ul>
<h2>Competing hypotheses (review scores, not probabilities)</h2><p>Leading: <b>{e(hyp['leading'])}</b></p>
<table><tr><th>ID</th><th>Hypothesis</th><th>Score</th><th>Supporting</th><th>Contradicting</th></tr>{hyp_rows}</table>
<h2>Ranked candidate leads</h2><table><tr><th>#</th><th>Address</th><th>Priority</th><th>Leading</th><th>Status</th><th>Reasons</th><th>Next check</th></tr>{lead_rows}</table>
<h2>Claims</h2><table><tr><th>Claim</th><th>Statement</th><th>Status</th><th>Support</th><th>Contradiction</th></tr>{claim_rows}</table>
<h2>Evidence</h2><table><tr><th>ID</th><th>Status</th><th>Evidence</th><th>Reliability rubric</th></tr>{ev_rows}</table>
<h2>Unresolved boundaries</h2><ul>{bnd}</ul>
<h2>Source register</h2><table><tr><th>Source</th><th>Type</th><th>URL</th><th>Published</th><th>Retrieved</th><th>Mode</th><th>Content hash</th></tr>{src_rows}</table>
{bench}
<h2>Limitations</h2><ul>{lim}</ul>
<p><small>{e(report['report_version'])} · Candidate associations require human verification. ASTRA does not identify people.</small></p>
</body></html>"""
