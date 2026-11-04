import sqlite3
from trader.dashboard.research_source import closure_summary
from trader.dashboard.overview_desk import research_board, research_counts


def fixture(tmp_path):
    path=tmp_path/'research.db'
    with sqlite3.connect(path) as c:
        for table in ('candidates','runs','experiment_plans','looks','qualifications'):
            c.execute('CREATE TABLE '+table+'(id TEXT PRIMARY KEY,payload TEXT)')
        c.execute('CREATE TABLE studies(study_id TEXT,status TEXT,stop_reason TEXT,next_work TEXT,last_progress_ms INTEGER)')
        c.execute('CREATE TABLE units(study_id TEXT,status TEXT)')
        c.execute('CREATE TABLE schedule_events(seq INTEGER,at_ms INTEGER,status TEXT,study_id TEXT,stop_reason TEXT,units_run INTEGER)')
        c.execute("INSERT INTO candidates VALUES ('c','{}')")
        c.execute("INSERT INTO studies VALUES ('s','COMPLETED','COMPLETE',NULL,1)")
        c.execute("INSERT INTO units VALUES ('s','FAILED')")
    return path,{'research':{'closure':{'enabled':True,'store_path':str(path)},'referee':True}}


def test_current_engine_does_not_report_empty_legacy_ledger_as_no_research(tmp_path):
    path,cfg=fixture(tmp_path);before=path.read_bytes()
    board=research_board(None,cfg);counts=research_counts(None,cfg)
    assert board['engine']=='compositional' and board['available']
    assert board['counts']['candidates']==1 and board['studies'][0]['failed_units']==1
    assert board['total'] is None and board['combos'] is None
    assert counts['counts']['candidates']==1 and counts['counts']['tests']==0
    assert path.read_bytes()==before


def test_missing_current_store_is_unavailable_not_created_or_replaced_by_legacy(tmp_path):
    path=tmp_path/'absent.db';cfg={'research':{'closure':{'enabled':True,'store_path':str(path)}}}
    assert research_board(None,cfg)['available'] is False
    assert research_counts(None,cfg)['counts']['candidates'] is None
    assert not path.exists()


def test_disabled_engine_keeps_legacy_reader_selection():
    assert closure_summary({'research':{'closure':{'enabled':False}}}) is None
