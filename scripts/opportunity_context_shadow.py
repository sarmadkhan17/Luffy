#!/usr/bin/env python3
"""Bounded current-evidence Stage-6 shadow. No venue calls or Journal writes."""
import argparse
from contextlib import ExitStack
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trader.portfolio import opportunity_live as live, economics as econ
from trader.portfolio.opportunity_registry import Registry
from trader.portfolio.allocator import (Source, Evidence, Status, Inputs, Portfolio, Position,
                                        allocate, canonical, persist, verify, inputs_from_payload)
from trader.engine.evidence_capture import verify_snapshot
from trader.engine.protection_snapshot import STALE_AFTER_S

PACKAGE = 'LUFFY-OPPORTUNITY-CONTEXT-LIVE-INTEGRATION-R1'
DIMENSIONS = dict(units='USDT', quantity_basis='UNAVAILABLE', capital_basis='UNAVAILABLE',
                  horizon_interpretation='UNAVAILABLE', cost_treatment='UNAVAILABLE',
                  uncertainty_treatment='UNAVAILABLE', freshness_semantics='source_authority_expiry')


def _read(stack, path, deadline):
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.2)
    stack.callback(db.close)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    db.execute('BEGIN')
    return db


def _tables(db):
    return set(json.loads(db.execute("SELECT json_group_array(name) FROM sqlite_master WHERE type='table'").fetchone()[0]))


def _json(text):
    if len(text.encode()) > 2 * 1024**2:
        raise ValueError('SOURCE_PAYLOAD_BOUND_EXCEEDED')
    return json.loads(text)


def capture(journal, attention, investigation, config, max_contexts=4):
    """Fixed row/payload bounds and five-second SQL deadline. No history scan."""
    if not 1 <= max_contexts <= 16:
        raise ValueError('CONTEXT_BOUND_INVALID')
    deadline = time.monotonic() + 5
    missing = []
    with ExitStack() as stack:
        db = _read(stack, journal, deadline)
        names = _tables(db)
        kv = dict(db.execute("SELECT key,value FROM state_kv WHERE key IN ('venue_position_snapshot','control_state')"))
        snapshot = _json(kv.get('venue_position_snapshot', 'null'))
        if verify_snapshot(snapshot) is None:
            raise ValueError('VENUE_PORTFOLIO_UNVERIFIED')
        versions = [dict(r) for r in db.execute('SELECT * FROM strategy_versions LIMIT 65')] if 'strategy_versions' in names else []
        if len(versions) > 64:
            raise ValueError('VERSION_READ_BOUND_EXCEEDED')
        authority_inventory = {}
        authority_tables = {'validation': 'strategy_validation_receipts', 'probation': 'strategy_probation_receipts',
                            'installs': 'strategy_version_installs', 'capacity': 'strategy_capacity_receipts',
                            'events': 'strategy_version_events', 'governor': 'strategy_governor_events'}
        for version in versions:
            inventory = {}
            for role, table in authority_tables.items():
                if table in names:
                    rows = [dict(r) for r in db.execute('SELECT * FROM ' + table + ' WHERE version_id=? LIMIT 65', (version['version_id'],))]
                    if len(rows) > 64:
                        raise ValueError('STRATEGY_AUTHORITY_READ_BOUND_EXCEEDED')
                    inventory[role] = rows
            authority_inventory[version['version_id']] = inventory
        # Read current stored decision occurrences, never turn historical
        # decisions/PF into current opportunities or synthesize signals.
        columns = set(json.loads(db.execute("SELECT json_group_array(name) FROM pragma_table_info('decisions')").fetchone()[0]))
        decisions = [dict(r) for r in db.execute('SELECT id,cycle_id,ts,symbol,signals_json FROM decisions ORDER BY ts DESC LIMIT 32')] if versions and 'signals_json' in columns else []
        selections = [dict(r) for r in db.execute('SELECT * FROM attention_selections ORDER BY cycle_as_of_ms DESC LIMIT 16')] if 'attention_selections' in names else []
        scan = None
        if attention.exists():
            ad = _read(stack, attention, deadline)
            from trader.observability.scan_source import latest
            from trader.observability.attention import settings
            scan = latest(ad, deadline, max_bytes=settings(config.get('attention'))['max_bytes']//2)
        else:
            missing.append('ATTENTION_STORE_MISSING')
        cases, allocations, updates = [], [], {}
        if investigation.exists():
            inv = _read(stack, investigation, deadline)
            tables = _tables(inv)
            if 'cases' in tables:
                # Bound the same ordered current rows, avoiding a scheduling
                # handoff for each SQLite cursor row under background load.
                payloads = json.loads(inv.execute('SELECT json_group_array(payload) FROM '
                    '(SELECT payload FROM cases ORDER BY created_ms DESC LIMIT 32)').fetchone()[0])
                cases = [_json(payload) for payload in payloads]
            if 'updates' in tables and cases:
                # Choose each case's exact latest revision, including rowid
                # ties, in one bounded statement instead of 32 separate reads.
                ids = json.dumps([case['investigation_id'] for case in cases])
                rows = json.loads(inv.execute('SELECT json_group_array(json_array(case_id,payload)) '
                    'FROM updates WHERE rowid IN (SELECT (SELECT u.rowid FROM updates u '
                    'WHERE u.case_id=ids.value ORDER BY u.observed_ms DESC,u.rowid DESC LIMIT 1) '
                    'FROM json_each(?) AS ids)', (ids,)).fetchone()[0])
                updates = {cid: _json(payload) for cid, payload in rows}
            if 'allocation_decisions' in tables and scan:
                allocations = [_json(r[0]) for r in inv.execute('SELECT payload FROM allocation_decisions WHERE scan_id=? LIMIT 2', (scan['scan_id'],))]
        else:
            missing.append('INVESTIGATION_STORE_MISSING')
        cut = time.time_ns() // 1_000_000  # evidence already read, never a pre-read availability claim
        source_set = Source.freeze('current-shadow-inventory', dict(versions=versions, decisions=decisions,
                                    selections=selections, scan=scan, cases=cases, allocations=allocations, updates=updates,
                                    strategy_authority_inventory=authority_inventory,
                                    venue_snapshot=snapshot, control_state=kv.get('control_state', 'UNKNOWN')))
    sources = []
    if scan:
        sources.append(live.source('attention_scan', scan, scan['persisted_at_ms'],
                       scan['as_of_ms'] + int(config.get('attention', {}).get('stale_seconds', 300) * 1000)))
        if scan.get('world_model', {}).get('status') == 'ok':
            rec = scan['world_model']
            from trader.world.replay import WorldModelRecord
            world = WorldModelRecord.from_json(rec['record_json'])
            if (rec['model_id'], rec['record_id'], rec['as_of_ms']) != (world.model_id, world.record_id, scan['as_of_ms']):
                raise ValueError('WORLD_MODEL_RECEIPT_MISMATCH')
            sources.append(live.source('world_model', rec['record_json'], scan['persisted_at_ms'],
                                       scan['as_of_ms'] + int(config['attention']['stale_seconds'] * 1000)))
    if snapshot['observed_at_ms'] > cut:
        raise ValueError('FUTURE_PORTFOLIO_SNAPSHOT')
    sources.append(live.source('portfolio', snapshot, snapshot['received_at_ms'],
                              snapshot['observed_at_ms'] + int(STALE_AFTER_S * 1000)))
    requests = []
    # Current observed Attention rows are observable setups, not eligible
    # strategy trades. Without a current row there is no fabricated record.
    scan_current = scan and 0 <= cut - scan['as_of_ms'] <= int(config.get('attention', {}).get('stale_seconds', 300) * 1000)
    if scan_current:
        rows = sorted(scan['rows'], key=lambda r: (not bool(r.get('selected')), r.get('rank') or 999, r['symbol']))[:max_contexts]
        for row in rows:
            symbol = row['symbol']
            from trader.engine.protective import venue_key
            key = venue_key(symbol)
            selected = [s for s in selections if s['selected_symbol'] and venue_key(s['selected_symbol']) == key and s['recorded_at_ms'] <= cut]
            holdings = [p for p in snapshot['positions'] if p['symbol'] == key]
            iid = selected[0]['selected_id'] if selected else holdings[0]['instrument_id'] if holdings else None
            bound = list(sources)
            if selected:
                sel = selected[0]
                bound.append(live.source('registry_selection', _json(sel['canonical_json']), sel['recorded_at_ms'],
                                         sel['snapshot_as_of_ms'] + sel['max_snapshot_age_ms']))
            matching_cases = [c for c in cases if c.get('state', {}).get('scan_id') == scan['scan_id'] and venue_key(c['state']['symbol']) == key]
            if matching_cases:
                c = sorted(matching_cases, key=lambda c: c['investigation_id'])[0]
                bound.append(live.source('investigation', c, cut))
                if c['investigation_id'] in updates:
                    bound.append(live.source('investigation_update', updates[c['investigation_id']], cut))
                if len(allocations) == 1:
                    a = dict(allocations[0]); a.pop('reused', None)
                    bound.append(live.source('allocation', a, cut))
            # Only bind exact current occurrence/version when its decision is
            # at this Attention cut or later, on the same instrument. No
            # carry-forward of old decisions or invented episode semantics.
            matches = []
            for d in decisions:
                ts = int(datetime.fromisoformat(d['ts'].replace('Z', '+00:00')).timestamp() * 1000)
                if scan['as_of_ms'] <= ts <= cut and venue_key(d['symbol']) == key:
                    for sig in _json(d['signals_json']):
                        from trader.strategy.signal_occurrence import signal_occurrence
                        occurrence, _ = signal_occurrence(sig)
                        if occurrence and occurrence[-1] <= cut:
                            matches.append((d, sig, occurrence))
            # A selected record is never generalized to other signal setups.
            # Bound each complete directional exact-version occurrence separately.
            exact = []
            for d, sig, occurrence in matches:
                if occurrence[3] not in ('BUY', 'SELL'):
                    continue
                for v in versions:
                    if v['strategy_id'] == occurrence[0] and v['recorded_at_ms'] <= cut:
                        exact.append((d, sig, v))
            if exact:
                for d, sig, v in exact[:max_contexts - len(requests)]:
                    requests.append(dict(as_of_ms=cut, symbol=symbol, instrument_id=iid,
                        cycle_id=d['cycle_id'], candidate_id=d['id'] + ':' + v['version_id'],
                        sources=tuple(bound + [live.source('signals', [sig], cut),
                                               live.source('strategy_version', v, v['recorded_at_ms']),
                                               live.source('strategy_authority_inventory', authority_inventory[v['version_id']], cut)]),
                        required_roles=('attention_scan', 'signals', 'strategy_version')))
            else:
                requests.append(dict(as_of_ms=cut, symbol=symbol, instrument_id=iid,
                    cycle_id=scan['scan_id'], candidate_id=scan['scan_id'] + ':' + key,
                    sources=tuple(bound), required_roles=('attention_scan',)))
            if len(requests) >= max_contexts:
                break
    else:
        missing.append('CURRENT_ATTENTION_EVIDENCE_UNAVAILABLE')
    from trader.portfolio.source_adapter import contexts as stored_contexts, availability
    persisted = stored_contexts(config, journal.parent, cut, snapshot)
    read_at_ms = cut
    if persisted:
        requests = persisted[:max_contexts]
        cut = requests[0]['as_of_ms']
    details = dict(as_of_ms=cut, read_at_ms=read_at_ms, normal_persisted_contexts=len(persisted), version_count=len(versions), requests=len(requests),
                   scan_id=scan['scan_id'] if scan else None, current_attention=bool(scan_current),
                   source_delivery=availability(config, journal.parent),
                   missing_sources=missing, source_set_sha256=source_set.sha256,
                   read_only=True, authenticated_requests=0, production_mutations=0)
    if time.monotonic() > deadline:
        raise ValueError('SOURCE_READ_DEADLINE_EXCEEDED')
    return requests, snapshot, kv.get('control_state', 'UNKNOWN'), source_set, details


def run(journal, attention, investigation, config, output, max_contexts=4, *, candidate_bridge=False, available_inputs=None):
    output = output.resolve()
    inputs_paths = (journal.resolve(), attention.resolve(), investigation.resolve())
    # Artifacts/registry must remain outside all live source directories.
    if any(output == p.parent or p.parent in output.parents for p in inputs_paths):
        raise ValueError('SHADOW_OUTPUT_MUST_BE_OUTSIDE_PRODUCTION_STORAGE')
    requests, snapshot, control, inventory, detail = capture(journal, attention, investigation, config, max_contexts)
    output.mkdir(parents=True, exist_ok=True)
    registry = Registry(output / 'opportunities.db')
    contexts, cs, sources, refused = [], [], [inventory], []
    try:
        for request in requests:
            try:
                receipt = live.produce(**request)
                p = json.loads(receipt.payload_json)
                path = live.persist(receipt, output / 'contexts')
                entry = registry.observe(receipt.context, request['cycle_id'], request['candidate_id'], p['strategy_version_id'])
                sources.append(receipt.as_source())
                status = 'UNAVAILABLE'
                if p['strategy_version_id'] and p['instrument_id']:
                    if candidate_bridge:
                        from trader.portfolio.candidate_bridge import build
                        with ExitStack() as reads:
                            db = _read(reads, journal, time.monotonic() + 5)
                            class Reader:
                                source_root = journal.parent
                                def query(self, sql, params=()):
                                    return [dict(r) for r in db.execute(sql, params)]
                            c, er, frozen_sources = build(Reader(), receipt, config,
                                available_inputs=available_inputs, dimensions=DIMENSIONS)
                    else:
                        binding = live.economic_binding(receipt, **DIMENSIONS)
                        er = econ.build(econ.Inputs(binding, context=(receipt.as_source(),)))
                        c, frozen_sources = live.candidate(receipt, er)
                    econ.persist(er, output / 'economics')
                    cs.append(c)
                    sources.extend(frozen_sources)
                    status = json.loads(er.result_json)['economic_status']
                contexts.append(dict(context_id=p['context_id'], receipt_id=receipt.receipt_id,
                                     opportunity_id=entry['opportunity_id'], path=str(path),
                                     expected_economics=status, candidate_created=bool(p['strategy_version_id'] and p['instrument_id']),
                                     portfolio_status=p['portfolio_status'],
                                     interaction=p['existing_position_interaction'], replay='PASS'))
            except (ValueError, KeyError, TypeError) as exc:
                refused.append(str(exc))
    finally:
        registry.close()
    vs = Source.freeze('venue_position_snapshot', snapshot)
    sources.append(vs)
    known = Evidence(Status.ESTABLISHED, (vs.source_id,))
    portfolio = Portfolio(snapshot['snapshot_id'], snapshot['observed_at_ms'],
        snapshot['observed_at_ms'] + int(STALE_AFTER_S * 1000), known,
        tuple(Position(p['instrument_id'], snapshot['market_type'], p['side'].upper(), str(p['quantity']), None)
              for p in snapshot['positions']), (vs.source_id,))
    unique = {s.source_id: s for s in sources}
    if any(unique[s.source_id] != s for s in sources):
        raise ValueError('SOURCE_ID_COLLISION')
    unknown = Evidence(Status.UNKNOWN, ())
    ai = Inputs(detail['as_of_ms'], tuple(cs), portfolio, unknown, unknown, control, tuple(unique.values()))
    proposal = allocate(ai)
    if not verify(proposal, ai) or allocate(inputs_from_payload(json.loads(proposal.inputs_json))) != proposal:
        raise ValueError('ALLOCATION_REPLAY_REFUSED')
    proposal_path = persist(proposal, output / 'proposals')
    result = json.loads(proposal.result_json)
    detail.update(package='LUFFY-STAGE6-CANDIDATE-FEASIBILITY-BRIDGE-R1' if candidate_bridge else PACKAGE, status='PASS' if (contexts or candidate_bridge and detail['version_count'] == 0) and not refused else 'BLOCKED',
                  contexts=contexts, context_count=len(contexts), candidate_count=len(cs),
                  refused=refused, expected_economics='UNAVAILABLE', decision=result['decision'],
                  reason=result['reason'], proposal_id=proposal.proposal_id, proposal_path=str(proposal_path),
                  portfolio_position_count=len(portfolio.positions),
                  portfolio_fresh=portfolio.as_of_ms <= ai.as_of_ms <= portfolio.valid_until_ms,
                  replay='PASS', trading_behavior_changed=False)
    target = output / ('shadow-' + str(detail['as_of_ms']) + '.json')
    with target.open('x') as f:
        f.write(canonical(detail) + '\n')
    return detail


def main():
    import yaml
    p = argparse.ArgumentParser()
    p.add_argument('--journal', type=Path, required=True)
    p.add_argument('--attention', type=Path, required=True)
    p.add_argument('--investigation', type=Path, required=True)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--max-contexts', type=int, default=4)
    args = p.parse_args()
    # No config/env credentials enter artifacts; only existing Attention
    # freshness policy is used. Risk stays an independent unavailable gate.
    cfg = yaml.safe_load(args.config.read_text())
    config = {'attention': {'stale_seconds': cfg.get('attention', {}).get('stale_seconds', 300)}}
    try:
        print(canonical(run(args.journal, args.attention, args.investigation, config, args.output, args.max_contexts)))
    except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(canonical(dict(package=PACKAGE, status='BLOCKED', blocker=str(exc), read_only=True,
                             production_mutations=0, authenticated_requests=0)))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
