"""Execute with a baseline or fixed trader package on PYTHONPATH."""
import json
from dataclasses import replace
from trader.world import Observation, Quality, Scope, ScopeLevel, Horizon, RelationshipCoordinate, RelationshipState

a = Scope(ScopeLevel.INSTRUMENT, 'BTC')
b = Scope(ScopeLevel.INSTRUMENT, 'ETH')
obs = Observation('BTC', 10, 10, '1h', 'returns', [1, 2], 'TEST_ONLY', 'raw-window', Quality.VALID,
                  available_at_ms=10, max_age_ms=10)
cases = {'missing_method_window_context': ('rolling-correlation', (obs,), 10),
         'stale_evidence_promoted': ('rolling-correlation', (replace(obs, quality=Quality.STALE),), 10),
         'expired_evidence_recut': ('beta', (obs,), 100),
         'guessed_attribution': ('attribution', (obs,), 10),
         'untested_cointegration': ('cointegration', (obs,), 10),
         'permanent_beta': ('permanent-beta', (obs,), 10)}
result = {}
for name, (kind, evidence, cut) in cases.items():
    try:
        RelationshipState(RelationshipCoordinate(a, b, kind, Horizon.SWING), cut,
            {'correlation': .9}, Quality.VALID, 'TEST_ONLY', 'guess', evidence)
        result[name] = 'ACCEPTED_GAP'
    except ValueError:
        result[name] = 'REFUSED'
print(json.dumps(result, sort_keys=True, indent=2))
