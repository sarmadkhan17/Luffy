# P4/C3 and P4/C4 reduced categorical certificate

Date: 2026-09-21

Status: **CERTIFIED** for P4/C3 and P4/C4 only.

The frozen unresolved representative in each family reduces analytically to a
single standard-Gaussian integral over two state and four linked singleton
factors (`r = 10/11`).  P4/C3 uses systematic/residual variance `1/10, 9/10`;
P4/C4 uses `3/5, 2/5`.  The certifier applies outward Arb Taylor/centered-moment
enclosures and independently guards every band with its direct interval range.
No random state, broader kernel run, population rework, or protocol change was
used.

## Primary certificates (224 bits)

The strict boundary is `20/31`.

| Cell | Conditional hit | Conditional miss | Population | Winners | Lift | Radius |
|---|---|---|---|---|---|---|
| P4/C3 | `[0.754712204448878, 0.858186016790569]` | `[0.891176556237041, 0.926728582940996]` | `[0.882600046704671, 0.894303709468138]` | `0 / 0 / 0` | `[0, 0]` | `0` |
| P4/C4 | `[0.647435666061937, 0.784434250555933]` | `[0.917155690491199, 0.960804609581829]` | `[0.886912820969780, 0.901829403337513]` | `0 / 0 / 0` | `[0, 0]` | `0` |

Every probability interval lies strictly above `20/31`.  Under the frozen
estimand, equal hit/miss/population winners give the same constant classifier,
so each signed macro-F1 lift is algebraically exactly `[0,0]`.  The radius
requirement is met; zero separation is not applicable to these true nulls.

## Independent replay (384 bits)

The replay used Taylor degree 16 and an independently finer initial partition
of 160 bands (primary: degree 14 and 128 bands).  It reproduced winner tuple
`0 / 0 / 0`, true-null classification, and exact lift `[0,0]` for both cells.
Its guarded hit, miss, and population intervals overlap their corresponding
primary intervals.  Replay result: **PASS** for P4/C3 and **PASS** for P4/C4.

## Runtime and scope

Primary certificate process runtimes total `0.3787817740230821 s`; replay
process runtimes total `0.5751863379846327 s`; total recorded process runtime is
`0.9539681120077148 s`.  P4/C3 used 128 primary and 160 replay bands.  P4/C4
resolved after 10 deterministic primary refinements (138 bands) and 7 replay
refinements (167 bands).

After incorporating the already frozen P5/C3 and P6/C4 results, the remaining
unresolved categorical families are P5/C4 and P6/C3.

Single next task: independently certify P5/C4 only with its existing analytic
reduction and the unchanged frozen estimand.
