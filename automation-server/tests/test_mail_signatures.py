"""Mail signature tests generate an ephemeral RSA key in memory with OpenSSL."""
from datetime import datetime, timezone
from email.message import EmailMessage
import shutil
import subprocess
import unittest
from unittest.mock import patch

import dkim

from app.mail_recovery import extract_apple_recovery_link


class MailSignatureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        openssl = shutil.which('openssl')
        if not openssl:
            raise unittest.SkipTest('OpenSSL is required for mail signature tests')
        cls.private_key = subprocess.run(
            [openssl, 'genrsa', '-traditional', '2048'],
            check=True, capture_output=True, timeout=30,
        ).stdout
        cls.addClassCleanup(delattr, cls, 'private_key')
        public_pem = subprocess.run(
            [openssl, 'rsa', '-pubout'], input=cls.private_key,
            check=True, capture_output=True, timeout=30,
        ).stdout
        cls.public_key = b''.join(public_pem.splitlines()[1:-1])
        cls.addClassCleanup(delattr, cls, 'public_key')

    def raw(self):
        message = EmailMessage()
        message['From'] = 'Apple <appleid@apple.com>'
        message['To'] = 'fixture@example.test'
        message['Date'] = 'Tue, 27 Oct 2026 12:00:00 +0000'
        message['Authentication-Results'] = 'untrusted.example; dkim=pass header.d=attacker.example; dmarc=fail header.from=apple.com'
        message.set_content('<a href="https://iforgot.apple.com/fixture-token">Restore previous security settings</a>', subtype='html')
        return message.as_bytes()

    def sign(self, raw, domain=b'apple.com'):
        signature = dkim.sign(raw, b'fixture', domain, self.private_key, include_headers=[b'from', b'to', b'date', b'content-type'])
        return signature+raw, b'v=DKIM1; k=rsa; p='+self.public_key

    def resolve(self, raw):
        return extract_apple_recovery_link(raw, 'fixture@example.test', 'security_rollback',
            datetime(2026, 10, 27, tzinfo=timezone.utc))

    def test_unsigned_forged_authentication_results_are_rejected(self):
        self.assertIsNone(self.resolve(self.raw()))

    def test_valid_apple_signature_is_required_and_tampering_is_rejected(self):
        raw, record = self.sign(self.raw())
        with patch('dkim.get_txt', return_value=record):
            self.assertIsNotNone(self.resolve(raw))
            self.assertIsNone(self.resolve(raw.replace(b'fixture-token', b'tampered-token')))

    def test_other_domain_signature_cannot_authenticate_spoofed_apple_sender(self):
        raw, record = self.sign(self.raw(), b'attacker.example')
        with patch('dkim.get_txt', return_value=record):
            self.assertIsNone(self.resolve(raw))

    def test_dns_failure_stops_recovery(self):
        raw, _ = self.sign(self.raw())
        with patch('dkim.get_txt', side_effect=TimeoutError('fixture DNS outage')):
            self.assertIsNone(self.resolve(raw))
