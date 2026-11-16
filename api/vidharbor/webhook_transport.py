"""Pin an allowed HTTPS hostname to a checked public address for this connection."""
import ipaddress
import queue
import socket
import threading
from http.client import HTTPSConnection
from urllib.request import HTTPSHandler


def public_address(host, port, timeout=5):
    answer = queue.Queue(maxsize=1)
    def resolve():
        try:
            answer.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError as exc:
            answer.put(exc)
    threading.Thread(target=resolve, daemon=True).start()
    try:
        infos = answer.get(timeout=timeout)
    except queue.Empty as exc:
        raise OSError("webhook_dns_timeout") from exc
    if isinstance(infos, OSError):
        raise infos
    addresses = list(dict.fromkeys(info[4][0] for info in infos))
    if not addresses:
        raise OSError("webhook_dns_empty")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        check = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip
        if not check.is_global:
            raise OSError("webhook_nonpublic_address")
    return next((ip for ip in addresses if ":" not in ip), addresses[0])


class PinnedHTTPSConnection(HTTPSConnection):
    def connect(self):
        address = public_address(self.host, self.port, self.timeout)
        # HTTPSConnection still verifies the original hostname and uses it for SNI.
        # DNS is not consulted a second time by the TCP connection.
        self._create_connection = lambda pair, timeout, source_address: socket.create_connection(
            (address, pair[1]), timeout, source_address)
        return super().connect()


class PinnedHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(PinnedHTTPSConnection, request, context=self._context)
