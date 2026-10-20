"""Load every saved discovery channel from one folder and turn it into leads (leads.py), with a report of what is missing.

Folder layout (each file as retrieved, never edited; dates inside decide what is usable at as_of):
    edgar/form_<YYYY>_QTR<n>.idx          EDGAR quarterly form index (scripts/collect_channels.py)
    frames_q/<tag>_<CYyyyyQq>.json        SEC quarterly revenue frames (scripts/collect_channels.py)
    insider_clusters_*.json               insiders.clusters output (fundamental_engine universe --insiders input)
    av_news_*.json                        Alpha Vantage NEWS_SENTIMENT
    av_estimates_<SYMBOL>.json            Alpha Vantage EARNINGS_ESTIMATES
    av_earnings_<SYMBOL>.json             Alpha Vantage EARNINGS
    lunarcrush_*.md                       LunarCrush stocks/topic table as returned (markdown)
    fmp_targets_<SYMBOL>.json             FMP price-target-consensus (with the quote in quote_<SYMBOL>.json or the universe row)
    ibkr_connections_<SYMBOL>.json        Interactive Brokers get_company_connections
    leads_*.json                          leads already in the normalized shape (leads.lead), e.g. written from any other connector
The filers' own SEC submissions (8-K items) are read from the universe folder's raw/submissions/.
"""
import glob
import json
import os
import re

from . import leads as L


def _load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def _sym(path, prefix, ext):
    return re.sub(rf'^{prefix}_|\.{ext}$', '', os.path.basename(path)).upper()


def collect(channels_dir, rows, as_of, submissions_dir=None, window_days=92):
    """-> {'leads': [...], 'prelisting': [...], 'channels': {channel: count}, 'missing': [channel ids with no file]}."""
    cik_to_symbol = {int(r['source']['cik']): r['symbol'] for r in rows if (r.get('source') or {}).get('cik')}
    price = {r['symbol']: (r.get('metrics') or {}).get('price') for r in rows}
    out, pre, used = [], [], {}

    def take(name, xs):
        out.extend(xs)
        used[name] = used.get(name, 0) + len(xs)
    for p in sorted(glob.glob(os.path.join(channels_dir, 'edgar', 'form_*.idx'))):
        with open(p, encoding='latin-1') as f:
            ls, pl = L.from_form_index(L.parse_form_index(f.read()), cik_to_symbol, as_of, window_days)
        take('sec_form_index', ls)
        pre.extend(pl)
    if submissions_dir and os.path.isdir(submissions_dir):
        n = 0
        for p in glob.glob(os.path.join(submissions_dir, 'CIK*.json')):
            cik = int(re.sub(r'\D', '', os.path.basename(p)))
            if cik in cik_to_symbol:
                take('sec_8k_items', L.from_8k_items(_load(p), cik_to_symbol[cik], as_of, window_days))
                n += 1
        used.setdefault('sec_8k_items', 0)
        used['sec_8k_items_filers_read'] = n
    fq = glob.glob(os.path.join(channels_dir, 'frames_q', '*.json'))
    if fq:
        frames = {}
        for p in fq:
            label = re.search(r'(CY\d{4}Q\d)', p).group(1)
            d = _load(p)
            frames.setdefault(label, {'data': []})['data'].extend(d.get('data') or [])
        latest = max(frames)
        series = L.quarterly_series(frames)
        if sum(1 for qs in series.values() if latest in qs) < .3 * len(series):      # latest quarter still being filed
            latest = L._prev(latest, 1)
        take('sec_quarterly_revenue', L.from_revenue_acceleration(series, cik_to_symbol, as_of, latest))
    for p in glob.glob(os.path.join(channels_dir, 'insider_clusters_*.json')):
        take('sec_insider_purchases', L.from_insider_clusters(_load(p)))
    for p in glob.glob(os.path.join(channels_dir, 'av_news_*.json')):
        take('news_sentiment', L.from_news_sentiment(_load(p), as_of))
    for p in glob.glob(os.path.join(channels_dir, 'av_estimates_*.json')):
        take('estimate_revisions', L.from_estimate_revisions(_load(p), as_of))
    for p in glob.glob(os.path.join(channels_dir, 'av_earnings_*.json')):
        take('earnings_surprises', L.from_earnings_surprises(_load(p), as_of))
    for p in glob.glob(os.path.join(channels_dir, 'lunarcrush_*.md')):
        with open(p, encoding='utf-8') as f:
            take('social_attention', L.from_social(L.parse_lunarcrush_table(f.read()), as_of))
    for p in glob.glob(os.path.join(channels_dir, 'fmp_targets_*.json')):
        s = _sym(p, 'fmp_targets', 'json')
        q = os.path.join(channels_dir, f'quote_{s}.json')
        px = (_load(q).get('price') if os.path.exists(q) else None) or price.get(s)
        take('analyst_targets', L.from_price_targets(_load(p), s, px, as_of))
    for p in glob.glob(os.path.join(channels_dir, 'ibkr_connections_*.json')):
        take('ibkr_connections', L.from_connections(_load(p), _sym(p, 'ibkr_connections', 'json'), as_of))
    for p in glob.glob(os.path.join(channels_dir, 'leads_*.json')):
        xs = [L.lead(**{k: x[k] for k in ('symbol', 'channel', 'role', 'kind', 'statement', 'source')}, date_=x['date'],
                     direction=x.get('direction', 'neutral'), strength=x.get('strength'), cik=x.get('cik'),
                     independence_key=x.get('independence_key')) for x in _load(p)]
        take('normalized_leads', [x for x in xs if x['date'] <= as_of])
    expected = ('sec_form_index', 'sec_8k_items', 'sec_quarterly_revenue', 'sec_insider_purchases', 'news_sentiment',
                'estimate_revisions', 'earnings_surprises', 'social_attention', 'analyst_targets', 'ibkr_connections')
    return {'leads': out, 'prelisting': pre, 'channels': used, 'missing': [c for c in expected if c not in used]}


def summary_he(merged, prelisting, top=25):
    """Hebrew overview: companies with independent positive signals first, then red flags, then pre-listing leads."""
    pct = lambda x: f'{x:.0%}'
    pos_channels = lambda p: len({x['channel'] for x in p['leads'] if x['role'] == 'signal' and x['direction'] == 'positive'})
    rows = sorted(merged.items(), key=lambda kv: (-pos_channels(kv[1]), -kv[1]['positive_signals'], len(kv[1]['red_flags']),
                                                  -len(kv[1]['channels']), kv[0]))
    lines = ['# ערוצי איתור — סיכום', '',
             'איתות = פעולה בכסף או נתון מדווח (רכישות של כמה מנהלים, האצת הכנסות בדוחות, עדכוני תחזיות אנליסטים, הפתעות רווח). '
             'רמז לבדיקה = משהו שצריך לקרוא לפני שמסיקים (החזקה של 5% עם כוונת השפעה, הסכם מהותי, החלפת מנהל). '
             'דגל אדום = אזהרה בדיווח (איחור בדוח, החלפת רואה חשבון, ביטול הסתמכות על דוחות). '
             'תשומת לב (חדשות, רשתות) = עומס או רעיון לבדיקה, לעולם לא ראיה. מקור שחוזר על אותו סיפור נספר פעם אחת.', '',
             '| מניה | איתותים חיוביים | איתותים שליליים | דגלים אדומים | אירועים | ערוצים | לדוגמה |', '|---|---|---|---|---|---|---|']
    for sym, p in rows[:top]:
        ex = next((x['statement'] for x in p['leads'] if x['role'] == 'signal' and x['direction'] == 'positive'), p['leads'][0]['statement'])
        lines.append(f"| {sym} | {p['positive_signals']} | {p['negative_signals']} | {', '.join(p['red_flags']) or '-'} | "
                     f"{', '.join(p['events']) or '-'} | {', '.join(p['channels'])} | {ex[:110]} |")
    flags = [(s, p) for s, p in merged.items() if p['red_flags']]
    if flags:
        lines += ['', '## דגלים אדומים (שאלות שהמחקר חייב לענות עליהן קודם)', '']
        for s, p in sorted(flags)[:40]:
            lines.append(f"- {s}: " + '; '.join(x['statement'] for x in p['leads'] if x['role'] == 'red_flag'))
    if prelisting:
        lines += ['', '## לפני רישום למסחר (ספין-אוף והנפקות) — למעקב', '']
        for x in sorted(prelisting, key=lambda x: x['date'], reverse=True)[:30]:
            lines.append(f"- {x['date']} {x['company']} ({x['form']}): {x['note']}")
    return '\n'.join(lines)


# What a session collects through its connectors, and where it saves each reply (the engine cannot call connectors itself).
# 'per' = 'market' (one call covers many companies) or 'candidate' (one call per researched company; quota-limited).
COLLECTION_PLAN = (
    {'channel': 'news_sentiment', 'tool': 'Alpha Vantage NEWS_SENTIMENT (tickers=<up to ~5 candidates>, limit=1000)',
     'file': 'av_news_<as_of>_<n>.json', 'per': 'candidate', 'quota': 'Alpha Vantage free tier: 25 calls a day for all tools'},
    {'channel': 'estimate_revisions', 'tool': 'Alpha Vantage EARNINGS_ESTIMATES (symbol)', 'file': 'av_estimates_<SYMBOL>.json',
     'per': 'candidate', 'quota': 'shares the 25-a-day limit'},
    {'channel': 'earnings_surprises', 'tool': 'Alpha Vantage EARNINGS (symbol)', 'file': 'av_earnings_<SYMBOL>.json',
     'per': 'candidate', 'quota': 'shares the 25-a-day limit'},
    {'channel': 'social_attention', 'tool': 'LunarCrush stocks (sort=social_dominance, limit up to 1000; or per sector)',
     'file': 'lunarcrush_<as_of>_<sector or all>.md (the table as returned)', 'per': 'market', 'quota': 'limited data mode'},
    {'channel': 'analyst_targets', 'tool': 'FMP analyst price-target-consensus (symbol)', 'file': 'fmp_targets_<SYMBOL>.json',
     'per': 'candidate', 'quota': 'FMP plan quota (rate-limited on 20.10.2026)'},
    {'channel': 'ibkr_connections', 'tool': 'Interactive Brokers get_company_connections (contract_id from search_contracts)',
     'file': 'ibkr_connections_<SYMBOL>.json', 'per': 'candidate', 'quota': 'read-only'},
    {'channel': 'owner_channels', 'tool': 'stock-idea-sourcing skill or web search: one normalized lead per thesis',
     'file': 'leads_<source>_<as_of>.json (leads.lead fields)', 'per': 'candidate', 'quota': 'manual'},
    {'channel': 'product_adoption', 'tool': 'web search: app-store ranks, search trends, traffic, reviews with their date',
     'file': 'leads_adoption_<as_of>.json (role attention)', 'per': 'candidate', 'quota': 'manual'},
)


def plan(symbols, as_of):
    """Connector calls for these candidates (market-wide calls once), with the file name each reply is saved under."""
    out = []
    for p in COLLECTION_PLAN:
        if p['per'] == 'market':
            out.append({**p, 'file': p['file'].replace('<as_of>', as_of)})
        else:
            for s in symbols:
                out.append({**p, 'symbol': s, 'file': p['file'].replace('<SYMBOL>', s).replace('<as_of>', as_of)})
    return out
