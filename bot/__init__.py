"""Institutional Swing Trading Alert System — v5 spec implementation.

Two-stage alert flow:
  Stage 1 (Zone Alert)       — confluence detected, retracement zone defined
  Stage 2 (Execution Alert)  — price in zone, LTF validation confirmed

Conditions:
  A — HTF Key Level Reaction (PRIMARY)
  B — HTF Pattern Detection (SECONDARY) — 11 classifiers
  C — HTF Market Structure Shift (PRIMARY)
  D — Liquidity Event (SECONDARY)

A delivered alert requires: (A or C) AND (B or D).
Confidence score >= 7 delivered; >= 8 marked PRIORITY.
"""
