"""Exact semantic authority comparison across the reviewed safety repairs.

53e7 remains the original anchor. fc6 supplies only the stop-adoption repair
already present at the owner's baseline. No current-checkout hashes are used.
"""
import ast
import copy

ORIGINAL = '53e7c8567c259b2326315a0acdd4b838af030998'
BASELINE = 'fc6c0b2836b7c226517a2c22fcce389eedd9a4c0'


def normalized(source):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
    return tree


def methods(tree, cls):
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    return {n.name: n for n in owner.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def dump(tree):
    return ast.dump(tree, include_attributes=False)


def verify(historical, current):
    for name, cls in [('risk.py', 'RiskManager'), ('executor.py', 'Executor')]:
        before = normalized(historical(ORIGINAL, name))
        after = normalized(current(name))
        old = methods(before, cls)
        new = methods(after, cls)
        # Lifecycle retains configuration to bind exact-version authority.
        expected_init = ast.parse('self.cfg = cfg').body[0]
        inserts = [n for n in new['__init__'].body if dump(n) == dump(expected_init)]
        assert len(inserts) == 1, 'configuration binding missing or duplicated'
        new['__init__'].body.remove(inserts[0])
        if name == 'risk.py':
            assert dump(after) == dump(before), 'Risk semantics changed'
            continue
        expected_open = ast.unparse(old['open']).replace(
            'fh.live_entry_block(self.journal, strategy_id)',
            'fh.live_entry_block(self.journal, strategy_id, cfg=self.cfg)')
        old['open'] = ast.parse(expected_open).body[0]
        expected_locked = ast.unparse(old['_open_locked']).replace(
            'positive(amount)\n        amount = quantize(self.ex, sym, amount)\n        positive(amount)',
            "requested_amount = positive(amount)\n        amount = quantize(self.ex, sym, amount)\n"
            "        if positive(amount) > requested_amount:\n"
            "            decision.skip_reason = 'entry_quantization_enlarged_size'\n"
            "            return None")
        assert expected_locked != ast.unparse(old['_open_locked'])
        old['_open_locked'] = ast.parse(expected_locked).body[0]
        baseline = methods(normalized(historical(BASELINE, name)), cls)
        for method in ('_place_native_stop', '_matching_venue_stop'):
            old[method] = baseline[method]
        # Same durable partial-exit exception as the original guard. Entry,
        # reservation, sizing, full exit and flatten remain compared exactly.
        partial = {'close_partial', 'recover_entries', 'recovery_pending'}
        assert {k: dump(v) for k, v in new.items() if k not in partial} == {
            k: dump(v) for k, v in old.items() if k not in partial}
    assert current('risk_intent.py') == historical(ORIGINAL, 'risk_intent.py')
