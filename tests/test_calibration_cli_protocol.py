"""Frozen CLI protocol reaches the real harness contract without preset substitution."""
from copy import deepcopy
import json
from unittest.mock import Mock
import pytest
from trader.research.closure import calibration as C
from trader.research.closure import __main__ as cli


def test_exact_protocol_retains_window_search_provider_intake_and_seeds(tmp_path,monkeypatch,capsys):
    protocol=deepcopy(C.PROTOCOL)
    protocol.update(tf='1h',dev_bars=2000,max_candidates=8,max_revisions=4,
        search={'max_attempts':24,'max_fits':40},intake_bank_path='/explicit/frozen/questions.db')
    path=tmp_path/'protocol.json';path.write_text(json.dumps(protocol))
    run=Mock(return_value={'calibration_status':'INCONCLUSIVE','authority_granted':False})
    monkeypatch.setattr(C,'run',run)
    assert cli.main(['calibrate','--protocol',str(path),'--workdir',str(tmp_path/'run')])==0
    run.assert_called_once_with(protocol,str(tmp_path/'run'),replicates=None)
    assert json.loads(capsys.readouterr().out)['authority_granted'] is False


@pytest.mark.parametrize('text',['not JSON','[]','{"schema":"wrong"}'])
def test_invalid_protocol_never_starts_a_provider_or_replicate(tmp_path,monkeypatch,capsys,text):
    path=tmp_path/'protocol.json';path.write_text(text)
    run=Mock();monkeypatch.setattr(C,'run',run)
    assert cli.main(['calibrate','--protocol',str(path),'--workdir',str(tmp_path/'run')])==2
    assert json.loads(capsys.readouterr().out)['reason_code']=='calibration_protocol_invalid'
    run.assert_not_called()


def test_protocol_and_preset_cannot_silently_override_each_other(tmp_path,monkeypatch):
    run=Mock();monkeypatch.setattr(C,'run',run)
    with pytest.raises(SystemExit):cli.main(['calibrate','--protocol','p.json','--preset','final','--workdir',str(tmp_path)])
    run.assert_not_called()


def test_existing_default_still_uses_smoke_without_claiming_final_qualification(tmp_path,monkeypatch,capsys):
    run=Mock(return_value={'calibration_status':'INCONCLUSIVE','authority_granted':False})
    monkeypatch.setattr(C,'run',run)
    assert cli.main(['calibrate','--workdir',str(tmp_path)])==0
    protocol=run.call_args.args[0]
    assert protocol['kind']=='DEVELOPMENT_SMOKE' and protocol['numerics']=='SMALL'
    assert json.loads(capsys.readouterr().out)['calibration_status']=='INCONCLUSIVE'
