"""Mail signature tests generate an ephemeral RSA key in memory with OpenSSL."""
from datetime import datetime, timezone
from email.message import EmailMessage
import shutil
import subprocess
import unittest
from unittest.mock import patch

import dkim

from app.mail_recovery import extract_apple_recovery_link, is_apple_security_rollback_confirmation


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

    def spanish(self, body):
        message = EmailMessage()
        message['From'] = 'Apple <noreply@apple.com>'
        message['To'] = 'fixture@example.test'
        message['Date'] = 'Tue, 27 Oct 2026 12:00:00 +0000'
        message.set_content(body, subtype='html')
        return message.as_bytes()

    def test_spanish_rollback_requires_signed_labelled_apple_action(self):
        body = ('Se activó la autenticación de dos factores. '
                '<a href="https://support.apple.com/">Soporte</a>'
                '<a href="https://iforgot.apple.com/fixture-token">'
                'regresar a la configuración de seguridad anterior</a>')
        unsigned = self.spanish(body)
        raw, record = self.sign(unsigned)
        with patch('dkim.get_txt', return_value=record):
            self.assertEqual(self.resolve(raw).url, 'https://iforgot.apple.com/fixture-token')
            self.assertIsNone(self.resolve(unsigned))
            self.assertIsNone(self.resolve(raw.replace(b'fixture-token', b'tampered-token')))
            self.assertIsNone(extract_apple_recovery_link(raw, 'other@example.test', 'security_rollback'))
            # Enrollment notification is not evidence that 2FA was disabled.
            self.assertFalse(is_apple_security_rollback_confirmation(raw, 'fixture@example.test'))
        for body in [
            'Se activó la autenticación de dos factores. <a href="https://support.apple.com/">Soporte</a>',
            '<a href="https://evil.example/">regresar a la configuración de seguridad anterior</a>',
        ]:
            raw, record = self.sign(self.spanish(body))
            with patch('dkim.get_txt', return_value=record):
                self.assertIsNone(self.resolve(raw))

    def test_spanish_disabled_confirmation_requires_authentication_and_recipient(self):
        for text in ['Se desactivó la autenticación de dos factores',
                     'La autenticación de dos factores se ha desactivado',
                     'La autenticación de dos factores está desactivada']:
            unsigned = self.spanish(text)
            raw, record = self.sign(unsigned)
            with patch('dkim.get_txt', return_value=record):
                self.assertTrue(is_apple_security_rollback_confirmation(raw, 'fixture@example.test'))
                self.assertFalse(is_apple_security_rollback_confirmation(raw, 'other@example.test'))
                self.assertFalse(is_apple_security_rollback_confirmation(unsigned, 'fixture@example.test'))
