"""The rule for an address web_fetch may connect to: public, IPv4, and not multicast."""

import ipaddress


def is_public(address: str) -> bool:
    """Say whether an address is one web_fetch may connect to.

    Python's `is_global` alone lets multicast through. IPv6 is left out: on
    a home network, this machine and the devices beside it have IPv6
    addresses that are global by every rule that looks at the address alone.
    """
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return (
        isinstance(parsed, ipaddress.IPv4Address)
        and parsed.is_global
        and not parsed.is_multicast
    )
