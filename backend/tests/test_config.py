"""Environment-driven configuration defaults."""
import pytest

from app.config import build_config


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("PUBLIC_BASE_URL", "FLASK_DEBUG", "SESSION_COOKIE_SECURE"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def secure(env, **values) -> bool:
    for name, value in values.items():
        env.setenv(name, value)
    return build_config()["SESSION_COOKIE_SECURE"]


def test_session_cookie_is_secure_when_public_base_url_is_missing(clean_env):
    """A production deploy that forgets PUBLIC_BASE_URL must not send the cookie without Secure."""
    assert secure(clean_env) is True


def test_session_cookie_secure_follows_public_base_url(clean_env):
    assert secure(clean_env, PUBLIC_BASE_URL="https://web-production-b64c0.up.railway.app") is True
    assert secure(clean_env, PUBLIC_BASE_URL="http://ringwise.example.com") is True  # only loopback opts out


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:5001", "http://localhost:5173", "http://[::1]:5001", "http://app.localhost:8080"]
)
def test_session_cookie_not_secure_for_local_http(clean_env, url):
    assert secure(clean_env, PUBLIC_BASE_URL=url) is False


def test_session_cookie_not_secure_in_flask_debug_without_public_url(clean_env):
    assert secure(clean_env, FLASK_DEBUG="1") is False


def test_explicit_session_cookie_secure_wins(clean_env):
    assert secure(clean_env, SESSION_COOKIE_SECURE="false") is False
    assert secure(clean_env, SESSION_COOKIE_SECURE="true", PUBLIC_BASE_URL="http://127.0.0.1:5001") is True
