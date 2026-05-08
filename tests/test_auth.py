"""
测试 auth.py：JWT 签发/校验/过期、bcrypt 哈希校验、SECRET_KEY 机制。
"""
import sys
import time
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent / "app"
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

import pytest
from jose import jwt

import auth as auth_module
from auth import (
    create_access_token,
    hash_password,
    init_password_hash,
    init_secret_key,
    is_weak_default_password,
    verify_password,
    ALGORITHM,
)


@pytest.fixture(autouse=True)
def reset_auth_state():
    """每个测试前后重置模块级状态，避免测试互相干扰。"""
    init_secret_key("test-secret-key-for-unit-tests")
    init_password_hash(None)
    yield
    init_secret_key("test-secret-key-for-unit-tests")
    init_password_hash(None)


class TestVerifyPassword:
    def test_correct_env_password(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "mySecurePass")
        init_password_hash(None)
        assert verify_password("mySecurePass") is True

    def test_wrong_env_password(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "mySecurePass")
        init_password_hash(None)
        assert verify_password("wrongPass") is False

    def test_bcrypt_hash_takes_priority(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "envPass")
        new_hash = hash_password("hashPass")
        init_password_hash(new_hash)
        assert verify_password("hashPass") is True
        assert verify_password("envPass") is False

    def test_hash_password_verifies(self):
        h = hash_password("testPass123")
        init_password_hash(h)
        assert verify_password("testPass123") is True
        assert verify_password("wrongPass") is False


class TestIsWeakDefaultPassword:
    def test_default_admin888_is_weak(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "admin888")
        init_password_hash(None)
        assert is_weak_default_password() is True

    def test_strong_password_not_weak(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "StrongPass!123")
        init_password_hash(None)
        assert is_weak_default_password() is False

    def test_bcrypt_hash_set_not_weak(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "admin888")
        init_password_hash(hash_password("admin888"))
        assert is_weak_default_password() is False


class TestCreateAccessToken:
    def test_token_contains_role(self):
        token = create_access_token({"role": "admin"})
        payload = jwt.decode(token, "test-secret-key-for-unit-tests", algorithms=[ALGORITHM])
        assert payload.get("role") == "admin"

    def test_token_contains_exp(self):
        token = create_access_token({"role": "admin"})
        payload = jwt.decode(token, "test-secret-key-for-unit-tests", algorithms=[ALGORITHM])
        assert "exp" in payload

    def test_token_with_wrong_secret_fails(self):
        from jose import JWTError
        token = create_access_token({"role": "admin"})
        with pytest.raises(JWTError):
            jwt.decode(token, "wrong-secret", algorithms=[ALGORITHM])

    def test_secret_key_change_invalidates_token(self):
        from jose import JWTError
        token = create_access_token({"role": "admin"})
        init_secret_key("new-secret-key")
        with pytest.raises(JWTError):
            jwt.decode(token, "test-secret-key-for-unit-tests", algorithms=[ALGORITHM])
