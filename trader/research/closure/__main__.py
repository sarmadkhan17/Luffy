"""Isolated entrypoint. Never touches production: every path is explicit.

  python -m trader.research.closure status  --store S [--study ID]
  python -m trader.research.closure seal    --store S --start-ms A --end-ms B   (FORWARD_SEAL; the clock is the trusted local clock, never an argument)
  python -m trader.research.closure create  --store S --spec spec.json
  python -m trader.research.closure step    --store S --study ID --budget-s 600
  python -m trader.research.closure calibrate --workdir D [--replicates N] [--preset smoke|final]

`create` accepts a spec whose "policy" is the string "CONFIGURED" (binds
config.yaml's current strictness) or an explicit TEST_ONLY policy object.
"""
import argparse
import json
import sys
import time

from . import ClosureRefused
from .store import Store


def scheduled_once(expected_revision, *, cfg=None, should_stop=None):
    """Bounded managed-role callback; no Kernel/venue/trading boot.

    Manager owns lifetime/identity. Existing study lock and schedule_events
    own single flight and durable progress. Approval gates remain unchanged.
    """
    from pathlib import Path
    from ... import runtime_identity
    runtime_identity.check_revision(expected_revision, Path(__file__).resolve().parents[3])
    if cfg is None:
        from ...core.config import load_config
        cfg = load_config()
    from . import study
    closure = (cfg.get('research') or {}).get('closure') or {}
    if not (cfg.get('research') or {}).get('enabled', False):
        return dict(status='NOT_ENABLED')
    return study.scheduled(cfg, slot_remaining_s=float(closure.get('slot_budget_s', 900)),
                           should_stop=should_stop or (lambda: False))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    scheduled_parser = sub.add_parser('scheduled')
    scheduled_parser.add_argument('--expected-revision', required=True)
    for name in ('status', 'seal', 'create', 'step'):
        p = sub.add_parser(name)
        p.add_argument('--store', required=True)
        if name in ('status', 'step'):
            p.add_argument('--study')
        if name == 'seal':
            p.add_argument('--start-ms', type=int, required=True)
            p.add_argument('--end-ms', type=int, required=True)
        if name == 'create':
            p.add_argument('--spec', required=True)
        if name == 'step':
            p.add_argument('--budget-s', type=float, default=600.0)
    cal = sub.add_parser('calibrate')
    cal.add_argument('--workdir', required=True)
    cal.add_argument('--replicates', type=int)
    mode=cal.add_mutually_exclusive_group()
    mode.add_argument('--preset', choices=('smoke', 'final'))
    mode.add_argument('--protocol', help='Frozen calibration protocol JSON, including exact search/window/intake settings')
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'scheduled':
            import signal
            import threading
            stopping = threading.Event()
            def stop(*_):
                stopping.set()
            prior = {s: signal.signal(s, stop) for s in (signal.SIGTERM, signal.SIGINT)}
            try:
                out = scheduled_once(a.expected_revision, should_stop=stopping.is_set)
            finally:
                for sig, handler in prior.items():
                    signal.signal(sig, handler)
        elif a.cmd == 'calibrate':
            from . import calibration
            if a.protocol:
                try:
                    with open(a.protocol) as source:proto=json.load(source)
                except (OSError,ValueError):
                    raise ClosureRefused('calibration_protocol_invalid','unreadable protocol JSON') from None
                if not isinstance(proto,dict) or proto.get('schema')!=calibration.PROTOCOL['schema']:
                    raise ClosureRefused('calibration_protocol_invalid','wrong protocol schema')
            else:
                proto = dict(calibration.PROTOCOL, **(calibration.SMOKE if (a.preset or 'smoke') == 'smoke' else {}))
            out = calibration.run(proto, a.workdir, replicates=a.replicates)
        else:
            from . import exposure, manifest, study
            store = Store(a.store)
            now = int(time.time() * 1000)
            if a.cmd == 'seal':
                out = exposure.seal(store, exposure.FORWARD_SEAL, a.start_ms, a.end_ms)
            elif a.cmd == 'create':
                spec = json.load(open(a.spec))
                if spec.get('policy') == 'CONFIGURED':
                    from ...core.config import load_config
                    spec['policy'] = manifest.configured_policy(load_config())
                out = dict(study_id=study.create_study(store, spec, now_ms=now))
            elif a.cmd == 'step':
                out = study.step(store, a.study, budget_s=a.budget_s)
            else:
                out = study.study_status(store, a.study) if a.study else dict(
                    studies=[study.study_status(store, r['study_id']) for r in store.studies()],
                    production_gates=dict(note='research.referee and research.handoff are not read or changed here'))
    except ClosureRefused as exc:
        print(json.dumps(dict(status='REFUSED', reason_code=exc.code, detail=str(exc))))
        return 2
    print(json.dumps(out, sort_keys=True, default=str))
    if a.cmd == 'scheduled' and out.get('status') in (
            'PREP_FAILED', 'CONFIG_INVALID', 'ERROR', 'CHILD_OWNERSHIP_UNKNOWN', 'CREATE_FAILED'):
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
