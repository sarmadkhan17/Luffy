"""Undefined similarity is unavailable, not NaN transport or fabricated zero."""
import numpy as np
from tests.test_compose_core import _ctx
from trader.research.compose import search
from trader.research.closure import canonical


def test_constant_factor_comparison_is_serializable_and_unavailable():
    ctx=_ctx()
    ctx.df['taker_buy']=ctx.df['volume']*.5
    result=search.factor_matrix(['takerfrac','ret24'],[ctx])
    pair=result['pairs'][0]
    assert pair['n_pair']>100
    assert pair['numeric_corr_pairwise'] is None
    assert pair['numeric_corr_matched'] is None
    canonical(result)


def test_finite_correlations_keep_the_same_numerical_value():
    from trader.research.compose import catalog,graph
    ctx=_ctx()
    result=search.factor_matrix(['ret6','ret24'],[ctx])
    a,b=[graph._values(catalog.BY_KEY[k].formula,ctx) for k in ('ret6','ret24')]
    valid=np.isfinite(a)&np.isfinite(b)
    expected=float(np.corrcoef(a[valid],b[valid])[0,1])
    assert result['pairs'][0]['numeric_corr_pairwise']==expected
    assert result['pairs'][0]['numeric_corr_matched']==expected
