#!/usr/bin/env python3
"""Complete only the 47 refused categorical equivalence kernels with Arb.

One-dimensional kernels use native adaptive validated integration. Existing
certified multidimensional PGF boxes are propagated and fail closed when they
cannot meet the frozen estimand acceptance gates. No RNG is constructed.
"""
from __future__ import annotations

import argparse
import hashlib
import json

from fractions import Fraction
from pathlib import Path

import flint
from flint import acb, arb, ctx


HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "m32_categorical_equivalence_20260920" / "certificate.json"
SPEC = HERE.parent / "m32_categorical_equivalence_20260920" / "estimand_spec.json"
OUTPUT = HERE / "certificate.json"
T = 16
STATE_TEXT = "0.6744897501960817"
LINKED_TEXT = "0.8416212335729143"
DELTA = Fraction(1, 10_000_000_000)
MAX_RADIUS = Fraction(1, 1_000_000_000_000)


def A(x: Fraction | int | str) -> arb:
    if isinstance(x, str):
        return arb(x)
    x = Fraction(x)
    return arb(f"{x.numerator}/{x.denominator}")


def frozen(text: str) -> arb:
    n, d = float(text).as_integer_ratio()
    return A(Fraction(n, d))


def qtail(x: arb) -> arb:
    return (x / A(2).sqrt()).erfc() / 2


def nonnegative_pad(bound):
    u=bound.upper()
    return arb(u/2,u/2)


def interval_record(x):
    lm,le=x.lower().man_exp(); um,ue=x.upper().man_exp()
    return {"lower_dyadic":{"mantissa":int(lm),"exponent":int(le)},
            "upper_dyadic":{"mantissa":int(um),"exponent":int(ue)},
            "decimal":x.str(40,more=True),
            "radius_decimal":x.rad().str(18,more=True,radius=False)}


def minus(count,which):
    out=list(count); out[which]-=1
    if out[which]<0: raise AssertionError("missing focal target")
    return tuple(out)


def normal_density(z):
    return (-z*z/2).exp() / (2*arb.pi()).sqrt()


def acb_membership(f, threshold, systematic, residual):
    return (1 + ((A(systematic).sqrt()*f-threshold)/(A(residual)*2).sqrt()).erf())/2


def acb_factor(ps, count, r):
    return (1-(1-r)*ps[0])**count[0] * (1-(1-r)*ps[1])**count[1]


def native_integral(func, tol):
    return acb.integral(func, -T, T, abs_tol=tol, rel_tol=tol, deg_limit=80,
                        eval_limit=400000, depth_limit=512, use_heap=True)


def integrate_native(spec, thresholds, tol):
    c=spec["correlation"]; r=A(Fraction(spec["r_n"],spec["r_d"]))
    def sector_value(f,count,mode="base"):
        ps=(acb_membership(f,thresholds[0],Fraction(3,5),Fraction(2,5)),
            acb_membership(f,thresholds[1],Fraction(3,5),Fraction(2,5)))
        base=acb_factor(ps,count,r)
        if mode=="base": return base
        ph=ps[spec["h_kind"]]
        if spec["is_target"]:
            focal=acb_factor(ps,minus(count,spec["h_kind"]),r)
            return ph*r*focal if mode=="hit" else (1-ph)*focal
        return ph*base if mode=="hit" else (1-ph)*base
    if c in (1,2):
        systematic=Fraction(3,10) if c==1 else Fraction(7,10); residual=1-systematic
        count=tuple(spec["target_counts"])
        def vals(x):
            ps=(acb_membership(x,thresholds[0],systematic,residual),
                acb_membership(x,thresholds[1],systematic,residual))
            base=acb_factor(ps,count,r); ph=ps[spec["h_kind"]]
            if spec["is_target"]:
                focal=acb_factor(ps,minus(count,spec["h_kind"]),r)
                return base,ph*r*focal,(1-ph)*focal
            return base,ph*base,(1-ph)*base
        raw=[native_integral(lambda x,a,k=k: normal_density(x)*vals(x)[k],tol).real for k in range(3)]
        tail=2*qtail(A(T))
    elif c==4:
        counts=tuple(tuple(z) for z in spec["sector_counts"]); sec=spec["h_sector"]
        def outer(x,k):
            def inner(y,_analytic):
                fs=(x,-x/2+A(3).sqrt()*y/2)
                bases=[sector_value(fs[s],counts[s]) for s in range(2)]
                value=bases[0]*bases[1] if k==0 else sector_value(fs[sec],counts[sec],"hit" if k==1 else "miss")*bases[1-sec]
                return normal_density(y)*value
            return normal_density(x)*native_integral(inner,tol)
        raw=[native_integral(lambda x,a,k=k: outer(x,k),tol).real for k in range(3)]
        tail=4*qtail(A(T))
    else:
        counts=[tuple(z) for z in spec["cluster_counts"]]; focal=spec["h_cluster"]
        groups={count:counts.count(count) for count in set(counts)}
        def outer(g,k):
            inner={}
            for count in groups:
                inner[count]=native_integral(lambda z,a,count=count: normal_density(z)*sector_value(
                    A(Fraction(1,10)).sqrt()*g+A(Fraction(3,5)).sqrt()*z,count),tol)
            result=acb(1)
            for count,ncount in groups.items():
                result *= inner[count]**(ncount-(1 if count==counts[focal] else 0))
            mode="base" if k==0 else "hit" if k==1 else "miss"
            result *= native_integral(lambda z,a: normal_density(z)*sector_value(
                A(Fraction(1,10)).sqrt()*g+A(Fraction(3,5)).sqrt()*z,counts[focal],mode),tol)
            return normal_density(g)*result
        raw=[native_integral(lambda g,a,k=k: outer(g,k),tol).real for k in range(3)]
        tail=66*qtail(A(T))
    q=qtail(thresholds[spec["h_kind"]]); padded=[x+nonnegative_pad(tail) for x in raw]
    return padded[0],padded[1]/q,padded[2]/(1-q),tail


def strict_argmax(ps):
    winners=[i for i in range(3) if all(i==j or ps[i].lower()>ps[j].upper() for j in range(3))]
    return winners[0] if len(winners)==1 else None


def class_probs(g):
    return A(Fraction(13,20))*g,A(Fraction(1,4))*g,1-A(Fraction(9,10))*g


def intersect(x,y):
    lo=x.lower() if x.lower()>y.lower() else y.lower(); hi=x.upper() if x.upper()<y.upper() else y.upper()
    if lo>hi: raise ArithmeticError("independent population enclosures are disjoint")
    return arb((lo+hi)/2,(hi-lo)/2)


def macro(ps_hit,ps_miss,q,pred_h,pred_m):
    pop=[q*ps_hit[c]+(1-q)*ps_miss[c] for c in range(3)]
    terms=[]
    for c in range(3):
        tp=(q*ps_hit[c] if pred_h==c else 0)+((1-q)*ps_miss[c] if pred_m==c else 0)
        predicted=(q if pred_h==c else 0)+((1-q) if pred_m==c else 0)
        fp=predicted-tp; fn=pop[c]-tp; den=2*tp+fp+fn
        terms.append(2*tp/den)
    return sum(terms,arb(0))/3


def classify(values,spec,thresholds):
    pg,gh,gm,_=values; q=qtail(thresholds[spec["h_kind"]])
    pg=intersect(pg,q*gh+(1-q)*gm)
    ph,pm,pp=class_probs(gh),class_probs(gm),class_probs(pg)
    wh,wm,wp=strict_argmax(ph),strict_argmax(pm),strict_argmax(pp)
    if None in (wh,wm,wp):
        return "refused",None,None,(wh,wm,wp),"possible_argmax_tie"
    if wh==wm==wp:
        return "true_null",None,arb(0),(wh,wm,wp),"algebraic_same_constant_classifier"
    lift=macro(ph,pm,q,wh,wm)-macro(ph,pm,q,wp,wp)
    radius=lift.rad()
    separated=(lift.lower()>=A(DELTA) or lift.upper()<=-A(DELTA))
    if radius<=A(MAX_RADIUS) and separated:
        sign=1 if lift.lower()>0 else -1
        return "non_null",sign,lift,(wh,wm,wp),"strict_argmax_and_zero_exclusion"
    return "refused",None,lift,(wh,wm,wp),"radius_or_zero_exclusion_failed"


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def record_ball(rec):
    lo=arb((int(rec["lower_dyadic"]["mantissa"]),int(rec["lower_dyadic"]["exponent"])))
    hi=arb((int(rec["upper_dyadic"]["mantissa"]),int(rec["upper_dyadic"]["exponent"])))
    return arb((lo+hi)/2,(hi-lo)/2)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--precision-bits",type=int,default=224)
    ap.add_argument("--abs-tol",default="1e-18")
    ap.add_argument("--output",type=Path,default=OUTPUT); args=ap.parse_args()
    if args.precision_bits<160 or A(args.abs_tol) > A("1e-14"): raise SystemExit("fail closed: weak configuration")
    ctx.prec=args.precision_bits; source=json.loads(SOURCE.read_text()); thresholds=(frozen(STATE_TEXT),frozen(LINKED_TEXT))
    refused={k:v for k,v in source["kernels"].items() if v["classification"]=="unclassifiable"}
    if len(refused)!=47: raise SystemExit(f"expected 47 refused kernels, got {len(refused)}")
    results={}
    for pos,(kid,old) in enumerate(sorted(refused.items()),1):
        spec=old["spec"]; c=spec["correlation"]
        values=(integrate_native(spec,thresholds,A(args.abs_tol)) if c in (1,2) else
                (record_ball(old["population_pgf"]),record_ball(old["hit_pgf"]),
                 record_ball(old["miss_pgf"]),arb(0)))
        cls,sign,lift,winners,reason=classify(values,spec,thresholds)
        results[kid]={"spec":spec,"pgf_method":("native_adaptive_acb_integral" if c in (1,2) else "propagated_existing_certified_arb_enclosures"),"classification":cls,"sign":sign,"strict_argmax":{"hit":winners[0],"miss":winners[1],"population":winners[2]},"reason":reason,
                      "population_pgf":interval_record(values[0]),"hit_pgf":interval_record(values[1]),"miss_pgf":interval_record(values[2]),
                      "signed_lift_enclosure":interval_record(lift) if lift is not None else None}
        print(f"[{pos:02d}/47] {kid} C{c} {cls}",flush=True)
    rows=[r for r in source["row_assignments"] if r["kernel_id"] in refused]
    certified={k for k,v in results.items() if v["classification"]!="refused"}
    newly=[r for r in rows if r["kernel_id"] in certified]
    cells=sorted({r["cell"] for r in rows}); remaining=sorted({r["cell"] for r in rows if r["kernel_id"] not in certified})
    newcells=sorted(set(cells)-set(remaining)); accepted=[v for v in results.values() if v["classification"]!="refused"]
    worst=max((v["signed_lift_enclosure"] for v in accepted),key=lambda z:A(z["radius_decimal"])) if accepted else None
    payload={"schema":"m3.2-categorical-arb-completion.v1","source_certificate_sha256":sha(SOURCE),"estimand_spec_sha256":sha(SPEC),
             "scope":"only 47 previously refused categorical kernels; persistence excluded","rng_constructed":False,"simulation_worlds_constructed":0,
             "thresholds_changed":False,"arithmetic":{"python_flint":flint.__version__,"flint":flint.__FLINT_VERSION__,"precision_bits":args.precision_bits,"method":"acb.integral adaptive validated Gauss-Legendre","absolute_tolerance":args.abs_tol,"relative_tolerance":args.abs_tol,"truncation":T},
             "acceptance":{"strict_interval_argmax":True,"possible_ties_fail_closed":True,"maximum_radius":"1e-12","minimum_zero_exclusion":"1e-10","exact_null":"algebraic identity only"},
             "counts":{"kernels_attempted":47,"kernels_certified":len(certified),"kernels_refused":47-len(certified),"rows_attempted":len(rows),"rows_newly_classified":len(newly),"true_null_kernels":sum(v["classification"]=="true_null" for v in results.values()),"non_null_kernels":sum(v["classification"]=="non_null" for v in results.values()),"cells_newly_classified":len(newcells)},
             "newly_classified_cells":newcells,"remaining_unresolved_categorical_cells":remaining,"categorical_truth_complete":not remaining,
             "worst_accepted_signed_lift_enclosure":worst,"kernels":results}
    payload["status"]="PASS" if not remaining else "PARTIAL_FAIL_CLOSED"; payload["implementation_sha256"]=sha(Path(__file__))
    args.output.write_text(json.dumps(payload,sort_keys=True,separators=(",",":"))+"\n")
    print(json.dumps({k:payload[k] for k in ("status","counts","newly_classified_cells","remaining_unresolved_categorical_cells","categorical_truth_complete")},indent=2))


if __name__=="__main__": main()
