"""Read-only portfolio layer: what is held, how holdings overlap economically, what one adverse
scenario does to all of them at once, and how a new idea compares with what is already owned.

Why (fit-check of 2026-10; brain/protocols/portfolio.md): the engine judged one stock at a time. A
second AI-capex name was added while the first already sat at the 20% single-name cap, so the
account carried one economic bet of ~30% while every single-name rule passed. Sector labels hid it:
a foundry, a GPU designer and a data-centre power supplier share one revenue driver.

Rules kept here
- READ-ONLY. Parses payloads the client already fetched; never calls a broker, never stages or sizes
  an order, never states a quantity to trade. Option sizes are illustrative portfolio weights and
  every option says `executable: False`. `_no_orders` asserts the outputs carry no order fields.
- Private. A book holds positions and values: keep it, and anything derived from it, out of Git.
  Account identifiers are never copied from the payload.
- Economic exposure, not names: a tag counts weight x exposure weight (share of the holding's value
  driven by that tag, -1..1; negative = benefits when the tag is hurt). A tag without a stated weight
  is assumed fully exposed and labelled `assumed_full`, so the user sees where names stood in for
  economics.
- Business quality, pricing, portfolio fit and tax stay separate fields. Nothing is blended into one
  score and `compare` lists options; it never selects one.
- Tax only with cost basis. The owner's stated regime (25% on realized gains) is an input assumption
  (`tax_rate`); lot identification, inflation adjustment and FX rules are not modelled.
"""
import json, math, re
from datetime import date
from itertools import combinations
from .finance import number

ORDER_KEYS = {'order', 'orders', 'ordertype', 'side', 'limitprice', 'stopprice', 'tif', 'timeinforce', 'transmit',
              'orderinstruction', 'sharestobuy', 'sharestosell', 'quantitytotrade', 'contractstotrade'}
DATA_MAPS = {'illustrative_weight_change', 'scenario_changes', 'by_sector', 'by_industry', 'by_tag', 'by_counterparty',
             'by_currency', 'ib_theme_membership', 'holdings', 'shared_tags', 'candidate_tags', 'tag_sources',
             'exposure_weights', 'exposure_sources', 'scenario_impacts', 'scenarios', 'shocks', 'tag_parts', 'tags'}
STALE_IRR = {'stale_analysis', 'price_moved', 'below_bear_value', 'above_bull_value', 'verdict_changed', 'business_changed'}
ANALYZE_DEFAULTS = {'single_name_cap': 0.20, 'review_age_days': 120, 'price_band_trigger': 0.20,
                    'link_threshold': 0.25, 'scenarios': {}, 'previous_verdicts': {}}
TAX_SOURCE = "owner's stated regime (25% on realized gains); an input assumption, not tax advice"
COMPARE_DEFAULTS = {'trial_weight': 0.03, 'tax_rate': 0.25, 'horizon_years': 5, 'max_targets': 5}
RISK_RANK = {'low': 1, 'medium': 2, 'moderate': 2, 'elevated': 3, 'high': 3, 'very_high': 4, 'extreme': 4,
             'strong': 1, 'adequate': 2, 'weak': 3}     # last three: decision.risk_and_error levels (strong = low risk)
SEVERITY = {'high': 3, 'medium': 2, 'low': 1}
SUPPORT = {'attractive_at_price': 'supports_price', 'quality_but_expensive': 'quality_not_price',
           'promising_funding_conditional': 'potential_funding_conditional',
           'interesting_not_yet_valuable': 'potential_not_yet_valued', 'insufficient_evidence': 'insufficient_evidence',
           'index_preferable': 'research_prefers_index', 'avoid': 'research_against'}
DERIV = {'OPT', 'WAR', 'FUT', 'CFD'}
AC = {'STK': 'STK', 'STOCK': 'STK', 'EQUITY': 'STK', 'COMMON': 'STK', 'CS': 'STK', 'ADR': 'STK', 'ETF': 'ETF',
      'FUND': 'FUND', 'MF': 'FUND', 'OPT': 'OPT', 'OPTION': 'OPT', 'FOP': 'OPT', 'WAR': 'WAR', 'WARRANT': 'WAR',
      'FUT': 'FUT', 'FUTURE': 'FUT', 'CASH': 'CASH', 'FX': 'CASH', 'FOREX': 'CASH', 'CURRENCY': 'CASH',
      'BOND': 'BOND', 'BND': 'BOND', 'CFD': 'CFD', 'CRYPTO': 'CRYPTO'}
ACCOUNT_LIKE = re.compile(r'^([A-Z]{0,2}\d{5,}|\d+)$', re.I)
FUND_CLASSES = {'ETF', 'FUND', 'MF'}
ALIASES = {'symbol': ('symbol', 'ticker', 'localsymbol', 'contractsymbol'),
           'description': ('description', 'contractdesc', 'contractdescription', 'name', 'fullname', 'longname', 'companyname'),
           'conid': ('conid', 'contractid', 'conidex'),
           'quantity': ('position', 'quantity', 'qty', 'pos', 'shares', 'units'),
           'price': ('mktprice', 'marketprice', 'price', 'lastprice', 'last', 'markprice'),
           'value': ('mktvalue', 'marketvalue', 'positionvalue', 'value', 'mktval'),
           'avg_cost': ('avgcost', 'averagecost', 'avgprice', 'averageprice', 'costpershare'),
           'cost_basis': ('costbasis', 'totalcost', 'costbasismoney', 'bookvalue'),
           'unrealized_pnl': ('unrealizedpnl', 'unrealizedpl', 'unrealized', 'upnl', 'unrealizedgain'),
           'currency': ('currency', 'ccy', 'curr'),
           'asset_class': ('assetclass', 'sectype', 'securitytype', 'instrumenttype', 'type'),
           'multiplier': ('multiplier', 'mult'),
           'underlying': ('undsym', 'underlying', 'underlyingsymbol', 'undsymbol'),
           'sector': ('sector',), 'industry': ('industry', 'group'),
           'holding_since': ('holdingsince', 'opendate', 'acquired', 'acquisitiondate', 'firsttradedate')}
NLV_TAGS = ('netliquidation', 'netliquidationvalue', 'netliq', 'nlv', 'totalnetliquidation')
CASH_TAGS = ('totalcashvalue', 'totalcash', 'cash', 'cashbalance', 'totalcashbalance')
ACCRUAL_TAGS = ('accruedcash', 'accrueddividend', 'accruedinterest')


# ---------------------------------------------------------------- tolerant parsing helpers
def _k(s):
    return re.sub(r'[^a-z0-9]', '', str(s).lower())


def _num(x):
    """1.5, '1,234.5', '$12', '(3.0)', {'amount': 3}, {'value': 3} -> float; anything else -> None."""
    if isinstance(x, dict):
        return next((v for v in (_num(x.get(k)) for k in ('amount', 'value', 'raw', 'val')) if v is not None), None)
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x) if math.isfinite(x) else None
    if isinstance(x, str):
        s = re.sub(r'^[A-Z]{3}\s+|\s+[A-Z]{3}$', '', x.strip()).replace(',', '').replace('$', '').strip()
        if s.startswith('(') and s.endswith(')'):
            s = '-' + s[1:-1]
        try:
            v = float(s)
        except ValueError:
            return None
        return v if math.isfinite(v) else None
    return None


def _str(x):
    if isinstance(x, str) and x.strip():
        return x.strip()
    if isinstance(x, int) and not isinstance(x, bool):
        return str(x)
    return None


def _pick(f, name, conv=_num):
    for a in ALIASES[name]:
        if a in f and conv(f[a]) is not None:
            return conv(f[a])
    return None


def _rowish(d):
    return bool({_k(k) for k in d} & set(ALIASES['quantity'] + ALIASES['value'] + ALIASES['price']))


def _looks_like_row(d):
    return _rowish(d) and bool({_k(k) for k in d} & set(ALIASES['symbol'] + ALIASES['conid'] + ALIASES['description']))


def _load(p, unparsed, path):
    if isinstance(p, (str, bytes)):
        try:
            return json.loads(p)
        except ValueError:
            unparsed.append({'path': path, 'reason': 'text is not JSON'})
            return None
    return p


def _rows(p, unparsed, path='positions'):
    """[(row, path)] from a list, {'positions': [...]}, {account: [...]}, {symbol: row} or JSON text.
    Paths use positions, never dict keys, so account identifiers cannot leak into the book."""
    p = _load(p, unparsed, path)
    if p is None:
        return []
    if isinstance(p, list):
        out = []
        for i, r in enumerate(p):
            if isinstance(r, dict):
                wrapper = not _rowish(r) and any(isinstance(v, (list, dict)) for v in r.values())
                out += _rows(r, unparsed, f'{path}[{i}]') if wrapper else [(r, f'{path}[{i}]')]
            elif isinstance(r, list):
                out += _rows(r, unparsed, f'{path}[{i}]')
            else:
                unparsed.append({'path': f'{path}[{i}]', 'reason': f'row is {type(r).__name__}, not an object'})
        return out
    if isinstance(p, dict):
        for k in ('positions', 'data', 'result', 'results', 'items', 'rows', 'holdings'):
            if isinstance(p.get(k), (list, dict)):
                return _rows(p[k], unparsed, f'{path}.{k}')
        if _looks_like_row(p):
            return [(p, path)]
        out = []
        for i, (k, v) in enumerate(p.items()):
            sub = f'{path}{{{i}}}'
            if isinstance(v, dict) and _rowish(v):
                f = {_k(a) for a in v}
                named = f & set(ALIASES['symbol'] + ALIASES['description'] + ALIASES['conid'])
                # {symbol: row}; a key that looks like an account or contract id is never used as a symbol
                out.append((v if named or not isinstance(k, str) or ACCOUNT_LIKE.match(k.strip()) else {**v, 'symbol': k}, sub))
            elif isinstance(v, (list, dict)):
                out += _rows(v, unparsed, sub)
            elif _k(k) not in {'accountid', 'acctid', 'account', 'count', 'total', 'page', 'timestamp'}:
                unparsed.append({'path': sub, 'reason': 'scalar where a position was expected'})
        return out
    unparsed.append({'path': path, 'reason': f'unsupported payload type {type(p).__name__}'})
    return []


def _summary(s, unparsed):
    """{normalized tag: (amount, currency)} from {tag: {'amount','currency'}}, {tag: number}, a list of
    {'tag','value','currency'} rows, or a wrapper. Segment variants ('-s', '-c') only fill gaps."""
    s = _load(s, unparsed, 'summary')
    out, seg, base = {}, {}, None
    if isinstance(s, dict):
        for k in ('summary', 'account_summary', 'data', 'result'):
            if isinstance(s.get(k), (dict, list)):
                s = s[k]
                break
    rows = []
    if isinstance(s, dict):
        rows = [(k, v, v.get('currency') if isinstance(v, dict) else None) for k, v in s.items()]
    elif isinstance(s, list):
        for i, r in enumerate(s):
            if isinstance(r, dict) and any(x in r for x in ('tag', 'key', 'name')):
                tag = r.get('tag') or r.get('key') or r.get('name')
                rows.append((tag, r.get('value', r.get('amount')), r.get('currency')))
            else:
                unparsed.append({'path': f'summary[{i}]', 'reason': 'summary row without tag/key/name'})
    elif s is not None:
        unparsed.append({'path': 'summary', 'reason': f'unsupported payload type {type(s).__name__}'})
    for tag, v, ccy in rows:
        raw = str(tag).lower()
        if _k(raw) in {'currency', 'basecurrency'} and _str(v):
            base = _str(v).upper()
            continue
        amt = _num(v)
        if amt is None:
            continue
        target = seg if re.search(r'-[sc]$', raw) else out
        target.setdefault(_k(re.sub(r'-[sc]$', '', raw)), (amt, _str(ccy).upper() if _str(ccy) else None))
    for k, v in seg.items():
        out.setdefault(k, v)
    return out, base


def _sget(summ, tags):
    for t in tags:
        if t in summ:
            return summ[t]
    return None, None


def _position(row):
    f = {_k(k): v for k, v in row.items()}
    ac = _str(_pick(f, 'asset_class', _str))
    ac = AC.get(ac.upper(), ac.upper()) if ac else 'UNKNOWN'
    sym, desc, conid = _pick(f, 'symbol', _str), _pick(f, 'description', _str), _pick(f, 'conid', _str)
    und, notes = _pick(f, 'underlying', _str), []
    if sym is None and desc and ac != 'CASH':
        sym = desc.split()[0].upper()
        notes.append('symbol taken from the first word of the description: confirm the ticker')
    if ac in DERIV and und is None and sym:
        und = sym.split()[0]
    qty, price, val = _pick(f, 'quantity'), _pick(f, 'price'), _pick(f, 'value')
    mult = _pick(f, 'multiplier')
    keys = sorted(f)
    if sym is None and conid is None:
        return 'unparsed', {'reason': 'no symbol, contract id or description', 'keys': keys}
    if qty == 0 or (qty is None and val == 0):
        return 'skipped', {'symbol': sym, 'reason': 'zero quantity (closed position row)'}
    method = 'reported_market_value'
    if val is None and qty is not None and price is not None:
        if ac in DERIV and mult is None:
            notes.append('multiplier missing: value = quantity x price without a contract multiplier')
        val, method = qty * price * (mult or 1.0), 'quantity_x_price'
    elif val is not None and qty is not None and price is not None and ac not in DERIV | {'CASH'}:
        calc = qty * price * (mult or 1.0)
        if abs(calc - val) > 0.02 * max(abs(val), 1.0):
            notes.append(f'reported market value {val:.2f} differs from quantity x price {calc:.2f} by >2%')
    if val is None:
        if ac == 'CASH' and qty is not None:
            val, method = qty, 'cash_balance_quantity'
        else:
            return 'unparsed', {'symbol': sym, 'reason': 'no market value and no quantity x price', 'keys': keys}
    upnl, cb_total, avg = _pick(f, 'unrealized_pnl'), _pick(f, 'cost_basis'), _pick(f, 'avg_cost')
    cb, cb_method = None, None
    if cb_total is not None:
        cb, cb_method = cb_total, 'reported_total'
    elif upnl is not None:
        cb, cb_method = val - upnl, 'market_value_minus_unrealized_pnl'
    elif avg is not None and qty is not None and ac not in DERIV:
        cb, cb_method = avg * qty, 'avg_cost_x_quantity'
    if cb is not None and cb_method != 'avg_cost_x_quantity' and avg is not None and qty is not None and ac not in DERIV:
        if abs(avg * qty - cb) > 0.01 * max(abs(cb), 1.0):
            notes.append('average cost x quantity disagrees with the cost implied by unrealized P&L by >1%')
    return 'ok', {'symbol': sym, 'conid': conid, 'asset_class': ac, 'underlying': und, 'description': desc,
                  'quantity': qty, 'price': price, 'value_local': val, 'value_method': method,
                  'currency': (_pick(f, 'currency', _str) or '').upper() or None, 'multiplier': mult,
                  'cost_basis': cb, 'cost_basis_method': cb_method, 'avg_cost': avg, 'unrealized_pnl': upnl,
                  'ib_sector': _pick(f, 'sector', _str), 'ib_industry': _pick(f, 'industry', _str),
                  'holding_since': _pick(f, 'holding_since', _str), 'notes': notes}


def tags_from_themes(payload):
    """Slug tags from an IB get_company_themes payload (list of names or {'name'|'theme'|'title'} rows).
    Theme membership is a vendor classification by name, not an economic exposure weight."""
    p = _load(payload, [], 'themes')
    if isinstance(p, dict):
        p = next((p[k] for k in ('themes', 'data', 'result', 'results', 'items') if isinstance(p.get(k), list)), [])
    out = set()
    for r in p if isinstance(p, list) else []:
        name = _str(r) if not isinstance(r, dict) else next((_str(r[k]) for k in ('name', 'theme', 'title', 'theme_name', 'label') if _str(r.get(k))), None)
        if name:
            out.add(re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_'))
    return sorted(out)


# ---------------------------------------------------------------- book
def from_ib(positions_payload, summary_payload=None, as_of=None, fx=None, tolerance=0.005):
    """Book from IB read-only payloads (get_account_positions, get_account_summary).

    fx = {currency: units of base currency per unit}; a position in another currency without a rate
    keeps its local value but gets value/weight None and is listed in reconciliation.unconverted.
    Cash comes from the summary (total cash value) when present; cash rows in the positions payload
    are used only when the summary has none, so cash is never counted twice.
    """
    if as_of is not None:
        date.fromisoformat(as_of)
    unparsed, skipped, warnings = [], [], []
    summ, base = _summary(summary_payload, unparsed)
    nlv, nlv_ccy = _sget(summ, NLV_TAGS)
    cash_s, cash_ccy = _sget(summ, CASH_TAGS)
    base = base or nlv_ccy or cash_ccy
    rates = {str(k).upper(): number(v, f'fx.{k}', 1e-12) for k, v in (fx or {}).items()}
    lines, cash_rows = [], []
    for row, path in _rows(positions_payload, unparsed):
        status, p = _position(row)
        if status == 'unparsed':
            unparsed.append({'path': path, **p})
        elif status == 'skipped':
            skipped.append({'path': path, **p})
        else:
            (cash_rows if p['asset_class'] == 'CASH' else lines).append(p)
    if base is None:
        ccys = [p['currency'] for p in lines if p['currency']]
        base = max(set(ccys), key=ccys.count) if ccys else None
        warnings.append(f'base currency not in the summary; assumed {base} (most common position currency)' if base
                        else 'base currency unknown: values are treated as one currency')
    merged, by_key = [], {}
    for p in lines:
        p['currency'] = p['currency'] or base
        rate = 1.0 if p['currency'] == base or base is None else rates.get(p['currency'])
        p['value'] = p['value_local'] * rate if rate is not None else None
        key = p['conid'] or f"{p['symbol']}|{p['asset_class']}|{p['currency']}"
        if key in by_key:                                             # same contract in several accounts
            q = by_key[key]
            for f in ('quantity', 'value_local', 'value', 'unrealized_pnl'):
                q[f] = q[f] + p[f] if q[f] is not None and p[f] is not None else None
            q['cost_basis'] = q['cost_basis'] + p['cost_basis'] if q['cost_basis'] is not None and p['cost_basis'] is not None else None
            q['avg_cost'] = q['cost_basis'] / q['quantity'] if q['cost_basis'] is not None and q['quantity'] and q['asset_class'] not in DERIV else None
            q['merged_rows'] = q.get('merged_rows', 1) + 1
            q['notes'] += p['notes']
        else:
            by_key[key] = p
            merged.append(p)
    unconverted = [{'symbol': p['symbol'], 'currency': p['currency'], 'value_local': p['value_local']}
                   for p in merged if p['value'] is None]
    for u in unconverted:
        warnings.append(f"{u['symbol']}: no FX rate {u['currency']}->{base}; excluded from weights")
    cash, cash_source = None, 'missing'
    if cash_s is not None:
        cash, cash_source = cash_s, 'summary_total_cash'
    elif cash_rows:
        conv = [c['value_local'] * (1.0 if (c['currency'] or base) == base else rates.get(c['currency'] or '', float('nan'))) for c in cash_rows]
        if all(math.isfinite(x) for x in conv):
            cash, cash_source = sum(conv), 'positions_cash_rows'
        else:
            warnings.append('cash rows in a currency without an FX rate: cash unknown')
    pv = sum(p['value'] for p in merged if p['value'] is not None)
    total = pv + (cash or 0.0)
    denom, basis = (nlv, 'net_liquidation') if nlv and nlv > 0 else ((total, 'positions_plus_cash') if total > 0 else (None, 'none'))
    if basis != 'net_liquidation':
        warnings.append('net liquidation missing: weights use positions + cash' if denom else 'no positive base for weights')
    for p in merged:
        p['weight'] = p['value'] / denom if denom and p['value'] is not None else None
        if p['asset_class'] in DERIV:
            p['notes'].append('weight is market value, not delta-adjusted economic exposure')
        if p['asset_class'] == 'UNKNOWN':
            p['notes'].append('asset class missing: treated as a single-issuer holding')
    rec = {'positions_value': pv, 'cash': cash, 'cash_source': cash_source, 'positions_plus_cash': total,
           'net_liquidation': nlv, 'formula': 'difference = net_liquidation - (sum of converted position values + cash)',
           'tolerance': tolerance, 'unconverted': unconverted}
    if nlv is None:
        rec.update(status='not_checked', difference=None, difference_pct=None,
                   possible_reasons=['net liquidation not in the summary payload'])
    else:
        diff = nlv - total
        rec.update(difference=diff, difference_pct=diff / nlv if nlv else None)
        acc = [summ[t][0] for t in ACCRUAL_TAGS if t in summ]
        if acc:
            rec['accruals'] = sum(acc)
            rec['difference_after_accruals'] = diff - sum(acc)
        ok = nlv and (abs(diff) <= tolerance * abs(nlv) or (acc and abs(diff - sum(acc)) <= tolerance * abs(nlv)))
        rec['status'] = 'reconciled' if ok else 'unreconciled'
        reasons = []
        if not ok:
            reasons += ['position prices and the summary may be stamped at different times']
            if unconverted:
                reasons.append(f'{len(unconverted)} position(s) without an FX rate are excluded')
            if unparsed:
                reasons.append(f'{len(unparsed)} unparsed row(s) may carry value')
            if cash is None:
                reasons.append('cash missing')
            if any(p['asset_class'] in DERIV for p in merged):
                reasons.append('derivative valuation (multipliers, marks) may differ')
            if not acc:
                reasons.append('accrued interest/dividends not in the summary (cannot be separated)')
        rec['possible_reasons'] = reasons
    gross_s, _ = _sget(summ, ('grosspositionvalue',))
    if gross_s is not None:
        g = sum(abs(p['value']) for p in merged if p['value'] is not None)
        rec['gross_position_check'] = {'summary': gross_s, 'positions': g, 'difference': gross_s - g}
    positions = [{k: p[k] for k in ('symbol', 'conid', 'asset_class', 'underlying', 'description', 'quantity', 'price',
                                    'value', 'value_local', 'currency', 'weight', 'cost_basis', 'cost_basis_method',
                                    'avg_cost', 'unrealized_pnl', 'value_method', 'ib_sector', 'ib_industry',
                                    'holding_since', 'notes')} | ({'merged_rows': p['merged_rows']} if 'merged_rows' in p else {})
                 for p in merged]
    return _clean({'kind': 'portfolio_book', 'private': True, 'read_only': True, 'source': 'ib_read_only_payloads',
                   'as_of': as_of, 'base_currency': base, 'positions': positions, 'cash': cash,
                   'cash_weight': cash / denom if denom and cash is not None else None, 'net_liquidation': nlv,
                   'weight_basis': basis, 'reconciliation': rec, 'unparsed': unparsed, 'skipped': skipped,
                   'warnings': warnings + ([] if as_of else ['as_of not supplied: staleness checks cannot run']),
                   'note': 'Private: positions, values and anything derived must never be written into Git. '
                           'Account identifiers are not copied.'})


# ---------------------------------------------------------------- analysis
def _exposure_map(i, label):
    """{tag: weight in [-1, 1]} and {tag: exposure_weights|assumed_full}."""
    raw, ew = i.get('exposures') or [], dict(i.get('exposure_weights') or {})
    if isinstance(raw, dict):
        ew, raw = {**raw, **ew}, list(raw)
    e, src = {}, {}
    for t in list(raw) + [t for t in ew if t not in raw]:
        if ew.get(t) is not None:
            e[t], src[t] = number(ew[t], f'{label}.exposure_weights.{t}', -1, 1), 'exposure_weights'
        else:
            e[t], src[t] = 1.0, 'assumed_full'
    return e, src


def _casefile(s, price_now=None):
    """Normalized casefile summary. downside_to_bear = bear_value / price - 1 (negative = loss)."""
    if not s:
        return None
    g = lambda k: _num(s.get(k))
    out = {k: g(k) for k in ('base_irr', 'bear_irr', 'bull_irr', 'bear_value', 'base_value', 'bull_value', 'price')}
    out.update({k: s.get(k) for k in ('symbol', 'verdict', 'quality', 'risk_level', 'as_of', 'currency', 'previous_verdict')})
    p = price_now if price_now is not None else out['price']
    out['downside_to_bear'] = out['bear_value'] / p - 1 if out['bear_value'] is not None and p else g('downside_to_bear')
    out['downside_basis'] = ('bear_value / current price - 1' if out['bear_value'] is not None and p
                             else 'as supplied' if out['downside_to_bear'] is not None else None)
    out['research_support'] = SUPPORT.get(out['verdict'], 'unknown_verdict' if out['verdict'] else 'no_verdict')
    out['verdict_status'] = s.get('verdict_status')
    if out['research_support'] == 'supports_price' and (s.get('price_claim_withheld') or s.get('verdict_status') == 'draft'):
        out['research_support'] = 'draft_unreviewed'   # a withheld price claim never supports buying
    return out


def _specs(raw):
    out = {}
    for name, s in (raw or {}).items():
        if not isinstance(s, dict):
            raise ValueError(f'scenario {name}: expected {{tag: shock}} or {{"shocks": ..., "mechanism": ...}}')
        src = s['shocks'] if isinstance(s.get('shocks'), dict) else {k: v for k, v in s.items() if k not in ('mechanism', 'combine', 'description')}
        out[name] = {'shocks': {t: number(v, f'scenario {name}.{t}', -1, 10) for t, v in src.items()},
                     'mechanism': s.get('mechanism') or s.get('description'),
                     'combine': s.get('combine', 'sum') if s.get('combine', 'sum') in ('sum', 'max_abs') else 'sum'}
    return out


def _impact(e, spec):
    parts = {t: spec['shocks'][t] * w for t, w in e.items() if t in spec['shocks']}
    if not parts:
        return 0.0, parts, 'no_mapped_exposure'
    v = sum(parts.values()) if spec['combine'] == 'sum' else max(parts.values(), key=abs)
    return max(v, -1.0), parts, 'tag_shocks'


def _holdings(book, info, specs):
    hold = {}
    for p in book.get('positions', []):
        if p.get('asset_class') == 'CASH' or p.get('weight') is None:
            continue
        sym, und = p.get('symbol'), p.get('underlying')
        i = info.get(sym) or (info.get(und) if und else None)
        key = (i or {}).get('issuer') or (und if p.get('asset_class') in DERIV and und else sym)
        i = i or info.get(key)
        h = hold.setdefault(key, {'key': key, 'symbols': [], 'weight': 0.0, 'value_local': 0.0, 'cost': 0.0,
                                  'cost_ok': True, 'ccys': set(), 'asset_classes': [], 'price': None, 'price_ccy': None,
                                  'info': None, 'ib_sector': None, 'ib_industry': None, 'holding_since': None})
        h['symbols'].append(sym)
        h['asset_classes'].append(p.get('asset_class'))
        h['weight'] += p['weight']
        h['value_local'] += p.get('value_local') or 0.0
        h['ccys'].add(p.get('currency'))
        h['cost_ok'] = h['cost_ok'] and p.get('cost_basis') is not None
        h['cost'] += p.get('cost_basis') or 0.0
        if p.get('asset_class') not in DERIV and (h['price'] is None or sym == key):
            h['price'], h['price_ccy'] = p.get('price'), p.get('currency')
        h['info'] = h['info'] or i
        h['ib_sector'] = h['ib_sector'] or p.get('ib_sector')
        h['ib_industry'] = h['ib_industry'] or p.get('ib_industry')
        h['holding_since'] = h['holding_since'] or p.get('holding_since')
    for key, h in hold.items():
        i = h['info'] or {}
        h['has_info'] = bool(h['info'])
        h['e'], h['src'] = _exposure_map(i, key)
        h['sector'] = i.get('sector') or h['ib_sector'] or 'unclassified'
        h['sector_source'] = 'holdings_info' if i.get('sector') else ('ib_position' if h['ib_sector'] else 'none')
        h['industry'] = i.get('industry') or h['ib_industry'] or 'unclassified'
        if i.get('diversified') is not None:
            h['diversified'], h['diversified_source'] = bool(i.get('diversified')), 'holdings_info'
        elif set(h['asset_classes']) & FUND_CLASSES:
            h['diversified'], h['diversified_source'] = True, 'asset_class_default'
        else:
            h['diversified'], h['diversified_source'] = False, 'assumed_single_name' if not h['has_info'] else 'holdings_info'
        h['derivative'] = any(a in DERIV for a in h['asset_classes'])
        h['price_comparable'] = ((i.get('casefile_summary') or {}).get('currency') or h['price_ccy']) == h['price_ccy']
        h['cf'] = _casefile(i.get('casefile_summary'), h['price'] if h['price_comparable'] else None)
        single_ccy = len(h['ccys']) == 1
        h['gain'] = ((h['value_local'] - h['cost']) / h['value_local']
                     if h['cost_ok'] and single_ccy and h['value_local'] > 0 else None)
        h['impacts'] = {}
        for name, spec in specs.items():
            sens = (i.get('scenario_sensitivity') or {}).get(name)
            if sens is not None:
                h['impacts'][name] = (number(sens, f'{key}.scenario_sensitivity.{name}', -1, 10), {}, 'holding_estimate')
            elif not h['has_info']:
                h['impacts'][name] = (None, {}, 'no_holding_info')
            else:
                h['impacts'][name] = _impact(h['e'], spec)
    return hold


def _concentration(ws, div, cap):
    """HHI over single-issuer holdings normalized by gross invested weight; diversified funds count as
    fully spread (0) and are reported separately. effective_n = 1 / HHI."""
    gross = sum(abs(w) for w in ws.values())
    single = sorted(((k, abs(w)) for k, w in ws.items() if not div.get(k) and w), key=lambda x: -x[1])
    hhi = sum((w / gross) ** 2 for _, w in single) if gross else None
    return {'top1': {'holding': single[0][0], 'weight': single[0][1]} if single else None,
            'top5_weight': sum(w for _, w in single[:5]), 'hhi': hhi,
            'effective_n': 1 / hhi if hhi else None,
            'over_single_name_cap': [{'holding': k, 'weight': w, 'cap': cap, 'excess': w - cap} for k, w in single if cap is not None and w > cap + 1e-12],
            'diversified_weight': sum(abs(w) for k, w in ws.items() if div.get(k)), 'gross_invested': gross}


def _aggregate(hold, getter):
    """{item: {'weight', 'holdings'}} summing holding weights by a text item (driver, risk, sector...)."""
    out = {}
    for key, h in hold.items():
        for item in getter(h):
            label = item if isinstance(item, str) else (item.get('driver') or item.get('text') or item.get('id')) if isinstance(item, dict) else None
            if not label:
                continue
            r = out.setdefault(' '.join(str(label).lower().split()), {'item': str(label), 'weight': 0.0, 'holdings': []})
            r['weight'] += h['weight']
            r['holdings'].append(key)
    return sorted(out.values(), key=lambda r: -abs(r['weight']))


def _tag_table(hold, field='e', srcfield='src', cap=None):
    tags = {}
    for key, h in hold.items():
        for t, w in h[field].items():
            r = tags.setdefault(t, {'exposure': 0.0, 'name_weight': 0.0, 'assumed_exposure': 0.0, 'holdings': []})
            c = h['weight'] * w
            r['exposure'] += c
            r['name_weight'] += h['weight']
            if h[srcfield].get(t) == 'assumed_full':
                r['assumed_exposure'] += c
            r['holdings'].append({'holding': key, 'weight': h['weight'], 'exposure_weight': w,
                                  'source': h[srcfield].get(t), 'contribution': c})
    for t, r in tags.items():
        r['holdings'].sort(key=lambda x: -abs(x['contribution']))
        r['assumed_share'] = r['assumed_exposure'] / r['exposure'] if r['exposure'] else None
        r['above_single_name_cap'] = cap is not None and r['exposure'] > cap
        r['formula'] = 'sum(holding weight x exposure weight)'
    return dict(sorted(tags.items(), key=lambda x: -abs(x[1]['exposure'])))


def _overlaps(hold, cap, thr):
    """Clusters are tag-centric: holdings that all carry the same tag at |weight| >= thr (same sign).
    A tag whose holder set sits inside a larger cluster joins it as a further shared tag. No
    single-linkage chaining (A~B on AI, B~C on rates does not make A~C), and a diversified fund's
    look-through exposure counts in tag totals but does not define a cluster."""
    single = {k: h for k, h in hold.items() if h['e'] and not h['diversified']}
    groups = {}
    for t in {t for h in single.values() for t in h['e']}:
        for sign in (1, -1):
            m = frozenset(k for k, h in single.items() if sign * h['e'].get(t, 0) >= thr)
            if len(m) >= 2:
                groups[(t, sign)] = m
    expo = lambda t, members: sum(hold[k]['weight'] * hold[k]['e'][t] for k in members)
    clusters = []
    for (t, sign), m in sorted(groups.items(), key=lambda x: (-len(x[1]), -abs(expo(x[0][0], x[1])))):
        host = next((c for c in clusters if m <= c['_m']), None)
        if host is None:
            host = {'_m': m, 'holdings': sorted(m, key=lambda k: -abs(hold[k]['weight'])), 'defining_tag': t, 'shared_tags': {}}
            clusters.append(host)
        host['shared_tags'][t] = expo(t, m)
    for c in clusters:
        m = c.pop('_m')
        dom = max(c['shared_tags'], key=lambda t: abs(c['shared_tags'][t]))
        sectors = sorted({hold[k]['sector'] for k in m})
        c.update(combined_weight=sum(hold[k]['weight'] for k in m), dominant_tag=dom, dominant_exposure=c['shared_tags'][dom],
                 sectors=sectors, hidden_by_sector_labels=len(sectors) > 1,
                 above_single_name_cap=cap is not None and abs(c['shared_tags'][dom]) > cap,
                 reading=f"{len(m)} holdings share {c['defining_tag']}; largest shared exposure {dom} = {c['shared_tags'][dom]:.1%} "
                         f"of the portfolio: one economic bet",
                 rule=f"all members carry {c['defining_tag']} at exposure weight >= {thr} (same sign); "
                      f"other shared tags are carried by at least two members")
    clusters.sort(key=lambda c: -abs(c['dominant_exposure']))
    pairs, label_only = [], []
    for a, b in combinations(single, 2):
        ea, eb = hold[a]['e'], hold[b]['e']
        shared = {t: min(abs(ea[t]), abs(eb[t])) for t in set(ea) & set(eb) if ea[t] * eb[t] > 0}
        if any(v >= thr for v in shared.values()):
            pairs.append({'holdings': [a, b], 'shared': {t: v for t, v in shared.items() if v >= thr},
                          'same_sector_label': hold[a]['sector'] == hold[b]['sector']})
        elif hold[a]['sector'] == hold[b]['sector'] != 'unclassified' and not shared:
            label_only.append({'holdings': [a, b], 'sector': hold[a]['sector'],
                               'note': 'same sector label, no shared economic tag: do not treat as one risk'})
    shared_tags = []
    for t, r in _tag_table({k: h for k, h in hold.items() if h['e']}, cap=cap).items():
        members = [x for x in r['holdings'] if x['exposure_weight'] >= thr]
        if len(members) >= 2:
            ex = sum(x['contribution'] for x in members)
            shared_tags.append({'tag': t, 'holdings': [x['holding'] for x in members],
                                'offsets': [x['holding'] for x in r['holdings'] if x['exposure_weight'] <= -thr],
                                'combined_weight': sum(x['weight'] for x in members), 'exposure': ex,
                                'above_single_name_cap': cap is not None and ex > cap,
                                'note': 'includes look-through exposure of diversified funds'})
    return {'clusters': clusters, 'shared_tags': sorted(shared_tags, key=lambda r: -abs(r['exposure'])),
            'pairs': pairs, 'same_label_different_drivers': label_only}


def _scenario(name, fallback, hold, clusters, mechanism=None):
    """Portfolio impact of one scenario; `fallback(h)` gives the impact when the scenario is not a tag spec."""
    rows, unknown = [], 0.0
    for key, h in hold.items():
        imp, parts, src = h['impacts'][name] if name in h['impacts'] else fallback(h)
        if imp is None:
            unknown += abs(h['weight'])
        rows.append({'holding': key, 'weight': h['weight'], 'impact': imp, 'source': src, 'tag_parts': parts,
                     'contribution': h['weight'] * imp if imp is not None else None})
    total = sum(r['contribution'] for r in rows if r['contribution'] is not None)
    loss = sum(r['contribution'] for r in rows if r['contribution'] and r['contribution'] < 0)
    rows.sort(key=lambda r: (r['contribution'] is None, r['contribution'] or 0))
    for r in rows:
        r['share_of_loss'] = r['contribution'] / loss if loss and r['contribution'] and r['contribution'] < 0 else None
    by_cluster = []
    for c in clusters:          # only the shocks on the cluster's own shared tags (whole impact for estimates / bear cases)
        cl = sum(r['weight'] * (sum(v for t, v in r['tag_parts'].items() if t in c['shared_tags']) if r['tag_parts'] else r['impact'])
                 for r in rows if r['holding'] in c['holdings'] and r['impact'] is not None)
        if cl < 0:
            by_cluster.append({'holdings': c['holdings'], 'defining_tag': c['defining_tag'], 'loss': cl,
                               'share_of_loss': cl / loss if loss else None,
                               'basis': "shocks on the cluster's shared tags; whole impact where only a holding estimate exists"})
    by_cluster.sort(key=lambda x: x['loss'])
    top3 = [r for r in rows if r['share_of_loss']][:3]
    return {'mechanism': mechanism, 'portfolio_impact': total, 'portfolio_loss_from_losers': loss,
            'formula': 'sum(weight x impact); impact = holding estimate, else sum of tag shock x exposure weight (floor -100%)',
            'unknown_weight': unknown, 'holdings': rows,
            'no_mapped_exposure': [r['holding'] for r in rows if r['source'] == 'no_mapped_exposure'],
            'top3_share_of_loss': sum(r['share_of_loss'] for r in top3) if top3 else None,
            'by_cluster': by_cluster,
            'warnings': ([] if mechanism else ['mechanism not stated: a shock without a mechanism is an unexplained correlation'])
                        + ([f'{unknown:.1%} of the portfolio has no impact estimate (no holdings_info or no bear value): unknown, not zero']
                           if unknown else [])
                        + (['no_mapped_exposure means no stated exposure to the shocked tags, not immunity']
                           if any(r['source'] == 'no_mapped_exposure' for r in rows) else [])}


def analyze(book, holdings_info=None, options=None):
    """Weights, concentration, economic exposures, overlaps, scenario stress, drivers and review
    triggers for a book from `from_ib`. holdings_info = {symbol: {'sector','industry','exposures',
    'exposure_weights'?, 'thesis_drivers', 'key_risks'?, 'counterparties'?, 'issuer'?, 'diversified'?,
    'casefile_summary'?, 'scenario_sensitivity'?, 'monitor_alerts'?, 'ib_themes'?}}."""
    o = {**ANALYZE_DEFAULTS, **(options or {})}
    cap = number(o['single_name_cap'], 'single_name_cap', 0, 1) if o['single_name_cap'] is not None else None
    thr = number(o['link_threshold'], 'link_threshold', 0, 1)
    specs = _specs(o['scenarios'])
    hold = _holdings(book, holdings_info or {}, specs)
    ws = {k: h['weight'] for k, h in hold.items()}
    div = {k: h['diversified'] for k, h in hold.items()}
    conc = _concentration(ws, div, cap)
    conc.update(net_invested=sum(ws.values()), cash_weight=book.get('cash_weight'),
                formula={'hhi': 'sum((|w_i| / gross invested)^2) over single-issuer holdings',
                         'effective_n': '1 / hhi', 'weights': f"value / {book.get('weight_basis')}"})
    for h in hold.values():
        h['cp'] = {}
        cps = (h['info'] or {}).get('counterparties') or []
        for c in cps if isinstance(cps, list) else list(cps):
            h['cp'][c] = number(cps[c], f"{h['key']}.counterparties.{c}", 0, 1) if isinstance(cps, dict) and cps[c] is not None else 1.0
        h['cp_src'] = {c: 'stated_share' if isinstance(cps, dict) and cps.get(c) is not None else 'assumed_full' for c in h['cp']}
        h['themes'] = {t: 1.0 for t in (h['info'] or {}).get('ib_themes') or []}
        h['theme_src'] = {t: 'membership' for t in h['themes']}
    ov = _overlaps(hold, cap, thr)
    table = lambda getter: {r['item']: {'weight': r['weight'], 'holdings': r['holdings']} for r in _aggregate(hold, getter)}
    exposures = {'by_sector': table(lambda h: [h['sector']]), 'by_industry': table(lambda h: [h['industry']]),
                 'by_tag': _tag_table(hold, cap=cap),
                 'by_counterparty': _tag_table(hold, 'cp', 'cp_src', cap),
                 'by_currency': table(lambda h: sorted(c for c in h['ccys'] if c)),
                 'ib_theme_membership': {t: {'name_weight': r['name_weight'], 'holdings': [x['holding'] for x in r['holdings']]}
                                         for t, r in _tag_table(hold, 'themes', 'theme_src').items()},
                 'unclassified': [k for k, h in hold.items() if not h['has_info']],
                 'note': 'by_tag weights each holding by its exposure weight (economic share), not by name; '
                         'ib_theme_membership is vendor classification by name only.'}
    scenarios = {name: _scenario(name, None, hold, ov['clusters'], spec['mechanism']) | {'shocks': spec['shocks'], 'combine': spec['combine']}
                 for name, spec in specs.items()}

    def own_bear(h):
        cf = h['cf']
        if cf and cf['downside_to_bear'] is not None:
            return max(cf['downside_to_bear'], -1.0), {}, 'casefile_bear_value'
        return None, {}, 'no_bear_value'
    if any(h['cf'] for h in hold.values()):
        scenarios['simultaneous_own_bear_cases'] = _scenario('simultaneous_own_bear_cases', own_bear, hold, ov['clusters'],
                                                             'every held thesis reaches its own bear value at once; not one coherent macro scenario')
    drivers = {'thesis_drivers': _aggregate(hold, lambda h: (h['info'] or {}).get('thesis_drivers') or []),
               'key_risks': _aggregate(hold, lambda h: (h['info'] or {}).get('key_risks') or []),
               'top_exposures': [{'tag': t, 'exposure': r['exposure']} for t, r in list(exposures['by_tag'].items())[:5]],
               'note': 'weights are summed per stated driver/risk text; identical wording is needed to aggregate.'}
    review = _review(book, hold, ov, o, cap)
    holdings = {}
    for k, h in hold.items():
        cf = h['cf'] or {}
        holdings[k] = {'symbols': h['symbols'], 'weight': h['weight'], 'sector': h['sector'], 'sector_source': h['sector_source'],
                       'industry': h['industry'], 'exposure_weights': h['e'], 'exposure_sources': h['src'],
                       'diversified': h['diversified'], 'derivative': h['derivative'], 'has_info': h['has_info'],
                       'price_now': h['price'], 'currency': h['price_ccy'],
                       'business_quality': cf.get('quality'), 'risk_level': cf.get('risk_level'),
                       'pricing': {'verdict': cf.get('verdict'), 'research_support': cf.get('research_support'),
                                   'base_irr': cf.get('base_irr'), 'bear_irr': cf.get('bear_irr'),
                                   'base_value_vs_price': cf['base_value'] / h['price'] - 1
                                   if cf.get('base_value') and h['price'] and h['price_comparable'] else None,
                                   'downside_to_bear': cf.get('downside_to_bear'), 'analysis_as_of': cf.get('as_of')} if h['cf'] else None,
                       'portfolio_fit': {'over_single_name_cap': cap is not None and not h['diversified'] and abs(h['weight']) > cap,
                                         'clusters': [i for i, c in enumerate(ov['clusters']) if k in c['holdings']]},
                       'tax': {'unrealized_gain_fraction': h['gain'], 'cost_basis_available': h['gain'] is not None,
                               'holding_since': h['holding_since']},
                       'scenario_impacts': {n: v[0] for n, v in h['impacts'].items()}}
    weights = {'basis': book.get('weight_basis'), 'cash': book.get('cash_weight'),
               'positions': [{'symbol': p.get('symbol'), 'asset_class': p.get('asset_class'), 'weight': p.get('weight')} for p in book.get('positions', [])],
               'holdings': dict(sorted(ws.items(), key=lambda x: -abs(x[1]))),
               'excluded': [p.get('symbol') for p in book.get('positions', []) if p.get('weight') is None]}
    out = {'as_of': book.get('as_of'), 'read_only': True, 'weights': weights, 'concentration': conc, 'exposures': exposures,
           'overlaps': ov, 'scenarios': scenarios, 'drivers': drivers, 'review_needed': review, 'holdings': holdings,
           'params': {'single_name_cap': cap,
                      'single_name_cap_source': 'options' if 'single_name_cap' in (options or {}) else 'default (20% cap from the 2026-10 fit-check)',
                      'link_threshold': thr, 'review_age_days': o['review_age_days'], 'price_band_trigger': o['price_band_trigger'],
                      'scenarios': specs},
           'notes': ['Read-only analysis; no orders are created or suggested.',
                     'A shared tag above the single-name cap means one economic bet larger than any single name is allowed to be.',
                     'Private output: derived from account positions; keep out of Git.']}
    return _no_orders(_clean(out))


def _review(book, hold, ov, o, cap):
    as_of = date.fromisoformat(book['as_of']) if book.get('as_of') else None
    band, age_max = number(o['price_band_trigger'], 'price_band_trigger', 0), o['review_age_days']
    out = []
    for key, h in hold.items():
        i, cf, t = h['info'] or {}, h['cf'], []
        raw = i.get('casefile_summary') or {}
        if h.get('diversified_source') == 'assumed_single_name' and 'STK' in h['asset_classes']:
            t.append({'code': 'fund_status_unknown', 'severity': 'medium',
                      'detail': 'listed as a stock with no holdings info: treated as a single issuer; if it is an ETF/fund, '
                                'set diversified in holdings info so concentration is not overstated'})
        if not cf:
            t.append({'code': 'no_analysis', 'severity': 'low' if h['diversified'] else 'high',
                      'detail': 'no casefile summary: quality, price and risk unassessed'})
        else:
            if cf['as_of'] and as_of:
                age = (as_of - date.fromisoformat(str(cf['as_of'])[:10])).days
                if age < 0:
                    t.append({'code': 'analysis_after_book_date', 'severity': 'high',
                              'detail': f'analysis dated {cf["as_of"]} is after the book as_of (look-ahead or mismatched dates)'})
                elif age > age_max:
                    t.append({'code': 'stale_analysis', 'severity': 'medium', 'detail': f'analysis is {age} days old (> {age_max})'})
            elif not cf['as_of']:
                t.append({'code': 'analysis_date_missing', 'severity': 'medium', 'detail': 'casefile summary has no as_of'})
            if raw.get('currency') and h['price_ccy'] and raw['currency'] != h['price_ccy']:
                t.append({'code': 'price_currency_mismatch', 'severity': 'medium',
                          'detail': f"analysis in {raw['currency']}, position priced in {h['price_ccy']}: price checks skipped"})
            else:
                p, p0 = h['price'], cf['price']
                if p and p0:
                    move = p / p0 - 1
                    if abs(move) > band:
                        t.append({'code': 'price_moved', 'severity': 'medium', 'detail': f'price {move:+.1%} since analysis (band {band:.0%})',
                                  'move': move, 'formula': 'price_now / analysis price - 1'})
                if p and cf['bear_value'] is not None and p < cf['bear_value']:
                    t.append({'code': 'below_bear_value', 'severity': 'high', 'detail': 'price below the bear value: thesis broken or opportunity; re-check which'})
                if p and cf['bull_value'] is not None and p > cf['bull_value']:
                    t.append({'code': 'above_bull_value', 'severity': 'high', 'detail': 'price above the bull value: expectations exceed the analysed range'})
            prev = cf.get('previous_verdict') or (o.get('previous_verdicts') or {}).get(key)
            if prev and cf['verdict'] and prev != cf['verdict']:
                t.append({'code': 'verdict_changed', 'severity': 'high', 'detail': f'verdict {prev} -> {cf["verdict"]}'})
            if cf['verdict'] in ('avoid', 'index_preferable', 'insufficient_evidence'):
                t.append({'code': 'held_against_verdict', 'severity': 'high', 'detail': f'held while the research verdict is {cf["verdict"]}'})
        alerts = i.get('monitor_alerts') or []
        if alerts:
            t.append({'code': 'business_changed', 'severity': 'high', 'detail': f'{len(alerts)} monitor alert(s) since the thesis was frozen', 'alerts': alerts})
        if cap is not None and not h['diversified'] and abs(h['weight']) > cap:
            t.append({'code': 'above_single_name_cap', 'severity': 'medium', 'detail': f"weight {h['weight']:.1%} > cap {cap:.0%}"})
        for c in ov['clusters']:
            if key in c['holdings'] and c['above_single_name_cap']:
                t.append({'code': 'in_cluster_above_cap', 'severity': 'medium',
                          'detail': f"cluster sharing {c['defining_tag']} ({', '.join(c['holdings'])}) carries {c['dominant_exposure']:.1%} "
                                    f"{c['dominant_tag']} exposure, above the {cap:.0%} single-name cap"})
        if h['derivative']:
            t.append({'code': 'derivative_not_delta_adjusted', 'severity': 'low', 'detail': 'option/derivative weight is market value, not economic exposure'})
        if t:
            sev = max(SEVERITY[x['severity']] for x in t)
            out.append({'holding': key, 'weight': h['weight'], 'priority': {3: 'high', 2: 'medium', 1: 'low'}[sev], 'triggers': t})
    out.sort(key=lambda r: (-SEVERITY[r['priority']], -abs(r['weight'])))
    return out


# ---------------------------------------------------------------- compare
def _after_tax_end(start, basis, r, years, t):
    end = start * (1 + r) ** years
    return end - t * max(end - basis, 0.0)


def _switch_tax(gain, sold, r_keep, r_new, o, holding_since=None):
    """Tax-adjusted hurdle for selling part of a holding to fund the candidate. Both paths are
    liquidated at the horizon, so keeping also pays its deferred tax then (no false penalty on
    switching). Per unit of current value: keep = after-tax end of (1, basis 1-g, r_keep); switch =
    after-tax end of (s, basis s + realized loss, r) with s = 1 - t*max(g, 0). r* solves switch = keep."""
    if gain is None:
        return {'status': 'not_assessed', 'reason': 'no cost basis for this holding: tax effect not computed'}
    t, years = number(o['tax_rate'], 'tax_rate', 0, 1), number(o['horizon_years'], 'horizon_years', 0.5, 50)
    s = 1 - t * max(gain, 0.0)
    basis_switch = s + max(-gain, 0.0)        # a realized loss is carried forward against the candidate's later gain
    out = {'status': 'assessed', 'tax_rate': t, 'tax_rate_source': o.get('tax_rate_source', TAX_SOURCE),
           'unrealized_gain_fraction': gain, 'immediate_tax_share_of_portfolio': t * max(gain, 0.0) * sold,
           'horizon_years': years, 'pre_tax_hurdle': r_keep,
           'caveats': ['average-cost basis from the broker; lot identification may differ',
                       'gain measured in the position currency; Israeli inflation/FX adjustment not modelled',
                       'flat rate as stated: holding period does not change it in this model'
                       + (f' (held since {holding_since})' if holding_since else '')]}
    if gain < 0:
        out['caveats'].append('realized loss: assumed carried forward against the candidate\'s later gain; '
                              'an earlier offset against other realized gains is a further benefit not counted')
    if r_keep is None:
        out.update(after_tax_hurdle=None, note='holding has no base IRR: hurdle cannot be computed')
        return out
    keep = _after_tax_end(1.0, 1 - gain, r_keep, years, t)
    lo, hi = -0.99, 5.0
    if _after_tax_end(s, basis_switch, hi, years, t) < keep:
        hurdle = None
    else:
        for _ in range(200):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if _after_tax_end(s, basis_switch, mid, years, t) < keep else (lo, mid)
        hurdle = (lo + hi) / 2
    out.update(after_tax_hurdle=hurdle, tax_drag=hurdle - r_keep if hurdle is not None else None,
               candidate_clears_after_tax=(r_new >= hurdle) if r_new is not None and hurdle is not None else None,
               formula='solve s(1+r)^H - t*max(s(1+r)^H - b, 0) = (1+r_keep)^H - t*max((1+r_keep)^H - (1-g), 0); '
                       's = 1 - t*max(g,0), b = s + max(-g,0), per unit of current value')
    return out


def _candidate(c, specs):
    cf = _casefile(c) or {}
    e, src = _exposure_map(c, c.get('symbol') or 'candidate')
    imp = {}
    for n, spec in specs.items():
        sens = (c.get('scenario_sensitivity') or {}).get(n)
        imp[n] = number(sens, f'candidate.scenario_sensitivity.{n}', -1, 10) if sens is not None else _impact(e, spec)[0]
    return {**cf, 'symbol': c.get('symbol'), 'e': e, 'src': src, 'impacts': imp, 'diversified': bool(c.get('diversified'))}


def _metrics(state, cap):
    ws = {k: v['weight'] for k, v in state.items() if v['weight']}
    m = _concentration(ws, {k: v['diversified'] for k, v in state.items()}, cap)
    tags, scen = {}, {}
    for k, v in state.items():
        for t, w in v['e'].items():
            tags[t] = tags.get(t, 0.0) + v['weight'] * w
        for n, x in v['impacts'].items():
            if x is not None:
                scen[n] = scen.get(n, 0.0) + v['weight'] * x
    m.update(tags=tags, scenarios=scen)
    return m


def _risk_change(before, after, cap):
    tags = []
    for t in sorted(set(before['tags']) | set(after['tags'])):
        b, a = before['tags'].get(t, 0.0), after['tags'].get(t, 0.0)
        if abs(a - b) > 1e-12:
            tags.append({'tag': t, 'before': b, 'after': a, 'change': a - b,
                         'above_single_name_cap_after': cap is not None and a > cap,
                         'crosses_single_name_cap': cap is not None and b <= cap < a})
    pick = lambda m: {'top1': m['top1'], 'top5_weight': m['top5_weight'], 'hhi': m['hhi'], 'effective_n': m['effective_n'],
                      'over_single_name_cap': [x['holding'] for x in m['over_single_name_cap']]}
    return {'before': pick(before), 'after': pick(after),
            'effective_n_change': after['effective_n'] - before['effective_n'] if after['effective_n'] and before['effective_n'] else None,
            'tag_changes': sorted(tags, key=lambda x: -abs(x['change'])),
            'scenario_changes': {n: {'before': before['scenarios'].get(n, 0.0), 'after': v, 'change': v - before['scenarios'].get(n, 0.0)}
                                 for n, v in after['scenarios'].items()}}


def _pp(x):
    return f'{x * 100:+.2f} pp/yr' if x is not None else 'n/a'


def compare(candidate, book_analysis, alternatives=None, options=None):
    """Reasoned options for a candidate against current holdings, a broad index and cash.

    candidate = casefile summary {'symbol','base_irr','bear_irr','downside_to_bear' or 'bear_value'+'price',
    'risk_level','exposures','exposure_weights'?,'quality','verdict'}; alternatives = {'index': {'symbol',
    'expected_return','basis','exposures'?,'downside'?}, 'cash': {'yield'}, 'holdings': {symbol: summary}}.
    Every option is simulated at an illustrative trial weight (default 3%) to show its concentration,
    overlap, scenario and tax effect. No option is selected and none is executable."""
    o = {**COMPARE_DEFAULTS, **(options or {})}
    tw = number(o['trial_weight'], 'trial_weight', 1e-6, 1)
    A, alt = book_analysis, alternatives or {}
    cap, specs = A['params']['single_name_cap'], A['params']['scenarios']
    H = A['holdings']
    cash_w = A['weights'].get('cash')
    cash_y = _num((alt.get('cash') or {}).get('yield'))
    idx = alt.get('index') or {}
    idx_r = _num(idx.get('expected_return'))
    if not (candidate or {}).get('symbol'):
        raise ValueError('candidate needs a symbol')
    c = _candidate(candidate, specs)
    held_key = next((k for k, h in H.items() if c['symbol'] == k or c['symbol'] in h['symbols']), None)
    ckey = held_key or c['symbol']
    state = {k: {'weight': h['weight'], 'diversified': h['diversified'], 'e': h['exposure_weights'], 'impacts': h['scenario_impacts']}
             for k, h in H.items()}
    base = _metrics(state, cap)
    port_tags = {t for t, v in base['tags'].items() if abs(v) > 1e-12}

    def summary(k):
        """A holding's research view: alternatives['holdings'] first, then the casefile used in analyze."""
        h, hs, p = H[k], alt.get('holdings') or {}, H[k]['pricing'] or {}
        s = _casefile(next((hs[x] for x in [k] + h['symbols'] if hs.get(x)), None), h['price_now']) or {}
        first = lambda name, fallback: s.get(name) if s.get(name) is not None else fallback
        return {'base_irr': first('base_irr', p.get('base_irr')), 'downside_to_bear': first('downside_to_bear', p.get('downside_to_bear')),
                'risk_level': first('risk_level', h.get('risk_level')), 'quality': first('quality', h.get('business_quality')),
                'verdict': first('verdict', p.get('verdict'))}
    review = {r['holding']: [t['code'] for t in r['triggers']] for r in A.get('review_needed', [])}

    def simulate(deltas, extra=None):
        st = {k: dict(v) for k, v in state.items()}
        for k, v in (extra or {}).items():
            st.setdefault(k, {**v, 'weight': 0.0})
        for k, d in deltas.items():
            st[k]['weight'] += d
        return _risk_change(base, _metrics(st, cap), cap)
    cand_state = {ckey: {'diversified': c['diversified'], 'e': c['e'], 'impacts': c['impacts']}} if not held_key else {}
    new_tags = [t for t in c['e'] if t not in port_tags]
    shared = [t for t in c['e'] if t in port_tags]
    tot = sum(abs(w) for w in c['e'].values())
    diversification = {'candidate_tags': c['e'], 'tag_sources': c['src'], 'new_tags': new_tags, 'shared_tags': shared,
                       'new_exposure_share': sum(abs(c['e'][t]) for t in new_tags) / tot if tot else None,
                       'reading': ('adds mostly new sources of return' if tot and sum(abs(c['e'][t]) for t in new_tags) / tot > 0.5
                                   else 'mostly more of exposures already held' if tot else 'candidate exposures not stated: overlap unknown')}
    support = c.get('research_support')
    c_reasons_against = []
    if support not in ('supports_price',):
        c_reasons_against.append(f"research verdict {c.get('verdict') or 'missing'} ({support}): the research does not support paying today's price")
    options_out = []

    def opt(code, label, target, deltas, er, er_formula, bear, tax=None, extra=None, sep=None, why=None, why_not=None, funding='cash', caveats=None):
        risk = simulate(deltas, extra) if deltas else _risk_change(base, base, cap)
        reasons_for, reasons_against = list(why or []), list(why_not or [])
        for tc in risk['tag_changes']:
            if tc['change'] > 0 and tc['above_single_name_cap_after']:
                reasons_against.append(f"raises {tc['tag']} exposure by {tc['change'] * 100:+.1f} pp to {tc['after']:.1%}, above the "
                                       f"{cap:.0%} single-name cap: more of one economic bet")
        for k, d in deltas.items():
            if d > 0 and k in risk['after']['over_single_name_cap']:
                w0 = H[k]['weight'] if k in H else 0.0
                reasons_against.append(f'{k} weight {w0:.1%} -> {w0 + d:.1%}, above the {cap:.0%} single-name cap')
        if risk['effective_n_change'] is not None and abs(risk['effective_n_change']) > 1e-9:
            (reasons_for if risk['effective_n_change'] > 0 else reasons_against).append(
                f"effective number of holdings {risk['before']['effective_n']:.1f} -> {risk['after']['effective_n']:.1f}")
        if funding == 'cash' and deltas and cash_w is not None and cash_w < tw:
            reasons_against.append(f'cash {cash_w:.1%} is below the {tw:.0%} trial weight: funding would need a sale or margin')
        if tax and tax.get('status') == 'assessed' and tax.get('after_tax_hurdle') is not None:
            text = (f"after {tax['tax_rate']:.0%} tax on the realized gain ({tax['immediate_tax_share_of_portfolio']:.2%} of the portfolio now) "
                    f"the replacement must return {tax['after_tax_hurdle']:.1%}/yr vs {tax['pre_tax_hurdle']:.1%} pre-tax over {tax['horizon_years']:g} years"
                    if tax['unrealized_gain_fraction'] > 0 else f"no gain realized: the hurdle stays at {tax['after_tax_hurdle']:.1%}/yr")
            (reasons_for if tax.get('candidate_clears_after_tax') else reasons_against).append(text)
        options_out.append({'option': code, 'label': label, 'target': target, 'executable': False,
                            'illustrative_weight_change': deltas,
                            'expected_return': {'portfolio_delta': er, 'formula': er_formula, 'reading': _pp(er)},
                            'bear_case_portfolio_effect': bear, 'risk_change': risk, 'tax': tax or {'status': 'no_realization'},
                            'separation': sep or {}, 'reasons_for': reasons_for, 'reasons_against': reasons_against,
                            'caveats': list(caveats or [])})

    cr, cd = c.get('base_irr'), c.get('downside_to_bear')
    sep_c = {'business_quality': c.get('quality'), 'pricing': {'verdict': c.get('verdict'), 'research_support': support, 'base_irr': cr,
             'downside_to_bear': cd, 'risk_level': c.get('risk_level')}, 'portfolio_fit': diversification['reading']}
    vs = lambda a, b: a - b if a is not None and b is not None else None
    # 1. the candidate itself (new, or added to if already held)
    why = [f'base IRR {cr:.1%} vs cash yield {cash_y:.1%}'] if cr is not None and cash_y is not None else []
    if new_tags:
        why.append(f"adds exposures not yet held: {', '.join(new_tags)}")
    opt('add_to_existing' if held_key else 'add_new', f'add {c["symbol"]} at an illustrative {tw:.0%} weight from cash', ckey,
        {ckey: tw}, tw * vs(cr, cash_y) if vs(cr, cash_y) is not None else None, 'trial_weight x (candidate base IRR - cash yield)',
        tw * cd if cd is not None else None, extra=cand_state, sep=sep_c, why=why, why_not=c_reasons_against
        + ([f"no exposure tags stated: overlap with holdings unknown"] if not c['e'] else []))
    # 2. trim the candidate when already held
    if held_key and H[held_key]['weight'] > 0:
        hk = H[held_key]
        sold = min(tw, abs(hk['weight']))
        tax = _switch_tax(hk['tax']['unrealized_gain_fraction'], sold, cr, cash_y, o, hk['tax']['holding_since'])
        opt('trim', f'trim {held_key} by an illustrative {sold:.1%} into cash', held_key, {held_key: -sold},
            -sold * vs(cr, cash_y) if vs(cr, cash_y) is not None else None, '-sold weight x (base IRR - cash yield)',
            -sold * cd if cd is not None else None, tax=tax, sep=sep_c, funding='none')
    # 3. add to other holdings / replace other holdings
    others = [k for k in H if k != held_key and not H[k]['diversified'] and H[k]['weight'] > 0]   # shorts are not funding sources
    sums = {k: summary(k) for k in others}
    adds = sorted([k for k in others if sums[k]['base_irr'] is not None], key=lambda k: -sums[k]['base_irr'])[:o['max_targets']]
    for k in adds:
        s = sums[k]
        stale = [x for x in review.get(k, []) if x in STALE_IRR]
        opt('add_to_existing', f'add to {k} at an illustrative {tw:.0%} weight from cash', k, {k: tw},
            tw * vs(s['base_irr'], cash_y) if vs(s['base_irr'], cash_y) is not None else None, 'trial_weight x (holding base IRR - cash yield)',
            tw * s['downside_to_bear'] if s['downside_to_bear'] is not None else None,
            sep={'business_quality': s['quality'], 'pricing': {x: s[x] for x in ('verdict', 'base_irr', 'downside_to_bear', 'risk_level')},
                 'portfolio_fit': 'same exposures as already held'},
            why=([f"base IRR {s['base_irr']:.1%} vs cash yield {cash_y:.1%}"] if cash_y is not None else [])
            + ([f"base IRR exceeds the candidate by {s['base_irr'] - cr:.1%}/yr"] if cr is not None and s['base_irr'] > cr else []),
            why_not=([f"base IRR is below the candidate by {cr - s['base_irr']:.1%}/yr"] if cr is not None and s['base_irr'] < cr else [])
            + ([f"open review triggers ({', '.join(stale)}): its IRR may be stale"] if stale else []))
    overlap_first = sorted(others, key=lambda k: (-sum(1 for t in c['e'] if H[k]['exposure_weights'].get(t)),
                                                  sums[k]['base_irr'] if sums[k]['base_irr'] is not None else 9))[:o['max_targets']]
    for k in overlap_first:
        s, hk = sums[k], H[k]
        sold = min(tw, abs(hk['weight']))
        tax = _switch_tax(hk['tax']['unrealized_gain_fraction'], sold, s['base_irr'], cr, o, hk['tax']['holding_since'])
        er = sold * vs(cr, s['base_irr']) if vs(cr, s['base_irr']) is not None else None
        bear = sold * (cd - s['downside_to_bear']) if cd is not None and s['downside_to_bear'] is not None else None
        overlap_tags = [t for t in c['e'] if hk['exposure_weights'].get(t)]
        opt('replace', f'replace an illustrative {sold:.1%} of {k} with {c["symbol"]}', k, {k: -sold, ckey: sold},
            er, 'sold weight x (candidate base IRR - holding base IRR), before tax', bear, tax=tax, extra=cand_state,
            sep={'business_quality': {'candidate': c.get('quality'), 'holding': s['quality']},
                 'pricing': {'candidate_base_irr': cr, 'holding_base_irr': s['base_irr'], 'candidate_verdict': c.get('verdict'), 'holding_verdict': s['verdict']},
                 'portfolio_fit': f"swaps like for like on {', '.join(overlap_tags)}" if overlap_tags else 'changes the exposure mix'},
            why=[f'candidate base IRR exceeds {k} by {er / sold:.1%}/yr before tax'] if er and er > 0 else [],
            why_not=c_reasons_against + ([f'{k} base IRR unknown: return effect not computed'] if s['base_irr'] is None else [])
            + ([f'candidate base IRR is below {k} by {-er / sold:.1%}/yr'] if er is not None and er < 0 else []), funding='sale')
    # 4. broad index, cash, nothing
    ikey = (next((k for k, h in H.items() if idx.get('symbol') and (idx['symbol'] == k or idx['symbol'] in h['symbols'])), None)
            or f"index:{idx.get('symbol') or 'broad'}")
    i_e, _ = _exposure_map({'exposures': idx.get('exposures') or {}}, 'index')
    i_sens = idx.get('scenario_sensitivity') or {}
    i_imp = {n: _num(i_sens[n]) if i_sens.get(n) is not None else (_impact(i_e, sp)[0] if i_e else None) for n, sp in specs.items()}
    idx_down = _num(idx.get('downside'))
    excess = vs(cr, idx_r)
    opt('index', f"put an illustrative {tw:.0%} in a broad index ({idx.get('symbol') or 'unspecified'})", ikey, {ikey: tw},
        tw * vs(idx_r, cash_y) if vs(idx_r, cash_y) is not None else None, 'trial_weight x (index expected return - cash yield)',
        tw * idx_down if idx_down is not None else None,
        extra={} if ikey in H else {ikey: {'diversified': True, 'e': i_e, 'impacts': i_imp}},
        sep={'business_quality': 'diversified basket', 'pricing': {'expected_return': idx_r, 'basis': idx.get('basis')}, 'portfolio_fit': 'diversified sleeve'},
        why=['adds no single-issuer concentration']
        + ([f'index expected return exceeds the candidate base IRR by {-excess:.1%}/yr'] if excess is not None and excess < 0 else [])
        + (['the research does not support the candidate at its price'] if c_reasons_against else []),
        why_not=[f"candidate base IRR exceeds the index expected return by {excess:.1%}/yr (index basis: {idx.get('basis') or 'not stated'})"]
        if excess is not None and excess > 0 else ([] if idx_r is not None else ['index expected return not supplied']),
        caveats=[] if idx.get('exposures') else ['index look-through exposures not supplied: its share of existing tags is not measured'])
    opt('hold_cash', f'keep the illustrative {tw:.0%} in cash', None, {}, 0.0, 'baseline (cash stays at its yield)', 0.0,
        sep={'pricing': {'cash_yield': cash_y}, 'portfolio_fit': 'keeps optionality'},
        why=['no new risk; keeps optionality for a better price'],
        why_not=[f"forgoes {_pp(tw * vs(cr, cash_y))} vs the candidate base case" if vs(cr, cash_y) is not None
                 else 'forgone return unknown (cash yield or candidate IRR missing)'])
    unresolved = ([f"{x['holding']} above the single-name cap" for x in base['over_single_name_cap']]
                  + [f"{c_['defining_tag']} cluster ({', '.join(c_['holdings'])}) above the single-name cap"
                     for c_ in A['overlaps']['clusters'] if c_['above_single_name_cap']]
                  + ([f"{len(A.get('review_needed', []))} holding(s) with open review triggers"] if A.get('review_needed') else []))
    opt('do_nothing', f'do nothing / avoid {c["symbol"]}', None, {}, 0.0, 'baseline', 0.0, sep={'portfolio_fit': 'unchanged'},
        why=['the research does not support the candidate at its price'] if c_reasons_against else [],
        why_not=[f'leaves unresolved: {"; ".join(unresolved)}'] if unresolved else [])
    hold_cmp = [{'holding': k, 'base_irr': sums[k]['base_irr'], 'candidate_minus_holding': vs(cr, sums[k]['base_irr']),
                 'downside_to_bear': sums[k]['downside_to_bear'], 'risk_level': sums[k]['risk_level'],
                 'shared_tags': [t for t in c['e'] if H[k]['exposure_weights'].get(t)]} for k in others]
    ratio = lambda r, d: (r - cash_y) / abs(d) if r is not None and d and d < 0 and cash_y is not None else None
    comparisons = {'candidate_vs_index': {'candidate_base_irr': cr, 'index_expected_return': idx_r, 'excess': excess,
                                          'index_basis': idx.get('basis')},
                   'candidate_vs_cash': {'cash_yield': cash_y, 'excess': vs(cr, cash_y)},
                   'candidate_vs_holdings': sorted(hold_cmp, key=lambda x: -(x['base_irr'] if x['base_irr'] is not None else -9)),
                   'excess_return_per_unit_bear_loss': {'candidate': ratio(cr, cd), 'index': ratio(idx_r, idx_down),
                                                        'formula': '(expected return - cash yield) / |downside to bear|'},
                   'risk_level': {'candidate': c.get('risk_level'), 'rank': RISK_RANK.get(str(c.get('risk_level')).lower())}}
    keep = ('symbol', 'verdict', 'research_support', 'quality', 'risk_level', 'base_irr', 'bear_irr', 'downside_to_bear', 'downside_basis')
    out = {'candidate': {k: c.get(k) for k in keep} | {'held': bool(held_key), 'holding_key': held_key},
           'options': options_out, 'diversification': diversification, 'comparisons': comparisons,
           'assumptions': {'trial_weight': tw, 'trial_weight_note': 'illustrative size to measure effects, not a sizing proposal',
                           'tax_rate': o['tax_rate'], 'tax_rate_source': o.get('tax_rate_source', TAX_SOURCE),
                           'horizon_years': o['horizon_years'], 'cash_yield': cash_y, 'index_expected_return': idx_r,
                           'single_name_cap': cap, 'max_targets': o['max_targets']},
           'notes': ['Options only: none is selected, none is executable, no order is created. Listing order is not a ranking.',
                     'Business quality, pricing, portfolio fit and tax are separate fields; they are not combined into a score.',
                     'Expected-return deltas are base-case IRRs at the analysis price; holdings with open review triggers may carry stale IRRs.',
                     'Tax is shown only where cost basis exists.'
                     + ('' if any(x['tax'].get('status') == 'assessed' for x in options_out) else ' No option here had cost basis data.')]}
    return _no_orders(_clean(out))


# ---------------------------------------------------------------- output guards
def _clean(x):
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, set):
        return sorted(_clean(v) for v in x)
    return x


def _no_orders(x, path='output', data_keys=False):
    """Read-only guarantee: refuse to return anything shaped like an order. Keys of data-keyed maps
    (symbols, tags, sectors, scenario names) are data, not fields, and are not checked."""
    if isinstance(x, dict):
        for k, v in x.items():
            if not data_keys and _k(k) in ORDER_KEYS:
                raise AssertionError(f'{path}.{k}: order-like field in a read-only portfolio output')
            _no_orders(v, f'{path}.{k}', not data_keys and k in DATA_MAPS)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            _no_orders(v, f'{path}[{i}]')
    return x
