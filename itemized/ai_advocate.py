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
2. `letter_body` - 3 to 4 sentences for a formal dispute letter to the billing office. It
   must NAME each flagged line, and its LAST sentence must make a specific request: ask the
   billing office to investigate the listed items and send either a written explanation or a
   corrected statement. A letter that lists discrepancies and stops gives the reader nothing
   to act on, so never end on the list.

ABSOLUTE RULE ON NUMBERS: you may only use dollar figures that appear in the data below,
copied exactly. Do not add, total, estimate, round or infer any amount. If you want to
describe a quantity you do not have a figure for, use words instead of a number.

EVERY line whose verdict is not VERIFIED must be mentioned in both the summary and the
letter, including any line whose difference is $0.00. A line can be a problem because of
its quantity even when the rate charged for each unit is correct - in that case say the
quantity is the thing being questioned, and do not describe the line as matching. Never
write that the other lines all match unless every other verdict really is VERIFIED.

Never print the verdict names (VERIFIED, ABOVE_POSTED, QUANTITY_SUSPECT, NOT_POSTED,
DUPLICATE, UNREADABLE). They are internal labels; the patient reads plain English.

Do not invent a reason for a verdict. Each line carries the Checker's own `why flagged`
note. Say what that note says and nothing beyond it - but put it in your own plain words.
Never copy the note verbatim, and never repeat it in brackets after your own sentence; it
is working text, not something a patient would write. In particular, a code appearing twice
means only that it appears twice - it may be two genuine units, so describe it as something
to confirm, and never claim the hospital's published file says anything about how many were
actually provided.

Do not promise a refund, allege fraud, or give legal advice. A flagged line means the
amount billed does not match the price the hospital itself publishes. The amount worth
disputing is a difference to ask about, not money the patient is owed or will get back -
never call it recoverable, a refund, or an amount they can claim. Call that figure the
unexplained difference; do not call it the amount worth disputing, which readers hear as
money coming back to them.

Return json in exactly this shape, and nothing else:
{{"summary":"...","letter_body":"..."}}

VERIFIED BILL DATA:
hospital: {hospital}
billed total: ${billed:,.2f}
hospital's posted total for the same lines: ${posted:,.2f}
unexplained difference: ${questionable:,.2f}
lines on the bill: {n_lines}

LINES:
{lines}"""

def _line_block(rv):
    """One line per charge, including why the Checker flagged it.

    User testing found the Advocate dropping flagged lines from both the summary and the
    letter. The cause was here: a QUANTITY_SUSPECT line whose per-unit rate is correct has
    a difference of $0.00, so what this function emitted was numerically identical to a
    VERIFIED line. The model was not ignoring the problem - it was never shown one. The
    Checker's own `reason` is now passed through, so the prose can say what the verdict means.
    """
    out = []
    for l in rv['lines']:
        bits = [f"- {l['verdict']} | code {l['code']} | qty {l['units']} "
                f"| billed ${l['charged']:,.2f}"]
        if l['expected_for_units'] is not None:
            # Named for what it is: the posted rate times the quantity billed. Calling it
            # "hospital posts" let the quantity in dispute pass as a price the hospital set.
            bits.append(f"posted rate x qty billed ${l['expected_for_units']:,.2f}")
        if l.get('posted_price') is not None:
            bits.append(f"posted rate for one ${l['posted_price']:,.2f}")
        if l['amount_over'] and l['amount_over'] > 0:
            bits.append(f"difference ${l['amount_over']:,.2f}")
        if l['medicare_payment'] is not None:
            bits.append(f"medicare pays ${l['medicare_payment']:,.2f}")
        if (l['verdict'] == 'QUANTITY_SUSPECT' and l.get('posted_price')
                and l.get('units', 0) > 1):
            # Without these the letter quotes the rate difference as if it were the whole
            # exposure on a line whose quantity is also disputed.
            per_unit = round(l['charged'] / l['units'], 2)
            beyond = round(l['charged'] - l['posted_price'] - (l.get('amount_over') or 0), 2)
            bits.append(f"actually billed per unit ${per_unit:,.2f}")
            bits.append(f"further at stake if the quantity is wrong ${beyond:,.2f} "
                        f"({l['units'] - 1} more at the posted rate), separate from any "
                        f"rate difference already counted")
        if l.get('reason'):
            label = 'note' if l['verdict'] == 'VERIFIED' else 'why flagged'
            bits.append(f"{label}: {l['reason']}")
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
