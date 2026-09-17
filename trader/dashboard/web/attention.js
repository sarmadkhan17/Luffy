/* This panel remains independent of trading/account dashboard requests. */
(() => {
  const status = document.getElementById('attention-status');
  const content = document.getElementById('attention-content');
  let busy = false;
  function line(parent, text, tag = 'p') {
    const element = document.createElement(tag);
    element.textContent = text;
    parent.appendChild(element);
    return element;
  }
  function render(data) {
    content.replaceChildren();
    status.textContent = `Attention diagnostics: ${data.status || 'unknown'}`;
    const health = data.health || {};
    if (data.execution_recovery) {
      const r = data.execution_recovery;
      line(content, `Execution recovery: new entries blocked · ${r.symbol || 'unknown symbol'} · ${r.reason || 'unresolved'} · attempts ${r.attempts || 0}. Existing operator controls remain in effect.`, 'strong');
    }
    line(content, `Collector: ${data.collector_status || health.status || 'unknown'} · queue ${health.queue_depth ?? 'unknown'} · drops ${health.dropped ?? 'unknown'} · errors ${health.errors ?? 'unknown'}`);
    if (health.last_error || health.kernel_error) line(content, `Last error: ${health.kernel_error || health.last_error}`);
    const learning = data.learning;
    if (learning) {
      const box = document.createElement('details'); content.appendChild(box);
      const counts = learning.counts || {};
      line(box, `Forward learning: ${learning.status} · pending ${counts.pending || 0} · resolved ${counts.resolved || 0} · unavailable ${counts.unavailable || 0}`, 'summary');
      line(box, 'Competing price forecasts: continuation, reversal, or unresolved. Probabilities are uncalibrated. These observations are not P&L or permission to trade.');
      if (learning.reason) line(box, learning.reason);
      for (const row of learning.recent || []) {
        line(box, `${row.symbol} · ${row.selected ? 'attention selected' : 'attention ignored'} · recent direction ${row.direction > 0 ? 'up' : 'down'} · ${row.status} · target close ${new Date(row.deadline_ms).toISOString()}${row.supports ? ' · supports ' + row.supports : ''} · source ${row.scan_id}`);
      }
      line(box, `Descriptive outcomes: ${JSON.stringify(learning.descriptive_outcomes || {})}. Correlated samples; no statistical edge is established.`);
    }
    const scan = data.scan;
    if (!scan) return;
    line(content, `Scan ${scan.scan_id} · observed ${new Date(scan.as_of_ms).toISOString()} · age ${Math.round(data.age_seconds || 0)}s`);
    const scope = scan.scope || {};
    line(content, `Scope: ${scope.included_count ?? '?'} of ${scope.candidate_count ?? '?'} scan candidates; ${scope.excluded_count ?? '?'} excluded by cap. Venue-wide coverage is not established.`);
    line(content, `Decision receipts: ${data.causes_complete ? 'received (subject to caps)' : 'pending or unavailable'}`);
    for (const row of scan.rows || []) {
      const detail = document.createElement('details'); content.appendChild(detail);
      line(detail, `${row.symbol} · ${row.reason} · salience ${row.salience ?? 'unknown'}`, 'summary');
      line(detail, JSON.stringify({components: row.components, evidence: row.evidence}), 'pre');
    }
    const receipts = document.createElement('details'); content.appendChild(receipts);
    line(receipts, 'Analyst and strategy evaluation receipts', 'summary');
    for (const cause of data.causes || []) {
      line(receipts, `${cause.symbol} · ${cause.action || cause.reason} · executed ${cause.executed ?? 'unknown'} · blocked ${cause.blocked ?? 'unknown'} · entries allowed ${cause.entry_allowed ?? 'unknown'} · decision ${cause.decision_id || 'none'} · omitted evaluations ${cause.omitted_causes || 0}`);
      for (const item of cause.evaluations || []) {
        line(receipts, `${item.component} ${item.component_id}: ${item.reason}${item.error_type ? ' (' + item.error_type + ')' : ''}${item.meaning ? ' — ' + item.meaning : ''}`);
      }
    }
    const evidence = document.createElement('details'); content.appendChild(evidence);
    line(evidence, 'Captured evidence and limitations', 'summary');
    const pre = line(evidence, JSON.stringify({issues: scan.issues, observations: scan.observations, limitations: scan.limitations, health}, null, 2), 'pre');
    pre.style.whiteSpace = 'pre-wrap';
  }
  async function refreshAttention() {
    if (busy) return;
    busy = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch('/api/attention/latest' + location.search, {signal: controller.signal, cache: 'no-store'});
      if (!response.ok) throw new Error('unavailable');
      render(await response.json());
    } catch (_) {
      status.textContent = 'Attention diagnostics: unavailable (previous display may be stale)';
    } finally { clearTimeout(timeout); busy = false; }
  }
  refreshAttention(); setInterval(refreshAttention, 8000);
})();
