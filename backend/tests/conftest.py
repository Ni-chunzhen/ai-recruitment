from cryptography.fernet import Fernet

from app.core.config import get_settings


def pytest_configure() -> None:
    import os

    os.environ.setdefault("DATA_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    get_settings.cache_clear()
