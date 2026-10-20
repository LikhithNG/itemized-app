#!/usr/bin/env python3
"""Groq client - same contract as gemini.py, so the Reader and Advocate do not change.

Groq is OpenAI-compatible, so structured output comes from JSON mode plus the schema
restated in the prompt, rather than Gemini's responseSchema field. The daily allowance is
in the thousands rather than Gemini free tier's 20 per model, which is the entire reason
for this module's existence.
"""
import json, os, time, urllib.request, urllib.error

BASE = "https://api.groq.com/openai/v1/chat/completions"
# Groq sits behind Cloudflare, which rejects urllib's default agent with error 1010.
# Identify the client properly rather than letting it look like an unnamed bot.
UA = "itemized-a4/1.0 (INFO7375 coursework; python-urllib)"

def _load_env():
    env = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
    if not os.path.exists(env):
        return
    for line in open(env):
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"\''))

_load_env()

MODEL    = os.environ.get('GROQ_MODEL', 'llama-3.3-70b-versatile')
TIMEOUT  = int(os.environ.get('GROQ_TIMEOUT', '60'))
ATTEMPTS = 4
BACKOFF  = 2.0
RETRY_ON = {429, 500, 502, 503, 504}

POOL = [m.strip() for m in os.environ.get(
    'GROQ_POOL', 'llama-3.3-70b-versatile,llama-3.1-8b-instant,'
                 'meta-llama/llama-4-scout-17b-16e-instruct').split(',') if m.strip()]
_exhausted = set()

class GroqError(RuntimeError):
    pass

def pool_status():
    return {'pool': POOL, 'exhausted': sorted(_exhausted),
            'available': [m for m in POOL if m not in _exhausted]}

def _key():
    k = os.environ.get('GROQ_API_KEY', '').strip()
    if not k:
        raise GroqError("GROQ_API_KEY is not set (env var or .env at the project root)")
    return k

def _post(payload):
    req = urllib.request.Request(
        BASE, data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json', 'User-Agent': UA,
                 'Accept': 'application/json',
                 'Authorization': f'Bearer {_key()}'}, method='POST')
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())

def _one(prompt, schema, temperature, model):
    body = {'model': model, 'temperature': temperature,
            'messages': [{'role': 'user', 'content': prompt}]}
    if schema:
        body['response_format'] = {'type': 'json_object'}

    last = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            raw = _post(body)
            choice = (raw.get('choices') or [{}])[0]
            text = ((choice.get('message') or {}).get('content')) or ''
            u = raw.get('usage') or {}
            out = {'ok': True, 'text': text, 'json': None, 'attempts': attempt,
                   'usage': {'prompt_tokens': u.get('prompt_tokens'),
                             'output_tokens': u.get('completion_tokens'),
                             'total_tokens': u.get('total_tokens')},
                   'finish_reason': choice.get('finish_reason')}
            if schema:
                try:
                    out['json'] = json.loads(text)
                except json.JSONDecodeError as ex:
                    return {'ok': False, 'attempts': attempt,
                            'error': f'malformed JSON from model: {ex}', 'text': text[:400]}
            return out
        except urllib.error.HTTPError as ex:
            body_txt = ex.read()[:900].decode('utf-8', 'ignore')
            last = f'HTTP {ex.code}: {body_txt}'
            # a per-day quota failure will not clear during a backoff window
            if ex.code == 429 and ('per day' in body_txt.lower() or 'rpd' in body_txt.lower()):
                return {'ok': False, 'error': last, 'attempts': attempt, 'quota': 'daily'}
            if ex.code not in RETRY_ON or attempt == ATTEMPTS:
                return {'ok': False, 'error': last, 'attempts': attempt}
        except (urllib.error.URLError, TimeoutError, OSError) as ex:
            last = f'{type(ex).__name__}: {ex}'
            if attempt == ATTEMPTS:
                return {'ok': False, 'error': last, 'attempts': attempt}
        time.sleep(BACKOFF * (2 ** (attempt - 1)))
    return {'ok': False, 'error': last or 'exhausted retries', 'attempts': ATTEMPTS}

def generate(prompt, schema=None, temperature=0.0, model=None):
    """Calls the pool in order, retiring any model that reports a daily quota failure."""
    if model:
        return _one(prompt, schema, temperature, model)
    last = {'ok': False, 'error': 'no models available', 'attempts': 0}
    for m in POOL:
        if m in _exhausted:
            continue
        r = _one(prompt, schema, temperature, m)
        if r['ok']:
            r['model'] = m
            return r
        last = r
        if r.get('quota') == 'daily':
            _exhausted.add(m)
            continue
        return r
    last['model'] = None
    last['pool_exhausted'] = True
    return last

def list_models():
    req = urllib.request.Request('https://api.groq.com/openai/v1/models',
                                 headers={'Authorization': f'Bearer {_key()}',
                                          'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return [m['id'] for m in json.loads(r.read()).get('data', [])]
