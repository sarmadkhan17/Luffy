import sys

import flint
from flint import acb, arb, ctx

print(sys.version)
print("python_flint", flint.__version__, "flint", flint.__FLINT_VERSION__)
print("acb.integral", hasattr(acb, "integral"))
print("arb_series", hasattr(flint, "arb_series"))
print("acb_series", hasattr(flint, "acb_series"))

ctx.prec = 200
if hasattr(acb, "integral"):
    res = acb.integral(lambda z, _a: (-z * z / 2).exp(), acb(-8), acb(8))
    print("gauss integral", res.str(30, more=True))
    print("sqrt(2pi)", (arb.pi() * 2).sqrt().str(30, more=True))
