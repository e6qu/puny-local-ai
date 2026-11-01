"""Reference solution: parse to value sets, then skip by day, hour and minute."""

import calendar
from datetime import datetime, timedelta

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]


def number(text, low, high, names):
    text = text.lower()
    if names and text in names:
        return names.index(text) + (1 if names is MONTHS else 0)
    if not text.isdigit():
        raise ValueError(f"cannot read {text!r}")
    value = int(text)
    if not low <= value <= high:
        raise ValueError(f"{value} is out of range {low}-{high}")
    return value


def field(text, low, high, names=None):
    values = set()
    for part in text.split(","):
        if not part:
            raise ValueError("empty list item")
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            if not step_text.isdigit() or int(step_text) == 0:
                raise ValueError(f"bad step {step_text!r}")
            step = int(step_text)
            single_with_step = True
        else:
            single_with_step = False
        if part == "*":
            start, end = low, high
        elif "-" in part:
            a, b = part.split("-", 1)
            start, end = number(a, low, high, names), number(b, low, high, names)
            if start > end:
                raise ValueError(f"range {part!r} runs backwards")
        else:
            start = number(part, low, high, names)
            end = high if single_with_step else start
        values.update(range(start, end + 1, step))
    return values


def next_time(expression, after):
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError("a cron expression has five fields")
    minutes = field(parts[0], 0, 59)
    hours = field(parts[1], 0, 23)
    doms = field(parts[2], 1, 31)
    months = field(parts[3], 1, 12, MONTHS)
    dows = {d % 7 for d in field(parts[4], 0, 7, DAYS)}
    both = parts[2] != "*" and parts[4] != "*"

    def day_matches(day):
        if day.month not in months:
            return False
        dom_ok = day.day in doms
        dow_ok = day.isoweekday() % 7 in dows
        return (dom_ok or dow_ok) if both else (dom_ok and dow_ok)

    start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    day = start.replace(hour=0, minute=0)
    for _ in range(5 * 366 + 1):
        if day_matches(day):
            first_hour = start.hour if day.date() == start.date() else 0
            for hour in sorted(h for h in hours if h >= first_hour):
                first_minute = start.minute if (day.date() == start.date() and hour == start.hour) else 0
                for minute in sorted(m for m in minutes if m >= first_minute):
                    return day.replace(hour=hour, minute=minute)
        day += timedelta(days=1)
    raise ValueError(f"{expression!r} does not fire in the 5 years after {after}")
