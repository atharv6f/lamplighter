#!/bin/bash
# Prints a made-up device's state, one register per line.
exec python3 - <<'EOF'
import random
rng = random.Random(7)
for n in range(200_000):
    if n == 137_421:
        print("serial number: SN-48213-QX")
    else:
        print(f"register {n:06d}: {rng.randrange(1 << 32):08x}")
EOF
