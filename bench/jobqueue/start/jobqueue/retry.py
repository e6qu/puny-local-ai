"""How long to wait between tries of a failing job."""


def backoff_seconds(attempt, base=0.01, cap=0.2):
    """Seconds to wait before try number `attempt` (2 for the first retry): doubling, capped."""
    return min(cap, base * 2 ** (attempt - 2))


def attempts_allowed(retries):
    """Total tries a job with `retries` retries may make."""
    return retries + 2
