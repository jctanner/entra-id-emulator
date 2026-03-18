"""Load and validate YAML configuration."""

import os
import yaml


DEFAULTS = {
    "server": {
        "host": "0.0.0.0",
        "port": 8080,
        "scheme": "http",
        "external_hostname": "localhost:8080",
    },
    "token_lifetimes": {
        "access_token_seconds": 3600,
        "id_token_seconds": 3600,
        "refresh_token_days": 90,
        "auth_code_seconds": 60,
    },
}


def load_config(path=None):
    """Load config from YAML file, merging with defaults.

    Looks for path in order:
    1. Explicit path argument
    2. ENTRA_MOCK_CONFIG environment variable
    3. config.yaml in current working directory
    """
    if path is None:
        path = os.environ.get("ENTRA_MOCK_CONFIG", "config.yaml")

    with open(path, "r") as f:
        cfg = yaml.safe_load(f)

    # Merge defaults for top-level sections
    for section, defaults in DEFAULTS.items():
        if section not in cfg:
            cfg[section] = {}
        for key, value in defaults.items():
            cfg[section].setdefault(key, value)

    # Ensure required sections exist
    cfg.setdefault("tenants", [])
    cfg.setdefault("users", [])
    cfg.setdefault("clients", [])

    return cfg
