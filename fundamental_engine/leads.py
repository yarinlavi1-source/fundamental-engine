"""Leads from every discovery channel in one shape, merged per company without double counting.

A lead says WHO (symbol, CIK) WHAT (kind, statement, direction) WHEN (date, on or before as_of) and FROM WHERE (channel,
source, role). The role comes from sources.CHANNELS and decides how far a lead can move a candidate:
- signal: an action with money or contracts (an activist stake, insider purchases, a material agreement, revenue
  acceleration in filed reports, estimate revisions); supports a research priority and can open a card;
- red_flag: a filed warning (a late filing, non-reliance on past statements, an auditor change, a delisting notice):
  never a reason to buy, always a question for the research;
- attention: news and social volume or tone; crowding or a lead to verify, never evidence;
- lead: someone's thesis or a peer link; opens a question to verify;
- event: a transaction under way (a tender offer, a spin-off registration) that changes what is being valued.
independence_key: two leads with the same key are one piece of evidence (a syndicated story, one filing listed twice).

SEC channels are automatic and cover every listed filer (EDGAR quarterly form index, the filer's own 8-K item list,
quarterly revenue frames, Form 4 data sets via insiders.py). Connector channels parse the payload a session saved
(Alpha Vantage NEWS_SENTIMENT / EARNINGS_ESTIMATES / EARNINGS, LunarCrush stock tables, FMP grades and price targets,
Interactive Brokers company connections); a payload in another shape can be turned into normalized leads with lead().
"""
import re
from datetime import date, timedelta

ROLES = ('signal', 'red_flag', 'attention', 'lead', 'event')

# ---------------------------------------------------------------- the lead record


def lead(symbol, channel, role, kind, date_, statement, source, direction='neutral', strength=None, cik=None, independence_key=None):
    if role not in ROLES:
        raise ValueError(f'role must be one of {ROLES}')
    if direction not in ('positive', 'negative', 'neutral'):
        raise ValueError('direction must be positive, negative or neutral')
    if not all(str(x or '').strip() for x in (channel, kind, date_, statement, source)):
        raise ValueError('channel, kind, date, statement and source are required')
    date.fromisoformat(str(date_)[:10])
    return {'symbol': (symbol or '').upper() or None, 'cik': int(cik) if cik else None, 'channel': channel, 'role': role,
            'kind': kind, 'direction': direction, 'date': str(date_)[:10], 'statement': statement, 'source': source,
            'strength': strength, 'independence_key': independence_key or f'{channel}:{source}'}


def _window(d, as_of, days):
    x, a = date.fromisoformat(str(d)[:10]), date.fromisoformat(as_of)
    return a - timedelta(days=days) <= x <= a


# ---------------------------------------------------------------- EDGAR quarterly form index (every filer)

FORM_EVENTS = {   # form type -> (kind, role, direction, statement, applies to a listed issuer?)
    # a 13D is filed by any 5% holder that may influence control (activists, founders, foundations, parents): read Item 4
    'SCHEDULE 13D': ('stake_13d', 'lead', 'neutral', 'a 5%+ holder that may seek to influence filed a Schedule 13D (read Item 4: '
                     'activist, founder, foundation or parent)', True),
    'SC 13D': ('stake_13d', 'lead', 'neutral', 'a 5%+ holder that may seek to influence filed a Schedule 13D (read Item 4: '
               'activist, founder, foundation or parent)', True),
    'SCHEDULE 13D/A': ('activist_amendment', 'lead', 'neutral', 'an activist holder amended its Schedule 13D (stake or plans changed)', True),
    'SC 13D/A': ('activist_amendment', 'lead', 'neutral', 'an activist holder amended its Schedule 13D (stake or plans changed)', True),
    'SC TO-T': ('tender_offer', 'event', 'neutral', 'a third-party tender offer for the shares is under way', True),
    'SC 14D9': ('tender_offer_response', 'event', 'neutral', 'the board responded to a tender offer (Schedule 14D-9)', True),
    'NT 10-K': ('late_annual_report', 'red_flag', 'negative', 'notified the SEC that the annual report will be late (NT 10-K)', True),
    'NT 10-Q': ('late_quarterly_report', 'red_flag', 'negative', 'notified the SEC that the quarterly report will be late (NT 10-Q)', True),
    'NT 20-F': ('late_annual_report', 'red_flag', 'negative', 'notified the SEC that the annual report will be late (NT 20-F)', True),
    # Forms 15 and 25 are mostly redeemed notes, preferred series or completed mergers, not the common stock: an event to read
    '15-12B': ('deregistration', 'event', 'neutral', 'filed to deregister a class of securities (Form 15: check which class)', True),
    '25-NSE': ('delisting', 'event', 'neutral', 'a class of securities was removed from listing (Form 25: often a redeemed note or a '
               'completed merger; check which class)', True),
    '10-12B': ('spin_off_registration', 'event', 'neutral', 'a company registered shares for a spin-off listing (Form 10)', False),
    '10-12G': ('spin_off_registration', 'event', 'neutral', 'a company registered shares (Form 10, often a spin-off)', False),
    'S-1': ('ipo_registration', 'attention', 'neutral', 'a new issuer registered shares (S-1): IPOs underperform on average, a lead only', False),
    'F-1': ('ipo_registration', 'attention', 'neutral', 'a foreign issuer registered shares (F-1): IPOs underperform on average, a lead only', False),
}


def parse_form_index(text):
    """EDGAR full-index form.idx -> [{form, company, cik, date, path, accession}]. The data columns do not line up with the
    header text, so each line is read from the right (path, date, CIK) and the rest split at the first run of spaces."""
    lines = text.splitlines()
    head = next((i for i, l in enumerate(lines) if l.startswith('Form Type') and 'Company Name' in l), None)
    if head is None:
        raise ValueError('not an EDGAR form index (no header line)')
    out = []
    for l in lines[head + 2:]:
        m = re.match(r'^(.*?)\s+(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(edgar/\S+)\s*$', l)
        if not m:
            continue
        left, cik, d, path = m.groups()
        parts = re.split(r'\s{2,}', left.strip(), maxsplit=1)
        a = re.search(r'(\d{10}-\d{2}-\d{6})', path)
        out.append({'form': parts[0], 'company': parts[1] if len(parts) > 1 else '', 'cik': int(cik), 'date': d, 'path': path,
                    'accession': a.group(1) if a else path})
    return out


def from_form_index(rows, cik_to_symbol, as_of, window_days=92):
    """Listed-issuer events and pre-listing leads from the form index. A Schedule 13D is listed under the target and the
    filer: the entry whose CIK is a listed issuer is the target (when both are listed, both are kept and flagged).
    -> (leads, prelisting) where prelisting are spin-off and IPO registrants not yet in the universe."""
    leads, pre = [], []
    by_acc = {}
    for r in rows:
        if r['form'] in FORM_EVENTS and _window(r['date'], as_of, window_days):
            by_acc.setdefault((r['form'], r['accession']), []).append(r)
    for (form, acc), rs in sorted(by_acc.items(), key=lambda x: x[1][0]['date']):
        kind, role, direction, statement, listed = FORM_EVENTS[form]
        url = 'https://www.sec.gov/Archives/' + rs[0]['path']
        if listed:
            hits = [r for r in rs if r['cik'] in cik_to_symbol]
            for r in hits:
                note = ' (both parties are listed: check which one is the target)' if len(hits) > 1 else ''
                leads.append(lead(cik_to_symbol[r['cik']], 'sec_form_index', role, kind, r['date'], statement + note, url, direction,
                                  cik=r['cik'], independence_key=f'sec:{acc}'))
        else:
            r = rs[0]
            if r['cik'] in cik_to_symbol:
                leads.append(lead(cik_to_symbol[r['cik']], 'sec_form_index', role, kind, r['date'], statement, url, direction,
                                  cik=r['cik'], independence_key=f'sec:{acc}'))
            else:
                pre.append({'company': r['company'], 'cik': r['cik'], 'form': form, 'kind': kind, 'date': r['date'], 'source': url,
                            'note': statement})
    return leads, pre


# ---------------------------------------------------------------- 8-K items from the filer's own submissions record

ITEMS_8K = {  # item -> (kind, role, direction, statement)
    # 1.01 covers customer contracts and credit facilities alike (770 filings in one quarter): a lead to read, not support
    '1.01': ('material_agreement', 'lead', 'neutral', 'entered a material definitive agreement (contract, partnership or financing: read it)'),
    '1.02': ('agreement_terminated', 'signal', 'negative', 'a material agreement was terminated'),
    '1.03': ('bankruptcy', 'red_flag', 'negative', 'bankruptcy or receivership'),
    '2.01': ('acquisition_or_disposal_completed', 'event', 'neutral', 'completed an acquisition or disposal of assets'),
    '2.04': ('obligation_accelerated', 'red_flag', 'negative', 'a triggering event accelerated a financial obligation'),
    '2.05': ('restructuring', 'event', 'negative', 'committed to exit or disposal activities (restructuring costs)'),
    '2.06': ('impairment', 'signal', 'negative', 'concluded that a material impairment is required'),
    '3.01': ('listing_deficiency', 'red_flag', 'negative', 'received a delisting or listing-standard deficiency notice'),
    '3.02': ('unregistered_equity_sale', 'signal', 'negative', 'sold equity in an unregistered offering (dilution)'),
    '4.01': ('auditor_change', 'red_flag', 'negative', 'changed its independent auditor'),
    '4.02': ('non_reliance', 'red_flag', 'negative', 'previously issued financial statements should no longer be relied on (restatement)'),
    '5.01': ('change_in_control', 'event', 'neutral', 'a change in control of the company'),
    '5.02': ('officer_change', 'lead', 'neutral', 'a director or officer departed or was appointed (read which role)'),
}


def from_8k_items(submissions, symbol, as_of, window_days=92):
    """Recent 8-K filings from the SEC submissions JSON (filings.recent), one lead per item of interest."""
    r = ((submissions or {}).get('filings') or {}).get('recent') or {}
    cik = submissions.get('cik')
    out = []
    for i, form in enumerate(r.get('form') or []):
        if form not in ('8-K', '8-K/A'):
            continue
        d, items = r['filingDate'][i], str((r.get('items') or [''] * (i + 1))[i] or '')
        if not _window(d, as_of, window_days):
            continue
        acc = r['accessionNumber'][i]
        url = (f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{r['primaryDocument'][i]}" if cik
               else f'sec:{acc}')
        for it in items.split(','):
            it = it.strip()
            if it in ITEMS_8K:
                kind, role, direction, statement = ITEMS_8K[it]
                out.append(lead(symbol, 'sec_8k_items', role, kind, d, f'8-K item {it}: {statement}', url, direction, cik=cik,
                                independence_key=f'sec:{acc}:{it}'))
    return out


# ---------------------------------------------------------------- quarterly revenue frames (every filer)

def quarterly_series(frames):
    """frames: {'CY2025Q2': SEC frames payload, ...} for revenue tags -> {cik: {quarter label: value}} (the larger value when
    two tags report the same quarter, the sec_frames rule)."""
    out = {}
    for label, payload in frames.items():
        for row in (payload or {}).get('data') or []:
            cik, v = int(row['cik']), row.get('val')
            if v is None:
                continue
            cur = out.setdefault(cik, {}).get(label)
            if cur is None or v > cur:
                out[cik][label] = float(v)
    return out


def _prev(label, n):
    y, q = int(label[2:6]), int(label[7])
    k = (y * 4 + q - 1) - n
    return f'CY{k // 4}Q{k % 4 + 1}'


def from_revenue_acceleration(series, cik_to_symbol, as_of, latest, min_quarter_revenue=5e7, jump=.10, floor=.10):
    """Year-over-year revenue growth of the latest quarter against the average of the two quarters before: acceleration of
    at least `jump` to at least `floor` growth is a signal (positive); a fall of at least `jump` to below zero is a signal
    (negative). Filed figures only (SEC frames), one lead per company."""
    out = []
    for cik, qs in series.items():
        sym = cik_to_symbol.get(cik)
        if not sym or latest not in qs or qs[latest] < min_quarter_revenue:
            continue
        yoy = []
        for n in range(3):
            a, b = qs.get(_prev(latest, n)), qs.get(_prev(latest, n + 4))
            yoy.append(a / b - 1 if a is not None and b and b > 0 else None)
        if None in yoy:
            continue
        before = (yoy[1] + yoy[2]) / 2
        d = yoy[0] - before
        src = f'https://data.sec.gov/api/xbrl/frames/us-gaap/Revenues/USD/{latest}.json'
        st = {'yoy_latest': yoy[0], 'yoy_prior_two_avg': before, 'change': d, 'quarter': latest}
        if d >= jump and yoy[0] >= floor:
            out.append(lead(sym, 'sec_quarterly_revenue', 'signal', 'revenue_acceleration', as_of,
                            f'revenue growth {yoy[0]:.0%} year over year in {latest}, up from {before:.0%} in the two quarters before '
                            '(filed figures; check for acquisitions)', src, 'positive', st, cik=cik, independence_key=f'frames:{cik}:{latest}'))
        elif d <= -jump and yoy[0] < 0:
            out.append(lead(sym, 'sec_quarterly_revenue', 'signal', 'revenue_deceleration', as_of,
                            f'revenue {yoy[0]:.0%} year over year in {latest}, down from {before:.0%} (filed figures)', src, 'negative', st,
                            cik=cik, independence_key=f'frames:{cik}:{latest}'))
    return out


# ---------------------------------------------------------------- insider clusters (insiders.py)

def from_insider_clusters(clusters):
    """insiders.clusters output -> positive signals dated by the latest Form 4 filing."""
    from .insiders import evidence
    out = []
    for c in clusters or []:
        if not re.match(r'^[A-Z][A-Z0-9.\-]{0,9}$', str(c.get('symbol') or '').upper()) or str(c['symbol']).upper() in ('NONE', 'N/A', 'NA'):
            continue
        ev = evidence(c)
        # strong: three or more insiders, or purchases of at least 0.05% of market value (the universe --insiders rule)
        strong = (c.get('n_buyers') or 0) >= 3 or (c.get('share_of_market_cap') or 0) >= .0005
        out.append(lead(c['symbol'], 'sec_insider_purchases', 'signal' if strong else 'lead', 'insider_purchase_cluster', ev['date'], c['statement'],
                        ev['source'], 'positive', {k: c.get(k) for k in ('n_buyers', 'n_officers', 'value', 'share_of_market_cap')},
                        cik=c.get('issuer_cik'), independence_key=f"form4:{c['symbol']}:{c.get('first')}..{c.get('last')}"))
    return out


# ---------------------------------------------------------------- connector payloads

def _av_time(t):
    return f'{t[:4]}-{t[4:6]}-{t[6:8]}'


def _norm_title(t):
    return re.sub(r'[^a-z0-9 ]', '', (t or '').lower())[:80]


def from_news_sentiment(payload, as_of, window_days=30, min_relevance=.3):
    """Alpha Vantage NEWS_SENTIMENT feed -> one attention lead per ticker: independent articles (syndicated copies with the
    same normalized title count once), mean ticker sentiment, distinct sources."""
    per = {}
    for a in (payload or {}).get('feed') or []:
        d = _av_time(a.get('time_published') or '')
        try:
            if not _window(d, as_of, window_days):
                continue
        except ValueError:
            continue
        key = _norm_title(a.get('title'))
        for t in a.get('ticker_sentiment') or []:
            if float(t.get('relevance_score') or 0) < min_relevance:
                continue
            p = per.setdefault(t['ticker'].upper(), {'titles': {}, 'sources': set(), 'last': d})
            p['titles'].setdefault(key, float(t.get('ticker_sentiment_score') or 0))
            p['sources'].add(a.get('source_domain') or a.get('source'))
            p['last'] = max(p['last'], d)
    out = []
    for sym, p in per.items():
        n, s = len(p['titles']), sum(p['titles'].values()) / len(p['titles'])
        out.append(lead(sym, 'news_sentiment', 'attention', 'news_flow', p['last'],
                        f'{n} independent articles in {window_days} days from {len(p["sources"])} sources, mean sentiment {s:+.2f} '
                        '(attention, not evidence)', 'Alpha Vantage NEWS_SENTIMENT', 'positive' if s > .15 else 'negative' if s < -.15 else 'neutral',
                        {'articles': n, 'sources': len(p['sources']), 'sentiment': s}, independence_key=f'news:{sym}:{as_of}'))
    return out


def from_estimate_revisions(payload, as_of, threshold=.03):
    """Alpha Vantage EARNINGS_ESTIMATES -> a signal when the next fiscal year's average EPS estimate moved at least
    `threshold` over 90 days (consensus is an estimate, never a fact)."""
    sym = (payload or {}).get('symbol')
    fy = [e for e in (payload or {}).get('estimates') or [] if 'fiscal year' in str(e.get('horizon', '')).lower()]
    fy = sorted((e for e in fy if (e.get('date') or '') > as_of), key=lambda e: e['date'])
    if not sym or not fy:
        return []
    e = fy[0]
    now, old = _f(e.get('eps_estimate_average')), _f(e.get('eps_estimate_average_90_days_ago'))
    if now is None or old is None or old == 0:
        return []
    ch = now / abs(old) - (1 if old > 0 else -1)
    up, dn = _f(e.get('eps_estimate_revision_up_trailing_30_days')) or 0, _f(e.get('eps_estimate_revision_down_trailing_30_days')) or 0
    if abs(ch) < threshold:
        return []
    return [lead(sym, 'estimate_revisions', 'signal', 'estimate_revision_up' if ch > 0 else 'estimate_revision_down', as_of,
                 f"fiscal year ending {e['date']}: average EPS estimate {now:.2f} against {old:.2f} 90 days ago ({ch:+.0%}); "
                 f'{up:.0f} up / {dn:.0f} down revisions in 30 days (consensus, not a fact)', 'Alpha Vantage EARNINGS_ESTIMATES',
                 'positive' if ch > 0 else 'negative', {'change_90d': ch, 'up_30d': up, 'down_30d': dn}, independence_key=f'estimates:{sym}:{as_of}')]


def from_earnings_surprises(payload, as_of, quarters=2, threshold=.05):
    """Alpha Vantage EARNINGS (quarterlyEarnings) -> a signal when each of the last `quarters` reports beat (or missed) the
    estimate by at least `threshold` (post-announcement drift is documented; one beat is noise)."""
    sym = (payload or {}).get('symbol')
    qs = [q for q in (payload or {}).get('quarterlyEarnings') or [] if (q.get('reportedDate') or '9999') <= as_of]
    qs = sorted(qs, key=lambda q: q['reportedDate'], reverse=True)[:quarters]
    s = [_f(q.get('surprisePercentage')) for q in qs]
    if not sym or len(s) < quarters or None in s:
        return []
    if all(x >= threshold * 100 for x in s) or all(x <= -threshold * 100 for x in s):
        up = s[0] > 0
        return [lead(sym, 'earnings_surprises', 'signal', 'earnings_beats' if up else 'earnings_misses', qs[0]['reportedDate'],
                     f"reported EPS {'above' if up else 'below'} the estimate in each of the last {quarters} quarters "
                     f"({', '.join(f'{x:+.0f}%' for x in s)})", 'Alpha Vantage EARNINGS', 'positive' if up else 'negative',
                     {'surprise_pct': s}, independence_key=f"earnings:{sym}:{qs[0]['reportedDate']}")]
    return []


def parse_lunarcrush_table(text):
    """LunarCrush stocks/topic markdown table -> [{symbol, name, galaxy_score, alt_rank, sentiment, social_dominance,
    mentions, creators, engagements}]."""
    rows = [l for l in (text or '').splitlines() if l.startswith('|')]
    if len(rows) < 3:
        return []
    cols = [c.strip().lower() for c in rows[0].strip('|').split('|')]
    num = lambda x: _f(re.sub(r'[,$%]', '', x)) if x.strip() not in ('', '-') else None
    out = []
    for r in rows[2:]:
        cells = [c.strip() for c in r.strip('|').split('|')]
        if len(cells) != len(cols):
            continue
        row = dict(zip(cols, cells))
        m = re.search(r'\(([A-Z][A-Z0-9.\-]{0,9})\)', row.get('stock', ''))
        if not m:
            continue
        out.append({'symbol': m.group(1), 'name': re.sub(r'^\[|\]\(.*$', '', row['stock']).split(' (')[0],
                    'alt_rank': num(row.get('altrank', '')), 'galaxy_score': num(row.get('galaxy score', '')),
                    'sentiment': num(row.get('sentiment', '')), 'social_dominance': num(row.get('social dominance', '')),
                    'mentions': num(row.get('mentions', '')), 'creators': num(row.get('creators', '')),
                    'engagements': num(row.get('engagements', ''))})
    return out


def from_social(rows, as_of, crowded_dominance=1.0):
    """Social table rows -> attention leads: 'crowded' when social dominance is at least `crowded_dominance` percent of all
    stock talk (priced for attention), else a mention count (crowding and leads only, never a reason to buy)."""
    out = []
    for r in rows:
        dom = r.get('social_dominance')
        crowded = dom is not None and dom >= crowded_dominance
        out.append(lead(r['symbol'], 'social_attention', 'attention', 'crowded' if crowded else 'social_mentions', as_of,
                        f"social dominance {dom}% of stock talk, {r.get('mentions')} mentions by {r.get('creators')} creators, "
                        f"sentiment {r.get('sentiment')}%" + (' (crowded: the attention may already be in the price)' if crowded else ''),
                        'LunarCrush stocks', 'negative' if crowded else 'neutral', {k: r.get(k) for k in ('social_dominance', 'mentions',
                                                                                                         'creators', 'sentiment', 'galaxy_score')},
                        independence_key=f"social:{r['symbol']}:{as_of}"))
    return out


def from_price_targets(payload, symbol, price, as_of, gap=.25):
    """FMP price-target-consensus ({targetConsensus, targetHigh, targetLow}) -> a lead when the consensus target is at least
    `gap` away from the price (an attributed opinion, never a valuation input)."""
    p = payload[0] if isinstance(payload, list) and payload else payload or {}
    t = _f(p.get('targetConsensus') or p.get('targetMedian'))
    if not t or not price:
        return []
    g = t / price - 1
    if abs(g) < gap:
        return []
    return [lead(symbol, 'analyst_targets', 'lead', 'target_gap', as_of, f'analyst consensus target {t:.2f} is {g:+.0%} from the price '
                 '(attributed opinion; verify the thesis behind it)', 'FMP price-target-consensus', 'positive' if g > 0 else 'negative',
                 {'target': t, 'gap': g}, independence_key=f'targets:{symbol}:{as_of}')]


def from_connections(payload, symbol, as_of):
    """Interactive Brokers company connections -> peer, supplier and customer leads (membership is not exposure)."""
    out = []
    for c in (payload or {}).get('connections') or (payload or {}).get('links') or []:
        other = (c.get('symbol') or c.get('ticker') or '').upper()
        if not other or other == symbol.upper():
            continue
        kind = str(c.get('link_type') or c.get('type') or 'company_competitor').lower()
        out.append(lead(other, 'ibkr_connections', 'lead', kind, as_of, f'{kind.replace("_", " ")} of {symbol} '
                        '(vendor link: verify the exposure in filings)', 'Interactive Brokers get_company_connections', 'neutral',
                        independence_key=f'ibkr:{symbol}:{other}:{kind}'))
    return out


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- merge

WEIGHT = {'signal': 'supports a research priority', 'red_flag': 'a question the research must answer first',
          'attention': 'crowding or a lead only; never evidence', 'lead': 'opens a question to verify',
          'event': 'a transaction under way: value what is changing'}


def merge(leads):
    """{symbol: {'leads', 'independent': {role: count}, 'positive_signals', 'negative_signals', 'red_flags', 'events',
    'crowded', 'channels'}}: leads with the same independence_key count once; the newest copy is kept."""
    out = {}
    seen = {}
    for x in sorted(leads, key=lambda x: x['date'], reverse=True):
        if not x.get('symbol'):
            continue
        k = (x['symbol'], x['independence_key'])
        if k in seen:
            continue
        seen[k] = True
        p = out.setdefault(x['symbol'], {'leads': [], 'independent': {r: 0 for r in ROLES}, 'positive_signals': 0, 'negative_signals': 0,
                                         'red_flags': [], 'events': [], 'crowded': False, 'channels': set()})
        p['leads'].append(x)
        p['independent'][x['role']] += 1
        p['channels'].add(x['channel'])
        if x['role'] == 'signal' and x['direction'] != 'neutral':
            p['positive_signals' if x['direction'] == 'positive' else 'negative_signals'] += 1
        elif x['role'] == 'red_flag':
            p['red_flags'].append(x['kind'])
        elif x['role'] == 'event':
            p['events'].append(x['kind'])
        if x['kind'] == 'crowded':
            p['crowded'] = True
    for p in out.values():
        p['channels'] = sorted(p['channels'])
    return out


def card_signals(merged, symbols=None):
    """merge() output -> candidates.cards(signals=...) evidence lists: positive signals and red flags with source and date."""
    out = {}
    for sym, p in merged.items():
        if symbols is not None and sym not in symbols:
            continue
        ev = [{'source': x['source'], 'date': x['date'], 'statement': x['statement'], 'kind': x['role'], 'channel': x['channel'],
               'direction': x['direction'], 'weight': WEIGHT[x['role']]}
              for x in p['leads'] if x['role'] in ('signal', 'red_flag', 'event')]
        if ev:
            out[sym] = ev
    return out
