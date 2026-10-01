"""Explicit deterministic TEST-ONLY evidence; no runtime registration path.

Amounts are fixture observations, not a venue fee/funding/slippage model.
Production never imports this module. Monkeypatch registration lasts one test.
"""
from trader.strategy import factory_handoff as F


def complete_test_cost_evidence(journal, trade, version):
    return {dimension: {"status": "KNOWN", "amount": amount, "currency": "USDT",
                        "evidence_id": f"TEST-ONLY:{trade['id']}:{dimension}",
                        "trade_id": trade['id'], "version_id": version['version_id'],
                        "install_id": F.verify_install(journal, version, current=False)['install_id']}
            for dimension, amount in (("commission", 0.01), ("slippage", 0.02), ("funding", 0.03))}


def register_test_cost_evidence(monkeypatch):
    monkeypatch.setattr(F, '_paper_cost_evidence', complete_test_cost_evidence)
