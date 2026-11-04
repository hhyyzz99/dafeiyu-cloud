"""Run our pinned Certbot over IPv4 without changing system networking.

The target server's default ACME route timed out while IPv4 completed renewal.
Only this short-lived process changes urllib3's address-family selection; TLS
verification, hostname checks and Certbot's normal renewal logic stay enabled.
"""
import socket

import urllib3.util.connection
from certbot.main import main


def run():
    urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET
    return main()


if __name__ == "__main__":
    raise SystemExit(run())
