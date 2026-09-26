"""Correctness tests for ocas-weave.

These are the fast, dependency-free tests. They run on throwaway fixtures and
never touch the production `weave.sqlite`. The slower data-shape assertions
against the live book live in `scripts/test_*.py` (run those directly).

Run:  python3 -m unittest discover -s tests
"""
