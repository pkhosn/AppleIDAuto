from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SecurityHardeningTest(unittest.TestCase):
    def text(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_share_html_sanitizer_removes_active_content(self):
        code = r'''require "vendor/autoload.php";
$s=new app\service\HtmlSanitizerService(new think\App());
echo $s->sanitize('<p onclick="x()">ok<script>alert(1)</script><a href="javascript:x()">x</a><img src="https://safe.example/a.png" onerror="x()"></p>');'''
        result = subprocess.run(["php", "-r", code], cwd=ROOT, check=True, text=True, capture_output=True).stdout
        self.assertEqual('<p>ok<a>x</a><img src="https://safe.example/a.png"></p>', result)

    def test_share_api_and_public_internal_boundary_are_explicit(self):
        routes = self.text("route/app.php")
        api = self.text("app/controller/ApiController.php")
        self.assertIn("shareapi/v2/resolve", routes)
        self.assertIn("shareAccessService->resolve", api)
        self.assertNotIn("backend.token", api)

    def test_nested_unknown_tags_are_sanitized_recursively_and_idempotently(self):
        code = r'''require "vendor/autoload.php";
$s=new app\service\HtmlSanitizerService(new think\App());
$once=$s->sanitize('<x-a><x-b><p onclick="x()">ok<script>bad()</script><meta http-equiv="refresh" content="0;url=https://example.test"><img src="https://safe.example/a.png" onerror="x()"><form><input></form></p></x-b></x-a>');
echo json_encode([$once,$s->sanitize($once)]);'''
        import json
        result = json.loads(subprocess.run(["php", "-r", code], cwd=ROOT, check=True,
                                           text=True, capture_output=True).stdout)
        self.assertEqual(result[0], result[1])
        for unsafe in ("onclick", "onerror", "<script", "<meta", "<form", "<input", "<x-"):
            self.assertNotIn(unsafe, result[0])

    def test_sessions_randomness_and_credentials_are_hardened(self):
        cookie = self.text("config/cookie.php")
        common = self.text("app/common.php")
        frontend = self.text("app/controller/FrontendApiController.php")
        self.assertIn("'secure'    => true", cookie)
        self.assertIn("'httponly'  => true", cookie)
        self.assertIn("'samesite'  => 'lax'", cookie)
        self.assertIn("random_int", common)
        self.assertNotIn("$safe['current_password']", frontend)
        self.assertIn("revealAccountPassword", frontend)

    def test_framework_is_outside_known_6_1_4_range(self):
        lock = self.text("composer.lock")
        self.assertIn('"name": "topthink/framework"', lock)
        self.assertIn('"version": "v8.1.4"', lock)


if __name__ == "__main__":
    unittest.main()
