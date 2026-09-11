"""The rent check: an hourly tally, one line a day, one verdict a week."""
import json
from datetime import datetime, timedelta, timezone

from trader.core.journal import Journal
from trader.engine.rent_keeper import RentKeeper, snapshot, status_text

MON = datetime(2026, 9, 14, tzinfo=timezone.utc)          # first judged week
MON_MS = int(MON.timestamp() * 1000)
CFG = {"rent": {"weekly_usdt": 50, "first_week_start": "2026-09-14"}}
NEXT_MON = MON + timedelta(days=7, minutes=5)


class Ledger:
    def __init__(self, rows=None):
        self.rows, self.down = list(rows or []), False

    def fapiPrivateGetIncome(self, p):
        if self.down:
            raise RuntimeError("503 Service Unavailable")
        return [r for r in self.rows
                if p["startTime"] <= r["time"] <= p["endTime"]][:p["limit"]]


class Notes:
    def __init__(self):
        self.sent = []

    def send(self, text, silent=False):
        self.sent.append(text)
        return True


def _income(kind, amt, t, tid):
    return {"incomeType": kind, "income": str(amt), "time": t,
            "tranId": tid, "symbol": "BTCUSDT"}


def _keeper(tmp_path, rows=None):
    j, led, n = Journal(tmp_path / "j.db"), Ledger(rows), Notes()
    return RentKeeper(led, j, n, CFG), j, led, n


def _verdicts(j):
    return [json.loads(r["detail"]) for r in j.query(
        "SELECT detail FROM brain_events WHERE kind='rent_verdict' ORDER BY id")]


def test_a_week_that_paid_is_a_pass(tmp_path):
    k, j, led, n = _keeper(tmp_path, [
        _income("REALIZED_PNL", 70, MON_MS + 3_600_000, 1),
        _income("COMMISSION", -12, MON_MS + 3_600_000, 2)])
    k.tick(NEXT_MON)
    v = _verdicts(j)
    assert [x["verdict"] for x in v] == ["PASS"]
    assert v[0]["net"] == 58.0 and v[0]["week_start"] == "2026-09-14"
    assert any("PASS" in s for s in n.sent)


def test_a_week_that_missed_is_a_fail(tmp_path):
    k, j, led, n = _keeper(tmp_path, [_income("REALIZED_PNL", 30, MON_MS + 1, 1)])
    k.tick(NEXT_MON)
    assert _verdicts(j)[0]["verdict"] == "FAIL"


def test_a_deposit_does_not_pay_the_rent(tmp_path):
    k, j, led, n = _keeper(tmp_path, [_income("TRANSFER", 1000, MON_MS + 1, 1)])
    k.tick(NEXT_MON)
    v = _verdicts(j)[0]
    assert v["verdict"] == "FAIL" and v["uncounted"] == {"TRANSFER": 1000.0}


def test_a_restart_does_not_judge_the_week_twice(tmp_path):
    k, j, led, n = _keeper(tmp_path, [_income("REALIZED_PNL", 70, MON_MS + 1, 1)])
    k.tick(NEXT_MON)
    k.tick(NEXT_MON + timedelta(hours=1))
    RentKeeper(led, j, n, CFG).tick(NEXT_MON + timedelta(hours=2))
    assert len(_verdicts(j)) == 1
    assert sum("PASS" in s for s in n.sent) == 1


def test_an_unreadable_week_is_unknown_then_corrected(tmp_path):
    k, j, led, n = _keeper(tmp_path, [_income("REALIZED_PNL", 70, MON_MS + 1, 1)])
    led.down = True
    k.tick(NEXT_MON)
    k.tick(NEXT_MON + timedelta(hours=1))
    assert [v["verdict"] for v in _verdicts(j)] == ["UNKNOWN"]   # said once
    led.down = False
    k.tick(NEXT_MON + timedelta(hours=2))
    v = _verdicts(j)
    assert [x["verdict"] for x in v] == ["UNKNOWN", "PASS"]
    assert v[-1]["correction"] is True
    assert snapshot(j)["history"][0]["verdict"] == "PASS"         # latest wins


def test_the_week_before_the_first_judged_week_gets_no_verdict(tmp_path):
    k, j, led, n = _keeper(tmp_path)
    k.tick(MON + timedelta(minutes=5))        # would finalise week of 2026-09-07
    assert _verdicts(j) == []


def test_the_tally_is_written_hourly_and_sent_once_a_day(tmp_path):
    k, j, led, n = _keeper(tmp_path, [_income("REALIZED_PNL", 12.5, MON_MS + 1, 1)])
    t = MON + timedelta(days=2, hours=9)
    k.tick(t)
    k.tick(t + timedelta(hours=1))
    s = json.loads(j.kv_get("rent_state"))
    assert s["net"] == 12.5 and s["week_start"] == "2026-09-14"
    assert s["status"] == "IN_PROGRESS" and s["judged"] is True
    assert sum("so far" in x for x in n.sent) == 1
    k.tick(t + timedelta(days=1))
    assert sum("so far" in x for x in n.sent) == 2


def test_status_text_reads_the_state_and_the_history(tmp_path):
    k, j, led, n = _keeper(tmp_path, [_income("REALIZED_PNL", 70, MON_MS + 1, 1)])
    k.tick(NEXT_MON)
    txt = status_text(j)
    assert "2026-09-14" in txt and "PASS" in txt and "+70.00" in txt
