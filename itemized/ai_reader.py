#!/usr/bin/env python3
"""The AI Reader - unstructured bill text in, structured line items out.

This is the step a regex cannot do: the same three facts (code, quantity, amount) arrive
in three different layouts, with scan artifacts, and the description column is truncated
differently on every hospital's statement. The Reader's only job is extraction. It is
told explicitly not to judge, price or correct anything, because that is the Checker's
job and the Checker is deterministic.
"""
import re
from llm import generate

SCHEMA = {
  'type': 'object',
  'properties': {
    'hospital_name_on_bill': {'type': 'string'},
    'account': {'type': 'string'},
    'date_of_service': {'type': 'string'},
    'line_items': {
      'type': 'array',
      'items': {
        'type': 'object',
        'properties': {
          'code':        {'type': 'string'},
          'description': {'type': 'string'},
          'units':       {'type': 'integer'},
          'charged':     {'type': 'number'},
        },
        'required': ['code', 'units', 'charged'],
      },
    },
  },
  'required': ['line_items'],
}

PROMPT = """You are reading an itemized hospital bill. Extract its line items exactly as printed.

Rules:
- One object per billed line. If the same code appears twice, return it twice - do not merge.
- `code` is the billing code as printed (CPT like 99284, or HCPCS like J2405, G0379). Never invent one.
- `units` is the quantity column. If no quantity is shown, use 1.
- `charged` is the dollar amount for that line, as a plain number: no currency symbol, no commas.
- `description` is the text as printed, even if truncated or containing scan errors.
- Do NOT judge, correct, re-price or comment on anything. Do NOT add lines that are not printed.
- Ignore totals, balances and subtotals - they are not line items.

Return json in exactly this shape, and nothing else:
{{"line_items":[{{"code":"99284","description":"ED VISIT LEVEL 4","units":1,"charged":1234.56}}]}}

BILL TEXT:
---
{bill}
---"""

# a bare minimum sanity filter; the Checker does the real verification
CODE_OK = re.compile(r'^[A-Z]?\d{3,5}[A-Z]?$')

def read_bill(bill_text):
    """Returns (line_items, meta). meta records what happened, for the error-handling log."""
    r = generate(PROMPT.format(bill=bill_text), schema=SCHEMA, temperature=0.0)
    if not r['ok']:
        return [], {'ok': False, 'error': r['error'], 'attempts': r['attempts'],
                    'items_extracted': 0, 'items_dropped': 0,
                    'model': r.get('model'), 'pool_exhausted': r.get('pool_exhausted', False)}

    data = r.get('json') or {}
    raw = data.get('line_items') or []
    items, dropped = [], 0
    for it in raw:
        code = str(it.get('code', '')).strip().upper()
        try:
            units   = int(it.get('units') or 1)
            charged = float(it.get('charged'))
        except (TypeError, ValueError):
            dropped += 1; continue
        if not CODE_OK.match(code) or charged <= 0 or units <= 0:
            dropped += 1; continue
        items.append({'code': code, 'description': str(it.get('description', '')).strip(),
                      'units': units, 'charged': round(charged, 2)})

    return items, {'ok': True, 'attempts': r['attempts'], 'usage': r['usage'], 'model': r.get('model'),
                   'items_extracted': len(items), 'items_dropped': dropped,
                   'finish_reason': r.get('finish_reason')}
