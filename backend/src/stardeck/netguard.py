"""Hosted data sources may only reach databases on the public internet.

Without this, a workspace admin could point a "database" at the server's own network: cloud
metadata services, other containers, or the private link to the AI server. Each connection resolves
the host, checks every address, and then connects to the checked address itself, so a DNS answer
can't switch to a private one between the check and the connection (DNS rebinding).
"""

import ipaddress
import re
import socket

# A host name, an IPv4 address, or an IPv6 address (optionally in brackets). No lists (libpq treats
# "a,b" as several hosts), paths (a Unix socket) or spaces.
_HOST = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9-]{1,63})*\.?|\[?[0-9A-Fa-f:.]+\]?)$")
# libpq reads a database name containing "=" or "://" as a whole connection string.
_BAD_FIELD = re.compile(r"[=\x00-\x1f]|://")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # carrier-grade NAT; also every Tailscale address


class BlockedAddress(ValueError):
    """The connection settings point somewhere a hosted source may not go (message is user-facing)."""


class Guard:
    def __init__(self, deny: list[str] | None = None):
        self.deny = [ipaddress.ip_network(d, strict=False) for d in deny or []]

    def check_fields(self, host: str, database: str = "", username: str = "") -> None:
        if not host or len(host) > 253 or not _HOST.match(host):
            raise BlockedAddress("Use the database server's host name or IP address.")
        for value in (database, username):
            if _BAD_FIELD.search(value or ""):
                raise BlockedAddress("The database name and user can't contain '=' or '://'.")

    def allowed(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if (not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback
                or ip.is_link_local or ip.is_private or ip in _CGNAT):
            return False
        return not any(ip in net for net in self.deny)

    def resolve(self, host: str, port: int) -> str:
        """The address to connect to. Every address the name resolves to must be public."""
        try:
            infos = socket.getaddrinfo(host.strip("[]"), port, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError) as exc:
            raise BlockedAddress(f"Couldn't find the server {host}.") from exc
        ips = []
        for *_rest, sockaddr in infos:
            ip = ipaddress.ip_address(sockaddr[0].split("%")[0])
            if not self.allowed(ip):
                raise BlockedAddress(f"{host} is on a private network. Stardeck Cloud can only connect to "
                                     "databases reachable over the internet.")
            ips.append(str(ip))
        if not ips:
            raise BlockedAddress(f"Couldn't find the server {host}.")
        return ips[0]
