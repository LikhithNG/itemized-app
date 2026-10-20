#!/usr/bin/env python3
"""The provenance guard.

Itemized tells a patient that a charge looks wrong. If the model invents a figure while
explaining why, the patient disputes a bill on a number nobody can defend - so the
guarantee cannot be "we asked the model not to". Every monetary amount in generated
prose is matched against the Checker's own payload, and prose that cites an unsourced
figure is rejected before it can reach a document.

Fail closed: a rejected draft falls back to the deterministic template, which can only
ever restate Checker values.
"""
import re

MONEY = re.compile(r'\$\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)')
CENT_TOLERANCE = 0.02      # absorbs rounding in the model's formatting, nothing more

def _amounts_in(text):
    out = []
    for m in MONEY.finditer(text or ''):
        try: out.append((m.group(0).strip(), float(m.group(1).replace(',',''))))
        except ValueError: pass
    return out

def allowed_amounts(reviewed):
    """Every figure the Checker actually established for this bill."""
    allowed = set()
    def add(v):
        if isinstance(v,(int,float)): allowed.add(round(float(v),2))
    for key in ('billed_total','posted_total','questionable_amount'):
        add(reviewed.get(key))
    for line in reviewed.get('lines',[]):
        for key in ('charged','posted_price','cash_price','medicare_payment',
                    'expected_for_units','amount_over'):
            add(line.get(key))
    return allowed

def verify(text, reviewed):
    """Returns (ok, findings). ok is False if the prose cites an unsourced amount."""
    allowed = allowed_amounts(reviewed)
    unsourced = []
    for literal, value in _amounts_in(text):
        if not any(abs(value-a) <= CENT_TOLERANCE for a in allowed):
            unsourced.append({'literal': literal, 'value': value})
    return (not unsourced), {
        'amounts_cited': len(_amounts_in(text)),
        'amounts_allowed': len(allowed),
        'unsourced': unsourced,
    }
