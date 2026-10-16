import pytest


@pytest.fixture(autouse=True)
def _clean(clean_db: None) -> None:
    """Every test in this directory starts from empty tables."""
