"""Vectorized twin of expectations.run_model for universe-scale reverse solving (numpy).

Must stay numerically identical to run_model for the pre-registered uniform assumptions
(tests/test_expectations_vec.py checks this on random inputs).
"""
import numpy as np


def ev_vec(rev0, g1, m1, m_mature, *, years=10, high_years=5, g_term=0.03, years_to_mature=5, tax=0.21, s2c=1.5,
           wacc=0.09, rev_ttm=None, ic0=None, terminal_roic=None):
    rev0 = np.asarray(rev0, float); g1 = np.asarray(g1, float)
    m1 = np.asarray(m1, float); mm = np.asarray(m_mature, float)
    rev_ttm = rev0 if rev_ttm is None else np.asarray(rev_ttm, float)
    ic = rev0 / s2c if ic0 is None else np.asarray(ic0, float).copy()
    pv = np.zeros_like(rev0); prev = rev0.copy()
    nopat = None
    for t in range(1, years + 1):
        if t <= high_years:
            g = g1
        else:
            w = (t - high_years) / max(years - high_years, 1)
            g = g1 + (g_term - g1) * w
        r = prev * (1 + g) if t > 1 else prev
        m = m1 + (mm - m1) * min(t / max(years_to_mature, 1), 1.0)
        ebit = r * m
        nopat = ebit - np.maximum(ebit, 0) * tax
        base = prev if t > 1 else rev_ttm
        reinv = np.maximum(r - base, 0) / s2c
        ic = ic + reinv
        pv = pv + (nopat - reinv) / (1 + wacc) ** t
        prev = r
    roic_final = np.where(ic > 0, nopat / np.where(ic > 0, ic, 1), np.nan)
    roic_t = np.maximum(np.nan_to_num(roic_final, nan=wacc), wacc) if terminal_roic is None else np.full_like(rev0, terminal_roic)
    nn = nopat * (1 + g_term)
    tv = np.where(roic_t > g_term, nn * (1 - g_term / roic_t) / (wacc - g_term), nn / wacc)
    return pv + tv / (1 + wacc) ** years


def solve_growth(ev_target, rev0, m1, m_mature, lo=-0.5, hi=1.5, iters=45, **kw):
    """Bisection on g1 (vectorized). Returns g with NaN where the target is not bracketed."""
    ev_target = np.asarray(ev_target, float)
    lo_a = np.full_like(ev_target, lo); hi_a = np.full_like(ev_target, hi)
    f_lo = ev_vec(rev0, lo_a, m1, m_mature, **kw) - ev_target
    f_hi = ev_vec(rev0, hi_a, m1, m_mature, **kw) - ev_target
    ok = (f_lo <= 0) & (f_hi >= 0)          # value increasing in growth over the bracket
    for _ in range(iters):
        mid = (lo_a + hi_a) / 2
        f = ev_vec(rev0, mid, m1, m_mature, **kw) - ev_target
        go_up = f < 0
        lo_a = np.where(go_up, mid, lo_a); hi_a = np.where(go_up, hi_a, mid)
    g = (lo_a + hi_a) / 2
    return np.where(ok, g, np.nan), ok, f_lo, f_hi
