"""Explicit TEST-ONLY adapters. Never imported by production code."""
from dataclasses import asdict
from decimal import Decimal, localcontext
import json
import sqlite3

from trader.portfolio import economics as econ
from trader.portfolio.allocator import Source, Evidence, Status, canonical, digest
from trader.engine import paper_cost_evidence as cost

SOURCE = Source.freeze('fixture-authority', {'kind': 'synthetic-test-only', 'version': 1})
OK = Evidence(Status.ESTABLISHED, (SOURCE.source_id,))
UNKNOWN = Evidence(Status.UNKNOWN, ())


def install_models(monkeypatch=None):
    registries = [(econ.GROSS_MODELS, ('TEST-ONLY-distribution', '1')),
                  (econ.RESERVE_MODELS, ('TEST-ONLY-reserve', '1')),
                  (econ.COST_SCOPE_MODELS, ('TEST-ONLY-cost-scope', '1')),
                  (cost.SOURCE_VALIDATORS, 'TEST-ONLY-paper-cost')]
    for registry, key in registries:
        validator = lambda raw: raw.get('test_only') is True
        if monkeypatch:
            monkeypatch.setitem(registry, key, validator)
        else:
            registry[key] = validator


def binding(name='a', instrument='venue:futures:BTCUSDT', direction='LONG', **kw):
    fields = dict(opportunity_id=name, strategy_id='strategy-' + name,
                  version_id='version-' + name, spec_hash=digest({'spec': name}),
                  instrument=instrument, market_type='futures', direction=direction,
                  horizon='4h', as_of_ms=1000, context_json=canonical({
                      'validation': asdict(OK), 'probation': asdict(OK),
                      'evidence_quality': asdict(OK), 'regime_world': asdict(UNKNOWN)}),
                  units='USDT', quantity_basis='base_quantity:1', capital_basis='one_unit',
                  horizon_interpretation='fixed_forward_window',
                  cost_treatment='paper_fill_adjustment_all_costs',
                  uncertainty_treatment='TEST-ONLY-calibrated-reserve',
                  freshness_semantics='captured_to_authority_expiry_inclusive')
    fields.update(kw)
    return econ.Binding(**fields)


def model(b, method, value=None):
    return dict(method=method, version='1', test_only=True, binding=asdict(b),
                units=b.units, value=value, captured_ms=900, valid_until_ms=2000,
                provenance={'authority': 'TEST-ONLY'}, limitations=['SYNTHETIC_NOT_REAL_ECONOMICS'],
                evidence_period_ms=[100, 800], calibration_evidence={'id': 'TEST-ONLY-calibration'},
                costs_excluded=True, cost_baseline=b.cost_treatment,
                borrow={'status': 'NOT_APPLICABLE', 'provenance': {'TEST-ONLY': 'unborrowed_capital'}})


def cost_source(b):
    version = {'strategy_id': b.strategy_id, 'version_id': b.version_id, 'spec_hash': b.spec_hash}
    inst = {'install_id': 'TEST-ONLY-install', 'exit_semantics_id': 'TEST-ONLY-exits'}
    identity = dict(**version, **inst)
    identity['spec_sha256'] = identity.pop('spec_hash')
    trade = dict(id='TEST-ONLY-trade', entry_identity_json=canonical(identity), **version,
                 **inst, status='closed', exec_mode='paper', amount=1, entry_price=100,
                 exit_price=110, realized_pnl=10 if b.direction == 'LONG' else -10,
                 side=b.direction.lower(), symbol=b.instrument, market_type='futures',
                 opened_at='1970-01-01T00:00:00+00:00', closed_at='1970-01-01T00:00:00.010+00:00',
                 reference_price=100, exit_reference_price=110)
    bound = cost.binding(trade, version, inst)
    with sqlite3.connect(':memory:') as db:
        db.row_factory = sqlite3.Row
        ids = {}
        for kind in ('slippage', 'commission', 'funding'):
            raw = dict(source_id='TEST-ONLY-' + kind, method='TEST-ONLY-paper-cost',
                       test_only=True, version='1', captured_ms=20, provenance={'test_only': True},
                       binding=bound, kind=kind, currency='USDT')
            if kind == 'slippage':
                raw.update(entry={'executable_price': 100}, exit={'executable_price': 110})
            elif kind == 'commission':
                raw.update(entry={'classification': 'taker', 'rate': .01},
                           exit={'classification': 'taker', 'rate': .01})
            else:
                raw.update(interval_complete=True, timing_semantics='TEST-ONLY-strict-interior', events=[])
            cost.freeze_source(db, raw)
            ids[kind] = raw['source_id']
        cost.finalize(db, trade, version, inst, ids)
        record = dict(db.execute('SELECT * FROM ' + cost.TABLE).fetchone())
        sources = [dict(r) for r in db.execute('SELECT * FROM ' + cost.SOURCES + ' ORDER BY source_id')]
    raw = model(b, 'TEST-ONLY-cost-scope')
    raw.update(trade=trade, strategy_version=version, install=inst,
               paper_receipt_record=record, paper_source_records=sources)
    return Source.freeze('TEST-ONLY-cost-bridge', raw)


def frozen(value='2', b=None):
    b = b or binding()
    costs = cost_source(b)
    with localcontext() as ctx:
        ctx.prec = 100
        gross = str(Decimal(value) + Decimal('3.1'))
    gross = Source.freeze('TEST-ONLY-gross', model(b, 'TEST-ONLY-distribution', gross))
    reserve = model(b, 'TEST-ONLY-reserve', '1')
    reserve.update(gross_source_sha256=gross.sha256, cost_source_sha256=costs.sha256)
    uncertainty = Source.freeze('TEST-ONLY-uncertainty', reserve)
    return econ.Inputs(b, gross, costs, uncertainty, (SOURCE,))
