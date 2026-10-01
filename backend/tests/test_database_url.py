import pytest
from durable_agent.db.session import postgres_url


@pytest.mark.parametrize("driver", ["postgresql", "postgresql+psycopg"])
def test_database_url_selects_psycopg_without_losing_connection_options(driver):
    url = postgres_url(f"{driver}://user:password@localhost:5433/test?sslmode=require")
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "password"
    assert url.port == 5433
    assert url.query["sslmode"] == "require"


@pytest.mark.parametrize("url", ["sqlite:///test.db", "postgresql+asyncpg://localhost/test"])
def test_other_database_drivers_are_rejected(url):
    with pytest.raises(ValueError, match="DATABASE_URL"):
        postgres_url(url)
