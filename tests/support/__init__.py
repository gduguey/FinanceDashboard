"""Test helpers that are imported rather than injected.

A `conftest.py` can only offer fixtures to the directory it sits in, so
anything two sibling suites both need would otherwise be copied into each of
their conftests. This package is where that shared thing lives instead.
"""
