#!/usr/bin/env python3
"""Independent higher-precision replay of every accepted classification."""
from __future__ import annotations
import hashlib, json, time
from fractions import Fraction
from pathlib import Path
import flint
from flint import acb, arb, ctx

HERE=Path(__file__).resolve().parent
SOURCE=HERE/"certificate.json"; OUTPUT=HERE/"replay.json"
T=16

def A(x):
    x=Fraction(x); return arb(f"{x.numerator}/{x.denominator}")
def threshold(text):
    n,d=float(text).as_integer_ratio(); return A(Fraction(n,d))
def density(x): return (-x*x/2).exp()/(2*arb.pi()).sqrt()
def member(x,t,s,r): return (1+((A(s).sqrt()*x-t)/(A(r)*2).sqrt()).erf())/2
def factor(ps,count,r): return (1-(1-r)*ps[0])**count[0]*(1-(1-r)*ps[1])**count[1]
def integrate(fn):
    return acb.integral(fn,-T,T,abs_tol=arb("1e-24"),rel_tol=arb("1e-24"),
                        deg_limit=120,eval_limit=800000,depth_limit=768,use_heap=False).real

def pgfs(spec):
    ts=(threshold("0.6744897501960817"),threshold("0.8416212335729143"))
    systematic=Fraction(3,10) if spec["correlation"]==1 else Fraction(7,10)
    residual=1-systematic; count=tuple(spec["target_counts"]); r=A(Fraction(spec["r_n"],spec["r_d"]))
    def vals(x):
        ps=(member(x,ts[0],systematic,residual),member(x,ts[1],systematic,residual))
        base=factor(ps,count,r); ph=ps[spec["h_kind"]]
        if spec["is_target"]:
            reduced=list(count); reduced[spec["h_kind"]]-=1; focal=factor(ps,reduced,r)
            return base,ph*r*focal,(1-ph)*focal
        return base,ph*base,(1-ph)*base
    raw=[integrate(lambda x,a,k=k:density(x)*vals(x)[k]) for k in range(3)]
    tail=2*(A(T)/A(2).sqrt()).erfc()/2; u=tail.upper(); pad=arb(u/2,u/2)
    q=(ts[spec["h_kind"]]/A(2).sqrt()).erfc()/2
    return raw[0]+pad,(raw[1]+pad)/q,(raw[2]+pad)/(1-q),q

def probs(g): return A(Fraction(13,20))*g,A(Fraction(1,4))*g,1-A(Fraction(9,10))*g
def winner(ps):
    winners=[i for i in range(3) if all(i==j or ps[i].lower()>ps[j].upper() for j in range(3))]
    return winners[0] if len(winners)==1 else None
def macro(ph,pm,q,a,b):
    pop=[q*ph[c]+(1-q)*pm[c] for c in range(3)]; terms=[]
    for c in range(3):
        tp=(q*ph[c] if a==c else 0)+((1-q)*pm[c] if b==c else 0)
        pred=(q if a==c else 0)+((1-q) if b==c else 0)
        terms.append(2*tp/(2*tp+(pred-tp)+(pop[c]-tp)))
    return sum(terms,arb(0))/3
def primary_ball(rec):
    lo=rec["lower_dyadic"]; hi=rec["upper_dyadic"]
    low=arb((int(lo["mantissa"]),int(lo["exponent"])))
    high=arb((int(hi["mantissa"]),int(hi["exponent"])))
    return arb((low+high)/2,(high-low)/2)

def main():
    ctx.prec=320; started=time.perf_counter(); primary=json.loads(SOURCE.read_text()); rows=[]
    for kid,record in sorted(primary["kernels"].items()):
        if record["classification"]=="refused": continue
        gp,gh,gm,q=pgfs(record["spec"]); ph,pm=probs(gh),probs(gm)
        pp=tuple(q*ph[c]+(1-q)*pm[c] for c in range(3))
        wh,wm,wp=winner(ph),winner(pm),winner(pp)
        lift=arb(0) if wh==wm==wp else macro(ph,pm,q,wh,wm)-macro(ph,pm,q,wp,wp)
        expected=(record["strict_argmax"]["hit"],record["strict_argmax"]["miss"],record["strict_argmax"]["population"])
        pball=primary_ball(record["signed_lift_enclosure"])
        overlap=not (lift.upper()<pball.lower() or lift.lower()>pball.upper())
        sign=1 if lift.lower()>0 else -1 if lift.upper()<0 else None
        ok=(wh,wm,wp)==expected and sign==record["sign"] and overlap
        rows.append({"kernel_id":kid,"result":"PASS" if ok else "FAIL_CLOSED",
                     "argmax":[wh,wm,wp],"signed_lift_enclosure":lift.str(40,more=True),
                     "overlaps_primary_enclosure":overlap})
    complete=len(rows)==primary["counts"]["kernels_certified"]; passed=complete and all(x["result"]=="PASS" for x in rows)
    payload={"schema":"m3.2-categorical-arb-completion-replay.v1","result":"PASS" if passed else "FAIL_CLOSED",
             "independent_implementation":True,"precision_bits":320,"absolute_tolerance":"1e-24",
             "relative_tolerance":"1e-24","kernels_replayed":len(rows),
             "all_accepted_classifications_replayed":complete,"rows":rows,
             "source_certificate_sha256":hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
             "implementation_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             "python_flint":flint.__version__,"flint":flint.__FLINT_VERSION__}
    OUTPUT.write_text(json.dumps(payload,sort_keys=True,separators=(",",":"))+"\n")
    print(json.dumps({k:payload[k] for k in ("result","kernels_replayed","all_accepted_classifications_replayed")},indent=2))
    if not passed: raise SystemExit(1)
if __name__=="__main__": main()
