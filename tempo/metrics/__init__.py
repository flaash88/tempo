"""Deterministic metric engine.

Everything the athlete sees as a number is computed here, tested against
synthetic input with a known expected value, and correct independently of
the AI layer. The AI reads finished numbers; it never produces them.
"""
