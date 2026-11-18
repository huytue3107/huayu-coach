"""Runtime configuration. Everything comes from HUAYU_* environment variables or a .env file."""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
LOCALES = ("vi", "zh-TW", "en")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HUAYU_", env_file=".env", extra="ignore", env_ignore_empty=True)

    env: Literal["dev", "prod", "test"] = "dev"
    secret_key: SecretStr | None = None
    data_dir: Path = ROOT / "data"
    database_url: str | None = None
    base_url: str = "http://localhost:8000"
    default_locale: Literal["vi", "zh-TW", "en"] = "vi"
    timezone: str = "Asia/Taipei"
    trusted_proxy: bool = False  # behind Caddy/nginx: trust X-Forwarded-For for rate limiting

    # access control
    registration_open: bool = True
    invite_code: str | None = None
    min_password_length: int = 8

    # learning content
    allow_unreviewed_questions: bool | None = None  # default: True in dev/test, False in prod

    # billing (everyone is free while billing_enabled is false)
    billing_enabled: bool = False
    trial_days: int = 14
    payment_instructions: str = ""
    support_email: str = ""
    operator_name: str = ""      # shown on /privacy and /terms; when empty a visible placeholder is shown
    operator_address: str = ""
    operator_tax_id: str = ""     # 統一編號

    # optional AI explanations
    llm_provider: Literal["none", "openai", "anthropic"] = "none"
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None  # OpenAI-compatible endpoint override
    llm_timeout_s: float = 8.0

    # optional SMTP (otherwise mail is written to data/outbox)
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"  # 587 -> starttls, 465 -> ssl, "none" only for a trusted local relay
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None

    @model_validator(mode="after")
    def _finalize(self) -> "Settings":
        self.data_dir = Path(self.data_dir)
        if self.allow_unreviewed_questions is None:
            self.allow_unreviewed_questions = self.env != "prod"
        if self.env == "prod":
            key = self.secret_key.get_secret_value() if self.secret_key else ""
            if len(key) < 32:
                raise ValueError("HUAYU_SECRET_KEY must be set to at least 32 characters when HUAYU_ENV=prod")
            from urllib.parse import urlparse
            parsed = urlparse(self.base_url)
            import re
            if (parsed.scheme != "https" or not parsed.hostname or "://" in (parsed.netloc + parsed.path)
                    or not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname)):
                raise ValueError("HUAYU_BASE_URL must look like https://your.domain (no second scheme, no spaces) when HUAYU_ENV=prod")
        if self.llm_provider != "none" and not (self.llm_api_key and self.llm_model):
            raise ValueError("HUAYU_LLM_PROVIDER needs both HUAYU_LLM_API_KEY and HUAYU_LLM_MODEL")
        return self

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'huayu.db'}"

    @property
    def cookie_secure(self) -> bool:
        return self.env == "prod"

    @property
    def secret(self) -> str:
        """Signing key. In dev one is generated once and kept in data/secret_key.txt."""
        if self.secret_key:
            return self.secret_key.get_secret_value()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / "secret_key.txt"
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(secrets.token_urlsafe(48))
        return path.read_text().strip()


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        # Print the field names and messages only: pydantic's default dump echoes raw values, which may be secrets.
        problems = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'settings'}: {e['msg']}" for e in exc.errors())
        raise SystemExit(f"Invalid configuration: {problems}") from None
