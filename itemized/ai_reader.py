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

# A bare-minimum sanity filter; the Checker does the real verification. The shape of a
# billing code lives in one place - see codes.py for why it is not written inline here.
from codes import is_code

def read_bill_image(image_bytes, mime_type):
    """Read a photographed or scanned bill.

    Same extraction, same schema, same sanity filter as read_bill - the only difference is
    that the statement arrives as pixels instead of characters. It goes to Gemini because
    that is the vision-capable provider configured here; Groq's pool is text only.

    This does not loosen anything downstream. The model still only reports what it can see
    on the bill, the Checker still produces every figure from the reference table, and the
    provenance guard still discards prose citing anything the Checker did not establish. A
    misread digit becomes a wrong line item, which the Checker will usually return as
    NOT_POSTED - visible to the reader rather than silently folded into a total.
    """
    import base64
    try:
        import gemini
    except ImportError:
        return [], {'ok': False, 'error': 'vision provider not available',
                    'items_extracted': 0, 'items_dropped': 0}

    prompt = (PROMPT.format(bill="(the statement is the attached image)")
              + "\n\nRead the attached image of a hospital statement. Report only line "
                "items you can actually see. If a digit is unclear, omit that line rather "
                "than guessing it.")
    r = gemini.generate(prompt, schema=SCHEMA, temperature=0.0,
                        image=(mime_type, base64.b64encode(image_bytes).decode()))
    if not r['ok']:
        return [], {'ok': False, 'error': r['error'], 'attempts': r.get('attempts'),
                    'items_extracted': 0, 'items_dropped': 0,
                    'model': r.get('model'), 'source': 'image'}

    data = r.get('json') or {}
    items, dropped = [], 0
    for it in (data.get('line_items') or []):
        code = str(it.get('code', '')).strip().upper()
        try:
            units, charged = int(it.get('units') or 1), float(it.get('charged'))
        except (TypeError, ValueError):
            dropped += 1; continue
        if not is_code(code) or charged <= 0 or units <= 0:
            dropped += 1; continue
        items.append({'code': code, 'description': str(it.get('description', '')).strip(),
                      'units': units, 'charged': round(charged, 2)})
    return items, {'ok': True, 'attempts': r.get('attempts'), 'usage': r.get('usage'),
                   'items_extracted': len(items), 'items_dropped': dropped,
                   'model': r.get('model'), 'source': 'image'}


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
        if not is_code(code) or charged <= 0 or units <= 0:
            dropped += 1; continue
        items.append({'code': code, 'description': str(it.get('description', '')).strip(),
                      'units': units, 'charged': round(charged, 2)})

    return items, {'ok': True, 'attempts': r['attempts'], 'usage': r['usage'], 'model': r.get('model'),
                   'items_extracted': len(items), 'items_dropped': dropped,
                   'finish_reason': r.get('finish_reason')}
