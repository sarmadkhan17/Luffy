"""Offline pytest evidence plugin; no application authority or runtime behavior."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
from urllib.parse import unquote, urlparse

REPORT = {'selected': [], 'deselected': [], 'reports': [], 'failures': [],
          'network_attempts': [], 'production_store_attempts': [], 'module_origins': {}}


def pytest_sessionstart(session):
    allowed = Path(os.environ['STAGE1_EVIDENCE_FIXTURES']).resolve()
    original_connect = sqlite3.connect
    def connect(database, *args, **kwargs):
        value = str(database)
        if value != ':memory:':
            path = Path(unquote(urlparse(value).path) if value.startswith('file:') else value).resolve()
            if not path.is_relative_to(allowed):
                REPORT['production_store_attempts'].append(str(path))
                raise AssertionError('evidence production-store guard')
        return original_connect(database, *args, **kwargs)
    sqlite3.connect = connect
    original_socket_connect = socket.socket.connect
    def socket_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            REPORT['network_attempts'].append(repr(address))
            raise AssertionError('evidence network guard')
        return original_socket_connect(sock, address)
    socket.socket.connect = socket_connect
    def no_connection(*args, **kwargs):
        REPORT['network_attempts'].append(repr(args))
        raise AssertionError('evidence network guard')
    socket.create_connection = no_connection
    REPORT['network_guard_active'] = REPORT['production_store_guard_active'] = True


def pytest_deselected(items):
    REPORT['deselected'].extend(item.nodeid for item in items)


def pytest_collection_finish(session):
    REPORT['selected'] = [item.nodeid for item in session.items]


def pytest_runtest_logreport(report):
    REPORT['reports'].append({'nodeid': report.nodeid, 'when': report.when,
                             'outcome': report.outcome, 'duration': report.duration})
    if report.failed:
        crash = getattr(report.longrepr, 'reprcrash', None)
        REPORT['failures'].append({'nodeid': report.nodeid, 'when': report.when,
            'path': str(crash.path) if crash else None, 'line': crash.lineno if crash else None,
            'message': crash.message if crash else str(report.longrepr),
            'full_traceback': str(report.longrepr)})


def pytest_sessionfinish(session, exitstatus):
    source = Path(os.environ['STAGE1_EVIDENCE_SOURCE']).resolve()
    for name, module in sorted(sys.modules.items()):
        path = getattr(module, '__file__', None)
        if name == 'trader' or name.startswith('trader.'):
            if path:
                REPORT['module_origins'][name] = str(Path(path).resolve())
                if not Path(path).resolve().is_relative_to(source):
                    REPORT.setdefault('wrong_source_imports', []).append(name)
    REPORT['exit_code'] = int(exitstatus)
    REPORT['counts'] = {key: sum(r['outcome'] == key and r['when'] == 'call' for r in REPORT['reports'])
                        for key in ('passed', 'failed', 'skipped')}
    REPORT['counts']['errors'] = sum(r['outcome'] == 'failed' and r['when'] != 'call' for r in REPORT['reports'])
    REPORT['counts']['deselected'] = len(REPORT['deselected'])
    Path(os.environ['STAGE1_EVIDENCE_RESULT']).write_text(json.dumps(REPORT, indent=2) + '\n')
