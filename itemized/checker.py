#!/usr/bin/env python3
"""The Checker - Itemized's verification core.

Every figure that reaches a patient originates here, from the Assignment 3 reference
table. The AI layer is allowed to explain these numbers and nothing else; it never
produces one. That boundary is what makes the output citable, and `provenance.py`
enforces it rather than trusting it.
"""
import csv, os, collections

HERE = os.path.dirname(os.path.abspath(__file__))
TOLERANCE = 1.01          # a 1% rounding allowance before a line counts as over
UNIT_SUSPICION = 10       # quantities at or above this on a once-per-visit code
ONCE_PER_VISIT = {        # codes a single encounter cannot legitimately repeat 10x
    '99281','99282','99283','99284','99285',   # ED visit levels
    '70450','70486','71046','72148','74177',   # imaging studies
    'G0378','G0379',                            # observation
}

def load_reference(path=None):
    path = path or os.path.join(HERE, 'a3_reference_table.csv')
    table = {}
    for r in csv.DictReader(open(path, encoding='utf-8-sig')):
        table[(r['hospital_name'].strip().lower(), r['code'].strip())] = r
    return table

def _f(v):
    try:    return float(v)
    except (TypeError, ValueError): return None

def check_line(item, hospital, table, seen_codes):
    """One line item in, one verdict out. Returns a new dict; nothing is mutated."""
    code = str(item.get('code','')).strip()
    units = int(item.get('units') or 1)
    charged = _f(item.get('charged'))
    ref = table.get((hospital.strip().lower(), code))

    base = {
        'code': code,
        'description': item.get('description',''),
        'units': units,
        'charged': charged,
        'posted_price': None,
        'cash_price': None,
        'medicare_payment': None,
        'expected_for_units': None,
        'amount_over': None,
        'source_url': None,
        'hospital_enforcement_actions': None,
    }

    if charged is None:
        return {**base, 'verdict': 'UNREADABLE',
                'reason': 'no charge amount could be read from this line'}

    # Duplicate is checked BEFORE the posted-price lookup. Being billed twice for the same
    # code is actionable whether or not the hospital publishes a price for it; checking the
    # lookup first made a repeated unposted code report NOT_POSTED and hid the duplicate.
    if code in seen_codes:
        return {**base, 'verdict': 'DUPLICATE',
                'reason': f'code {code} appears more than once on this bill'}

    if ref is None:
        return {**base, 'verdict': 'NOT_POSTED',
                'reason': f'{hospital} publishes no price for code {code}, '
                          'so this charge cannot be checked against its own price file'}

    posted   = _f(ref['hospital_gross_charge'])
    cash     = _f(ref['hospital_cash_price'])
    medicare = _f(ref['medicare_avg_payment'])
    expected = round(posted * units, 2) if posted is not None else None
    over     = round(charged - expected, 2) if expected is not None else None

    enriched = {**base,
        'posted_price': posted,
        'cash_price': cash,
        'medicare_payment': medicare,
        'expected_for_units': expected,
        'amount_over': over,
        'source_url': ref['mrf_source_url'],
        'hospital_enforcement_actions': int(ref['hospital_cms_enforcement_actions'] or 0),
        'posted_description': ref['description'],
    }

    if units >= UNIT_SUSPICION and code in ONCE_PER_VISIT:
        return {**enriched, 'verdict': 'QUANTITY_SUSPECT',
                'reason': f'billed {units} times; {code} is normally billed once per visit'}

    if over is not None and charged > expected * TOLERANCE:
        return {**enriched, 'verdict': 'ABOVE_POSTED',
                'reason': f'charged ${charged:,.2f} against a posted price of '
                          f'${expected:,.2f} for {units} unit(s) - ${over:,.2f} over'}

    return {**enriched, 'verdict': 'VERIFIED',
            'reason': f'matches the ${posted:,.2f} this hospital posts for code {code}'}

def check_bill(bill, table):
    """A whole bill in, a reviewed bill out."""
    hospital = bill['hospital_name']
    seen, lines = set(), []
    for item in bill.get('line_items', []):
        v = check_line(item, hospital, table, seen)
        seen.add(v['code'])
        lines.append(v)

    flagged = [l for l in lines if l['verdict'] not in ('VERIFIED',)]
    recoverable = round(sum(l['amount_over'] for l in lines
                            if l['verdict'] in ('ABOVE_POSTED','QUANTITY_SUSPECT')
                            and l['amount_over'] and l['amount_over'] > 0), 2)
    dup_amount = round(sum(l['charged'] for l in lines if l['verdict']=='DUPLICATE'), 2)

    return {
        'bill_id': bill.get('bill_id'),
        'hospital_name': hospital,
        'hospital_state': bill.get('hospital_state'),
        'account': bill.get('account'),
        'date_of_service': bill.get('date_of_service'),
        'lines': lines,
        'line_count': len(lines),
        'billed_total': round(sum(l['charged'] for l in lines if l['charged']), 2),
        'posted_total': round(sum(l['expected_for_units'] for l in lines
                                  if l['expected_for_units'] is not None), 2),
        'flagged_count': len(flagged),
        'questionable_amount': round(recoverable + dup_amount, 2),
        'verdict_counts': dict(collections.Counter(l['verdict'] for l in lines)),
        'checkable_lines': sum(1 for l in lines if l['verdict'] != 'NOT_POSTED'),
    }
