"""Stage-5 start() must never raise into kernel boot (review 03, item 3)."""
import json
import threading
from types import SimpleNamespace

import pytest

from trader.observability import stage5_activation as S


def _kernel(tmp_path):
    return SimpleNamespace(journal=SimpleNamespace(db_path=str(tmp_path / "luffy.db")))


@pytest.mark.parametrize("body", ["not json", "{}", json.dumps({"started_ms": 1, "deadline_ms": 10 ** 12}),
                                  json.dumps({"started_ms": 4102444800000, "deadline_ms": 4102444900000})])
def test_invalid_or_future_window_file_does_not_raise_and_starts_nothing(tmp_path, body):
    (tmp_path / "stage5-activation.json").write_text(body)
    before = threading.active_count()
    S.start(_kernel(tmp_path))                      # must not raise
    assert threading.active_count() == before
    status = json.loads((tmp_path / "stage5-public" / "status.json").read_text())
    assert status["status"] == "EXTERNAL_BLOCKED" and status["reason"].startswith("WINDOW_FILE_")


def test_absent_window_file_is_a_no_op(tmp_path):
    S.start(_kernel(tmp_path))
    assert not (tmp_path / "stage5-public").exists()
