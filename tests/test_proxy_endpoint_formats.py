import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProxyEndpointFormatTest(unittest.TestCase):
    def test_php_save_path_canonicalizes_supported_formats(self):
        cases = {}
        for password in ("secret", "1234"):
            expected = f"user:{password}@proxy.test:8080"
            for endpoint in (
                f"proxy.test:8080:user:{password}",
                f"user:{password}:proxy.test:8080",
                f"user:{password}@proxy.test:8080",
                f"proxy.test:8080@user:{password}",
            ):
                cases[endpoint] = expected
        code = r'''
require "vendor/autoload.php";
$cases = json_decode($argv[1], true);
$result = [];
foreach ($cases as $endpoint => $_) {
    $result[$endpoint] = app\service\ProxyService::canonicalizeEndpoint($endpoint);
}
echo json_encode($result, JSON_UNESCAPED_SLASHES);
'''
        completed = subprocess.run(
            ["php", "-r", code, json.dumps(cases)], cwd=ROOT,
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(cases, json.loads(completed.stdout))


if __name__ == "__main__":
    unittest.main()
