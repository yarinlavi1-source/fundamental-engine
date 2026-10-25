"""Analyst sheet: the owner's six questions about one company, answered the way an analyst would, from the casefile.

1. Is it really a good business?            (quality: returns, margins, cash, balance sheet, red flags)
2. Is the market pricing it below its worth? (the price against the base case, and what the price needs to be true)
3. What is the geopolitical exposure?        (where the customers and the assets are; the analyst's scenario)
4. Is there asymmetry?                       (what can be gained in the good case against what can be lost in the bad one)
5. Does it sit on a bottleneck?              (a resource others cannot do without; what confirming it is worth)
6. Will it matter more in the future?        (which parts grow, contracted revenue, R&D, evidence of demand)

The sheet reads and weighs; it does not gate. Numbers come from the stages that computed them (valuation, risk, decision,
business map); judgement comes from the analyst (inputs['analyst_sheet'], evidenced) or is marked open with what would
close it. A computed number never stands in for an unread question: a sheet with open questions is a research agenda,
not a verdict, and every value is a draft until a reviewer signs it.

inputs['analyst_sheet'] (all optional): {'<question>': {'reading_he': text, 'evidence_ids': [...]}}; for geopolitics also
'scenario_he' and 'value_impact' (fraction of the base value lost if the scenario happens); for bottleneck 'confirmed'
(true / false) with evidence. Readings without evidence ids are kept but labelled as opinion.

inputs['analyst_sheet']['scenario_events'] (optional): {'bear'|'base'|'bull': [{'event_he': what has to happen, in plain
Hebrew, 'evidence_ids': [...]}]}: the events behind each scenario price, shown by scenario_reasons as the scenario's
condition ('אם …'). They are validated here against the FactBook (sources and qualitative ids): an event without evidence
ids, or with an id the FactBook does not hold, is kept and marked a hypothesis. They change no number. evidence_ids is a list
of ids or one id as text (evidence_id_list, shared with scenario_reasons). The sheet carries 'scenario_events' only when the
input is supplied; whatever cannot be read (an unknown scenario key, a scenario that is not a list, an event that is not an
object or has no event_he, evidence_ids of another type) is listed in 'scenario_events_rejected' [{scenario, index, code,
reason}] and never raises: a malformed optional input cannot cost the rest of the sheet.
"""
from copy import deepcopy

QUESTIONS = (('quality', 'האם זה עסק טוב באמת?'),
             ('mispricing', 'האם השוק מתמחר אותה נמוך ממה שהיא שווה?'),
             ('geopolitics', 'מה החשיפה הגאופוליטית?'),
             ('asymmetry', 'האם יש אסימטריה: כמה אפשר להרוויח מול כמה אפשר להפסיד?'),
             ('bottleneck', 'האם היא יושבת על צוואר בקבוק?'),
             ('future', 'האם היא תהיה רלוונטית יותר בעתיד?'))
STATUS_HE = {'evidenced': 'נבדק עם ראיות', 'numbers_only': 'יש מספרים, חסרים ראיות ושיקול', 'open': 'פתוח'}
# regions whose politics can move a business on their own (export controls, sanctions, conflict); matched on the member
# names the filings use ('CN', 'Greater China', 'Taiwan', 'Country Of Domicile' resolved to the company's country)
SENSITIVE = {'סין': ('CN', 'CHINA', 'GREATER CHINA', 'MAINLAND CHINA', 'HONG KONG', 'HK', 'PRC'),
             'טייוואן': ('TW', 'TAIWAN'), 'רוסיה': ('RU', 'RUSSIA'), 'ישראל': ('IL', 'ISRAEL')}
COUNTRY_HE = {'TAIWAN': 'טייוואן', 'CHINA': 'סין', 'ISRAEL': 'ישראל', 'RUSSIA': 'רוסיה'}
TOPICS = {'bottleneck': ('moat', 'bottleneck', 'constraint', 'pricing_power', 'switching_costs', 'market_share', 'competition'),
          'future': ('tailwind', 'demand', 'technology_ai', 'frontier', 'adoption', 'secular'),
          'geopolitics': ('geopolitics', 'export_controls', 'tariffs', 'sanctions', 'regulation'),
          'quality': ('management', 'governance', 'accounting')}
LEVEL_HE = {'strong': 'חזק', 'adequate': 'סביר', 'weak': 'חלש', 'unknown': 'לא ידוע', None: 'לא ידוע'}


def _p(x, d=0):
    return 'לא ידוע' if x is None else f'{x * 100:.{d}f}%'


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _region(name, country):
    u = (name or '').upper().replace('_', ' ')
    if 'DOMICILE' in u and country:
        u = country.upper()
    for he, keys in SENSITIVE.items():
        if u in keys or any(k in u.split() for k in keys if len(k) > 2) or any(u.startswith(k + ' ') for k in keys if len(k) > 2):
            return he
    return None


def _exposure(shares, country):
    out = {}
    for name, sh in (shares or {}).items():
        he = _region(name, country)
        if he and sh and sh > 0:
            out[he] = out.get(he, 0.) + sh
    return out


def _evidence(fb, topics):
    return [{'id': q.get('id'), 'topic': q.get('topic'), 'claim': q.get('claim') or q.get('text') or q.get('title'),
             'source_ids': q.get('source_ids')} for q in (fb.get('qualitative') or []) if q.get('topic') in topics]


def _known(fb):
    return set(fb.get('sources') or {}) | {q.get('id') for q in fb.get('qualitative') or []}


def _analyst(inputs, key, fb):
    a = ((inputs or {}).get('analyst_sheet') or {}).get(key) or {}
    if not a:
        return None
    known = _known(fb)
    ids = list(a.get('evidence_ids') or [])
    return dict(a, evidence_ids=ids, unknown_ids=[i for i in ids if i not in known], opinion=not ids)


SCENARIO_KEYS = ('bear', 'base', 'bull')
EVENT_KEYS = frozenset({'event_he', 'evidence_ids', 'hypothesis'})


def evidence_id_list(v):
    """evidence_ids as supplied -> (ids, problem). Missing -> []; one id as text -> [id]; a list keeps its non-blank text
    items (problem names any other item, which is dropped); any other type -> (None, problem): rejected, never iterated."""
    if v is None:
        return [], None
    if isinstance(v, str):
        return ([v] if v.strip() else []), None
    if isinstance(v, (list, tuple)):
        ids = [i for i in v if isinstance(i, str) and i.strip()]
        bad = [i for i in v if not isinstance(i, str)]
        return ids, (f'{len(bad)} evidence id(s) that are not text were dropped' if bad else None)
    return None, f'evidence_ids is {type(v).__name__}, not a list of ids or one id as text'


def _event(e, known):
    """One supplied event -> (row or None, (code, reason) or None)."""
    if not isinstance(e, dict):
        return None, ('event_not_object', 'the event is not an object {event_he, evidence_ids}')
    extra = sorted(str(k) for k in set(e) - EVENT_KEYS)
    if extra:
        return None, ('unknown_keys', f'unknown keys {extra} (event_he, evidence_ids, hypothesis)')
    text = e.get('event_he')
    if not isinstance(text, str) or not text.strip():
        return None, ('no_event_he', 'the event has no event_he text')
    ids, problem = evidence_id_list(e.get('evidence_ids'))
    if ids is None:
        return None, ('bad_evidence_ids', problem)
    unknown = [i for i in ids if i not in known]
    row = {'event_he': text.strip(), 'evidence_ids': ids, 'unknown_ids': unknown, 'hypothesis': not ids or bool(unknown)}
    return row, (('evidence_ids_items_dropped', problem) if problem else None)


def scenario_events(inputs, fb):
    """inputs.analyst_sheet.scenario_events validated against the FactBook -> ({scenario: [{event_he, evidence_ids,
    unknown_ids, hypothesis}]}, rejected [{scenario, index, code, reason}]), or (None, []) when not supplied. Never raises
    on a malformed input: what cannot be read is listed, not dropped silently."""
    raw = ((inputs or {}).get('analyst_sheet') or {}).get('scenario_events')
    if raw is None:
        return None, []
    if not isinstance(raw, dict):
        return {}, [{'scenario': None, 'index': None, 'code': 'not_object',
                     'reason': 'scenario_events is not an object {bear|base|bull: [events]}'}]
    known, out = _known(fb), {}
    rejected = [{'scenario': str(k), 'index': None, 'code': 'unknown_scenario', 'reason': 'not a scenario name (bear, base or bull)'}
                for k in sorted(str(k) for k in raw if k not in SCENARIO_KEYS)]
    for s in SCENARIO_KEYS:
        items = raw.get(s)
        if items is None:
            continue
        if not isinstance(items, list):
            rejected.append({'scenario': s, 'index': None, 'code': 'not_list',
                             'reason': 'the events of a scenario must be a list of {event_he, evidence_ids}'})
            continue
        rows = []
        for i, e in enumerate(items):
            try:
                row, why = _event(e, known)
            except (TypeError, ValueError, AttributeError) as exc:
                row, why = None, ('unreadable', f'the event could not be read ({type(exc).__name__})')
            if why:
                rejected.append({'scenario': s, 'index': i, 'code': why[0], 'reason': why[1]})
            if row:
                rows.append(row)
        if rows:
            out[s] = rows
    return out, rejected


def conditional_values(fb, an, card, qa, fc_inputs=None):
    """Value today (bear/base/bull) if the moat is confirmed (an analyst rates it strong): what the bottleneck question is
    worth in the model. None when the forecast cannot be rebuilt or the moat is already an analyst's strong rating."""
    from . import forecast
    from .drivers import build_case
    from .valuation import value_company
    dims = (card or {}).get('dimensions') or {}
    if not card or ((dims.get('moat') or {}).get('source') == 'analyst' and (dims.get('moat') or {}).get('assessment') == 'strong'):
        return None
    c2 = deepcopy(card)
    c2.setdefault('dimensions', {})['moat'] = {'assessment': 'strong', 'source': 'analyst',
                                              'note': 'conditional: the bottleneck / competitive advantage is confirmed'}
    try:
        fc2 = forecast.build(fb, an, c2, qa, fc_inputs)
        if fc2.get('status') != 'built' or not fc2.get('spec'):
            return None
        case = fc2['spec'] if fc2['spec'].get('valuation_version') == 1 else build_case(fc2['spec'])
        today = value_company(case)['annual_values'][0]
    except (ValueError, KeyError, ZeroDivisionError, TypeError):
        return None
    return {'moat_confirmed': {s: today.get(s) for s in ('bear', 'base', 'bull')},
            'meaning': 'conditional value if an analyst confirms a strong moat with evidence; not a forecast'}


def build(fb, cf, inputs=None, conditional=None):
    """-> {'sheet_version', 'questions': [...], 'thesis_he', 'open', 'meaning'} (see the module docstring)."""
    company = fb.get('company') or {}
    country = company.get('country')
    ccy = company.get('currency') or ''
    dc, rk, val, card, an = (cf.get(k) or {} for k in ('dc', 'rk', 'val', 'card', 'an'))
    fc = cf.get('fc') or {}
    vv, er = dc.get('valuation_view') or {}, dc.get('expected_return') or {}
    bm = fb.get('business_map') or {}
    price, base, bear, bull = (_num(vv.get(k)) for k in ('price', 'base', 'bear', 'bull'))
    warn = {w.get('code') for w in fc.get('warnings') or []}
    qs = {}

    # 1. quality
    a = dc.get('assessments') or {}
    lv = {k: (a.get(k) or {}).get('level') for k in ('business_quality', 'growth_quality', 'financial_strength',
                                                     'management_capital_allocation')}
    reasons = [r for r in ((a.get('business_quality') or {}).get('reasons') or []) if ': strong' in r or ': weak' in r][:4]
    flags = [d.get('description') or d.get('id') for d in (rk.get('disqualifying') or []) if isinstance(d, dict)]
    ev = _evidence(fb, TOPICS['quality'])
    an_q = _analyst(inputs, 'quality', fb)
    bq = lv['business_quality']
    reading = (f"איכות העסק לפי המספרים: {LEVEL_HE.get(bq, bq)}; איכות הצמיחה {LEVEL_HE.get(lv['growth_quality'])}; "
               f"חוסן פיננסי {LEVEL_HE.get(lv['financial_strength'])}; הקצאת הון {LEVEL_HE.get(lv['management_capital_allocation'])}.")
    if flags:
        reading += ' דגלים שיכולים לפסול: ' + '; '.join(str(f) for f in flags[:2]) + '.'
    if (a.get('business_quality') or {}).get('reasons') and any('competitive_position: unknown' in r for r in a['business_quality']['reasons']):
        reading += ' היתרון התחרותי עצמו עוד לא הוכח בראיות, ולכן "חזק" כאן הוא תוצאה ולא הסבר.'
    if an_q:
        reading = an_q.get('reading_he') or reading
    qs['quality'] = {'status': 'evidenced' if (an_q and not an_q['opinion']) or ev else ('numbers_only' if bq else 'open'),
                     'reading_he': reading, 'numbers': {'levels': lv, 'reasons': reasons, 'disqualifying': flags},
                     'evidence': ev, 'analyst': an_q,
                     'to_close_he': 'לקרוא מה בונה את היתרון (מותג, עלות, רשת, מעבר יקר בין ספקים, רגולציה) ולבדוק שהוא מחזיק גם בשנה רעה.'}

    # 2. mispricing
    wm = {w.get('lever'): w for w in (val.get('what_must_be_true') or []) if isinstance(w, dict)}
    g = wm.get('growth') or {}
    gap = (base / price - 1) if base and price else None
    if gap is None:
        reading = 'אין עדיין שווי בסיס מחושב מול מחיר, ולכן אי אפשר לומר אם המחיר נמוך.'
    else:
        reading = (f"המחיר {price:,.2f} {ccy} מול שווי בסיס {base:,.2f} {ccy} (טיוטה): "
                   + (f'שווי הבסיס גבוה ב־{_p(gap)} מהמחיר. ' if gap > 0 else f'שווי הבסיס נמוך ב־{_p(-gap)} מהמחיר. '))
        if g.get('status') == 'solved' and _num(g.get('required')) is not None:
            reading += (f"כדי שהמחיר יהיה הוגן, המכירות צריכות לצמוח בממוצע {_p(g['required'], 1)} בשנה בחמש השנים הקרובות, "
                        f"לעומת {_p(g.get('anchor'), 1)} בתרחיש הבסיסי.")
        elif g:
            reading += 'שום קצב צמיחה סביר לבדו לא מסביר את המחיר: השוק מניח גם רווחיות או משך צמיחה אחרים.'
        bq_level = ((dc.get('assessments') or {}).get('business_quality') or {}).get('level')
        if gap <= -.3 and bq_level == 'strong':
            reading += (' פער כזה מול עסק חזק אומר בדרך כלל שהשוק מניח יתרון שמחזיק הרבה יותר זמן, או צמיחה ארוכה יותר, ממה שהמודל '
                        'מניח: זו השאלה המרכזית לבדוק, לא מסקנה שהמניה יקרה.')
        elif gap >= .3:
            reading += (' פער כזה יכול להיות הזדמנות או מלכודת: צריך למצוא ממה השוק חושש ולבדוק בראיות אם זה זמני או מבני.')
        if 'no_consensus' in warn:
            reading += ' אין כאן תחזיות אנליסטים, ולכן התרחיש הבסיסי נשען על העבר בלבד ונוטה להיות שמרני לחברות בצמיחה.'
        if card and ((card.get('dimensions') or {}).get('moat') or {}).get('source') != 'analyst':
            reading += ' המודל מניח שהיתרון התחרותי נשחק עם הזמן כל עוד לא הוכח אחרת (ראו שאלת צוואר הבקבוק).'
    qs['mispricing'] = {'status': 'numbers_only' if gap is not None else 'open', 'reading_he': reading,
                        'numbers': {'price': price, 'base': base, 'gap_to_base': gap, 'base_irr': er.get('base_irr'),
                                    'index_expected_return': (er.get('index_expected_return') or {}).get('value')
                                    if isinstance(er.get('index_expected_return'), dict) else er.get('index_expected_return'),
                                    'price_needs_growth': g.get('required'), 'base_growth': g.get('anchor')},
                        'evidence': [], 'analyst': _analyst(inputs, 'mispricing', fb),
                        'to_close_he': 'להבין מה הפחד שהמחיר מגלם (למשל תחרות, רגולציה, מחזור) ולבדוק בראיות אם הוא מוגזם; להוסיף תחזיות אנליסטים כנקודת השוואה.'}

    # 3. geopolitics
    geo = ((bm.get('tables') or {}).get('geography') or {})
    shares, last = {}, None
    if geo.get('revenue'):
        last = (geo.get('reconciliation') or {}).get('year')
        tot = (geo.get('total_revenue') or {}).get(last) or sum((v or {}).get(last, 0) for v in geo['revenue'].values())
        shares = {m: (v or {}).get(last, 0) / tot for m, v in geo['revenue'].items() if tot}
    rev_exp = _exposure(shares, country)
    ab = bm.get('assets_by_geography') or {}
    asset_exp = _exposure(ab.get('shares'), country)
    if country and any('DOMICILE' in (k or '').upper() for k in (ab.get('shares') or {})) and country.upper() not in COUNTRY_HE:
        asset_exp.setdefault(f'מדינת הבית ({country})', sum(v for k, v in ab['shares'].items() if 'DOMICILE' in k.upper()))
    ev = _evidence(fb, TOPICS['geopolitics'])
    an_g = _analyst(inputs, 'geopolitics', fb)
    reg = next((r for r in rk.get('register') or [] if 'geopolit' in str(r.get('id', ''))), None)
    parts = []
    if rev_exp:
        parts.append('הכנסות מאזורים רגישים: ' + ', '.join(f'{k} {_p(v)}' for k, v in sorted(rev_exp.items(), key=lambda x: -x[1])))
    elif shares:
        parts.append('לפי הדוח אין הכנסות מהותיות מאזורים רגישים (סין, טייוואן, רוסיה, ישראל) בפילוח שפורסם')
    if asset_exp:
        parts.append('נכסים (מפעלים, ציוד) באזורים רגישים: ' + ', '.join(f'{k} {_p(v)}' for k, v in sorted(asset_exp.items(), key=lambda x: -x[1])))
    reading = ('; '.join(parts) + '.') if parts else 'הדוח לא מפרט הכנסות או נכסים לפי מדינה בצורה שאפשר לקרוא.'
    reading += ' החשיפה ללקוחות שונה מהחשיפה לייצור: מפעל באזור סכסוך יכול לעצור את כל העסק גם כשהלקוחות במקום אחר.'
    mc = cf.get('market_context') or {}
    mc_numbers, mc_evidenced = None, False
    if mc:                                     # market_context.py: the country-risk row and the evidenced exposure scenarios
        from .market_context import country_risk_he, exposure_lines_he
        lines = [x for x in [country_risk_he(mc)] + exposure_lines_he(cf) if x]
        if lines:
            reading += ' ' + ' '.join(lines)
        sc = {s.get('name'): s for s in rk.get('failure_scenarios') or []}
        mc_numbers = {'country_risk_add_on': (mc.get('country_risk') or {}).get('add_on'),
                      'exposures': [{'id': x['id'], 'share': x['share'], 'share_basis': x['share_basis'],
                                     'base_value_with_shock': (sc.get('exposure:' + x['id']) or {}).get('base_value_with_shock'),
                                     'base_effect': (sc.get('exposure:' + x['id']) or {}).get('base_effect'),
                                     'evidence_ids': x['evidence_ids']} for x in mc.get('exposures') or []]}
        mc_evidenced = any(e['base_effect'] is not None for e in mc_numbers['exposures'])
    scen_value = None
    if an_g:
        if an_g.get('reading_he'):
            reading = an_g['reading_he'] + ' ' + ('; '.join(parts) + '.' if parts else '')
        imp = _num(an_g.get('value_impact'))
        if imp is not None and base:
            scen_value = base * (1 - imp)
            reading += f" תרחיש האנליסט: {an_g.get('scenario_he') or 'לא תואר'} — שווי בסיס בתרחיש {scen_value:,.2f} {ccy} ({_p(-imp)})."
    status = 'evidenced' if (an_g and not an_g['opinion'] and an_g.get('scenario_he')) or mc_evidenced else ('numbers_only' if parts else 'open')
    qs['geopolitics'] = {'status': status, 'reading_he': reading,
                         'numbers': {'revenue_by_region_year': last, 'revenue_shares': shares, 'sensitive_revenue': rev_exp,
                                     'assets_as_of': ab.get('as_of'), 'asset_shares': ab.get('shares'),
                                     'sensitive_assets': asset_exp, 'scenario_value': scen_value,
                                     'register_status': reg and reg.get('status'),
                                     **({'market_context': mc_numbers} if mc_numbers is not None else {})},
                         'evidence': ev, 'analyst': an_g,
                         'to_close_he': 'לכתוב תרחיש מפורש (מה קורה, כמה הכנסות או ייצור נפגעים, לכמה זמן) ולתמחר אותו כאחוז מהשווי; '
                                        'לבדוק מגבלות יצוא, מכסים, תלות בספק יחיד ותוכניות פיזור ייצור.'}

    # 4. asymmetry
    up = (bull / price - 1) if bull and price else None
    down = max(0., 1 - bear / price) if bear is not None and price else None
    perm = (rk.get('downside') or {}).get('loss_to_permanent')
    if up is None or down is None:
        reading = 'אין עדיין תרחישים מחושבים מול המחיר.'
        st = 'open'
    else:
        ratio = up / down if down > 0 else None
        reading = (f'בתרחיש השורי {_p(up) if up >= 0 else "−" + _p(-up)} מהמחיר, בתרחיש הדובי {"−" + _p(down) if down > 0 else "בלי הפסד"}'
                   + (f' (יחס {ratio:.1f} ל־1)' if ratio is not None and ratio >= .5 else '')
                   + (f'; בתרחיש הקיצון (הפסד קבוע) {"−" + _p(perm)}' if _num(perm) else '') + '. ')
        if up <= 0:
            reading += 'גם התרחיש השורי של המודל לא מגיע למחיר: אין אסימטריה לטובתנו לפי ההנחות הנוכחיות, אלא אם הראיות ישנו אותן.'
        elif down == 0:
            reading += 'גם התרחיש הדובי לא מתחת למחיר: אסימטריה חיובית לפי המודל — לבדוק שהתרחיש הדובי באמת רע מספיק.'
        elif ratio and ratio >= 2:
            reading += 'האסימטריה חיובית: הרווח האפשרי גדול בבירור מההפסד האפשרי.'
        else:
            reading += 'אין אסימטריה ברורה: הרווח האפשרי לא גדול בהרבה מההפסד האפשרי.'
        reading += ' אלה תרחישים ולא הסתברויות.'
        st = 'numbers_only'
    qs['asymmetry'] = {'status': st, 'reading_he': reading,
                       'numbers': {'gain_to_bull': up, 'loss_to_bear': down, 'loss_to_permanent': perm,
                                   'five_year': {k: (vv.get('five_year') or {}).get(k) for k in ('bear_value', 'base_value', 'bull_value')}},
                       'evidence': [], 'analyst': _analyst(inputs, 'asymmetry', fb),
                       'to_close_he': 'לוודא שהתרחיש הדובי כולל את הפחד המרכזי ושהתרחיש השורי נשען על ראיות ולא על תקווה.'}

    # 5. bottleneck
    moat = (card.get('dimensions') or {}).get('moat') or {}
    ev = _evidence(fb, TOPICS['bottleneck'])
    an_b = _analyst(inputs, 'bottleneck', fb)
    proxy = next((x for x in fc.get('assumptions') or [] if x.get('id') == 'moat_basis'), None)
    cond = (conditional or {}).get('moat_confirmed')
    reading = ('יתרון תחרותי: ' + ({'strong': 'חזק', 'adequate': 'סביר', 'weak': 'חלש'}.get(moat.get('assessment'), 'לא הוערך')
                                    + (' (הערכת אנליסט)' if moat.get('source') == 'analyst' else ' (לא הוערך בראיות)')) + '. ')
    mets = (an.get('metrics') or {}) if isinstance(an, dict) else {}
    mv = lambda k: _num((mets.get(k) or {}).get('value')) if isinstance(mets.get(k), dict) else None
    gm, om, roic, roic_x, inc = (mv(k) for k in ('gross_margin', 'operating_margin', 'roic', 'roic_ex_goodwill', 'incremental_roic_3y'))
    signs = [f'{lbl} {_p(v)}' for lbl, v in (('רווח גולמי', gm), ('רווח תפעולי', om), ('תשואה על ההון', roic),
                                             ('תשואה על ההון בלי מוניטין', roic_x), ('תשואה על ההון החדש', inc)) if v is not None]
    if signs:
        reading += 'סימנים מהמספרים: ' + ', '.join(signs) + '. '
        if (roic_x or roic or 0) >= .2 and (om or 0) >= .2:
            reading += 'רווחיות ותשואה כאלה נשמרות לאורך שנים בדרך כלל רק כשיש יתרון אמיתי, אבל המספרים לא אומרים מהו ועד מתי. '
    if cond and base:
        reading += (f"אם יוכח שהחברה יושבת על צוואר בקבוק (יתרון חזק), שווי הבסיס עולה מ־{base:,.2f} ל־{cond['base']:,.2f} {ccy}"
                    + (f" ({_p(cond['base'] / price - 1)} מול המחיר)" if price else '') + ' — זה כמה השאלה הזו שווה במודל.')
    if an_b:
        reading = (an_b.get('reading_he') or '') + ' ' + reading
    status = 'evidenced' if (an_b and not an_b['opinion']) or (moat.get('source') == 'analyst' and ev) else ('numbers_only' if proxy else 'open')
    qs['bottleneck'] = {'status': status, 'reading_he': reading.strip(),
                        'numbers': {'moat': moat.get('assessment'), 'moat_source': moat.get('source'),
                                    'value_if_moat_confirmed': cond, 'base': base},
                        'evidence': ev, 'analyst': an_b,
                        'to_close_he': 'למפות את שרשרת האספקה: מה המשאב שאחרים לא יכולים בלעדיו, מה נתח השוק, כמה זמן ייקח למתחרה להשלים פער, '
                                       'ומה יכול לעקוף אותו (טכנולוגיה חלופית, לקוח שבונה לבד, רגולציה).'}

    # 6. future relevance
    mets = (an.get('metrics') or {}) if isinstance(an, dict) else {}
    m = lambda k: _num((mets.get(k) or {}).get('value')) if isinstance(mets.get(k), dict) else _num(mets.get(k))
    c3, c5 = m('revenue_cagr_3y'), m('revenue_cagr_5y')
    biz = cf.get('business') or {}
    parts_rows = biz.get('parts') or {}
    fast = []
    for tables in (('segment', 'product'), ('geography',)):
        for table in tables:
            block = parts_rows.get(table) if isinstance(parts_rows, dict) else None
            for r in (block or {}).get('rows') or []:
                if _num(r.get('cagr')) is not None and (r.get('share') or 0) >= .05:
                    fast.append((r['cagr'], r['name'] if table != 'geography' else f"אזור {r['name']}", r.get('share'), table))
        if fast:
            break                                  # business lines when disclosed; customer regions only otherwise
    fast.sort(reverse=True)
    rpo = (biz.get('rpo') or {}).get('value') if isinstance(biz.get('rpo'), dict) else None
    rev_last = None
    ttm = (an.get('ttm') or {}) if isinstance(an, dict) else {}
    rev_last = _num(ttm.get('revenue'))
    ev = _evidence(fb, TOPICS['future'])
    an_f = _analyst(inputs, 'future', fb)
    reading = f"צמיחת המכירות: {_p(c3, 1)} בשנה ב־3 השנים האחרונות" + (f", {_p(c5, 1)} ב־5" if c5 is not None else '') + '. '
    if fast:
        reading += 'החלקים שצומחים הכי מהר: ' + '; '.join(f'{n} ({_p(s)} מההכנסות, {_p(c, 0)} בשנה)' for c, n, s, _ in fast[:3]) + '. '
    if rpo and rev_last:
        reading += f'הכנסות חוזיות שעוד לא הוכרו: {rpo / rev_last:.1f} שנות מכירות. '
    reading += ('המספרים מראים מה קרה; האם הביקוש מבני וגדל — זו שאלה שצריך לענות עליה בראיות (מגמה, לקוחות, טכנולוגיה).'
                if not (ev or an_f) else '')
    if an_f:
        reading = (an_f.get('reading_he') or '') + ' ' + reading
    qs['future'] = {'status': 'evidenced' if (an_f and not an_f['opinion']) or ev else ('numbers_only' if c3 is not None else 'open'),
                    'reading_he': reading.strip(),
                    'numbers': {'revenue_cagr_3y': c3, 'revenue_cagr_5y': c5, 'fastest_parts': fast[:3], 'rpo_years': (rpo / rev_last) if rpo and rev_last else None},
                    'evidence': ev, 'analyst': an_f,
                    'to_close_he': 'לזהות את המגמה שמניעה את הביקוש (למשל בינה מלאכותית, חשמול, הזדקנות), לבדוק שהחברה מקבלת ממנה '
                                   'הכנסות משלמות ולא רק הכרזות, ומה יכול לעצור אותה.'}

    questions = [{'key': k, 'question_he': q, **qs[k], 'status_he': STATUS_HE[qs[k]['status']]} for k, q in QUESTIONS]
    open_ = [x['key'] for x in questions if x['status'] != 'evidenced']
    kr = _analyst(inputs, 'key_risk', fb)          # the analyst's main risk in plain words (shown in the valuation table)
    out = {'sheet_version': 1, 'questions': questions, 'thesis_he': _thesis(qs, dc, price, base, ccy), 'open': open_,
           'key_risk': kr if kr and kr.get('reading_he') and not kr['opinion'] and not kr['unknown_ids'] else None,
           # a supplied key risk the FactBook cannot back is listed, never dropped silently (the table falls back to the engine's)
           **({'key_risk_rejected': {'reading_he': kr.get('reading_he'), 'unknown_ids': kr['unknown_ids'],
                                     'reason': 'evidence ids not in the FactBook (not citable at as_of, or unknown)' if kr['unknown_ids']
                                     else 'no evidence ids: an opinion' if kr['opinion'] else 'no reading_he'}}
              if kr and not (kr.get('reading_he') and not kr['opinion'] and not kr['unknown_ids']) else {}),
           'meaning': 'an analyst reading of the casefile: numbers from the engine, judgement from evidence; open questions are '
                      'the research agenda; values are drafts until reviewed'}
    try:                                             # the analyst's events behind each scenario price (scenario_reasons)
        events, rejected = scenario_events(inputs, fb)
    except (TypeError, ValueError, AttributeError, KeyError) as exc:     # an optional input never costs the rest of the sheet
        events, rejected = {}, [{'scenario': None, 'index': None, 'code': 'unreadable',
                                 'reason': f'scenario_events could not be read ({type(exc).__name__})'}]
    if events is not None:
        out['scenario_events'] = events
        if rejected:
            out['scenario_events_rejected'] = rejected
    return out


def _thesis(qs, dc, price, base, ccy):
    """One human paragraph: what kind of business, what the price fears, where the asymmetry is, what the case depends on."""
    lv = qs['quality']['numbers']['levels'].get('business_quality')
    out = [f"עסק {'איכותי' if lv == 'strong' else 'סביר' if lv == 'adequate' else 'חלש' if lv == 'weak' else 'שאיכותו עוד לא נקבעה'} לפי המספרים."]
    gap = qs['mispricing']['numbers']['gap_to_base']
    if gap is not None:
        out.append(f"שווי הבסיס של המודל {'גבוה' if gap > 0 else 'נמוך'} ב־{_p(abs(gap))} מהמחיר.")
    reason = (dc.get('verdict') or {}).get('reason_he')
    if reason:
        out.append(str(reason).split('.')[0] + '.')
    if gap is not None and gap <= -.3 and lv == 'strong':
        out.append('בעסק חזק פער כזה הוא בעיקר שאלה: כמה זמן היתרון יחזיק. המודל מניח שהוא נשחק, השוק מניח שלא.')
    elif gap is not None and gap >= .3:
        out.append('הזדמנות אפשרית, בתנאי שהסיבה שהשוק חושש ממנה זמנית ולא מבנית.')
    up, down = qs['asymmetry']['numbers']['gain_to_bull'], qs['asymmetry']['numbers']['loss_to_bear']
    if up is not None and down is not None:
        out.append(f"תרחיש שורי {'+' if up >= 0 else '−'}{_p(abs(up))}, תרחיש דובי −{_p(down)}.")
    cond = qs['bottleneck']['numbers'].get('value_if_moat_confirmed')
    if cond and base and cond.get('base') and cond['base'] > base * 1.15:
        out.append(f"התמונה תלויה בעיקר בשאלה אם יש לה יתרון שמחזיק לאורך שנים: אם כן, שווי הבסיס {cond['base']:,.2f} {ccy}.")
    sens = qs['geopolitics']['numbers']
    hot = {**(sens.get('sensitive_revenue') or {}), **(sens.get('sensitive_assets') or {})}
    big = [k for k, v in hot.items() if v >= .2]
    if big:
        out.append('סיכון גאופוליטי מהותי (' + ', '.join(big) + ') שעוד צריך לתמחר בתרחיש מפורש.')
    opened = [q for q in ('bottleneck', 'future', 'geopolitics') if qs[q]['status'] != 'evidenced']
    if opened:
        out.append('כדי להפוך את זה למסקנה צריך לסגור: ' + ', '.join({'bottleneck': 'צוואר הבקבוק', 'future': 'הרלוונטיות העתידית',
                                                                     'geopolitics': 'התרחיש הגאופוליטי'}[q] for q in opened) + '.')
    return ' '.join(out)


def render_he(sheet):
    """Markdown in Hebrew: the thesis paragraph, then each question with its status, reading and what would close it."""
    if not sheet:
        return ''
    lines = ['## דף אנליסט', '', sheet['thesis_he'], '']
    for q in sheet['questions']:
        lines += [f"**{q['question_he']}** — {q['status_he']}", '', q['reading_he']]
        if q['status'] != 'evidenced':
            lines.append(f"מה יסגור את השאלה: {q['to_close_he']}")
        lines.append('')
    return '\n'.join(lines)
