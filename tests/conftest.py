from pathlib import Path

import pytest

from alert_tuning.data import load

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "data"


@pytest.fixture(scope="session")
def ds():
    return load(EXAMPLE)


@pytest.fixture(scope="session")
def example_dir():
    return EXAMPLE
