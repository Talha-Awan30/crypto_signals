"""Format a Signal into a human-readable message."""
from __future__ import annotations

from .signals import Signal


def format_signal(sig: Signal) -> tuple[str, str]:
    arrow = "LONG" if sig.direction == "long" else "SHORT"
    title = f"[{sig.symbol}] {arrow} · {sig.timeframe} · {sig.setup_type}"

    lines = [
        f"Symbol:       {sig.symbol}",
        f"Timeframe:    {sig.timeframe}",
        f"Direction:    {arrow}",
        f"Setup:        {sig.setup_type}",
        f"Price now:    {sig.price_now:.6f}",
        f"Entry zone:   {sig.entry_zone[0]:.6f} – {sig.entry_zone[1]:.6f}",
        f"Invalidation: {sig.invalidation:.6f}",
        "",
        "Reasoning:",
    ]
    for r in sig.reasoning:
        lines.append(f"  - {r}")
    if sig.news_context:
        lines.append("")
        lines.append("News context:")
        lines.append(sig.news_context)
    lines.append("")
    lines.append("Rule: conditions → reasoning → execution. Wait for price to enter the zone.")
    return title, "\n".join(lines)
