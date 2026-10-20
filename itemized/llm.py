#!/usr/bin/env python3
"""Provider router. Set LLM_PROVIDER=groq|gemini in .env; the Reader and Advocate import
from here and never learn which service answered them."""
import os

def _load_env():
    env = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env')
    if os.path.exists(env):
        for line in open(env):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"\''))

_load_env()
PROVIDER = os.environ.get('LLM_PROVIDER', 'gemini').lower()

if PROVIDER == 'groq':
    from groq import generate, pool_status, list_models, POOL   # noqa: F401
else:
    from gemini import generate, pool_status, list_models, POOL  # noqa: F401
