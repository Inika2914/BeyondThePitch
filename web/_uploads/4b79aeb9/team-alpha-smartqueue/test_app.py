"""Minimal smoke tests for the queue endpoints."""
import pytest
from app import app, init_db
import os


@pytest.fixture
def client():
    if os.path.exists("queue.db"):
        os.remove("queue.db")
    init_db()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_checkin_requires_phone(client):
    resp = client.post("/checkin", data={})
    assert resp.status_code == 400


def test_checkin_success(client):
    resp = client.post("/checkin", data={"phone": "+15551234567"})
    assert resp.status_code == 201
