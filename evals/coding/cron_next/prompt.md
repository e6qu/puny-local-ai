Write a Python module `solution.py` that finds when a cron schedule next fires:

```python
def next_time(expression, after):
```

`expression` has five fields separated by spaces: minute (0-59), hour (0-23), day of month (1-31), month (1-12) and day of week (0-7, where both 0 and 7 are Sunday). `after` is a naive `datetime.datetime`. Return the first naive datetime strictly after `after`, with seconds and microseconds zero, that matches every field.

Each field is a comma-separated list of parts. A part is one of:

- `*`, every value;
- `N`, one value;
- `A-B`, a range from A to B inclusive, with A not after B;
- any of those followed by `/S`, every S-th value of it starting at its first; `N/S` means from N to the field's maximum.

Months may also be written `jan` to `dec` and days of the week `sun` to `sat`, in any case, including in ranges such as `mon-fri`.

When both the day of month and the day of week are restricted (neither is `*`), a day matches if *either* matches, as in classic cron. When one of them is `*`, only the other decides. A field written `*/S` counts as restricted.

Raise `ValueError` for a malformed expression or a value out of range, and also when nothing matches in the 5 years after `after` (for example `0 0 30 2 *`). That case, and every other, must take well under a second, so do not test every minute one by one.

Examples:

```python
next_time("*/15 * * * *", datetime(2026, 1, 1, 10, 7))   == datetime(2026, 1, 1, 10, 15)
next_time("0 9 * * mon-fri", datetime(2026, 1, 2, 9, 0)) == datetime(2026, 1, 5, 9, 0)   # 2 Jan is a Friday
next_time("0 0 29 2 *", datetime(2026, 3, 1))            == datetime(2028, 2, 29, 0, 0)
```
