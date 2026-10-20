#!/usr/bin/env python3
"""The AI Advocate - writes the prose a patient reads, around numbers it cannot choose.

The Advocate receives the Checker's verdict and may only restate the figures in it. Every
draft goes through the provenance guard; a draft citing an unsourced amount is discarded
and the deterministic template runs instead. The model improves the writing, never the
arithmetic.
"""
from llm import generate, PROVIDER
from provenance import verify

SCHEMA = {
  'type': 'object',
  'properties': {
    'summary':     {'type': 'string'},
    'letter_body': {'type': 'string'},
  },
  'required': ['summary', 'letter_body'],
}

PROMPT = """You write for patients disputing hospital bills. Plain language, no jargon, no drama.

You are given a completed verification of one bill. Write two things:

1. `summary` - 3 to 5 sentences telling the patient what was found and what it means.
2. `letter_body` - 2 to 3 sentences opening a formal dispute letter to the billing office.

ABSOLUTE RULE ON NUMBERS: you may only use dollar figures that appear in the data below,
copied exactly. Do not add, total, estimate, round or infer any amount. If you want to
describe a quantity you do not have a figure for, use words instead of a number.

Do not promise a refund, allege fraud, or give legal advice. A flagged line means the
amount billed does not match the price the hospital itself publishes.

Return json in exactly this shape, and nothing else:
{{"summary":"...","letter_body":"..."}}

VERIFIED BILL DATA:
hospital: {hospital}
billed total: ${billed:,.2f}
hospital's posted total for the same lines: ${posted:,.2f}
amount worth disputing: ${questionable:,.2f}
lines on the bill: {n_lines}

LINES:
{lines}"""

def _line_block(rv):
    out = []
    for l in rv['lines']:
        bits = [f"- {l['verdict']} | code {l['code']} | billed ${l['charged']:,.2f}"]
        if l['expected_for_units'] is not None:
            bits.append(f"hospital posts ${l['expected_for_units']:,.2f}")
        if l['amount_over'] and l['amount_over'] > 0:
            bits.append(f"difference ${l['amount_over']:,.2f}")
        if l['medicare_payment'] is not None:
            bits.append(f"medicare pays ${l['medicare_payment']:,.2f}")
        out.append(' | '.join(bits))
    return '\n'.join(out)

def write_narrative(rv):
    """Returns (summary, letter_body, meta). On any failure both texts are None and the
    renderer falls back to its deterministic template."""
    prompt = PROMPT.format(hospital=rv['hospital_name'], billed=rv['billed_total'],
                           posted=rv['posted_total'], questionable=rv['questionable_amount'],
                           n_lines=rv['line_count'], lines=_line_block(rv))
    r = generate(prompt, schema=SCHEMA, temperature=0.2)
    if not r['ok']:
        return None, None, {'ok': False, 'error': r['error'], 'attempts': r['attempts'],
                            'guard': 'not reached', 'source': 'template'}

    d = r.get('json') or {}
    summary, letter = d.get('summary', ''), d.get('letter_body', '')
    ok_s, f_s = verify(summary, rv)
    ok_l, f_l = verify(letter, rv)

    meta = {'ok': True, 'attempts': r['attempts'], 'usage': r['usage'],
            'guard': {'summary': f_s, 'letter_body': f_l},
            'guard_passed': bool(ok_s and ok_l),
            # name the model that actually wrote it, not the provider this module was built for
            'source': (f"{PROVIDER}:{r.get('model') or 'default'}" if (ok_s and ok_l)
                       else 'template (guard rejected AI prose)')}
    if not (ok_s and ok_l):
        return None, None, meta
    return summary, letter, meta
