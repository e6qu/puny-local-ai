"""Reference solution: integer floor shares plus largest remainders."""


def split(total_cents, weights):
    if type(total_cents) is not int or any(type(w) is not int for w in weights):
        raise TypeError("the total and the weights must be ints")
    if not weights or any(w < 0 for w in weights) or not any(weights):
        raise ValueError("weights must be zero or more, with at least one positive")
    sign = -1 if total_cents < 0 else 1
    amount = abs(total_cents)
    whole = sum(weights)
    shares = []
    remainders = []
    for w in weights:
        share, remainder = divmod(amount * w, whole)
        shares.append(share)
        remainders.append(remainder)
    left = amount - sum(shares)
    for i in sorted(range(len(weights)), key=lambda i: (-remainders[i], i))[:left]:
        shares[i] += 1
    return [sign * s for s in shares]
