import pytest

from app.config import settings
from app.services.broker_credentials import _decrypt, _encrypt


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
