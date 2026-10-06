# QNT-06 canonical reconciliation

Original condition: Controls demonstrate the power required by the declared experiment; insufficient cases return UNTESTED with reasons.

Result: CLOSED at architecture/offline engineering scope. Engineering commit 126a8a5 (selection gates now report structured UNTESTED/FAIL/PASS).

- Declared sufficiency: the experiment's own configured minimum (select_min_trades / gauntlet_min_test_trades, or the research control's control_max_p and MIN_CONTROL_SYMBOLS) is the requirement; no threshold was added or changed.
- Insufficient evidence returns outcome=UNTESTED with untested_code=insufficient_trades, required_trades, observed_trades and a reason from both selection gates (recent_verdict, run_gauntlet); an empty sample is UNTESTED, never FAIL or PASS. The legacy untested flag (missing data) is unchanged.
- Adequate samples are judged: a powered positive control reads PASS and an impossible-PF no-edge control reads FAIL, never UNTESTED. In the research path an unpowered window labels a negative underpowered, never no_edge; empty/untested/untestable/error verdicts pass through unlabelled; powered(None) is False.
- Retry/restart: outcomes are pure functions of inputs and config (identical on repeat and on deep-copied inputs); the research control status (measured/untestable) is persisted in the ledger and an empty/error control is deliberately left uncontrolled for retry. Idle decay is not decay.

Limits: Architecture/offline proof on synthetic fixtures. analyst.admit refuses via reason text and does not yet pass outcome through; no real-data calibration, held-out look or admission is claimed.

QNT-08 remains blocked on QNT-03 (via WRLD-07) and QNT-05 (via QNT-04). Details in reconciliation.json.
