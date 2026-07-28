import base64
import json

import pytest

from app.config import settings
from app.services.broker_credentials import _decrypt, _encrypt, extract_broker_account_id


def test_broker_credentials_are_encrypted_at_rest(monkeypatch):
    monkeypatch.setattr(settings, "broker_credentials_key", "k" * 64)
    ciphertext = _encrypt("sensitive-longbridge-token")
    assert "sensitive-longbridge-token" not in ciphertext
    assert _decrypt(ciphertext) == "sensitive-longbridge-token"


def test_broker_credentials_reject_wrong_encryption_key(monkeypatch):
    monkeypatch.setattr(settings, "broker_credentials_key", "a" * 64)
    ciphertext = _encrypt("sensitive-longbridge-token")
    monkeypatch.setattr(settings, "broker_credentials_key", "b" * 64)
    with pytest.raises(RuntimeError, match="cannot be decrypted"):
        _decrypt(ciphertext)


def test_extracts_longbridge_aaid_from_legacy_token():
    payload = base64.urlsafe_b64encode(json.dumps({"aaid": 20129403}).encode()).decode().rstrip("=")
    token = f"hk_m_header.{payload}.signature"
    assert extract_broker_account_id(token) == "20129403"


def test_rejects_token_without_aaid():
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "access_token"}).encode()).decode().rstrip("=")
    with pytest.raises(RuntimeError, match="AAID"):
        extract_broker_account_id(f"header.{payload}.signature")
