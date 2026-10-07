"""Bounded current-source reader. No venue calls or trading-journal writes."""
from contextlib import ExitStack
from dataclasses import replace
import json
from pathlib import Path
import time

from scripts.opportunity_context_shadow import capture, _read, DIMENSIONS
from . import opportunity_live as live, candidate_bridge as bridge, common_factor
from .runtime_metrics import capture as metric_capture
from .allocator import Source, Evidence, Status, Portfolio, Position, Inputs, canonical
from trader.engine.protection_snapshot import STALE_AFTER_S


def receipt_sources(receipt):
    return tuple(Source(**s) for s in json.loads(receipt.payload_json)['sources'])


def admitted_requests(requests, market_snapshots, receipt):
    """Candidate omission cannot touch the independent venue holdings source."""
    if receipt is None: return []
    from trader.attention_admission import verify_receipt
    verify_receipt(receipt)
    out=[]
    for request in requests:
        market=next((snap for did,snap in market_snapshots.items()
                     if request['candidate_id']==did or request['candidate_id'].startswith(did+':')),None)
        context=getattr(market,'admission_context',None)
        if (market is not None and context and market.symbol in receipt['admitted_symbols']
                and context.get('receipt_id')==receipt['receipt_id']):
            out.append(request)
    return out


def freeze(journal, attention, investigation, config, available_inputs=None, market_snapshots=None, holdings_market_snapshots=None, admission_receipt=None):
    requests, snap, control, inventory, detail = capture(Path(journal),Path(attention),Path(investigation),config)
    # A persisted cut must still be current under the venue's own contract.
    common_factor.snapshot(Source.freeze('venue_position_snapshot',snap),
                           detail.get('read_at_ms',detail['as_of_ms']))
    candidates, sources, lineages, contexts, entry_contexts = [], [inventory], [], [], []
    market_snapshots = market_snapshots or {}
    holdings_market_snapshots = holdings_market_snapshots or {}
    if config.get('attention',{}).get('admission'):
        from trader.attention_admission import verify_receipt
        if admission_receipt is not None:
            verify_receipt(admission_receipt)
            sources.append(Source.freeze('attention-admission',dict(
                receipt_id=admission_receipt['receipt_id'],policy_version=admission_receipt['policy_version'],
                source_cut_ms=admission_receipt['source_cut_ms'],roles=admission_receipt['roles'],
                peer_cohort_id=admission_receipt['peer_cohort_id'])))
        # Missing admission means no new candidates. Whole-book truth remains
        # independently captured, so candidate omission cannot erase holdings.
        requests = admitted_requests(requests,market_snapshots,admission_receipt)

    with ExitStack() as stack:
        db = _read(stack,Path(journal),time.monotonic()+5)
        kv = dict(db.execute("SELECT key,value FROM state_kv WHERE key IN "
            "('venue_position_snapshot','account_margin_observation','account_observation','risk_state','risk_baseline_marker',"
            "'risk_state_corrupt_latch','risk_assessment','control_state')"))
        if json.loads(kv.get('venue_position_snapshot','null')) != snap or kv.get('control_state','UNKNOWN') != control:
            raise ValueError('CURRENT_WHOLE_BOOK_CHANGED_DURING_READ')
        class Reader:
            source_root = Path(journal).parent
            current_time_ms = detail.get('read_at_ms', detail['as_of_ms'])
            def query(self,sql,params=()):
                return [dict(r) for r in db.execute(sql,params)]
        observed = json.loads(inventory.payload_json)
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        versions = Reader().query('SELECT * FROM strategy_versions LIMIT 65') if 'strategy_versions' in names else []
        if versions != observed['versions']:
            raise ValueError('CURRENT_VERSION_INVENTORY_CHANGED_DURING_READ')
        if 'strategy_versions' not in names:
            raise ValueError('FACTORY_VERSION_SCHEMA_UNAVAILABLE')
        for request in requests:
            try:
                receipt = live.produce(**request)
            except live.ContextBlocked as exc:
                # A missing/stale REQUIRED input blocks this candidate only, explicitly;
                # integrity/cut refusals are not ContextBlocked and still abort.
                detail['missing_sources'].append('CONTEXT_BLOCKED:' + request['candidate_id'] + ':' + str(exc))
                continue
            raw = json.loads(receipt.payload_json)
            contexts.append(receipt.as_source())
            if raw['strategy_version_id'] and raw['instrument_id']:
                decision_id = request['candidate_id'].removesuffix(':' + raw['strategy_version_id'])
                market = market_snapshots.get(decision_id)
                observed_inputs = available_inputs
                if observed_inputs is None and market is not None:
                    observed_inputs = ['ohlcv'] if any(v is not None and len(v) for v in market.dfs.values()) else []
                candidate, economics, frozen = bridge.build(Reader(),receipt,config,
                    available_inputs=observed_inputs,dimensions=DIMENSIONS)
                if not raw.get('decision_grade'):
                    detail['missing_sources'].append('CONTEXT_BLOCKED:' + request['candidate_id'] + ':DECISION_GRADE_CONTEXT_REQUIRED')
                    continue
                decision = live.finalize_decision(receipt, economics, Reader.current_time_ms)
                if decision.status != live.BOUND:
                    # Real economics receipt bound to this exact context; costs are not
                    # established, so the decision-grade candidate is blocked, never defaulted.
                    detail['missing_sources'].append('CONTEXT_BLOCKED:' + request['candidate_id'] + ':' + decision.block_reason)
                    detail.setdefault('blocked_decisions', []).append(json.loads(decision.payload_json))
                    continue
                sources.append(decision.as_source())
                if market is not None:
                    binding_text = getattr(market, 'instrument_binding_json', None)
                    if binding_text:
                        from trader.engine.entry_authority import CAP_KEY, digest as capability_digest
                        capability = json.loads(binding_text)
                        row = db.execute('SELECT value FROM state_kv WHERE key=?',
                                         (CAP_KEY + market.symbol,)).fetchone()
                        if (row is None or json.loads(row[0]) != capability
                                or capability.get('instrument_id') != candidate.instrument
                                or capability.get('receipt_id') != capability_digest({k:v for k,v in capability.items() if k!='receipt_id'})):
                            raise ValueError('PROPOSAL_CAPABILITY_CHANGED_DURING_READ')
                        source = Source.freeze('entry-capability:' + candidate.instrument, capability)
                        if source not in sources:
                            sources.append(source)
                    from trader.strategy.spec import StrategySpec
                    from trader.strategy import exit_policy as exits
                    from trader.engine.versioned_exits import entry_contract
                    from trader.core.types import TF_MS, closed_bars
                    version = json.loads(next(json.loads(s.payload_json)['data']['canonical_json']
                        for s in receipt_sources(receipt) if s.source_id == 'strategy_version'))
                    spec = StrategySpec.from_dict(version['spec'])
                    sig = next(json.loads(s.payload_json)['data'][0] for s in receipt_sources(receipt) if s.source_id=='signals')
                    try:
                        ref, atr, entry_bar = entry_contract(spec,market,sig['params']['signal_bar_close_ms'])
                        levels,_ = exits.initialize(spec.exit,ref,atr,candidate.direction.lower(),entry_bar,TF_MS[spec.timeframe])
                        bars = closed_bars(market.df(spec.timeframe),spec.timeframe,detail['as_of_ms'])
                        geometry_source = Source.freeze('entry-geometry:' + candidate.opportunity_id,
                            dict(decision_id=decision_id,version_id=candidate.version_id,spec_hash=candidate.spec_hash,
                                 snapshot_ts=market.ts,price=ref,atr=atr,stop=levels.stop,
                                 bars=bars.to_json(orient='split',date_unit='ms'),signal=sig))
                        sources.append(geometry_source)
                        entry_contexts.append(dict(opportunity_id=candidate.opportunity_id,
                            version_id=candidate.version_id,spec_hash=candidate.spec_hash,
                            instrument=candidate.instrument,direction=candidate.direction,
                            portfolio_snapshot_id=snap['snapshot_id'],as_of_ms=detail['as_of_ms'],
                            source_ids=[geometry_source.source_id],price=ref,atr=atr,
                            side_risk_frac=abs(ref-levels.stop)/ref))
                    except (ValueError,KeyError,TypeError,AttributeError):
                        pass  # Missing exact geometry remains INCOMPLETE at Risk.
                authority = next(s for s in frozen if s.source_id.startswith('candidate-bridge:'))
                authority_result = json.loads(authority.payload_json)['result']
                if authority_result['capacity'].get('current'):
                    from trader.strategy import capacity, factory_handoff
                    if not capacity.check_current(bridge.ReadEvidence(Reader()),config,
                        factory_handoff.load_version(Reader(),candidate.version_id),
                        authority_result['capacity']['receipt_id'],now_ms=Reader.current_time_ms)['current']:
                        raise ValueError('CURRENT_CAPACITY_CUT_STALE')
                candidates.append(candidate)
                sources.extend(frozen)
                if candidate.freshness.status == Status.ESTABLISHED and candidate.valid_until_ms is not None:
                    lineages.append(bridge.freeze_lineage(Reader(),candidate.version_id,
                        as_of_ms=detail['as_of_ms'],valid_until_ms=candidate.valid_until_ms,
                        candidate_input=candidate,receipt=receipt,economic_receipt=economics))
        if versions and not candidates:
            detail['missing_sources'].append('CURRENT_VERSION_OPPORTUNITIES_UNAVAILABLE')
        opens = Reader().query("SELECT * FROM trades WHERE status='open' LIMIT 65")
        if len(opens)>64:
            raise ValueError('CURRENT_BOOK_READ_BOUND_EXCEEDED')
        closed = db.execute("SELECT COUNT(*) FROM trades WHERE status='closed'").fetchone()[0]
        metric = metric_capture(snapshot=snap, account_margin=json.loads(kv.get('account_margin_observation','null')),
            journal_positions=opens,risk_kv={k:kv[k] for k in ('risk_state','risk_baseline_marker','risk_state_corrupt_latch') if k in kv},
            config=config,as_of_ms=detail['as_of_ms'],closed_count=closed,
            risk_assessment=json.loads(kv.get('risk_assessment','null')),entry_contexts=entry_contexts,
            account_observation=json.loads(kv.get('account_observation','null')))
    venue = Source.freeze('venue_position_snapshot',snap)
    policy = Source.freeze('owner-risk-policy',{'risk':config['risk']})
    sources.extend((venue,policy,*contexts))
    portfolio = Portfolio(snap['snapshot_id'],snap['observed_at_ms'],
        snap['observed_at_ms']+int(STALE_AFTER_S*1000),Evidence(Status.ESTABLISHED,(venue.source_id,)),
        tuple(Position(p['instrument_id'],snap['market_type'],p['side'].upper(),str(p['quantity']),None) for p in snap['positions']),
        (venue.source_id,metric.source_id))
    unique = {s.source_id:s for s in sources}
    if any(unique[s.source_id]!=s for s in sources):
        raise ValueError('CURRENT_SOURCE_ID_COLLISION')
    unique[metric.source_id]=metric
    inputs = Inputs(detail['as_of_ms'],tuple(candidates),portfolio,Evidence(Status.UNKNOWN,()),
        Evidence(Status.ESTABLISHED,(policy.source_id,)),control,tuple(unique.values()))
    inputs,context = common_factor.attach(inputs,venue,policy,tuple(lineages),measurement_sources=(metric,))
    from trader.learning.consumers import retained
    from .allocator import attach_learning
    with retained(journal) as learned:
        if learned is not None:
            inputs = attach_learning(inputs, learned)
    holding_observations = {symbol:dict(symbol=symbol,ts=market.ts,price=market.price,
        category='exposure_required',admission_context=getattr(market,'admission_context',None))
        for symbol,market in holdings_market_snapshots.items()}
    if holding_observations:
        holding_source=Source.freeze('held-market-observations',holding_observations)
        inputs=replace(inputs,sources=inputs.sources+(holding_source,))
    detail.update(holdings_market_observations=holding_observations,
        admission_receipt_id=admission_receipt.get('receipt_id') if admission_receipt else None,
        context_count=len(contexts),candidate_count=len(candidates),
        holdings_count=len(snap['positions']),portfolio_snapshot_id=snap['snapshot_id'],
        common_factor=json.loads(context.result_json), economics='UNAVAILABLE' if not candidates else 'SEE_EXACT_RECEIPTS')
    return inputs,detail


def checkpoint(journal, config, ledger=None, available_inputs=None, market_snapshots=None, holdings_market_snapshots=None, admission_receipt=None):
    """The normal Kernel checkpoint, after the current cycle's observations.

    No refresh/new request and no recurring allocation: the exact event gate
    decides whether to invoke the allocator. Failures are caller-contained.
    """
    from .runtime import Consumer
    source=Path(journal)
    inputs,detail=freeze(source,source.parent/'attention.db',source.parent/'investigation.db',config,available_inputs,market_snapshots,holdings_market_snapshots,admission_receipt)
    ledger=Path(ledger) if ledger else source.parent/'runtime-portfolio.db'
    if ledger.resolve()==source.resolve():
        raise ValueError('PROPOSAL_LEDGER_MUST_BE_SEPARATE')
    result = Consumer(ledger).consume(inputs)
    # Deliver the exact proposal's inputs at the consuming boundary.
    import sqlite3
    from trader.learning import capture as C, capture_runtime as R
    with sqlite3.connect(source,timeout=2) as db:
        db.row_factory=sqlite3.Row
        C.ensure(db)
        detail['learning_source_delivery'] = C.safely(db,'portfolio:'+result['portfolio_cut_id'],
            R.deliver_allocation,config,inputs,result,market_snapshots)
        if result.get('proposal') is not None and detail['learning_source_delivery'] is None:
            R.failed_allocation_delivery(db, inputs, result)
    return result,detail
