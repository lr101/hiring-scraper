from __future__ import annotations

import ipaddress
import socket


class UnsafeNetworkAddress(ValueError):
    """A hostname resolved to an address that server-side fetches must not use."""


def validate_public_hostname(hostname: str) -> None:
    """Reject hostnames that resolve to anything other than globally routable addresses."""
    normalized = hostname.casefold().rstrip(".")
    try:
        results = socket.getaddrinfo(normalized, None, type=socket.SOCK_STREAM)
    except OSError:
        # Let the HTTP client report an unresolved hostname. This also keeps mock transports
        # usable while still rejecting every address returned by a real resolver.
        return

    addresses: set[str] = set()
    for result in results:
        sockaddr = result[4]
        if isinstance(sockaddr, tuple) and sockaddr and isinstance(sockaddr[0], str):
            addresses.add(sockaddr[0])
    if not addresses:
        return
    try:
        parsed_addresses = [ipaddress.ip_address(address) for address in addresses]
    except ValueError as error:
        raise UnsafeNetworkAddress("The hostname resolved to an invalid address.") from error
    if any(not address.is_global for address in parsed_addresses):
        raise UnsafeNetworkAddress("The hostname resolved to a private or reserved address.")
