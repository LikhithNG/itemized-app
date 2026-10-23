#!/usr/bin/env python3
"""What a billing code looks like. One definition, imported everywhere it is needed.

This module exists because the same loose pattern - `[A-Z]?\\d{3,5}[A-Z]?` - was written
four separate times across this project and was wrong in each of them. It accepts a bare
`310`, which is what sits inside `4,310.00`, and a bare `2026`, which is what sits at the
front of a dated statement line. Both faults reached users:

  * the input validator reported "has billing codes" for a summary bill carrying any
    dollar amount, so the error message written for that case could never fire;
  * a deterministic fallback parser read the service date as the billing code on every
    line of a bill and produced a review in which nothing could be checked;
  * the reader's sanity filter, which decides whether a model-extracted line is kept,
    would have accepted either of those as a code.

The shapes below are the real ones:

  CPT                     five digits               74177, 20610
  HCPCS Level II          a letter then four        J1885, G0379
  CPT Category II / III   four digits then a letter 0042T, 0001F

All 31 distinct codes in this project's reference table match; none is rejected. Category
II and III codes do not appear in that set but are valid and cost nothing to accept.
"""
import re

PATTERN = r"(?:[A-Z]\d{4}|\d{5}|\d{4}[A-Z])"

#: Matches when the whole string is a billing code and nothing else.
EXACT = re.compile(rf"^{PATTERN}$")

#: Matches a billing code appearing anywhere in a line of text.
ANYWHERE = re.compile(rf"\b{PATTERN}\b")


def is_code(value):
    """True when `value` is a billing code and nothing else."""
    return bool(EXACT.match(str(value or "").strip().upper()))


def find_code(text):
    """The first billing code in `text`, or None. Does not match a year, a quantity, or
    the digits inside a money amount."""
    m = ANYWHERE.search(str(text or "").upper())
    return m.group(0) if m else None


def has_code(text):
    """True when `text` contains at least one billing code."""
    return bool(ANYWHERE.search(str(text or "").upper()))
