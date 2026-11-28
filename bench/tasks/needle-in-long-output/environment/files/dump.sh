#!/bin/bash
# Prints a made-up device's state, one register per line.
exec python3 - <<'EOF'
import random
rng = random.Random(7)
for n in range(200_000):
    value = rng.randrange(1 << 32)
    if n == 137_421:
        digits = value % 100_000
        first = chr(65 + value // 100_000 % 26)
        second = chr(65 + value // 2_600_000 % 26)
        print(f"serial number: SN-{digits:05d}-{first}{second}")
    else:
        print(f"register {n:06d}: {value:08x}")
EOF
