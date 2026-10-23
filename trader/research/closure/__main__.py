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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
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
    cal.add_argument('--preset', default='smoke', choices=('smoke', 'final'))
    a = ap.parse_args(argv)
    try:
        if a.cmd == 'calibrate':
            from . import calibration
            proto = dict(calibration.PROTOCOL, **(calibration.SMOKE if a.preset == 'smoke' else {}))
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
    return 0


if __name__ == '__main__':
    sys.exit(main())
