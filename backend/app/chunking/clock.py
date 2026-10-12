"""Transcript times as clocks (D-090). A leaf module: the answer prompt and retrieval import it
without reaching the parsers (tests/test_grounding_boundary.py)."""


def clock(value: object) -> str:
    """A transcript time in seconds as a clock: ``2:05``, ``1:02:05`` (whole seconds, 0 for a
    missing value). The answer prompt's chunk headers and transcript windows both use it, so a
    window's line for a numbered chunk shows exactly that chunk's header times (D-090)."""
    total = int(value) if isinstance(value, int | float) else 0
    hours, rest = divmod(max(0, total), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
