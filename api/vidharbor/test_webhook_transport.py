import socket
from unittest.mock import patch

from django.test import SimpleTestCase

from .webhook_transport import PinnedHTTPSConnection, public_address


class WebhookAddressTests(SimpleTestCase):
    def dns(self, addresses):
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
                for ip in addresses]

    def test_any_private_dns_answer_blocks_the_destination(self):
        for ip in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "192.168.1.1"):
            with self.subTest(ip=ip), patch("vidharbor.webhook_transport.socket.getaddrinfo", return_value=self.dns(["8.8.8.8", ip])):
                with self.assertRaisesRegex(OSError, "webhook_nonpublic_address"):
                    public_address("allowed.example", 443)

    def test_tcp_uses_checked_ip_while_hostname_remains_for_tls(self):
        connection = PinnedHTTPSConnection("allowed.example", timeout=5)
        with patch("vidharbor.webhook_transport.public_address", return_value="8.8.8.8"), \
             patch("http.client.HTTPSConnection.connect") as connect:
            connection.connect()
        with patch("vidharbor.webhook_transport.socket.create_connection") as create:
            connection._create_connection(("allowed.example", 443), 5, None)
            create.assert_called_once_with(("8.8.8.8", 443), 5, None)
        self.assertEqual(connection.host, "allowed.example")

    def test_dns_timeout_is_bounded(self):
        import threading
        event = threading.Event()
        def stalled(*args, **kwargs):
            event.wait(1)
            return self.dns(["8.8.8.8"])
        with patch("vidharbor.webhook_transport.socket.getaddrinfo", side_effect=stalled):
            try:
                with self.assertRaisesRegex(OSError, "webhook_dns_timeout"):
                    public_address("allowed.example", 443, timeout=0.01)
            finally:
                event.set()
