"""One operator view: is the book able to trade, and is it protected?

Answers the four questions that actually matter between trades, in the order
you need them:

  1. PROTECTION — does every open position have exactly one live stop on the
     venue, sized to the position? Binance books stops as ALGO orders that
     fetch_open_orders() cannot see, so "the dashboard shows no orders" is
     not evidence either way. This asks the endpoint that knows.
  2. REACH — is each live strategy actually evaluated, and how far is each of
     its symbols from firing? "No trades" reads identically whether the setup
     has not appeared or the strategy was never reached. An empty regime
     filter once meant the latter for weeks.
  3. HEALTH — is the live record still inside the envelope the strategy was
     admitted on? A 38.85% win rate throws three losses in a row a quarter of
     the time; that is not breakage and must not read as breakage.
  4. GATE — what did the strategy-signal gate refuse, and did those refusals
     cost anything? Reopening the analyst blend must be an evidence decision.

    ./venv/bin/python -m scripts.monitor
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.config import load_config          # noqa: E402

if TYPE_CHECKING:
    from trader.core.journal import Journal
    from trader.data.feed import DataFeed

DB = "data/luffy.db"


def _rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "─" * 72)


# ── 1. protection ────────────────────────────────────────────────────────
def protection(j: Journal, ex) -> list[str]:
    from trader.engine import protective as P

    _rule("1. PROTECTION — every position, one correctly sized stop")
    alarms: list[str] = []
    try:
        positions = {P.venue_key(p["symbol"]): p for p in ex.fetch_positions()
                     if float(p.get("contracts") or 0)}
    except Exception as e:
        print(f"  cannot read positions: {e}")
        return [f"positions unreadable: {e}"]
    stops = P.open_stops(ex)
    by_sym: dict[str, list] = {}
    for s in stops:
        by_sym.setdefault(P.venue_key(s["symbol"]), []).append(s)

    if not positions:
        print("  no open positions")
    for key, pos in positions.items():
        held = float(pos["contracts"])
        mine = by_sym.get(key, [])
        if not mine:
            alarms.append(f"{key} is NAKED — {held} held, no stop")
            print(f"  \033[31m✗ {key:10} {held:>12} held — NO STOP\033[0m")
            continue
        for s in mine:
            short = abs(s["amount"] - held) > held * 0.01
            flag = "\033[33m(size mismatch)\033[0m" if short else ""
            print(f"  {'✓' if len(mine) == 1 and not short else '!'} "
                  f"{key:10} {held:>12} held · stop {s['amount']:>12} "
                  f"@ {s['stop_price']:<12} {flag}")
            if short:
                alarms.append(f"{key} stop covers {s['amount']} of {held}")
        if len(mine) > 1:
            alarms.append(f"{key} carries {len(mine)} stops — ratchet residue")

    orphans = [s for k, v in by_sym.items() if k not in positions for s in v]
    for s in orphans:
        alarms.append(f"orphan stop {s['id']} on {s['symbol']}")
        print(f"  \033[31m✗ ORPHAN {s['symbol']:10} {s['side']} "
              f"{s['amount']} @ {s['stop_price']}\033[0m")
    return alarms


# ── 2. reach ─────────────────────────────────────────────────────────────
def reach(j: Journal, feed: DataFeed) -> list[str]:
    from trader.strategy.compile import compile_spec

    _rule("2. REACH — is the book evaluated, and how close is it to firing?")
    alarms: list[str] = []
    specs = [sp for _r, sp in j.list_specs(["paper", "active"])]
    if not specs:
        print("  \033[31mno tradeable strategy in the book\033[0m")
        return ["book is empty — nothing can trade"]
    for spec in specs:
        syms = list((spec.universe or {}).get("include") or [])
        print(f"  {spec.name}  [{spec.id}]  {spec.timeframe}  "
              f"direction={spec.direction}  {len(syms)} symbols")
        ev = compile_spec(spec).to_evaluator()
        rows = []
        for sym in syms:
            try:
                df = feed.cached_ohlcv(sym, spec.timeframe, limit=400)
                if df is None or len(df) < 120:
                    rows.append((sym, None, None, "thin history"))
                    continue
                px = float(df["close"].iloc[-1])
                hi = float(df["high"].rolling(100).max().shift(1).iloc[-1])
                lo = float(df["low"].rolling(100).min().shift(1).iloc[-1])
                up = (hi - px) / px * 100      # % to the upside break
                dn = (px - lo) / px * 100      # % to the downside break
                rows.append((sym, up, dn, ""))
            except Exception as e:
                rows.append((sym, None, None, str(e)[:40]))
        rows.sort(key=lambda r: min([x for x in (r[1], r[2])
                                     if x is not None] or [9e9]))
        for sym, up, dn, note in rows:
            if up is None:
                print(f"     {sym:12} {note}")
                continue
            near = min(up, dn)
            side = "LONG" if up <= dn else "SHORT"
            colour = "\033[32m" if near < 1.0 else ""
            end = "\033[0m" if colour else ""
            print(f"     {colour}{sym:12} {near:6.2f}% from a {side:5} break"
                  f"   (up {up:5.2f}% / down {dn:5.2f}%){end}")
        if ev is None:
            alarms.append(f"{spec.id} does not compile to an evaluator")
    return alarms


# ── 3. health ────────────────────────────────────────────────────────────
def health(j: Journal) -> list[str]:
    from trader.strategy.health import assess_health

    _rule("3. HEALTH — is the live record inside the validated envelope?")
    alarms: list[str] = []
    for row, spec in j.list_specs(["paper", "active"]):
        exp = float((spec.provenance or {}).get("expected_winrate") or 0)
        trades = j.query(
            "SELECT realized_pnl FROM trades WHERE strategy_id=? "
            "AND status!='open'", (spec.id,))
        wins = sum(1 for t in trades if float(t["realized_pnl"] or 0) > 0)
        losses = len(trades) - wins
        if not exp:
            print(f"  {spec.name}: no validated win rate recorded")
            continue
        if not trades:
            print(f"  {spec.name}: \033[33mNO LIVE TRADES YET\033[0m — "
                  f"validated at {exp:.2%}; everything known is backtest")
            continue
        h = assess_health(exp, wins, losses)
        colour = {"DIVERGED": "\033[31m", "CONSISTENT": "\033[32m"}.get(
            h.verdict, "\033[33m")
        print(f"  {spec.name}: {colour}{h.summary}\033[0m")
        if h.verdict == "DIVERGED":
            alarms.append(f"{spec.id} has DIVERGED from its envelope")
    return alarms


# ── 4. gate ──────────────────────────────────────────────────────────────
def gate(j: Journal) -> list[str]:
    _rule("4. GATE — what the strategy-signal gate refused, and its cost")
    vetoed = j.query(
        "SELECT COUNT(*) n FROM decisions WHERE skip_reason LIKE '%strategy%'")
    n = int(vetoed[0]["n"]) if vetoed else 0
    print(f"  directional calls refused for want of a strategy: {n}")
    graded = j.query(
        "SELECT o.fwd_ret_4h r FROM outcomes o JOIN decisions d "
        "ON d.id=o.decision_id WHERE d.skip_reason LIKE '%strategy%' "
        "AND o.fwd_ret_4h IS NOT NULL")
    if not graded:
        print("  none graded yet — the 4h horizon has not elapsed")
        return []
    rets = [float(g["r"]) for g in graded]
    mean = sum(rets) / len(rets)
    wins = sum(1 for x in rets if x > 0)
    print(f"  graded at 4h: n={len(rets)}  mean={mean * 100:+.3f}%  "
          f"win={wins / len(rets):.1%}")
    if mean > 0.002 and len(rets) >= 60:
        print("  \033[33mthe gate may be refusing calls that pay — "
              "worth re-measuring\033[0m")
        return ["gate is refusing profitable calls; re-measure"]
    print("  the refusals are not costing anything measurable")
    return []


def _paused_store(path, role, health, now, *, journal=False):
    """Bounded SQL reads only; never resolve an existing write failure."""
    from contextlib import closing
    import sqlite3
    import time
    started = time.monotonic()
    result = dict(component=role, verification='bounded_read_only', committed=False)
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError('store_missing_or_symlink')
        with closing(sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
            db.execute('PRAGMA query_only=ON')
            db.set_progress_handler(lambda: int(time.monotonic() - started > 1), 1000)
            db.execute('BEGIN')
            db.execute('SELECT name FROM sqlite_master LIMIT 1').fetchone()
            if journal:
                # No credentialed venue client is instantiated here. A stale or
                # absent retained receipt is uncertainty, never current safety.
                from trader.observability.preflight import _protection
                db.row_factory = sqlite3.Row
                try:
                    receipt = _protection(db, now)
                    result['venue_protection'] = dict(status='VERIFIED', receipt=receipt,
                                                       source='retained_venue_evidence')
                except Exception as exc:
                    result['venue_protection'] = dict(status='UNAVAILABLE', error_type=type(exc).__name__,
                                                       source='retained_venue_evidence')
            db.rollback()
        result['status'] = 'AVAILABLE'
    except (OSError, ValueError, sqlite3.Error) as exc:
        result.update(status='UNAVAILABLE', error_type=type(exc).__name__)
    health.observe(role + '_readonly', None if result['status'] == 'AVAILABLE' else 'READ_UNAVAILABLE', context=result)
    if journal:
        venue = result.get('venue_protection', dict(status='UNAVAILABLE', source='retained_venue_evidence'))
        health.observe('paused_venue_protection', None if venue['status'] == 'VERIFIED' else 'VENUE_STATE_UNCONFIRMED', context=venue)
    return result


def _paused_disk(root, cfg, health):
    import os
    from trader.observability.preflight import MIN_FREE_BYTES
    result = dict(component='disk', verification='filesystem_metadata')
    try:
        minimum = (cfg.get('safety_monitor') or {}).get('min_free_bytes', MIN_FREE_BYTES)
        if type(minimum) is not int or minimum <= 0:
            raise ValueError('invalid_disk_threshold')
        fs = os.statvfs(root)
        result.update(free_bytes=fs.f_bavail * fs.f_frsize, min_free_bytes=minimum,
                      read_only=bool(fs.f_flag & os.ST_RDONLY))
        result['status'] = 'AVAILABLE' if not result['read_only'] and result['free_bytes'] >= minimum else 'CAPACITY_OR_READONLY'
    except (OSError, ValueError) as exc:
        result.update(status='UNAVAILABLE', error_type=type(exc).__name__)
    health.observe('paused_disk', None if result['status'] == 'AVAILABLE' else result['status'], context=result)
    return result


def _pending_managed_kernel(root, identity, health, *, manager=None, proc_root=Path('/proc')):
    """Recognize only the owned, fenced pre-lock child; never call it RUNNING.

    Its normal child admission still checks revision, storage, protection and
    FROZEN controls before taking the instance lock. Heartbeat alarms continue.
    """
    import os
    import re
    import stat
    import time
    from trader import runtime_identity as ri
    from trader.observability import managed
    try:
        if (identity.get('state') != 'UNLOCKED_PROCESS' or identity.get('reasons')
                or identity.get('holder_pids') or len(identity.get('processes', [])) != 1):
            return False
        value = health.read()
        fence = value.get('bootstrap', {})
        token, revision = fence.get('token'), fence.get('intended_revision')
        if (value.get('recovery_required') is not True or fence.get('status') != 'PENDING'
                or not isinstance(token, str) or not re.fullmatch('[0-9a-f]{32}', token)
                or not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{40}', revision)):
            return False
        root = Path(root).resolve()
        path = root / 'data/bootstrap' / token / 'observers.json'
        st = path.lstat()
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid()
                or st.st_mode & 0o022 or st.st_nlink != 1
                or not 0 <= time.time() - st.st_mtime <= 240):
            return False
        import json
        manifest = json.loads(path.read_text())
        units = ['luffy-bootstrap-' + token + '-' + mode for mode in ('heartbeat-only', 'safety-only')]
        if (manifest.get('status') != 'COVERED' or manifest.get('cleanup') != 'OWNER_ONLY'
                or manifest.get('root') != str(root) or manifest.get('revision') != revision
                or manifest.get('token') != token or manifest.get('units') != units):
            return False
        process = identity['processes'][0]
        pid, ticks = process['pid'], process['start_ticks']
        if (type(pid) is not int or pid <= 0 or type(ticks) is not int or ticks <= 0
                or process.get('revision') != revision or process.get('token') != token):
            return False
        record = identity.get('record')
        if record and ri.proc_start_ticks(record['pid'], proc_root) == record['start_ticks']:
            return False
        if (proc_root / str(pid) / 'cwd').resolve() != root:
            return False
        command = [str(root / 'venv/bin/python'), '-B', '-m', 'trader.kernel',
                   '--expect-revision', revision, '--bootstrap-token', token]
        return not managed.check_identity(manager or managed.UserManager(),
            'luffy-kernel-' + token, pid, ticks, proc_root, command)
    except (OSError, ValueError, TypeError, KeyError, managed.ManagedUnavailable):
        return False


def safety_check(root, cfg, *, clock=None, sink=None, heartbeat_only=False, integrity_only=False,
                 identity_probe=None):
    """Independent local safety pass; no Journal initialization or venue calls."""
    import time
    from trader.observability.safety import SafetyHealth, SafetyObserver, HeartbeatPolicy
    from trader.observability.pause_state import status
    root = Path(root)
    pauses = status(root)
    # Never follow a substituted data directory or safety-health lock just to
    # report that its ownership could not be trusted.
    if not pauses['directory_valid'] or not pauses['locks']['safety_health.lock']['valid']:
        return dict(schema='luffy-safety-observation.v1', pause_state=pauses,
                    components=[dict(component='control_paths', status='UNAVAILABLE')],
                    health=dict(recovery_required=True))
    health = SafetyHealth(root / 'data/safety_health.json', clock=clock or time.time, sink=sink)
    if pauses['safety_monitor_paused']:
        return dict(schema='luffy-safety-observation.v1', pause_state=pauses,
                    components=[dict(component='safety_monitor', status='DISABLED', reason='safety_monitor.off')],
                    health=health.read())
    health.observe('control_paths', None if pauses['valid'] else 'UNSAFE_CONTROL_PATH', context=pauses)
    observer = SafetyObserver(health, clock=clock or time.time)
    # Heartbeat-only has its own host-side schedule: database maintenance can
    # never hold its observation behind a scan or a contended write.
    intentionally_stopped = False
    if pauses['watchdog_paused']:
        if identity_probe is None:
            from trader.runtime_identity import status_only
            identity_probe = status_only
        try:
            identity = identity_probe(root)
        except Exception:
            identity = dict(state='UNKNOWN')
        intentionally_stopped = identity.get('state') in ('NONE', 'STALE_RECORD')
        pending_owned = _pending_managed_kernel(root, identity, health)
        health.observe('paused_kernel_identity', None if intentionally_stopped or pending_owned or (
                           identity.get('state') == 'RUNNING' and identity.get('verified') is True)
                       else 'IDENTITY_UNCONFIRMED', context=dict(state=identity.get('state')))
    results = ([dict(component='luffy', status='INTENTIONALLY_STOPPED', reason='watchdog.off')]
               if intentionally_stopped else
               [observer.heartbeat(root / 'data/heartbeat_luffy.json', 'luffy', HeartbeatPolicy.configured(cfg))])
    if not heartbeat_only:
        stores = [(root / 'data/luffy.db', 'journal')]
        stores += [(root / s['path'], s['role']) for s in (cfg.get('safety_monitor') or {}).get('critical_stores', [])]
        if pauses['watchdog_paused']:
            results.append(_paused_disk(root, cfg, health))
        for path, role in stores:
            if pauses['watchdog_paused'] and not integrity_only:
                results.append(_paused_store(path, role, health, (clock or time.time)(), journal=role == 'journal'))
            else:
                method = observer.integrity if integrity_only else observer.store
                results.append(method(path, role, **({'journal':True} if role == 'journal' and not integrity_only else {})))
    return dict(schema='luffy-safety-observation.v1', components=results, pause_state=pauses, health=health.read())


def main() -> int:
    if any(flag in sys.argv for flag in ('--status','--safety-only','--heartbeat-only','--integrity-only')):
        import json
        from trader.core.config import ROOT
        from trader.observability.pause_state import status
        pauses = status(ROOT)
        if '--status' in sys.argv:
            print(json.dumps(pauses, sort_keys=True))
            return 0 if pauses['valid'] else 2
        cfg = {} if pauses['safety_monitor_paused'] else load_config()
        result = safety_check(ROOT, cfg, sink=lambda event: print(json.dumps(event), file=sys.stderr),
                              heartbeat_only='--heartbeat-only' in sys.argv, integrity_only='--integrity-only' in sys.argv)
        print(json.dumps(result, sort_keys=True))
        return int(bool(result['health']['recovery_required']))
    cfg = load_config()
    from trader.core.journal import Journal
    from trader.data.feed import DataFeed, make_exchange

    j = Journal(DB)
    ex = make_exchange("futures",
                       demo=bool(cfg.get("exchange", {}).get("demo", True)),
                       with_keys=True)
    feed = DataFeed()
    alarms: list[str] = []
    alarms += protection(j, ex)
    alarms += reach(j, feed)
    alarms += health(j)
    alarms += gate(j)

    _rule("VERDICT")
    if not alarms:
        print("  \033[32mnothing needs attention\033[0m")
        return 0
    for a in alarms:
        print(f"  \033[31m• {a}\033[0m")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
