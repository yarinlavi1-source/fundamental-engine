"""Conservative SEC exact-concept duration normalization; no concept substitutions."""
from datetime import date
from .finance import number, standalone_ytd, ttm
from .sec import select_facts


def normalize_concept(payload, taxonomy, tag, unit, as_of, basis='GAAP', scope='group'):
    if basis not in {'GAAP', 'IFRS'}:
        raise ValueError('Explicit GAAP/IFRS accounting basis required')
    if taxonomy != {'GAAP':'us-gaap', 'IFRS':'ifrs-full'}[basis]:
        raise ValueError('Taxonomy/basis mismatch')
    facts = select_facts(payload, taxonomy, tag, unit, as_of)
    rows, quarters, annuals, instants, issues = [], {}, [], [], []
    for f in facts:
        number(f['value'], 'SEC fact value')
        row = {**f, 'basis':basis, 'scope':scope, 'currency':unit, 'source_id':f['accession'],
               'available_at':f['filed'], 'provenance':[{'accession':f['accession'], 'filed':f['filed']}],
               'derived':False}
        if f['kind'] == 'instant':
            instants.append(row)
            continue
        days = (date.fromisoformat(f['end'])-date.fromisoformat(f['start'])).days+1
        if days <= 0:
            raise ValueError('Invalid SEC period')
        rows.append(row)
        if 70 <= days <= 105:
            quarters[(f['start'],f['end'])] = row
        elif 330 <= days <= 380:
            annuals.append(row)
    # Only nested same-fiscal-start YTD periods, differences of 70..105 days.
    # Original filing dates remain visible: this is not a certified PIT history.
    for current in rows:
        for previous in rows:
            if current['start'] != previous['start']:
                continue
            delta = (date.fromisoformat(current['end'])-date.fromisoformat(previous['end'])).days
            if not 70 <= delta <= 105:
                continue
            candidate = standalone_ytd(current, previous)
            candidate.update(derived=True, available_at=max(current['filed'],previous['filed']),
                             provenance=current['provenance']+previous['provenance'])
            key = (candidate['start'],candidate['end'])
            if key in quarters:
                if abs(quarters[key]['value']-candidate['value']) > 1e-7:
                    issues.append({'period':key,'issue':'Direct and derived quarter disagree; direct retained, analyst review required'})
            else:
                quarters[key] = candidate
    quarter_rows = sorted(quarters.values(), key=lambda r:r['end'])
    trailing = []
    for i in range(3,len(quarter_rows)):
        window = quarter_rows[i-3:i+1]
        try:
            value = ttm(window)
        except ValueError:
            continue
        trailing.append({'start':window[0]['start'], 'end':window[-1]['end'], 'value':value,
                         'available_at':max(r['available_at'] for r in window),
                         'metric':f'{taxonomy}:{tag}', 'unit':unit, 'basis':basis,
                         'provenance':[p for r in window for p in r['provenance']]})
    return {'concept':f'{taxonomy}:{tag}', 'unit':unit, 'basis':basis, 'annual':annuals,
            'quarters':quarter_rows, 'ttm':trailing, 'instants':instants, 'raw_periods':rows,
            'issues':issues, 'limitations':['Exact tag only; no ADR, split or currency conversion',
            'YTD subtraction can mix filing versions; inspect provenance and conflicts',
            'No segment context inference; companyfacts may omit company-specific disclosures']}
