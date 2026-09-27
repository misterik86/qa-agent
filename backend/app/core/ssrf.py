"""SSRF protection: validate URL scheme and block resolution to private ranges."""
import ipaddress
import socket
from urllib.parse import urlparse

from app.config import settings

PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
]


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL должен начинаться с http:// или https://")
    if not parsed.hostname:
        raise ValueError("Некорректный URL")
    return url


def _resolve(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    return addresses


def is_private_host(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    for addr in _resolve(host):
        if any(addr in net for net in PRIVATE_NETWORKS):
            return True
    # If DNS fails, fall back to checking whether host itself looks private (IP literal).
    try:
        addr = ipaddress.ip_address(host)
        if any(addr in net for net in PRIVATE_NETWORKS):
            return True
    except ValueError:
        pass
    return False


def assert_public_url(url: str, allow_private: bool | None = None) -> None:
    """Raise ValueError if URL is private and not allowed."""
    validate_url(url)
    allow = settings.ALLOW_PRIVATE_HOSTS if allow_private is None else allow_private
    if allow:
        host = urlparse(url).hostname or ""
        if host not in settings.private_whitelist:
            raise ValueError(f"Хост {host} не входит в whitelist внутренних доменов")
        return
    if is_private_host(url):
        raise ValueError("Доступ к локальным/внутренним адресам запрещён (SSRF-защита)")
