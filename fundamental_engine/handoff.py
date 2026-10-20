"""Discovery -> deep research without copying: the research queue goes straight into the one decision path.

Flow: universe scan -> candidates.research_queue -> plan() (company folders and the SEC calls for each) ->
scripts/collect_companies.py (network: SEC companyfacts + submissions, saved verbatim with a manifest) -> run_company()
(FactBook from the saved files, then pipeline.run: dataqa, analysis, model card, forecast, valuation, risk, review,
decision) -> compare() (one row per company, every dimension kept separate).

Pure module: no network. Without analyst inputs and an independent reviewer every casefile is a DRAFT: the pipeline
withholds price claims and says so; compare() carries that status on every row instead of hiding it. Analyst work is
picked up automatically from the company folder (inputs.json, evidence_bundle.json) when present.
"""
from datetime import date, datetime, timedelta
import json
import re
import os

from . import pipeline
from .candidates import LANES_HE

SEC_FACTS = 'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json'
SEC_SUBMISSIONS = 'https://data.sec.gov/submissions/CIK{cik:010d}.json'
GROUPS = {'quality_compounder': 'established_quality', 'quality_fair_price': 'established_quality',
          'early_commercial_growth': 'growth', 'structural_tailwind': 'growth',
          'mispriced_turning': 'undervalued_or_turnaround', 'recovery_mispriced': 'undervalued_or_turnaround',
          'exploratory': 'exploratory'}
GROUPS_HE = {'established_quality': 'איכות מבוססת', 'growth': 'צמיחה', 'undervalued_or_turnaround': 'תמחור חסר / מפנה',
             'exploratory': 'מסלול חקר'}
DIMS = ('business_quality', 'growth_quality', 'financial_strength', 'management_capital_allocation', 'trajectory',
        'price_attractiveness', 'risk_and_error', 'data_uncertainty')


def _load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _num(text):
    try:
        return float(str(text).replace('$', '').replace(',', ''))
    except (TypeError, ValueError):
        return None


def company_record(row):
    """Company identity for the FactBook from a universe row (SEC profile when present). Currency is USD: rows come from
    us-gaap USD frames; the FactBook check (dataqa) still reads the units of every fact."""
    p = row.get('sec_profile') or {}
    fye = p.get('fiscal_year_end')
    return {'ticker': row['symbol'], 'name': row.get('name'), 'cik': str((row.get('source') or {}).get('cik') or p.get('cik') or ''),
            'currency': 'USD', 'exchange': row.get('exchange'), 'sector': row.get('sector'), 'industry': row.get('industry'),
            'industry_listing': row.get('industry_listing'), 'country': row.get('country'),
            'fiscal_year_end': f'{fye[:2]}-{fye[2:]}' if fye and len(fye) == 4 else None,
            'sic': p.get('sic'), 'annual_form': p.get('annual_form'), 'identity_source': 'universe row (SEC ticker map, SEC submissions, listing file)'}


def session_date(retrieved_utc):
    """Trading session a last-sale snapshot belongs to: before 13:30 UTC (the US open) it is the previous weekday's close.
    Exchange holidays are not modelled (stated in the price note)."""
    t = datetime.fromisoformat(str(retrieved_utc).replace('Z', '+00:00'))
    d = t.date()
    if (t.hour, t.minute) < (13, 30):
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
    return d.isoformat(), (t.hour, t.minute) < (13, 30) or (t.hour, t.minute) >= (21, 0)


def listing_price(universe_raw, symbol):
    """Last sale for one symbol from the saved listing file (the price fallback when no broker quote was saved)."""
    man = _load(os.path.join(universe_raw, 'MANIFEST.json'))
    call = next(c for c in man['calls'] if c['file'] == 'nasdaq__screener_stocks.json')
    r = listing_row(universe_raw, symbol)
    if not r or not _num(r.get('lastsale')):
        return None
    d, closed = session_date(call['retrieved_at_utc'])
    return {'symbol': symbol.upper(), 'price': _num(r['lastsale']), 'date': d, 'provider': 'nasdaq', 'dataset': 'screener stocks (last sale)',
            'url': call.get('url'), 'retrieved_at_utc': call['retrieved_at_utc'], 'sha256_of_listing_file': call.get('sha256'),
            'note': (f"listing last sale retrieved {call['retrieved_at_utc']}; "
                     + ('a closing price (retrieved outside the session)' if closed else 'an intraday last sale')
                     + '; unofficial public endpoint, exchange holidays not modelled; replace with a broker quote when available')}


def listing_row(universe_raw, symbol):
    rows = _load(os.path.join(universe_raw, 'nasdaq__screener_stocks.json'))['data']['rows']
    return next((x for x in rows if x['symbol'].replace('/', '-').replace('^', '-').upper() == symbol.upper()), None)


def identity_check(companyfacts, listing=None, company=None, annual_html=None):
    """Issuer, currency and security class before any per-share number (CLAUDE.md: resolve them first).

    The latest annual report's monetary facts decide the reporting currency (USD figures beside another currency are
    convenience translations). A non-USD reporter is valued in its own currency and its quote converted at a dated rate
    (fx.FRED, H.10 daily): blocked only when no such rate series exists. A depositary share needs the ratio stated in the
    annual report (business.ads_ratio): blocked when the report is not saved, states none or states several. The ratio
    implied by the listing market cap is a cross-check only (share counts are dated up to a year apart). The check reads the
    saved payloads only; the reasons say what would unblock the company."""
    from . import business, fx
    rows = []
    for tax, concepts in (companyfacts.get('facts') or {}).items():
        if tax == 'dei':
            continue
        for c in concepts.values():
            for unit, rs in (c.get('units') or {}).items():
                if '/' not in unit and len(unit) == 3 and unit.isupper():
                    rows += [(unit, r) for r in rs]
    # the latest annual report decides (Nebius: Yandex-era RUB history, USD since); else the latest filing; else all facts
    annual = [r for _, r in rows if r.get('form') in ('10-K', '20-F', '40-F') and r.get('accn')]
    latest = max(annual or [r for _, r in rows if r.get('accn')], key=lambda r: r.get('filed') or '', default=None)
    scope = [(u, r) for u, r in rows if latest and r.get('accn') == latest['accn']] or rows
    counts = {}
    for u, _ in scope:
        counts[u] = counts.get(u, 0) + 1
    cur = max(counts, key=counts.get) if counts else None
    if cur == 'USD' and len(counts) > 1:
        other = max((k for k in counts if k != 'USD'), key=counts.get)
        if counts[other] >= .5 * counts['USD']:        # USD convenience translations beside the reporting currency
            cur = other
    reasons, notes = [], []
    out = {'reporting_currency': cur, 'monetary_units': counts,
           'currency_basis': f"facts of filing {latest['accn']} ({latest.get('form')}, filed {latest.get('filed')})"
           if latest and scope is not rows else 'all facts (no filing identifiers)'}
    if cur and cur != 'USD':
        if cur in fx.FRED:
            notes.append(f'reporting currency {cur}: valued in {cur}; the quote is converted at the FRED H.10 rate '
                         f'({fx.FRED[cur][0]}) dated on or within 7 days before the quote')
        else:
            reasons.append(f'reporting currency {cur} ({counts[cur]} facts vs {counts.get("USD", 0)} in USD, which are convenience '
                           f'translations) has no dated exchange-rate series in fx.FRED: the quote cannot be converted')
    name = (listing or {}).get('name') or ''
    stated = business.ads_ratio(annual_html) if annual_html else None
    # the listing name may omit it (TSMC): a depositary ratio stated in the annual report marks the US security as one
    ads = bool(re.search(r'(?i)depositary|\bADS\b|\bADR\b', name)) or bool(stated and not stated.get('direct_listing'))
    out['depositary_shares'] = ads
    if stated:
        out['stated_shares_per_depositary_share'] = {k: stated[k] for k in ('ratio', 'evidence', 'candidates', 'ambiguous')}
        out['direct_listing'] = bool(stated.get('direct_listing'))
    if ads:
        price, cap = _num((listing or {}).get('lastsale')), _num((listing or {}).get('marketCap'))
        dei = ((companyfacts.get('facts') or {}).get('dei') or {}).get('EntityCommonStockSharesOutstanding') or {}
        sh = [r for rs in (dei.get('units') or {}).values() for r in rs]
        shares = sh and max(sh, key=lambda r: r.get('end') or '')['val']
        implied = (shares / (cap / price)) if price and cap and shares else None
        out['implied_shares_per_ads'] = implied
        if not stated or stated.get('direct_listing'):
            reasons.append('American depositary shares: ' + ('the saved annual report states no depositary ratio' if annual_html else
                                                              'the annual report is not saved')
                           + (f' (about {implied:.2f} ordinary shares per ADS implied by the listing market cap)' if implied else '')
                           + '; per-share values need the ratio stated in the annual report')
        elif stated['ambiguous']:
            reasons.append(f"American depositary shares: the annual report states several ratios {stated['candidates']}; "
                           'a reviewer must name the one for the listed class')
        elif implied and abs(implied / stated['ratio'] - 1) > .25:
            notes.append(f"stated ratio {stated['ratio']:g} ordinary shares per ADS vs about {implied:.2f} implied by the listing "
                         'market cap: check for a ratio change after the annual report')
    if (company or {}).get('annual_form') in ('20-F', '20-F/A', '40-F'):
        notes.append('foreign private issuer: annual reports only (no 10-Q); interim 6-K figures are not in companyfacts')
    if notes:
        out['note'] = '; '.join(notes)
    out['status'] = 'blocked' if reasons else 'ok'
    out['reasons'] = reasons
    return out


def lane_candidates(card_set, lane):
    """Backfill order inside one lane: shortlist position, then lane rank; cards marked 'no' are skipped."""
    xs = [c for c in card_set['cards'] if c['full_analysis']['decision'] != 'no' and any(x['lens'] == lane for x in c['lanes'])]
    rank = lambda c: next(x['rank'] for x in c['lanes'] if x['lens'] == lane) or 99
    return [c['symbol'] for c in sorted(xs, key=lambda c: (c.get('shortlist_position') or 999, rank(c), c['symbol']))]


ANNUAL_FORMS = ('10-K', '20-F', '40-F')


def filing_documents(submissions, cik):
    """Latest annual report and latest quarterly report with inline XBRL, from an SEC submissions payload:
    [{'file','url','accession','form','filed','report_date'}]. The annual report carries the segment note, the business
    description and the risk factors; the quarterly report the latest cover share count; the proxy statement (DEF 14A) how
    management is paid (pay-versus-performance table, inline XBRL since 2022)."""
    r = (submissions.get('filings') or {}).get('recent') or {}
    rows = list(zip(r.get('form', []), r.get('accessionNumber', []), r.get('primaryDocument', []), r.get('filingDate', []),
                    r.get('reportDate', []), r.get('isInlineXBRL', [0] * len(r.get('form', [])))))
    out = []
    for kind, forms in (('annual_report', ANNUAL_FORMS), ('latest_quarterly', ('10-Q',)), ('proxy', ('DEF 14A',))):
        hit = next((x for x in rows if x[0] in forms and x[5]), None)
        if hit:
            form, accn, doc, filed, rep, _ = hit
            out.append({'file': f'sec__{kind}.htm', 'url': f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn.replace('-', '')}/{doc}",
                        'accession': accn, 'form': form, 'filed': filed, 'report_date': rep})
    return out


def plan(universe_folder, symbols, rows=None):
    """Company folders and the SEC calls for each queued symbol. rows: universe rows (default rows.json in the folder)."""
    rows = rows if rows is not None else _load(os.path.join(universe_folder, 'rows.json'))
    by = {r['symbol']: r for r in rows}
    tick = os.path.join(universe_folder, 'raw', 'sec__company_tickers_exchange.json')
    sec_map = {}
    if os.path.exists(tick):                 # holdings outside the screened universe still resolve to an SEC filer
        t = _load(tick)
        sec_map = {d['ticker'].upper(): d for d in (dict(zip(t['fields'], x)) for x in t['data'])}
    out, missing = [], []
    for s in symbols:
        r = by.get(s)
        if r is None and s.upper() in sec_map:
            m = sec_map[s.upper()]
            lr = listing_row(os.path.join(universe_folder, 'raw'), s) if os.path.exists(
                os.path.join(universe_folder, 'raw', 'nasdaq__screener_stocks.json')) else None
            r = {'symbol': s, 'name': m.get('name'), 'source': {'cik': m['cik']}, 'exchange': m.get('exchange'),
                 'sector': (lr or {}).get('sector') or None, 'industry': (lr or {}).get('industry') or None,
                 'country': (lr or {}).get('country') or None}
        cik = r and (r.get('source') or {}).get('cik')
        if not cik:
            missing.append(s)
            continue
        folder = os.path.join(universe_folder, 'companies', s)
        out.append({'symbol': s, 'cik': int(cik), 'folder': folder, 'raw': os.path.join(folder, 'raw'), 'company': company_record(r),
                    'calls': [{'file': 'sec__companyfacts.json', 'url': SEC_FACTS.format(cik=int(cik))},
                              {'file': 'sec__submissions.json', 'url': SEC_SUBMISSIONS.format(cik=int(cik))}]})
    return {'companies': out, 'not_in_universe': missing}


def run_company(folder, as_of, inputs=None, qualitative=None):
    """FactBook from <folder>/raw + pipeline.run. inputs / evidence_bundle.json in the folder are used when not passed.
    -> (factbook, casefile)."""
    company = _load(os.path.join(folder, 'company.json'))
    sub = os.path.join(folder, 'raw', 'sec__submissions.json')
    if os.path.exists(sub) and not company.get('sic'):
        d = _load(sub)                                    # the filer's own SEC record: SIC code and description
        if str(d.get('sic') or '').isdigit():
            company = {**company, 'sic': int(d['sic']), 'sic_description': d.get('sicDescription')}
    fb = pipeline.factbook_from_run_dir(os.path.join(folder, 'raw'), company, as_of)
    q = qualitative if qualitative is not None else (_load(os.path.join(folder, 'evidence_bundle.json'))
                                                    if os.path.exists(os.path.join(folder, 'evidence_bundle.json')) else None)
    if q:
        from .facts import add_qualitative_bundle
        add_qualitative_bundle(fb, q)
    inp = inputs if inputs is not None else (_load(os.path.join(folder, 'inputs.json'))
                                             if os.path.exists(os.path.join(folder, 'inputs.json')) else None)
    return fb, pipeline.run(fb, inp)


def reading_pack(folder):
    """Item 1 (business), 1A (risk factors) and 7 (MD&A) of the saved annual report as plain text, for the analyst who
    answers the business-understanding questions with sources. {} when no annual report was saved."""
    from . import business
    path = os.path.join(folder, 'raw', 'sec__annual_report.htm')
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8', errors='replace') as f:
        return business.sections(business.html_text(f.read()))


def _lvl(dc, k):
    return ((dc.get('assessments') or {}).get(k) or {}).get('level')


def _business_brief(b):
    """The parts that make the money, in one line each, from the casefile's business summary."""
    if not b or b.get('status') != 'parsed':
        return {'status': 'not parsed'}
    parts = b.get('parts') or {}
    t = parts.get('segment') if len((parts.get('segment') or {}).get('rows') or []) > 1 else parts.get('product') or parts.get('revenue_type')
    rows = (t or {}).get('rows') or []
    return {'status': 'parsed', 'parts': [{k: x.get(k) for k in ('name', 'share', 'cagr', 'operating_margin')} for x in rows[:5]],
            'rpo': (b.get('rpo') or {}).get('value'), 'cross_check': b.get('cross_check'),
            'top_customer': (b.get('customer_concentration_revenue') or [None])[0]}


def row(cf, card=None):
    """One comparison row from a casefile (and the discovery card that queued it). No field is blended into another."""
    dc, rk, val = cf.get('dc') or {}, cf.get('rk') or {}, cf.get('val') or {}
    er, v = dc.get('expected_return') or {}, dc.get('verdict') or {}
    down = rk.get('downside') or {}
    vv = dc.get('valuation_view') or {}
    lanes = [x['lens'] for x in (card or {}).get('lanes', [])]
    hurdle, base = er.get('hurdle'), er.get('base_irr')
    idx = er.get('index_expected_return')
    sev = {'high': 0, 'medium': 1, 'low': 2}
    reg = sorted(rk.get('register') or [], key=lambda r: sev.get(r.get('severity'), 3))
    evidenced = [r for r in reg if r.get('pricing_status') != 'open_question' and not str(r.get('id', '')).startswith('data.')]
    open_q = [r for r in reg if r.get('pricing_status') == 'open_question' and r.get('severity') == 'high']
    return {'symbol': cf['company']['ticker'], 'name': cf['company'].get('name'),
            'lanes': lanes, 'group': GROUPS.get(lanes[0]) if lanes else None,
            'why_found': (card or {}).get('why_found'),
            'stages_failed': [k for k, s in (cf.get('stages') or {}).items() if s != 'ok'],
            'errors': cf.get('errors') or [],
            'verdict': v.get('code'), 'verdict_he': v.get('label_he'), 'verdict_reason': v.get('reason'),
            'price_claim_withheld': bool(v.get('price_claim_withheld', True)),
            'review_status': (cf.get('rv') or {}).get('status'),
            'confidence': (dc.get('confidence') or {}).get('level'), 'confidence_reasons': ((dc.get('confidence') or {}).get('reasons') or [])[:3],
            'levels': {k: _lvl(dc, k) for k in DIMS},
            'price': vv.get('price') or er.get('price'), 'price_as_of': vv.get('price_as_of'),
            'value_bear_base_bull_today': [vv.get('bear'), vv.get('base'), vv.get('bull')],
            'value_at_required_return': er.get('value_at_required_return'),
            'irr': {'bear': er.get('bear_irr'), 'base': base, 'bull': er.get('bull_irr')},
            'required_return': hurdle, 'index_expected_return': idx.get('value') if isinstance(idx, dict) else idx,
            'index_basis': idx.get('basis') if isinstance(idx, dict) else None,
            'base_minus_required': base - hurdle if base is not None and hurdle is not None else None,
            'meets_owner_aspiration_14pct': er.get('meets_owner_aspiration_14pct'),
            'loss_to_bear': down.get('loss_to_bear'), 'loss_to_permanent': down.get('loss_to_permanent'),
            'permanent_loss_case': down.get('permanent_loss_case'),
            'valuation_error_size': (rk.get('error_size') or {}).get('level'),
            'valuation_method': (val.get('case') or {}).get('method') or vv.get('method'),
            'key_risks': [r['description'] for r in evidenced[:3]],
            'open_questions': [r['id'].split('.', 1)[-1] for r in open_q[:5]],
            'screening_flags': ((card or {}).get('risks') or [])[:3], 'falsifiers': ((card or {}).get('falsifiers') or [])[:2],
            'what_would_change_it': v.get('what_would_change_it') or [],
            'judgement': (dc.get('judgement') or {}).get('reading'),
            'material_disqualifier': (dc.get('judgement') or {}).get('material_disqualifier'),
            'business': _business_brief(cf.get('business')),
            'portfolio_fit': 'not assessed here: portfolio.compare against the read-only book',
            'casefile_sha256': cf.get('casefile_sha256')}


def compare(casefiles, card_set=None):
    """Cross-opportunity comparison. Separate dimensions, no composite score and no single winner: for each dimension the
    names are listed best-first, and the open conditions (draft, withheld price, low confidence) stay on every row."""
    cards = {c['symbol']: c for c in (card_set or {}).get('cards', [])}
    rows = [row(cf, cards.get(cf['company']['ticker'])) for cf in casefiles]
    order = {'strong': 0, 'adequate': 1, 'weak': 2}
    by_dim = {}
    for k in DIMS:
        ranked = sorted((r for r in rows if r['levels'].get(k) in order), key=lambda r: (order[r['levels'][k]], r['symbol']))
        by_dim[k] = [{'symbol': r['symbol'], 'level': r['levels'][k]} for r in ranked]
    irr = sorted((r for r in rows if r['base_minus_required'] is not None), key=lambda r: -r['base_minus_required'])
    loss = sorted((r for r in rows if r['loss_to_bear'] is not None), key=lambda r: r['loss_to_bear'])
    groups = {}
    for r in rows:
        groups.setdefault(r['group'] or 'unqueued', []).append(r['symbol'])
    drafts = [r['symbol'] for r in rows if r['review_status'] != 'passed' or r['price_claim_withheld']]
    return {'rows': rows, 'by_dimension': by_dim,
            'base_irr_minus_required': [{'symbol': r['symbol'], 'value': r['base_minus_required']} for r in irr],
            'loss_to_bear': [{'symbol': r['symbol'], 'value': r['loss_to_bear']} for r in loss],
            'groups': groups, 'drafts': drafts,
            'notes': ['No composite score: each list orders one dimension; a name can lead one and trail another.',
                      'Expected returns are conditional on each scenario; the index expected return is the engine assumption '
                      'recorded per casefile (risk-free + equity premium unless inputs.index replaces it).',
                      'The 14% owner aspiration is reported, never targeted: no forecast is tuned to reach it.',
                      'Drafts (no independent review) are mechanical results: price claims are withheld until review.']
            + ([f"{len(drafts)} of {len(rows)} casefiles are drafts: {', '.join(drafts)}"] if drafts else []),
            'meaning': 'research comparison, not a recommendation; no orders'}


def _p(x, d=0):
    return f'{x * 100:.{d}f}%' if isinstance(x, (int, float)) else 'n/a'


def _v(x):
    return f'{x:,.1f}' if isinstance(x, (int, float)) else 'n/a'


def render_he(comp):
    """Short Hebrew comparison: per group, per company, the separate dimensions in one line each."""
    he = {'strong': 'חזקה', 'adequate': 'סבירה', 'weak': 'חלשה'}
    lv = lambda x: he.get(x, 'לא ידוע')
    out = []
    for g, syms in comp['groups'].items():
        out.append(f"## {GROUPS_HE.get(g, g)}")
        for s in syms:
            r = next(x for x in comp['rows'] if x['symbol'] == s)
            L = r['levels']
            bear, base, bull = r['value_bear_base_bull_today']
            out += [f"### {s} — {r['name']} ({', '.join(LANES_HE.get(x, x) for x in r['lanes'])})",
                    f"- פסק דין: {r['verdict_he'] or r['verdict'] or 'לא חושב'}; ביטחון {r['confidence'] or 'n/a'}; "
                    f"בדיקה בלתי תלויה: {r['review_status'] or 'n/a'}" + (' (טיוטה: מסקנת מחיר מושהית)' if r['price_claim_withheld'] else ''),
                    f"- איכות עסק {lv(L['business_quality'])}, איכות צמיחה {lv(L['growth_quality'])}, חוסן פיננסי "
                    f"{lv(L['financial_strength'])}, הקצאת הון {lv(L['management_capital_allocation'])}, אטרקטיביות מחיר "
                    f"{lv(L['price_attractiveness'])}, אי־ודאות נתונים {lv(L['data_uncertainty'])}",
                    f"- מחיר {_v(r['price'])}; שווי היום דובי/בסיס/שורי {_v(bear)} / {_v(base)} / {_v(bull)}; "
                    f"שווי בתשואה הנדרשת {_v(r['value_at_required_return'])}",
                    f"- תשואה שנתית משתמעת דובי/בסיס/שורי {_p(r['irr']['bear'], 1)} / {_p(r['irr']['base'], 1)} / {_p(r['irr']['bull'], 1)}; "
                    f"נדרשת {_p(r['required_return'], 1)}; מדד {_p(r['index_expected_return'], 1)}",
                    f"- הפסד עד התרחיש הדובי {_p(r['loss_to_bear'])}; בתרחיש פגיעה קבועה {_p(r['loss_to_permanent'])}",
                    '- איך מרוויחה: ' + ('; '.join(f"{x['name']} {_p(x['share'])} מההכנסות, צמיחה {_p(x['cagr'])}/שנה"
                                                    + (f", שולי תפעול {_p(x['operating_margin'])}" if x.get('operating_margin') is not None else '')
                                                    for x in (r['business'].get('parts') or [])[:4]) or 'לא פוענח מהדוח'),
                    *([f"- בדיקת מגזרים מול התחזית: {_p(r['business']['cross_check']['segment_built_avg_growth'], 1)} לשנה לפי המגזרים מול "
                       f"{_p(r['business']['cross_check']['consolidated_base_avg_growth'], 1)} בתחזית המאוחדת"]
                      if (r['business'].get('cross_check') or {}).get('reading', 'consistent') != 'consistent' else []),
                    f"- שיפוט: {r['judgement'] or 'n/a'}",
                    f"- סיכונים מהניתוח: {'; '.join(r['key_risks']) or 'לא סומנו'}",
                    f"- שאלות פתוחות מהותיות: {', '.join(r['open_questions']) or 'אין'}", '']
    if comp.get('blocked'):
        out += ['לא נותחו (בדיקת זהות/מטבע):'] + [f"- {b['symbol']}: {b['reasons'][0]}" for b in comp['blocked']] + ['']
    out += ['הערות:'] + [f'- {n}' for n in comp['notes']]
    return '\n'.join(out)
