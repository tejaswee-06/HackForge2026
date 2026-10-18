const stamp = () => new Date().toISOString().replace(/[:.]/g, '-');

export function downloadText(filename, content, mime = 'text/plain;charset=utf-8') {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = 'none';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function downloadJson(name, data) {
  downloadText(`${name}-${stamp()}.json`, JSON.stringify(data, null, 2), 'application/json;charset=utf-8');
}

export function downloadCsv(name, rows) {
  const values = rows.map(row => row.map(value => {
    const text = value == null ? '' : String(value);
    return `"${text.replace(/"/g, '""')}"`;
  }).join(',')).join('\r\n');
  downloadText(`${name}-${stamp()}.csv`, `\uFEFF${values}`, 'text/csv;charset=utf-8');
}

export function openReportPreview(report) {
  const escape = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const w = window.open('', '_blank');
  if (!w) return false;
  try { w.opener = null; } catch { /* browser may lock opener */ }
  const body = escape(JSON.stringify(report, null, 2));
  w.document.write(`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ASTRA Casebook Preview</title><style>body{font:15px/1.6 system-ui,sans-serif;max-width:900px;margin:48px auto;padding:0 24px;color:#172536}h1{letter-spacing:-.03em}p{color:#526273}.flag{display:inline-block;padding:5px 9px;border:1px solid #aab7c4;border-radius:6px;font-size:12px;letter-spacing:.08em}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;border:1px solid #dfe5eb;padding:20px;border-radius:10px}button{padding:9px 14px;border:1px solid #aab7c4;border-radius:7px;background:white;cursor:pointer}</style></head><body><span class="flag">UI FIXTURE · NOT ANALYSIS OUTPUT</span><h1>ASTRA Casebook</h1><p>CASE-042 · Generated locally from the original frontend fixture (backend not used). Candidate leads are not identity attribution.</p><button onclick="window.print()">Print / Save as PDF</button><h2>Investigation snapshot</h2><pre>${body}</pre><p>Limitations: this preview contains demo fixtures; validate all source records and independently verify any proposed association.</p></body></html>`);
  w.document.close();
  return true;
}
