from pathlib import Path
import json
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TurnstileHostnameTest(unittest.TestCase):
    def run_php(self, expression):
        script = (
            "require $argv[1] . '/vendor/autoload.php'; "
            f"echo json_encode({expression}, JSON_UNESCAPED_SLASHES);"
        )
        result = subprocess.run(
            ["php", "-r", script, str(ROOT)],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_normalizes_comma_separated_hostnames(self):
        value = self.run_php(
            "app\\service\\TurnstileService::normalizeAllowedHostnames(' APPLE.example.com,login.example.com,apple.example.com ')"
        )
        self.assertEqual(value, "apple.example.com,login.example.com")

    def test_each_configured_hostname_is_allowed(self):
        expression = (
            "["
            "app\\service\\TurnstileService::hostnameAllowed('apple.example.com', 'apple.example.com,login.example.com'),"
            "app\\service\\TurnstileService::hostnameAllowed('LOGIN.EXAMPLE.COM', 'apple.example.com, login.example.com'),"
            "app\\service\\TurnstileService::hostnameAllowed('other.example.com', 'apple.example.com,login.example.com')"
            "]"
        )
        self.assertEqual(self.run_php(expression), [True, True, False])

    def test_invalid_list_never_disables_hostname_check(self):
        expression = (
            "["
            "app\\service\\TurnstileService::hostnameAllowed('anything.example.com', ','),"
            "app\\service\\TurnstileService::hostnameAllowed('anything.example.com', '')"
            "]"
        )
        self.assertEqual(self.run_php(expression), [False, True])
