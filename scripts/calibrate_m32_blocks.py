"""Synthetic-only CLI. No empirical-data input parameter or trading imports."""
import argparse
import hashlib
import json
from pathlib import Path

from trader.cognition import m32_block_calibration as C


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worlds',type=int,default=1000)
    p.add_argument('--family-worlds',type=int,default=250)
    p.add_argument('--draws',type=int,default=2000)
    p.add_argument('--blocks',type=int,default=48)
    p.add_argument('--seed',type=int,default=20260919)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists(): p.error('output already exists; do not overwrite calibration evidence')
    def progress(r):
        print(json.dumps({k:r[k] for k in ('scenario','calendar_mode','effect','family_size','tested','refused','rejection_rate')}),flush=True)
    report=C.run_suite(args.worlds,args.draws,args.blocks,args.seed,args.family_worlds,progress)
    report['code_manifest']={str(path):hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in (Path(C.__file__),Path(__file__))}
    encoded=json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f: f.write(encoded)
    print(str(args.output))


if __name__=='__main__': main()
