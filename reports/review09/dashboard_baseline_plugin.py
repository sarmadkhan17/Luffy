"""Restore only three reviewed module bodies in-process from the untouched review base.
Temporary journals/fixture routes only; no worktree/source/production state rewrite.
"""
import importlib
import subprocess

def pytest_sessionstart(session):
    for name in ('trader.api.graphql_schema', 'trader.dashboard.overview_desk', 'trader.dashboard.server'):
        module = importlib.import_module(name)
        path = name.replace('.', '/') + '.py'
        source = subprocess.check_output(['git', 'show', 'ef9fca8:' + path], text=True)
        exec(compile(source, path, 'exec'), module.__dict__)
