"""New ids from the operating system's source of random bytes."""

import secrets


class SystemIds:
    """Gives 16 random hex characters per id. Satisfies IdSource.

    That is 64 random bits. Among a million ids, the chance that any two are
    equal is about 1 in 37 million, which is small enough to call never.
    """

    def new_id(self) -> str:
        """Give a new random id."""
        return secrets.token_hex(8)
