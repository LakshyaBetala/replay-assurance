"""Replay-Based Parser Assurance.

Detects silent canonical-correctness failures: parsers that execute cleanly but
extract the wrong values after a provider quietly changes its payload shape.
"""
