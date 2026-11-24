import pytest
from alembic import command
from alembic.config import Config


@pytest.mark.usefixtures("migrated")
def test_models_match_migrations(alembic_config: Config) -> None:
    """Модели SQLAlchemy и миграция не расходятся: autogenerate не находит отличий."""
    command.check(alembic_config)
