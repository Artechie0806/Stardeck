"""Hosted data sources can't reach the server's own network."""

import socket

import pytest
from sqlalchemy import event

from stardeck import netguard
from stardeck.connectors import (
    MySQLConfig,
    PostgresConfig,
    SourceConnectionError,
    build_engine,
    check_connection,
)
from stardeck.netguard import BlockedAddress, Guard


class Stop(Exception):
    """Raised by a test listener to stop before a real connection is attempted."""


def answers(monkeypatch, *ips: str):
    """Make DNS answer `ips` (in turn, one per lookup)."""
    queue = list(ips)

    def fake(host, port, *a, **kw):
        ip = queue.pop(0) if len(queue) > 1 else queue[0]
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(netguard.socket, "getaddrinfo", fake)


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.0.141", "169.254.169.254", "0.0.0.0",
    "100.64.0.1", "100.100.100.100", "100.101.102.103",  # carrier NAT and Tailscale
    "::1", "fe80::1", "fd00::5", "::ffff:127.0.0.1", "224.0.0.1",
])
def test_private_and_special_addresses_are_blocked(monkeypatch, ip):
    answers(monkeypatch, ip)
    with pytest.raises(BlockedAddress):
        Guard().resolve("db.example.com", 5432)


def test_public_addresses_are_allowed_unless_denied(monkeypatch):
    answers(monkeypatch, "8.8.8.8")
    assert Guard().resolve("db.example.com", 5432) == "8.8.8.8"
    with pytest.raises(BlockedAddress):
        Guard(["8.8.8.0/24"]).resolve("db.example.com", 5432)


@pytest.mark.parametrize("host,database", [
    ("db.example.com,localhost", "d"),     # libpq reads a list of hosts
    ("/var/run/postgresql", "d"),          # a Unix socket
    ("db example.com", "d"),
    ("db.example.com", "host=localhost"),  # a whole connection string in the database name
    ("db.example.com", "postgres://x"),
])
def test_odd_connection_fields_are_refused(host, database):
    with pytest.raises(BlockedAddress):
        Guard().check_fields(host, database, "user")


def test_connections_are_pinned_to_the_checked_address(monkeypatch):
    """The address is checked again on every connect and used directly, so DNS can't swap it."""
    answers(monkeypatch, "93.184.216.34")
    engine = build_engine(PostgresConfig(host="db.example.com", database="d", username="u"), guard=Guard())
    seen = {}

    @event.listens_for(engine, "do_connect")
    def _capture(dialect, record, cargs, cparams):
        seen.update(cparams)
        raise Stop

    with pytest.raises(Stop):
        engine.connect()
    assert seen["hostaddr"] == "93.184.216.34" and seen["host"] == "db.example.com"


def test_dns_rebinding_is_caught_at_connect(monkeypatch):
    """First answer public (passes any up-front check), then private when actually connecting."""
    answers(monkeypatch, "93.184.216.34", "10.0.0.5")
    guard = Guard()
    assert guard.resolve("db.example.com", 3306) == "93.184.216.34"
    with pytest.raises(SourceConnectionError, match="private network"):
        check_connection(MySQLConfig(host="db.example.com", database="d", username="u"), guard=guard)


def test_self_hosted_connections_are_not_restricted(monkeypatch):
    answers(monkeypatch, "127.0.0.1")
    engine = build_engine(PostgresConfig(host="localhost", database="d", username="u"))  # no guard
    seen = {}

    @event.listens_for(engine, "do_connect")
    def _capture(dialect, record, cargs, cparams):
        seen.update(cparams)
        raise Stop

    with pytest.raises(Stop):
        engine.connect()
    assert "hostaddr" not in seen
