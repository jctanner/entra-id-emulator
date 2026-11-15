"""Exercise startup, persisted signing keys and OIDC metadata without upstreams."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from entra_mock.app import create_app


class StartupTest(unittest.TestCase):
    def test_discovery_and_jwks_survive_restart(self):
        tenant = "00000000-0000-0000-0000-000000000001"
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.yaml"
            config.write_text(
                "server: {admin_password: synthetic-ci-admin, scheme: http}\n"
                "tenants:\n  - id: " + tenant + "\n"
                "    domain: ci.example\n    display_name: CI\n"
            )
            with patch.dict(os.environ, {"ENTRA_MOCK_DB": str(Path(directory) / "state.db")}):
                client = create_app(str(config)).test_client()
                self.assertEqual(client.get("/").status_code, 200)
                discovery = client.get(f"/{tenant}/v2.0/.well-known/openid-configuration")
                self.assertEqual(discovery.status_code, 200)
                self.assertIn("jwks_uri", discovery.json)
                keys = client.get(f"/{tenant}/discovery/v2.0/keys")
                self.assertEqual(keys.status_code, 200)
                self.assertTrue(keys.json["keys"])
                restarted = create_app(str(config)).test_client()
                self.assertEqual(
                    restarted.get(f"/{tenant}/discovery/v2.0/keys").json,
                    keys.json,
                )
