import pytest


@pytest.fixture(autouse=True)
def _clean(clean_db: None) -> None:
    """Benchmarks start from empty tables."""
