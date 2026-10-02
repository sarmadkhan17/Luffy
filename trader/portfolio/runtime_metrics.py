"""Independent current book measurements from trusted, frozen source readers.

Integrity receipts are replay evidence, not authentication. No concentration
policy is created. Risk heat/margin reuse RiskManager.portfolio_metrics.
"""
from dataclasses import asdict
from decimal import Decimal, localcontext
import json
from datetime import datetime

from .allocator import Source, canonical, digest, number
from trader.core.types import Position, Side, norm_symbol
from trader.engine.risk import RiskManager, policy_from_config
from trader.engine.evidence_capture import verify_margin
from trader.dashboard.current_truth import ACCOUNT_STALE_S

SCHEMA = 'runtime-portfolio-evidence.v1'
SOURCE_ID = 'runtime-portfolio-evidence'


def positions(raw, book):
    """Reconcile the complete held book; retain Risk's recorded entry-price basis."""
    if len(raw) != len(book['positions']):
        raise ValueError('RISK_BOOK_NOT_RECONCILED')
    result = []
    for v in book['positions']:
        symbol = v['instrument_id'].split(':')[-1]
        matches = [p for p in raw if norm_symbol(p['symbol']).replace('/', '') == symbol]
        if len(matches) != 1:
            raise ValueError('RISK_BOOK_NOT_RECONCILED')
        p = matches[0]
        fields = ('amount', 'entry_price', 'notional_usdt', 'stop_loss')
        if any(isinstance(p.get(k), bool) or number(str(p.get(k))) is None for k in fields):
            raise ValueError('RISK_POSITION_FIELDS_UNAVAILABLE')
        if (p['side'].lower() != v['side'] or Decimal(str(p['amount'])) != Decimal(str(v['quantity']))
                or p['market_type'] != book['market_type']
                or float(p['entry_price']) <= 0 or float(p['notional_usdt']) <= 0
                or float(p['amount']) <= 0 or float(p['stop_loss']) < 0
):
            raise ValueError('RISK_BOOK_NOT_RECONCILED')
        result.append(Position(id=p['id'], symbol=norm_symbol(p['symbol']), side=Side(p['side'].lower()),
            amount=float(p['amount']), entry_price=float(p['entry_price']),
            notional_usdt=float(p['notional_usdt']), stop_loss=float(p['stop_loss']),
            leverage=int(p['leverage']), market_type=p['market_type']))
    return result


def capture(*, snapshot, account_margin, journal_positions, risk_kv, config, as_of_ms, closed_count,
            risk_assessment=None, entry_contexts=(), account_observation=None):
    """Caller freezes these in one journal read transaction, not from chat/artifacts."""
    from trader.engine.evidence_capture import verify_snapshot
    if verify_snapshot(snapshot) is None:
        raise ValueError('METRIC_SNAPSHOT_UNVERIFIED')
    raw = dict(schema=SCHEMA, portfolio_snapshot_id=snapshot['snapshot_id'],
        snapshot_sha256=digest(snapshot), as_of_ms=as_of_ms,
        snapshot_as_of_ms=snapshot['observed_at_ms'], account_margin=account_margin, account_observation=account_observation,
        journal_positions=journal_positions, risk_kv=risk_kv, closed_count=closed_count,
        config={'risk': config['risk']}, policy_sha256=policy_from_config(config)['digest'],
        risk_assessment=risk_assessment, entry_contexts=list(entry_contexts),
        source_hashes={k: digest(v) for k,v in dict(venue=snapshot, account_margin=account_margin,
            journal_positions=journal_positions, risk_state=risk_kv, risk_assessment=risk_assessment, account_observation=account_observation).items()})
    return Source.freeze(SOURCE_ID, raw)


def project(source, venue_source, policy_source, as_of_ms):
    with localcontext() as ctx:
        ctx.prec = 100
        return _project(source, venue_source, policy_source, as_of_ms)


def _project(source, venue_source, policy_source, as_of_ms):
    from .common_factor import snapshot as checked_snapshot
    checked_snapshot(venue_source, as_of_ms)
    Source(**asdict(source))
    raw, venue = json.loads(source.payload_json), json.loads(venue_source.payload_json)
    policy = json.loads(policy_source.payload_json)
    cfg = policy if 'risk' in policy else {'risk': policy}
    if (raw.get('schema') != SCHEMA or raw['portfolio_snapshot_id'] != venue['snapshot_id']
            or raw['snapshot_sha256'] != digest(venue) or raw['as_of_ms'] != as_of_ms
            or raw['policy_sha256'] != policy_from_config(cfg)['digest']
            or policy_from_config(raw['config'])['digest'] != raw['policy_sha256']):
        raise ValueError('METRIC_PORTFOLIO_CUT_DIFFERS')
    assessment = raw.get('risk_assessment')
    if assessment and assessment.get('policy', {}).get('digest') != raw['policy_sha256']:
        raise ValueError('CURRENT_RISK_POLICY_DIFFERS')
    expected = {k: digest(v) for k,v in dict(venue=venue, account_margin=raw['account_margin'],
        journal_positions=raw['journal_positions'], risk_state=raw['risk_kv'],
        risk_assessment=raw['risk_assessment'], account_observation=raw.get('account_observation')).items()}
    if raw['source_hashes'] != expected:
        raise ValueError('METRIC_SOURCE_HASH_DIFFERS')
    metrics = {}
    def add(name, value, basis, reason=None, observed=None):
        metrics[name] = dict(value=value, status='ESTABLISHED' if value is not None else 'UNAVAILABLE',
            reason=reason, basis=basis, portfolio_snapshot_id=venue['snapshot_id'],
            source_ids=[venue_source.source_id, source.source_id, policy_source.source_id],
            source_hashes={venue_source.source_id:venue_source.sha256, source.source_id:source.sha256,
                           policy_source.source_id:policy_source.sha256}, as_of_ms=as_of_ms,
            observed_at_ms=(venue['observed_at_ms'] if basis in ('COMPLETE_VENUE_BOOK',
                'VENUE_MARK_PRICE_TIMES_QUANTITY','CURRENT_MARKED_USDT') else observed),
            freshness='CURRENT' if value is not None else 'UNAVAILABLE',
            completeness='COMPLETE' if value is not None else 'UNAVAILABLE')
    add('open_position_count', len(venue['positions']), 'COMPLETE_VENUE_BOOK')
    exposure = {}
    for p in venue['positions']:
        price = number(p.get('mark_price_text'))
        exposure[p['instrument_id']] = str(Decimal(str(p['quantity'])) * price) if price is not None and price > 0 else None
    add('per_instrument_exposure', exposure, 'VENUE_MARK_PRICE_TIMES_QUANTITY',
        'CURRENT_MARK_UNAVAILABLE' if any(v is None for v in exposure.values()) else None)
    metrics['per_instrument_exposure']['components'] = {
        iid: dict(value=value,status='ESTABLISHED' if value is not None else 'UNAVAILABLE')
        for iid,value in exposure.items()}
    if any(v is None for v in exposure.values()):
        metrics['per_instrument_exposure'].update(status='UNAVAILABLE',completeness='INCOMPLETE')
    gross = str(sum((Decimal(v) for v in exposure.values()), Decimal(0))) if all(v is not None for v in exposure.values()) else None
    add('gross_exposure', gross, 'CURRENT_MARKED_USDT', 'CURRENT_MARK_UNAVAILABLE' if gross is None else None)
    margin = raw['account_margin']
    valid = verify_margin(margin)
    t = margin.get('observed_at_ms') if valid else None
    equity = number(margin.get('equity_value_text')) if valid else None
    account_current = (type(t) is int and t <= as_of_ms <= t + int(ACCOUNT_STALE_S*1000)
                       and margin.get('environment') == venue['environment'] and equity is not None and equity > 0)
    initial = number(margin.get('initial_margin_text')) if account_current else None
    utilization = str(initial / equity * 100) if initial is not None and initial >= 0 else None
    add('actual_margin_utilization_pct', utilization, 'VENUE_TOTAL_INITIAL_MARGIN_OVER_EQUITY',
        None if utilization is not None else 'ACCOUNT_MARGIN_FIELD_STALE_OR_UNAVAILABLE', t)
    # Equity authority is independent of whether the account margin field exists.
    account_observation = raw.get('account_observation')
    equity_id = margin.get('observation_id') if account_current else None
    if not account_current and isinstance(account_observation, dict):
        try:
            at = int(datetime.fromisoformat(account_observation['observed_at'].replace('Z','+00:00')).timestamp()*1000)
            value = number(str(account_observation['value']))
            assessed_equity = (raw.get('risk_assessment') or {}).get('equity', {}).get('value')
            if (account_observation.get('authoritative') is True
                    and account_observation.get('currency') == 'USDT'
                    and account_observation.get('status') in ('FRESH','VENUE_FALLBACK')
                    and account_observation.get('completion_relation') == 'ok'
                    and value is not None and value > 0
                    and value == number(str(account_observation.get('risk_input')))
                    and (assessed_equity is None or value == number(str(assessed_equity)))
                    and at <= as_of_ms <= at + int(ACCOUNT_STALE_S*1000)):
                equity, t, account_current, equity_id = value, at, True, digest(account_observation)
        except (ValueError, KeyError, TypeError):
            pass
    add('account_equity', str(equity) if account_current else None, 'EXISTING_AUTHORITATIVE_ACCOUNT_EQUITY',
        None if account_current else 'ACCOUNT_EQUITY_STALE_OR_UNAVAILABLE', t)
    metrics['account_equity']['evidence_id'] = equity_id
    reason = 'ACCOUNT_EQUITY_STALE_OR_UNAVAILABLE'
    computed = {}
    if account_current:
        try:
            ps = positions(raw['journal_positions'], venue)
            computed = RiskManager(cfg, None).portfolio_metrics(ps, float(equity))
            reason = None
        except (ValueError, KeyError, TypeError) as exc:
            reason = str(exc)
    for name in ('risk_heat_pct','per_symbol_risk_pct','position_margin_pct','total_margin_pct'):
        add(name, computed.get(name), 'EXISTING_RISK_ENTRY_BASIS_CALCULATIONS', reason, t)
    return metrics
