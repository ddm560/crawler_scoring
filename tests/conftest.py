import json
import pytest
from pathlib import Path


@pytest.fixture
def default_config():
    config_path = Path(__file__).parent.parent / "scoring_config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)
