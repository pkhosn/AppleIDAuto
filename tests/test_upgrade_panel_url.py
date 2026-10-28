"""Upgrade URL policy and authenticated health checks use disposable fixtures only."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import subprocess
import threading
import unittest

import test_upgrade


class UpgradePanelUrlTest(unittest.TestCase):
    setUp = test_upgrade.UpgradeTest.setUp
    git = test_upgrade.UpgradeTest.git

    def set_url(self, url):
        path = self.root / 'automation-server/.env'
        lines = path.read_text().splitlines()
        path.write_text('\n'.join('PANEL_URL=' + url if line.startswith('PANEL_URL=') else line for line in lines))

    def helper(self, action):
        return subprocess.run(
            [test_upgrade.REAL_PHP, '-d', 'display_errors=0', '-d', 'log_errors=0',
             str(self.root / 'scripts/upgrade_helper.php'), str(self.root), action],
            cwd=self.root, env=self.environment, capture_output=True, text=True, timeout=30,
        )

    def start_api(self):
        requests = []
        secret = 'panel-' + 'x' * 40

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                requests.append((self.path, self.headers.get('key') == secret))
                status, payload = self.server.reply
                self.send_response(status)
                if status == 302:
                    self.send_header('Location', '/must-not-receive-key')
                self.end_headers()
                self.wfile.write(payload)

        server = HTTPServer(('127.0.0.1', 0), Handler)
        server.reply = (200, json.dumps({'code': 200, 'version': 3}).encode())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        self.set_url('http://127.0.0.1:' + str(server.server_port))
        return server, requests

    def test_public_https_and_private_http_roots_are_allowed(self):
        for url in ['https://fixture.example.test', 'https://fixture.example.test/',
                    'http://host.docker.internal:18080', 'http://localhost:18080/',
                    'http://127.0.0.1:18080', 'http://[::1]:18080', 'http://10.0.0.1',
                    'http://172.16.0.1', 'http://172.31.255.254', 'http://192.168.0.1']:
            with self.subTest(url=url):
                self.set_url(url)
                result = self.helper('platform')
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_public_http_metadata_and_non_root_urls_are_rejected(self):
        for url in ['http://fixture.example.test', 'http://8.8.8.8', 'http://169.254.169.254',
                    'http://100.100.100.200', 'http://0.0.0.0', 'http://172.15.0.1', 'http://172.32.0.1',
                    'http://192.169.0.1', 'http://host.docker.internal.evil.test',
                    'http://host.docker.internal:18080/api', 'https://fixture.example.test/path',
                    'http://host.docker.internal:18080/?key=fixture', 'https://fixture.example.test/#fragment',
                    'http://user:fixture@host.docker.internal:18080', 'ftp://host.docker.internal',
                    'http://host.docker.internal:0', 'https://fixture.example.test:99999']:
            with self.subTest(url=url):
                self.set_url(url)
                result = self.helper('platform')
                self.assertNotEqual(0, result.returncode)
                self.assertIn('PANEL_URL', result.stderr)
                self.assertNotIn(url, result.stderr)

    def test_internal_health_uses_authenticated_api_without_frontend_routes(self):
        _server, requests = self.start_api()
        before = (self.root / 'automation-server/.env').read_bytes()
        result = self.helper('bootstrap')
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual([('/api/check_api', True)], requests)
        self.assertEqual(before, (self.root / 'automation-server/.env').read_bytes())
        self.assertNotIn('panel-' + 'x' * 40, (self.base / 'events.jsonl').read_text())
        self.assertEqual('', result.stdout)

    def test_redirects_never_forward_the_api_key(self):
        server, requests = self.start_api()
        server.reply = (302, b'')
        result = self.helper('bootstrap')
        self.assertNotEqual(0, result.returncode)
        self.assertEqual([('/api/check_api', True)], requests)
        self.assertIn('内网 PHP API 健康检查失败', result.stderr)
        self.assertNotIn('panel-' + 'x' * 40, result.stdout + result.stderr)

    def test_http_json_and_contract_errors_have_controlled_output(self):
        server, _requests = self.start_api()
        for reply in [(403, b'private-fixture-response'), (200, b'<html>private-fixture-response</html>'),
                      (200, b'{"code":403,"version":3}'), (200, b'{"code":200,"version":2}'),
                      (200, b'[]'), (200, b'x' * 65537)]:
            with self.subTest(status=reply[0], body_length=len(reply[1])):
                server.reply = reply
                result = self.helper('bootstrap')
                self.assertNotEqual(0, result.returncode)
                self.assertIn('内网 PHP API 健康检查失败', result.stderr)
                self.assertNotIn('private-fixture-response', result.stdout + result.stderr)
                self.assertNotIn('panel-' + 'x' * 40, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
