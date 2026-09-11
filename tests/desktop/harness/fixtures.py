from __future__ import annotations
def realistic_text(size=1_048_576):
    line = 'alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu\n'
    return (line * ((size // len(line)) + 1))[:size]
