import socket

import pytest

from tutanak.netguard import (
    RemoteEndpointBlocked,
    assert_local,
    is_local_host_header,
    is_local_url,
)

_HOSTS = {
    "localhost": ["127.0.0.1", "::1"],
    "api.openai.com": ["104.18.7.192"],
    "evil.example": ["93.184.216.34"],
    # A name that answers with a loopback and a public address must not count as local.
    "rebind.example": ["127.0.0.1", "93.184.216.34"],
}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    """Name resolution without touching the network."""

    def getaddrinfo(host, port, *args, **kwargs):
        if host not in _HOSTS:
            raise socket.gaierror(socket.EAI_NONAME, "name not known")
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))
            for address in _HOSTS[host]
        ]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434/v1",
        "http://localhost:1234",
        "http://[::1]:8080",
        "http://127.0.0.53:8080/v1",
    ],
)
def test_loopback_urls_are_local(url):
    assert is_local_url(url)
    assert_local(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "http://192.168.1.5:11434",
        "http://10.0.0.7/v1",
        "http://0.0.0.0:11434",
        "http://127.0.0.1.evil.example/v1",
        "http://127.0.0.1:11434@evil.example/v1",
        "http://rebind.example:11434",
        "http://does-not-resolve.example",
        "",
        "http://",
        "not a url",
    ],
)
def test_everything_else_is_refused(url):
    assert not is_local_url(url)
    with pytest.raises(RemoteEndpointBlocked):
        assert_local(url)


def test_allow_remote_bypasses_the_check():
    assert_local("https://api.openai.com/v1", allow_remote=True)


def test_the_error_names_the_endpoint_and_the_way_out():
    with pytest.raises(RemoteEndpointBlocked) as blocked:
        assert_local("http://192.168.1.5:11434")
    assert "192.168.1.5" in str(blocked.value)
    assert "allow_remote_llm" in str(blocked.value)


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "127.0.0.1", "localhost", "LOCALHOST:8765", "[::1]:8765"])
def test_loopback_host_headers_are_accepted(host):
    assert is_local_host_header(host)


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "evil.example:8765",
        "127.0.0.1.evil.example:80",
        "localhost.evil.example",
        "192.168.1.5:8765",
        "[2001:db8::1]:8765",
        "",
        None,
    ],
)
def test_other_host_headers_are_rejected(host):
    assert not is_local_host_header(host)
