"""Parses access log lines of the form:

    2026-10-30T09:37:12Z GET /api/items/42 200 183ms user=ana
"""


def parse(line):
    """(timestamp, method, path, status, millis, user), or None for a malformed line."""
    parts = line.split()
    if len(parts) != 6:
        return None
    timestamp, method, path, status, millis, user = parts
    if not (status.isdigit() and millis.endswith("ms") and millis[:-2].isdigit() and user.startswith("user=")):
        return None
    return timestamp, method, path, int(status), int(millis[:-2]), user[len("user="):]
