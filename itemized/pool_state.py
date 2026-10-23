#!/usr/bin/env python3
"""Which models in a pool are out of daily quota, and for how long.

Both clients retire a model that answers RESOURCE_EXHAUSTED and move to the next one.
That was written for a batch run: a process that lives for minutes, inside which a daily
cap never resets, so retirement could be permanent and nothing was lost.

The Streamlit server is not that process. It lives for days. A cap that resets at midnight
never un-retired anything, so one busy afternoon left the pool permanently empty and every
later visitor was told the reading service did not respond - which was true, and which no
amount of waiting or retrying fixed, because the state was in memory rather than at Google.

Retirement is therefore timed. A model comes back after RETIRE_SECONDS and is tried again;
if the cap really is still in force it answers RESOURCE_EXHAUSTED and is retired afresh,
which costs one request an hour and keeps the app alive.
"""
import os, time

# An hour. The cap resets daily, so an expiry this short usually re-retires the model
# immediately - that is the intended trade: one wasted request against a dead deployment.
RETIRE_SECONDS = int(os.environ.get("POOL_RETIRE_SECONDS", "3600"))


def new_state():
    """A fresh retirement record. Each client keeps its own - their quotas are separate."""
    return {}


def retire(state, model, now=None):
    """Record that `model` is out of quota. Returns a new state; `state` is not touched."""
    return {**state, model: (now if now is not None else time.time())}


def is_retired(state, model, now=None):
    at = state.get(model)
    if at is None:
        return False
    now = now if now is not None else time.time()
    return (now - at) < RETIRE_SECONDS


def retired(state, pool, now=None):
    """The models in `pool` still serving out a retirement, in pool order."""
    return [m for m in pool if is_retired(state, m, now)]


def available(state, pool, now=None):
    return [m for m in pool if not is_retired(state, m, now)]
