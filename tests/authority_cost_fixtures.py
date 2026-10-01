"""TEST-ONLY frozen authoritative fixtures; never imported by production.

These observations exercise registered-source replay, not venue estimates.
"""
from trader.strategy import factory_handoff as F
from trader.engine import paper_cost_evidence as C


def test_sources(bound, *, commission=0.01, slippage=0.02, funding=0.03):
    qty = bound['entry']['quantity']
    common = dict(binding=bound, currency='USDT', version='TEST-ONLY.v1',
                  captured_ms=bound['exit']['time_ms'], provenance='TEST-ONLY frozen authority',
                  method='TEST-ONLY.authority.v1')
    fee = {**common, 'kind':'commission', 'source_id':'TEST-ONLY:'+bound['trade_id']+':commission'}
    execution = {**common, 'kind':'slippage', 'source_id':'TEST-ONLY:'+bound['trade_id']+':slippage'}
    for leg in ('entry','exit'):
        fee[leg] = dict(rate=commission/2/(qty*bound[leg]['fill']), classification='taker')
        direction = (1 if bound['side']=='long' else -1) * (1 if leg=='entry' else -1)
        execution[leg] = dict(executable_price=bound[leg]['fill']+direction*slippage/2/qty)
    carry = {**common, 'kind':'funding', 'source_id':'TEST-ONLY:'+bound['trade_id']+':funding',
             'interval_complete':True, 'timing_semantics':'TEST-ONLY exact event interval, exclusive fill boundaries',
             'events':[dict(timestamp_ms=(bound['entry']['time_ms']+bound['exit']['time_ms'])//2,
                  instrument=bound['instrument'],rate=funding/(qty*100)*(1 if bound['side']=='long' else -1),
                  position_basis_price=100,quantity=qty,basis_provenance='TEST-ONLY historical mark')]}
    return fee, execution, carry


def freeze_complete(journal, trade, version, **amounts):
    install = F.verify_install(journal,version,current=False)
    bound = C.binding(trade,version,install)
    sources = test_sources(bound,**amounts)
    with journal._tx() as db:
        C.ensure(db)
        for source in sources:
            C.freeze_source(db,source)
        return C.finalize(db,trade,version,install,{s['kind']:s['source_id'] for s in sources})


def complete_test_cost_evidence(journal, trade, version):
    if not journal.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(C.TABLE,)) or not journal.query(f'SELECT 1 FROM {C.TABLE} WHERE trade_id=?',(trade['id'],)):
        freeze_complete(journal,trade,version)
    return _REAL_READER(journal,trade,version)


_REAL_READER = F._paper_cost_evidence


def register_test_cost_evidence(monkeypatch):
    monkeypatch.setitem(C.SOURCE_VALIDATORS,'TEST-ONLY.authority.v1',
                        lambda source: source['provenance']=='TEST-ONLY frozen authority')
    monkeypatch.setattr(F,'_paper_cost_evidence',complete_test_cost_evidence)
    monkeypatch.setattr(C,'finalize',finalize_test_costs)

_REAL_FINALIZE = C.finalize


def finalize_test_costs(db, trade, version, install, source_ids=None):
    if source_ids is None:
        sources = test_sources(C.binding(trade,version,install))
        for source in sources:
            C.freeze_source(db,source)
        source_ids = {s['kind']:s['source_id'] for s in sources}
    return _REAL_FINALIZE(db,trade,version,install,source_ids)
