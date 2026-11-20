import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.make_synthetic_data import generate  # noqa: E402


@pytest.fixture(scope="session")
def synth():
    return generate(n_days=700, seed=11)
