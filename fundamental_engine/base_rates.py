"""Outside view: how often companies of the same size grew as fast as a forecast (or a price) needs.

Data: Mauboussin, Callahan & Majd, "The Base Rate Book" (Credit Suisse, 26 September 2016), exhibit 4: compound annual sales
growth of the 1,000 largest global companies by market value, 1950-2015, dead companies included, adjusted for inflation
to 2015 dollars, by size class of prior-year sales. Each row: frequency of growth in 5-point bins from below -25% to above
45% a year (percent of observations). The reference class is the company's sales in 2015 dollars.

Caveats carried into every reading: growth includes acquisitions (a forecast of organic growth sits higher in the
distribution than the same organic growth would), each horizon only includes companies that survived it (about half of
listed companies disappear within ten years, mostly by merger), and 1950-2015 US real GDP grew 3.2% a year, faster than
current long-run projections. A percentile is a reality check on a forecast, not a probability of the outcome for one
company: skill, a backlog or evidence can justify a place in the tail (the book's own advice)."""
import bisect

SOURCE = ('Mauboussin, Callahan & Majd, The Base Rate Book, Credit Suisse 2016, exhibit 4 (sales CAGR by size, 1950-2015, '
          'inflation-adjusted to 2015 dollars)')
SOURCE_URL = 'https://josephnoko.com/wp-content/uploads/2024/04/The-Base-Rate-Book-Michael-Mauboussin.pdf'   # the copy read
# CPI-U to restate today's sales in 2015 dollars: 2015 annual average 237.017 (BLS); September 2026 336.298 (FMP CPI series,
# retrieved 2026-10-20). Refresh with the latest CPI; the size class only moves at the class edges.
CPI_2015, CPI_NOW, CPI_NOW_DATE = 237.017, 336.298, '2026-09-01'
# Expected inflation to turn a nominal forecast into a real one: US 10-year breakeven 2.35% on 2026-10-08 (macroradar.io
# daily snapshot of the FRED series T10YIE). Refresh with the dated breakeven when the forecast date moves.
INFLATION, INFLATION_DATE = .0235, '2026-10-08'

# bin lower edges (growth a year); the first bin is open below, the last open above
EDGES = (-.25, -.20, -.15, -.10, -.05, 0., .05, .10, .15, .20, .25, .30, .35, .40, .45)
HORIZONS = (1, 3, 5, 10)
# (label, lower sales bound, upper sales bound in $ millions of 2015) -> {horizon: 16 frequencies (percent), median (percent)}
CLASSES = (
    ('$0-325m', 0, 325, {
        1: ((1.5, .7, 1.1, 1.7, 3.5, 7.2, 14.3, 14.8, 12.2, 8.9, 6.6, 4.1, 3.7, 2.4, 2.2, 15.1), 12.1),
        3: ((.4, .2, .4, 1.0, 1.8, 5.9, 15.3, 19.1, 15.2, 10.4, 6.4, 4.5, 3.3, 2.8, 2.0, 11.3), 11.7),
        5: ((.3, .1, .3, .5, 1.2, 4.4, 16.1, 22.1, 18.2, 10.1, 6.7, 4.8, 3.2, 3.0, 1.9, 7.2), 11.2),
        10: ((0., 0., .2, .5, .7, 3.5, 16.7, 29.3, 20.4, 10.5, 6.2, 4.2, 2.7, 1.8, 1.1, 2.1), 9.8)}),
    # the printed percentages of this class sum to about 92% in every column (an error in the book); rebuilt from the
    # observation counts of its appendix (1-year total 5,731; 3-year 5,609; 5-year 5,496; 10-year 5,123)
    ('$325-700m', 325, 700, {
        1: ((1., 0.5, 1.2, 2.5, 4.4, 8.8, 19.3, 18.8, 13.3, 7.9, 6.4, 3.5, 2.1, 1.8, 1.3, 7.3), 8.2),
        3: ((0.2, 0.3, 0.6, 1.1, 2.6, 8.2, 24., 23.6, 16.3, 8., 4.8, 2.7, 2.1, 1.4, 1.1, 3.2), 7.5),
        5: ((0.1, 0.1, 0.4, 0.8, 2., 7.2, 25.4, 28.8, 16.6, 7.9, 3.8, 2.3, 1.6, 0.9, 0.7, 1.4), 7.3),
        10: ((0., 0., 0.1, 0.6, 1.8, 6.5, 27.2, 36., 16.7, 5.9, 2.8, 1.3, 0.7, 0.3, 0.1, 0.1), 6.7)}),
    ('$700-1,250m', 700, 1250, {
        1: ((1.6, .9, 1.4, 2.7, 4.7, 10.2, 19.5, 18.3, 12.3, 7.9, 5.2, 3.1, 2.8, 1.8, 1.4, 6.1), 7.1),
        3: ((.4, .3, .8, 1.8, 3.5, 9.5, 23.6, 23.6, 14.5, 8.2, 4.3, 2.9, 1.9, 1.5, .9, 2.2), 6.8),
        5: ((.2, .3, .5, 1.3, 3.3, 8.8, 26.6, 26.6, 15.3, 7.3, 4.2, 2.3, 1.0, 1.0, .4, .9), 6.4),
        10: ((.2, .1, .2, .8, 2.2, 9.2, 31.9, 32.1, 14.8, 4.9, 2.0, 1.1, .3, .1, 0., 0.), 5.7)}),
    ('$1,250-2,000m', 1250, 2000, {
        1: ((1.4, .9, 1.4, 2.7, 5.1, 10.1, 20.6, 19.6, 12.4, 7.4, 4.1, 3.4, 2.2, 1.6, 1.5, 5.8), 6.8),
        3: ((.3, .4, .7, 1.8, 3.9, 10.7, 25.7, 23.8, 13.1, 7.0, 4.0, 2.9, 1.7, 1.0, .5, 2.3), 6.2),
        5: ((.3, .2, .4, 1.0, 3.1, 10.4, 29.6, 27.0, 13.6, 6.2, 3.4, 2.1, .9, .6, .4, .7), 5.7),
        10: ((0., .1, .4, .7, 1.8, 9.8, 36.8, 31.2, 12.3, 4.0, 1.6, .7, .3, .1, .2, .1), 5.1)}),
    ('$2,000-3,000m', 2000, 3000, {
        1: ((1.4, 1.0, 1.6, 2.8, 5.3, 11.1, 22.1, 18.7, 12.2, 7.1, 4.8, 2.9, 2.0, 1.4, .8, 4.8), 6.0),
        3: ((.4, .3, .9, 1.6, 4.8, 12.4, 27.2, 22.6, 12.5, 6.5, 4.2, 2.4, 1.3, .9, .6, 1.3), 5.4),
        5: ((.3, .1, .4, 1.2, 3.6, 11.7, 31.9, 26.9, 12.0, 5.4, 3.2, 1.7, .6, .3, .2, .4), 5.1),
        10: ((0., .1, .1, .4, 2.6, 12.1, 40.0, 28.8, 9.8, 4.1, 1.1, .8, .1, .1, .1, 0.), 4.5)}),
    ('$3,000-4,500m', 3000, 4500, {
        1: ((1.6, 1.0, 1.8, 3.5, 6.3, 12.4, 22.1, 17.9, 11.4, 7.0, 4.7, 2.9, 1.6, 1.2, .7, 4.0), 5.4),
        3: ((.4, .4, .9, 2.0, 5.0, 14.3, 26.8, 22.8, 11.7, 7.0, 3.3, 2.0, 1.3, .7, .4, .8), 5.0),
        5: ((.2, .2, .7, 1.7, 3.7, 14.4, 31.3, 25.2, 12.3, 5.2, 2.6, 1.4, .5, .2, .3, .2), 4.7),
        10: ((0., 0., 0., .8, 2.6, 15.0, 40.1, 28.1, 8.7, 3.2, .8, .4, 0., 0., 0., 0.), 4.1)}),
    ('$4,500-7,000m', 4500, 7000, {
        1: ((1.8, 1.0, 1.6, 3.8, 6.7, 12.9, 21.8, 19.0, 11.2, 6.4, 3.8, 2.8, 1.7, .9, .8, 3.8), 5.1),
        3: ((.5, .7, 1.0, 2.7, 5.6, 14.8, 28.4, 20.8, 11.0, 6.2, 3.6, 1.9, 1.1, .5, .4, 1.0), 4.4),
        5: ((.2, .2, .6, 1.9, 4.5, 15.6, 33.2, 23.1, 10.5, 5.6, 2.2, 1.1, .6, .3, .2, .3), 4.1),
        10: ((0., .1, .1, 1.0, 4.1, 15.5, 40.8, 26.5, 7.7, 2.7, .8, .5, .2, 0., 0., 0.), 3.7)}),
    ('$7,000-12,000m', 7000, 12000, {
        1: ((2.0, 1.2, 1.9, 3.6, 8.0, 14.4, 21.9, 18.4, 10.9, 5.5, 3.7, 2.2, 1.6, .9, .8, 3.1), 4.3),
        3: ((.5, .5, 1.2, 3.0, 7.2, 16.8, 27.7, 20.4, 10.4, 5.4, 3.1, 1.5, 1.0, .4, .3, .7), 3.7),
        5: ((.3, .3, .7, 2.3, 6.3, 17.7, 31.9, 23.5, 9.5, 4.0, 1.5, 1.2, .5, .2, .1, .1), 3.5),
        10: ((0., .1, .6, 1.0, 4.4, 18.5, 40.8, 25.1, 6.3, 2.1, .7, .3, .1, 0., 0., 0.), 3.2)}),
    ('$12,000-25,000m', 12000, 25000, {
        1: ((2.6, 1.4, 2.3, 3.8, 8.2, 16.5, 22.5, 17.5, 9.5, 4.9, 3.0, 2.3, 1.4, .8, .5, 2.6), 3.4),
        3: ((.9, .6, 1.9, 2.9, 7.7, 19.4, 27.9, 19.6, 8.9, 4.4, 2.6, 1.2, .9, .4, .3, .5), 3.0),
        5: ((.3, .6, 1.2, 2.5, 6.4, 19.7, 33.3, 21.1, 8.2, 3.6, 1.7, .7, .4, .3, 0., .1), 2.9),
        10: ((0., .1, .5, 1.4, 5.7, 20.2, 41.7, 20.8, 6.3, 2.5, .6, .1, .1, 0., 0., .1), 2.7)}),
    ('>$25,000m', 25000, 50000, {
        1: ((3.6, 1.5, 2.4, 4.8, 9.1, 16.6, 21.8, 15.9, 9.0, 5.6, 3.2, 2.3, 1.1, .7, .5, 1.9), 2.7),
        3: ((1.6, .8, 2.0, 3.8, 9.0, 19.8, 26.9, 18.2, 9.1, 4.3, 2.2, 1.1, .4, .4, .2, .3), 2.2),
        5: ((1.0, .6, 1.5, 3.0, 8.2, 21.3, 32.6, 18.1, 8.5, 3.2, 1.2, .4, .2, .1, .1, 0.), 2.0),
        10: ((.1, .2, .8, 2.5, 9.0, 22.9, 37.1, 20.2, 5.8, 1.3, .2, 0., 0., 0., 0., 0.), 1.8)}),
    ('>$50,000m', 50000, float('inf'), {
        1: ((4.0, 1.9, 2.6, 5.0, 10.1, 16.9, 21.8, 15.0, 8.6, 5.1, 3.3, 2.3, 1.1, .6, .4, 1.3), 2.1),
        3: ((2.0, .8, 2.3, 4.2, 10.7, 21.4, 26.5, 16.9, 9.2, 3.2, 1.6, .7, .1, .3, .1, 0.), 1.5),
        5: ((1.6, 1.0, 1.5, 2.7, 9.3, 22.8, 34.0, 16.9, 7.0, 2.3, .6, .3, .1, 0., 0., 0.), 1.5),
        10: ((0., .3, 1.0, 3.1, 9.8, 26.9, 37.8, 17.2, 3.0, .9, 0., 0., 0., 0., 0., 0.), 1.1)}),
)
TAIL_WIDTH = .05     # the open tails are read as 5-point bins for interpolation (a reading convention, flagged in the output)


def sales_2015(sales_usd, cpi_now=CPI_NOW):
    """Today's annual sales (USD) in 2015 dollars, millions."""
    return sales_usd * CPI_2015 / cpi_now / 1e6


def reference_class(sales_usd, cpi_now=CPI_NOW):
    """(label, table) of the size class; >$50B uses the mega-company class the book reports separately, $25-50B the top
    decile (whose own sample also holds the mega companies)."""
    s = sales_2015(sales_usd, cpi_now)
    for label, lo, hi, table in CLASSES:
        if lo <= s < hi:
            return label, table
    return CLASSES[-1][0], CLASSES[-1][3]


def share_at_least(freqs, g):
    """Share of observations with CAGR >= g, linear inside a bin (open tails read as 5-point bins)."""
    lows = (EDGES[0] - TAIL_WIDTH,) + EDGES
    total = sum(freqs)
    above = 0.
    for i, f in enumerate(freqs):
        lo = lows[i]
        hi = EDGES[i] if i < len(EDGES) else EDGES[-1] + TAIL_WIDTH
        if g <= lo:
            above += f
        elif g < hi:
            above += f * (hi - g) / (hi - lo)
    return above / total


def real(g_nominal, inflation=INFLATION):
    return (1 + g_nominal) / (1 + inflation) - 1


def cagr(growth_path, years):
    """Compound annual growth of the first `years` rates of a path (None when the path is shorter)."""
    if len(growth_path) < years:
        return None
    level = 1.
    for g in growth_path[:years]:
        level *= 1 + g
    return level ** (1 / years) - 1


def outside_view(sales_usd, cagrs_nominal, inflation=INFLATION, cpi_now=CPI_NOW):
    """cagrs_nominal = {horizon_years: nominal CAGR}. Returns the reference class and, per horizon, the real CAGR, the share
    of the class that grew at least that fast and the class median (all as fractions)."""
    if not sales_usd or sales_usd <= 0:
        return None
    label, table = reference_class(sales_usd, cpi_now)
    out = {'reference_class': label, 'sales_2015_usd_m': round(sales_2015(sales_usd, cpi_now)),
           'inflation_used': inflation, 'inflation_as_of': INFLATION_DATE, 'cpi': {'2015': CPI_2015, CPI_NOW_DATE: cpi_now},
           'source': SOURCE, 'horizons': {}}
    for n, g in sorted(cagrs_nominal.items()):
        if n not in table or g is None:
            continue
        freqs, med = table[n]
        r = real(g, inflation)
        out['horizons'][n] = {'nominal_cagr': g, 'real_cagr': r, 'share_at_least': share_at_least(freqs, r),
                              'class_median_real': med / 100,
                              'in_open_tail': r < EDGES[0] or r > EDGES[-1]}
    return out


def describe_he(view, horizon):
    """One Hebrew sentence for a horizon of an outside_view reading."""
    h = (view or {}).get('horizons', {}).get(horizon)
    if not h:
        return None
    return (f"צמיחה ריאלית של {h['real_cagr']:.1%} בשנה ל־{horizon} שנים: רק {h['share_at_least']:.0%} מהחברות בגודל הזה "
            f"({view['reference_class']} במחירי 2015) השיגו לפחות כך ב־1950–2015; החציון שלהן {h['class_median_real']:.1%}.")
