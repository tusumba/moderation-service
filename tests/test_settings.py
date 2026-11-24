import pytest
from pydantic import ValidationError

from app.settings import Settings


def test_database_url_must_use_asyncpg() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://localhost/tusumba", rabbitmq_url="amqp://localhost")


def test_database_url_is_hidden_in_repr() -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://user:secret@localhost/db",
        rabbitmq_url="amqp://user:secret@localhost/",
    )
    assert "secret" not in repr(settings)
