"""Shared test fixtures."""
import pytest

from app.db import Database


@pytest.fixture
def db(tmp_path):
    """In-memory SQLite database with all tables created."""
    db_path = str(tmp_path / "test.db")
    database = Database(db_path)
    database.connect()
    database.ensure_all_tables()
    yield database
    database.close()


class MockLLMBackend:
    """A mock LLM backend for testing — returns canned responses."""

    def __init__(self, response: str = '{"result": "ok"}'):
        self.response = response
        self.calls: list[dict] = []

    def chat(self, messages, json_schema=None, temperature=None) -> str:
        self.calls.append({
            "messages": messages,
            "json_schema": json_schema,
            "temperature": temperature,
        })
        return self.response


@pytest.fixture
def mock_backend():
    return MockLLMBackend()
