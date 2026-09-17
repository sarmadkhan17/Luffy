/* Independent, authenticated read-only dossiers. All source text uses textContent. */
(() => {
  const status = document.getElementById('investigation-status');
  const content = document.getElementById('investigation-content');
  let busy = false;
  const names = {volume_anomaly:'volume anomaly', volatility_transition:'volatility transition',
    relative_return_divergence:'relative move', market_breadth:'market breadth',
    same_direction:'same-direction persistence', normalization:'return toward baseline', opposite_direction:'opposite-direction move'};
  function line(parent, text, tag='p') {
    const el = document.createElement(tag); el.textContent = text; parent.appendChild(el); return el;
  }
  function render(data) {
    content.replaceChildren(); status.textContent = `Investigations: ${data.status || 'unknown'}`;
    const h = data.health || {};
    line(content, `Open ${h.active ?? '?'} · retained ${h.cases ?? '?'} · latest scan ${h.source_scan_id || 'unavailable'}`);
    if (h.reason) line(content, `Consumer: ${h.reason}. Deferred work: ${JSON.stringify(h.skipped || {})}`);
    for (const row of data.cases || []) {
      const inv = row.investigation, updates = row.updates || [], last = updates[updates.length-1];
      const box = document.createElement('details'); content.appendChild(box);
      line(box, `${inv.state.symbol}: ${inv.question}`, 'summary');
      line(box, `Opened ${new Date(inv.registered_ms).toISOString()}. Primary question: ${names[inv.primary_trigger] || inv.primary_trigger}.`);
      line(box, 'Observed facts', 'strong');
      for (const d of inv.state.dimensions || []) {
        if (d.value !== null) line(box, `${names[d.name] || d.name}: ${d.value} (${d.status}).`);
      }
      line(box, `Conflicting evidence: ${(inv.state.contradictions || []).join(', ') || 'none recorded'}.`);
      line(box, `Unknown inputs: ${(inv.state.missing || []).join(', ')}. Volume and price alone cannot identify who acted or why.`);
      line(box, 'Competing observable paths; no calibrated probabilities', 'strong');
      const assessment = Object.fromEntries(last ? last.assessment : []);
      for (const a of inv.alternatives || []) {
        line(box, `${names[a.name] || a.name}: ${assessment[a.name] || a.status}. Test: ${a.prediction}. ${a.invalidator}.`);
      }
      if (last) {
        line(box, `Assessment at ${new Date(last.observed_ms).toISOString()}: ${last.evidence.reason}. ${last.rationale}`);
        line(box, `Next: ${last.next_action.kind}. ${last.next_action.test}${last.next_action.earliest_ms ? ' Earliest complete measurement: '+new Date(last.next_action.earliest_ms).toISOString() : ''}`);
        line(box, `Still unknown: ${last.evidence.explanatory_uncertainty}`);
      }
      const memory = row.memory || {}, ctx = memory.context;
      line(box, `Memory: ${memory.status || 'unavailable'}.`, 'strong');
      if (ctx) {
        const typed = ctx.typed_outcomes || {cases:[], audit:[]};
        line(box, `Frozen at registration: ${ctx.cases.length} investigation cases and ${typed.cases.length} price observations. ${ctx.limitation}`);
        for (const c of typed.cases) {
          const o = c.observation || {};
          const measured = o.measurement === 'observed_path_not_pnl'
            ? `${o.family}: ${o.winner}, score ${o.score}; registered observable alternative only`
            : `${o.price_change_bps} bps observed movement; ${o.supports}`;
          line(box, `${c.kind}: ${measured}. Known at ${new Date(Math.max(c.imported_ms, (typed.local_imports || {})[c.case_id] || 0)).toISOString()}. This is not P&L.`);
          for (const ref of c.links || []) line(box, `${ref.kind} reference: ${ref.id}; version ${ref.version}; available ${new Date(ref.available_ms).toISOString()}.`);
        }
        for (const c of ctx.cautions || []) line(box, `${c.case_id}: ${c.text}`);
        const r = (memory.reasoning || []).find(r => last && r.event_id === last.event_id);
        if (r && r.changed) line(box, `Evidence request with memory: ${r.with_memory.test}`);
        const audit = document.createElement('details'); box.appendChild(audit);
        line(audit, 'Memory inclusion and exclusion reasons', 'summary');
        for (const a of [...(ctx.audit || []), ...typed.audit]) line(audit, `${a.case_id}: ${a.reason}`);
      }
      const history = document.createElement('details'); box.appendChild(history);
      line(history, `What changed (${updates.length} retained updates)`, 'summary');
      for (const u of updates) line(history, `${new Date(u.observed_ms).toISOString()}: ${u.reason_codes.join(', ')}. ${JSON.stringify(u.previous_assessment)} → ${JSON.stringify(u.assessment)}`);
      const details = document.createElement('details'); box.appendChild(details);
      line(details, 'Sources and frozen measurement', 'summary');
      const pre = line(details, JSON.stringify({source:inv.state.scan_id, state:inv.state.state_id,
        evidence:inv.state.evidence_ids, rules:inv.state.dimensions, measurement:inv.measurement,
        version:inv.schema_version, code:inv.state.code_json, config:inv.state.config_id}, null, 2), 'pre');
      pre.style.whiteSpace = 'pre-wrap';
    }
  }
  async function refresh() {
    if (busy) return; busy = true;
    const controller = new AbortController(), timer = setTimeout(()=>controller.abort(),5000);
    try {
      const response = await fetch('/api/investigations/latest'+location.search,{signal:controller.signal,cache:'no-store'});
      if (!response.ok) throw Error('unavailable');
      render(await response.json());
    } catch (_) { status.textContent = 'Investigations: unavailable (previous display may be stale)'; }
    finally { clearTimeout(timer); busy = false; }
  }
  refresh(); setInterval(refresh, 8000);
})();
