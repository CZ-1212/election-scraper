"""
Shared utilities for all election scrapers.
"""
import re


def _parse_choice_str(raw: str, county: str, contest_title: str) -> "dict | None":
    """Parse a whitespace/newline-tokenized choice string into the canonical choice dict.

    Expected string endings (working from the right):
      "... pct% votes"  →  last token is votes (digits+commas), second-to-last is pct%
      "... votes pct%"  →  last token is pct (ends with %), second-to-last is votes

    Everything before those two tokens is the name.  Parsing from the end protects
    names that contain spaces, suffixes, or embedded numbers (e.g. "District 2 Board").

    Returns {'name': str, 'votes': int, 'pct': float} or None on failure.
    Logs a clear error naming county and contest when it cannot parse.
    """
    # Flatten newlines and runs of whitespace to single spaces.
    tokens = re.sub(r"\s+", " ", raw.strip()).split()

    if len(tokens) < 3:
        print(
            f"[PARSE ERROR] [{county}] {contest_title!r}: "
            f"too few tokens to parse choice {raw!r}"
        )
        return None

    last = tokens[-1]
    second_last = tokens[-2]

    # Identify which end token is votes vs pct.
    if re.match(r"^[\d,]+$", last):
        # Format: "name ... pct% votes"
        votes_raw = last
        pct_raw = second_last.rstrip("%")
        name_tokens = tokens[:-2]
    elif re.match(r"^\d[\d.]*%?$", last) and "%" in last:
        # Format: "name ... votes pct%"
        pct_raw = last.rstrip("%")
        votes_raw = second_last
        name_tokens = tokens[:-2]
    else:
        print(
            f"[PARSE ERROR] [{county}] {contest_title!r}: "
            f"unrecognized tail in choice {raw!r}"
        )
        return None

    try:
        votes = int(votes_raw.replace(",", ""))
    except ValueError:
        print(
            f"[PARSE ERROR] [{county}] {contest_title!r}: "
            f"votes not an integer in choice {raw!r}"
        )
        return None

    try:
        pct = float(pct_raw)
    except ValueError:
        print(
            f"[PARSE ERROR] [{county}] {contest_title!r}: "
            f"pct not a number in choice {raw!r}"
        )
        return None

    name = " ".join(name_tokens).strip()
    if not name:
        print(
            f"[PARSE ERROR] [{county}] {contest_title!r}: "
            f"empty name in choice {raw!r}"
        )
        return None

    return {"name": name, "votes": votes, "pct": pct}
