"""SQL helpers."""

LIKE_ESCAPE = "\\"


def contains_pattern(term: str) -> str:
    """An ILIKE pattern matching `term` anywhere, with LIKE wildcards in the term taken literally
    (a search for "100%" or "a_b" must not match everything). Use with `escape=LIKE_ESCAPE`."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
