#!/usr/bin/env python3
"""The pool must recover from a daily cap without a restart.

Written against the defect it caught: retirement was a plain set, so a model that hit the
cap was retired for the life of the process. In a batch run that is correct. On a server
that runs for days it meant one busy afternoon emptied the pool permanently, and every
visitor afterwards was told the reading service did not respond.

No network: the client's single-model call is replaced and the clock is driven by hand.
Run with  python3 tests/test_pool_state.py
"""
import pathlib, sys

APP = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP / "itemized"))

import pool_state                                                    # noqa: E402
import gemini                                                        # noqa: E402

CLOCK = [1_000_000.0]
pool_state.time = type("_c", (), {"time": staticmethod(lambda: CLOCK[0])})()


def test_retirement_expires_after_the_window():
    # Arrange
    state = pool_state.retire(pool_state.new_state(), "m1")

    # Act / Assert
    assert pool_state.is_retired(state, "m1")
    CLOCK[0] += pool_state.RETIRE_SECONDS - 1
    assert pool_state.is_retired(state, "m1"), "still inside the window"
    CLOCK[0] += 2
    assert not pool_state.is_retired(state, "m1"), "past the window it must come back"


def test_retire_does_not_mutate_the_state_it_is_given():
    # Arrange
    before = pool_state.new_state()

    # Act
    after = pool_state.retire(before, "m1")

    # Assert
    assert before == {}, "the caller's state must be untouched"
    assert "m1" in after


def test_a_capped_pool_recovers_without_a_restart():
    # Arrange — every model reports the daily cap
    calls = []

    def always_capped(prompt, schema, temperature, model, image=None):
        calls.append(model)
        return {"ok": False, "error": "HTTP 429: RESOURCE_EXHAUSTED daily limit",
                "attempts": 1}

    gemini._generate_one = always_capped
    gemini._exhausted = pool_state.new_state()

    # Act — one caller exhausts the pool
    result = gemini.generate("x")

    # Assert — it reports that honestly, and spends nothing on the next caller
    assert result["pool_exhausted"] is True
    assert calls == list(gemini.POOL)
    assert gemini.pool_status()["available"] == []
    calls.clear()
    gemini.generate("x")
    assert calls == [], "inside the window a retired model must not be retried"

    # Act — the window passes
    CLOCK[0] += pool_state.RETIRE_SECONDS + 1

    # Assert — the deployment is alive again with no intervention
    assert gemini.pool_status()["available"] == list(gemini.POOL)
    calls.clear()
    gemini.generate("x")
    assert calls == list(gemini.POOL), "after the window every model is tried again"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  pass · {name}")
            passed += 1
    print(f"{passed} passed")
