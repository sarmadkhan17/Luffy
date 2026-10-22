"""Vault notes are published atomically: a reader sees a whole old note or a whole new one.

Atomic replacement fixes partial visibility only. It does not serialise writers:
two complete notes built from different state still race, and the last one to
publish wins (see test_last_complete_writer_wins_even_if_it_is_stale).
"""
import os
import stat
import threading
import time
from pathlib import Path

import pytest

import trader.knowledge.vault as V
from trader.core.journal import Journal
from trader.knowledge.vault import _atomic_write


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setattr(V, 'VAULT', tmp_path / 'vault')
    return V.Vault(Journal(tmp_path / 'j.db'))


def leftovers(directory):
    return sorted(p.name for p in Path(directory).rglob('*') if p.name.endswith('.tmp'))


# ── same bytes, mode and errors as Path.write_text ─────────────────────────
def test_content_and_new_file_mode_match_write_text(tmp_path):
    text = '---\ntype: ledger\n---\n# σ Ledger\n| a | 1 |\n'
    _atomic_write(tmp_path / 'new.md', text)
    Path(tmp_path / 'ref.md').write_text(text)
    assert (tmp_path / 'new.md').read_bytes() == (tmp_path / 'ref.md').read_bytes()
    assert stat.S_IMODE((tmp_path / 'new.md').stat().st_mode) == stat.S_IMODE((tmp_path / 'ref.md').stat().st_mode)
    assert leftovers(tmp_path) == []


def test_existing_file_keeps_its_mode_like_write_text(tmp_path):
    target = tmp_path / 'n.md'
    target.write_text('old')
    os.chmod(target, 0o640)
    _atomic_write(target, 'new')
    assert target.read_text() == 'new' and stat.S_IMODE(target.stat().st_mode) == 0o640


def test_symlinked_note_updates_its_target_and_stays_a_link(tmp_path):
    real = tmp_path / 'real.md'
    real.write_text('old')
    link = tmp_path / 'link.md'
    link.symlink_to(real)
    _atomic_write(link, 'new')
    assert link.is_symlink() and real.read_text() == 'new' and link.read_text() == 'new'


def test_missing_directory_raises_like_write_text(tmp_path):
    with pytest.raises(FileNotFoundError):
        _atomic_write(tmp_path / 'nope' / 'n.md', 'x')
    with pytest.raises(FileNotFoundError):
        (tmp_path / 'nope' / 'n.md').write_text('x')


def test_vault_write_returns_the_path_and_the_same_text(vault):
    p = vault._write('40 Regimes/X.md', 'body\n')
    assert p == V.VAULT / '40 Regimes/X.md' and p.read_text() == 'body\n'


# ── failure before replacement keeps the previous complete file ────────────
@pytest.mark.parametrize('where', ['replace', 'fsync', 'write'])
def test_failure_before_publication_preserves_the_previous_file(tmp_path, monkeypatch, where):
    target = tmp_path / 'n.md'
    target.write_text('previous complete note\n')
    if where == 'replace':
        monkeypatch.setattr(os, 'replace', lambda *a: (_ for _ in ()).throw(OSError(28, 'no space')))
    elif where == 'fsync':
        monkeypatch.setattr(os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError(5, 'io error')))
    else:
        real = os.fdopen

        class Boom:
            def __init__(self, f): self.f = f
            def __enter__(self): return self
            def __exit__(self, *a): self.f.close()
            def write(self, text): self.f.write(text[:5]); raise OSError(28, 'no space')
        monkeypatch.setattr(os, 'fdopen', lambda fd, mode: Boom(real(fd, mode)))
    with pytest.raises(OSError):
        _atomic_write(target, 'NEW ' * 1000)
    assert target.read_text() == 'previous complete note\n' and leftovers(tmp_path) == []


def test_failed_first_write_leaves_no_file_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(os, 'replace', lambda *a: (_ for _ in ()).throw(OSError(28, 'no space')))
    with pytest.raises(OSError):
        _atomic_write(tmp_path / 'n.md', 'x')
    assert list(tmp_path.iterdir()) == []


# ── exclusive publication (post-mortems never overwrite) ───────────────────
def test_exclusive_publication_never_overwrites(tmp_path):
    target = tmp_path / 'p.md'
    _atomic_write(target, 'first\n', replace=False)
    with pytest.raises(FileExistsError):
        _atomic_write(target, 'second\n', replace=False)
    assert target.read_text() == 'first\n' and leftovers(tmp_path) == []


def test_same_minute_postmortems_keep_the_first_note_and_add_a_suffix(vault, monkeypatch):
    import datetime as real_dt
    fixed = real_dt.datetime(2026, 10, 22, 12, 34, tzinfo=real_dt.timezone.utc)

    class FixedDT(real_dt.datetime):
        @classmethod
        def now(cls, tz=None): return fixed
    monkeypatch.setattr(V, 'datetime', FixedDT)
    vault.incident_note('Book health', 'first body')
    vault.incident_note('Book health', 'second body')
    notes = sorted(p.name for p in (V.VAULT / '30 Postmortems').glob('*.md'))
    assert notes == ['20261022-1234 Book health (1).md', '20261022-1234 Book health.md']
    assert 'first body' in (V.VAULT / '30 Postmortems/20261022-1234 Book health.md').read_text()
    assert 'second body' in (V.VAULT / '30 Postmortems/20261022-1234 Book health (1).md').read_text()
    assert leftovers(V.VAULT) == []


def test_temporary_files_are_hidden_from_note_listings(tmp_path, monkeypatch):
    seen = []
    real = os.replace

    def spy(src, dst):
        seen.append(Path(src).name)
        return real(src, dst)
    monkeypatch.setattr(os, 'replace', spy)
    _atomic_write(tmp_path / 'Note.md', 'x')
    assert seen[0].startswith('.') and not seen[0].endswith('.md')       # not matched by rglob("*.md")


# ── concurrent readers ──────────────────────────────────────────────────────
def hammer(target, texts, writers, seconds, write):
    """Writers publish whole texts in a loop; a reader checks every read is one of them."""
    stop, torn, reads, errors = threading.Event(), [], [0], []
    allowed = set(texts)

    def writer(i):
        try:
            n = 0
            while not stop.is_set():
                write(target, texts[(i + n) % len(texts)]); n += 1
        except Exception as exc:      # noqa: BLE001
            errors.append(repr(exc)); stop.set()

    def reader():
        while not stop.is_set():
            try:
                got = target.read_text()
            except FileNotFoundError:
                continue
            reads[0] += 1
            if got not in allowed:
                torn.append(len(got))
    threads = [threading.Thread(target=writer, args=(i,), daemon=True) for i in range(writers)]
    threads.append(threading.Thread(target=reader, daemon=True))
    for t in threads: t.start()
    time.sleep(seconds); stop.set()
    for t in threads: t.join(10)
    return reads[0], torn, errors


TEXTS = [('---\ntype: ledger\n---\n# Agent Ledger\n' + ''.join(f'| row {i} | {k} |\n' for i in range(4000))) for k in range(3)]


@pytest.mark.parametrize('writers', [1, 2, 3])
def test_readers_never_see_a_partial_note(tmp_path, writers):
    target = tmp_path / 'Agent Ledger.md'
    _atomic_write(target, TEXTS[0])
    reads, torn, errors = hammer(target, TEXTS, writers, 2.0, _atomic_write)
    assert errors == [] and reads > 100 and torn == []
    assert target.read_text() in TEXTS and leftovers(tmp_path) == []


def test_control_the_old_write_is_visibly_torn(tmp_path):
    """Negative control: the same harness against Path.write_text does observe partial files."""
    target = tmp_path / 'Agent Ledger.md'
    target.write_text(TEXTS[0])
    for _ in range(5):
        reads, torn, errors = hammer(target, TEXTS, 1, 2.0, lambda p, t: p.write_text(t))
        if torn:
            break
    if not torn:
        pytest.skip('tear not observed on this host; the atomic tests above are still binding')
    assert torn


def test_real_vault_writers_race_without_torn_notes(vault, monkeypatch):
    """The identified writers (maintenance job, boot-time ledger, dashboard post-mortem) on one vault."""
    from trader.brain.postmortem import run_postmortem
    import yaml
    cfg = yaml.safe_load(Path('config.yaml').read_text())
    journal = vault.journal
    vault.agent_ledger(); vault.write_moc()
    ledger, moc = V.VAULT / '40 Regimes/Agent Ledger.md', V.VAULT / 'MOC.md'
    stop, bad, errors = threading.Event(), [], []

    def job(fn):
        def run():
            try:
                while not stop.is_set():
                    fn()
            except Exception as exc:      # noqa: BLE001
                errors.append(repr(exc)); stop.set()
        return run

    def maintenance():
        v = V.Vault(journal); v.refresh_strategy_notes(); v.daily_review(); v.agent_ledger(); v.write_moc()

    def reader():
        while not stop.is_set():
            for p, marker in ((ledger, '# Agent Ledger'), (moc, '# Map of Content')):
                try:
                    t = p.read_text()
                except FileNotFoundError:
                    continue
                if not (t.startswith('---') and t.endswith('\n') and marker in t):
                    bad.append((p.name, len(t)))
    threads = [threading.Thread(target=job(f), daemon=True) for f in (
        maintenance, lambda: V.Vault(journal).agent_ledger(), lambda: run_postmortem(journal, V.Vault(journal), cfg=cfg))]
    threads.append(threading.Thread(target=reader, daemon=True))
    for t in threads: t.start()
    time.sleep(4); stop.set()
    for t in threads: t.join(30)
    assert errors == [] and bad == []
    assert leftovers(V.VAULT) == []


def test_last_complete_writer_wins_even_if_it_is_stale(tmp_path):
    """Documented limit: atomic publication does not order writers or detect stale content."""
    target = tmp_path / 'Agent Ledger.md'
    older_state, newer_state = 'ledger built from the older state\n', 'ledger built from the newer state\n'
    _atomic_write(target, newer_state)         # the writer with newer data publishes first ...
    _atomic_write(target, older_state)         # ... and a slower writer holding older data replaces it
    assert target.read_text() == older_state    # both complete; the stale one won
