#!/usr/bin/env python3
"""Minimal Gemini client - urllib only, no SDK, same no-dependency discipline as A3.

Carries the error handling Part 1 of the rubric asks for: bounded retries with
exponential backoff on rate limits and transient server errors, a hard timeout, and a
structured failure return rather than an exception that kills a 120-bill run.
"""
import json, os, time, urllib.request, urllib.error

BASE = "https://generativelanguage.googleapis.com/v1beta"

def _load_env():
    """Settings come from the real environment first, then .env at the project root.
    Loading both here - not just the key - avoids the trap of a stale default model
    silently overriding what .env says."""
    env = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
    if not os.path.exists(env):
        return
    for line in open(env):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"\''))

_load_env()

MODEL   = os.environ.get('GEMINI_MODEL', 'gemini-3.8-flash')
TIMEOUT = int(os.environ.get('GEMINI_TIMEOUT', '60'))

# The free tier's limit is GenerateRequestsPerDayPerProjectPerModel: 20 requests per day
# PER MODEL. Pacing cannot help with a daily cap - but the cap is scoped to a model, so a
# pool of models multiplies the budget. A model that returns RESOURCE_EXHAUSTED is retired
# for the rest of the process and the next one takes over.
POOL = [m.strip() for m in os.environ.get(
    'GEMINI_POOL',
    'gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,'
    'gemini-flash-lite-latest,gemini-3.5-flash-lite').split(',') if m.strip()]
_exhausted = set()

def pool_status():
    return {'pool': POOL, 'exhausted': sorted(_exhausted),
            'available': [m for m in POOL if m not in _exhausted]}
ATTEMPTS = 4
BACKOFF  = 2.0          # seconds, doubled each retry
RETRY_ON = {429, 500, 502, 503, 504}

class GeminiError(RuntimeError):
    pass

def _key():
    k = os.environ.get('GEMINI_API_KEY', '').strip()
    if not k:
        raise GeminiError("GEMINI_API_KEY is not set (env var or .env at the project root)")
    return k

def _post(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read())

def generate(prompt, schema=None, temperature=0.0, model=None, image=None):
    """Calls the pool in order, skipping models already known to be out of daily quota.
    Returns the first success, or the last failure if every model is exhausted."""
    if model:
        return _generate_one(prompt, schema, temperature, model, image)
    last = {'ok': False, 'error': 'no models available', 'attempts': 0}
    for m in POOL:
        if m in _exhausted:
            continue
        r = _generate_one(prompt, schema, temperature, m, image)
        if r['ok']:
            r['model'] = m
            return r
        last = r
        err = str(r.get('error', ''))
        if 'RESOURCE_EXHAUSTED' in err or '429' in err:
            _exhausted.add(m)          # out of daily quota - retire it and move on
            continue
        # A 503 means this model is overloaded right now, which is the other thing a pool
        # is for. The comment below used to say a non-quota failure is not fixed by another
        # model; that is true of a malformed request and false of an overloaded one. Tried
        # on a day when gemini-3.8-flash was returning 503 to every request, the pool gave
        # up after one model while five others were available and working.
        if any(code in err for code in ('HTTP 503', 'HTTP 502', 'HTTP 500', 'UNAVAILABLE')):
            continue                   # overloaded, not broken - the next model may serve
        return r                       # a malformed request is not fixed by another model
    last['model'] = None
    last['pool_exhausted'] = True
    return last

def _generate_one(prompt, schema, temperature, model, image=None):
    """One model, with bounded retries and exponential backoff.

    `image` is an optional (mime_type, base64_data) pair. A photograph of a bill has no
    text layer to extract, so the only way to read one is to let the model look at it -
    the same extraction task, a different input channel. Everything downstream is
    unchanged: the Checker still produces every figure and the guard still holds.
    """
    url = f"{BASE}/models/{model}:generateContent?key={_key()}"
    cfg = {'temperature': temperature}
    if schema:
        cfg['responseMimeType'] = 'application/json'
        cfg['responseSchema'] = schema
    parts = [{'text': prompt}]
    if image:
        mime, b64 = image
        parts.append({'inline_data': {'mime_type': mime, 'data': b64}})
    payload = {'contents': [{'parts': parts}], 'generationConfig': cfg}

    last = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            raw = _post(url, payload)
            cand = (raw.get('candidates') or [{}])[0]
            parts = (cand.get('content') or {}).get('parts') or [{}]
            text = parts[0].get('text', '')
            usage = raw.get('usageMetadata', {}) or {}
            out = {'ok': True, 'text': text, 'json': None, 'attempts': attempt,
                   'usage': {'prompt_tokens': usage.get('promptTokenCount'),
                             'output_tokens': usage.get('candidatesTokenCount'),
                             'total_tokens': usage.get('totalTokenCount')},
                   'finish_reason': cand.get('finishReason')}
            if schema:
                try:
                    out['json'] = json.loads(text)
                except json.JSONDecodeError as ex:
                    # the model returned prose where JSON was demanded - a real failure mode
                    return {'ok': False, 'attempts': attempt,
                            'error': f'malformed JSON from model: {ex}', 'text': text[:400]}
            return out
        except urllib.error.HTTPError as ex:
            body = ex.read()[:900].decode('utf-8', 'ignore')
            last = f'HTTP {ex.code}: {body}'
            # a per-day quota failure will not clear in 15 seconds; do not burn retries on it
            if ex.code == 429 and 'PerDay' in body:
                return {'ok': False, 'error': last, 'attempts': attempt, 'quota': 'daily'}
            if ex.code not in RETRY_ON or attempt == ATTEMPTS:
                return {'ok': False, 'error': last, 'attempts': attempt}
        except (urllib.error.URLError, TimeoutError, OSError) as ex:
            last = f'{type(ex).__name__}: {ex}'
            if attempt == ATTEMPTS:
                return {'ok': False, 'error': last, 'attempts': attempt}
        time.sleep(BACKOFF * (2 ** (attempt - 1)))
    return {'ok': False, 'error': last or 'exhausted retries', 'attempts': ATTEMPTS}

def list_models():
    """Confirms which models this key can actually reach - run once after adding a key."""
    url = f"{BASE}/models?key={_key()}"
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        data = json.loads(r.read())
    return [m['name'].replace('models/', '') for m in data.get('models', [])
            if 'generateContent' in m.get('supportedGenerationMethods', [])]
