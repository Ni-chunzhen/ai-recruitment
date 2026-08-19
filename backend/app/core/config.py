from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "ai-recruitment-api"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = "development"

    database_url_secret: SecretStr = Field(
        validation_alias="DATABASE_URL",
        default=SecretStr(
            "postgresql+asyncpg://recruit:change-me@127.0.0.1:5432/recruit"
        ),
    )
    redis_url_secret: SecretStr = Field(
        validation_alias="REDIS_URL",
        default=SecretStr("redis://127.0.0.1:6379/0"),
    )

    jwt_secret_secret: SecretStr = Field(
        validation_alias="JWT_SECRET",
        default=SecretStr("change-me-jwt-secret"),
    )
    ACCESS_TOKEN_MINUTES: int = 15
    REFRESH_TOKEN_DAYS: int = 7
    REFRESH_COOKIE_NAME: str = "refresh_token"
    REFRESH_COOKIE_PATH: str = "/api/v1/auth"
    REFRESH_COOKIE_SAMESITE: str = "lax"
    REFRESH_COOKIE_SECURE: bool = False
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    CELERY_BROKER_URL: str = ""
    AI_PROVIDER: str = "mock"
    DIFY_API_BASE_URL: str = ""
    dify_api_key_secret: SecretStr = Field(
        validation_alias="DIFY_API_KEY",
        default=SecretStr(""),
    )
    # 各工作流对应 Dify「应用」自己的 API Key；未配置时回退 DIFY_API_KEY
    dify_jd_parse_api_key_secret: SecretStr = Field(
        validation_alias="DIFY_JD_PARSE_API_KEY",
        default=SecretStr(""),
    )
    dify_score_dimension_api_key_secret: SecretStr = Field(
        validation_alias="DIFY_SCORE_DIMENSION_API_KEY",
        default=SecretStr(""),
    )
    DIFY_JD_PARSE_WORKFLOW_ID: str = ""
    DIFY_SCORE_DIMENSION_WORKFLOW_ID: str = ""
    # 简历解析 / 多维评分：未配置时 mock 可用；接 Dify 时需单独提供
    dify_resume_parse_api_key_secret: SecretStr = Field(
        validation_alias="DIFY_RESUME_PARSE_API_KEY",
        default=SecretStr(""),
    )
    dify_resume_score_api_key_secret: SecretStr = Field(
        validation_alias="DIFY_RESUME_SCORE_API_KEY",
        default=SecretStr(""),
    )
    DIFY_RESUME_PARSE_WORKFLOW_ID: str = ""
    DIFY_RESUME_SCORE_WORKFLOW_ID: str = ""
    dify_interview_question_generate_api_key_secret: SecretStr = Field(
        validation_alias="DIFY_INTERVIEW_QUESTION_GENERATE_API_KEY",
        default=SecretStr(""),
    )
    dify_interview_question_generate_workflow_id: str = Field(
        validation_alias="DIFY_INTERVIEW_QUESTION_GENERATE_WORKFLOW_ID",
        default="",
    )
    dify_interview_question_live_enabled: bool = Field(
        validation_alias="DIFY_INTERVIEW_QUESTION_LIVE_ENABLED",
        default=False,
    )
    AI_TASK_TIMEOUT_SECONDS: int = 60
    AI_RAW_PAYLOAD_RETENTION_DAYS: int = 90

    MINIO_ENDPOINT: str = "127.0.0.1:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    minio_secret_key_secret: SecretStr = Field(
        validation_alias="MINIO_SECRET_KEY",
        default=SecretStr("minioadmin"),
    )
    MINIO_BUCKET: str = "resumes"
    MINIO_SECURE: bool = False
    MINIO_PRESIGN_SECONDS: int = 600

    data_encryption_key_secret: SecretStr = Field(
        validation_alias="DATA_ENCRYPTION_KEY",
        default=SecretStr(""),
    )

    RESUME_UPLOAD_MAX_FILES: int = 5
    RESUME_UPLOAD_MAX_BYTES: int = 10 * 1024 * 1024

    @property
    def database_url(self) -> str:
        return self.database_url_secret.get_secret_value()

    @property
    def redis_url(self) -> str:
        return self.redis_url_secret.get_secret_value()

    @property
    def celery_broker_url(self) -> str:
        return self.CELERY_BROKER_URL.strip() or self.redis_url

    @property
    def jwt_secret(self) -> str:
        return self.jwt_secret_secret.get_secret_value()

    @property
    def dify_api_key(self) -> str:
        return self.dify_api_key_secret.get_secret_value()

    def dify_api_key_for(self, task_type: str) -> str:
        """Dify 按应用鉴权：不同工作流需使用对应应用的 API Key。"""
        from app.models.ai_task import (
            TASK_TYPE_INTERVIEW_QUESTION_GENERATE,
            TASK_TYPE_JD_PARSE,
            TASK_TYPE_RESUME_PARSE,
            TASK_TYPE_RESUME_SCORE,
            TASK_TYPE_SCORE_DIMENSION_RECOMMEND,
        )

        if task_type == TASK_TYPE_JD_PARSE:
            specific = self.dify_jd_parse_api_key_secret.get_secret_value().strip()
        elif task_type == TASK_TYPE_SCORE_DIMENSION_RECOMMEND:
            specific = (
                self.dify_score_dimension_api_key_secret.get_secret_value().strip()
            )
        elif task_type == TASK_TYPE_RESUME_PARSE:
            specific = self.dify_resume_parse_api_key_secret.get_secret_value().strip()
        elif task_type == TASK_TYPE_RESUME_SCORE:
            specific = self.dify_resume_score_api_key_secret.get_secret_value().strip()
        elif task_type == TASK_TYPE_INTERVIEW_QUESTION_GENERATE:
            return (
                self.dify_interview_question_generate_api_key_secret.get_secret_value().strip()
            )
        else:
            specific = ""
        return specific or self.dify_api_key

    @property
    def minio_access_key(self) -> str:
        return self.MINIO_ACCESS_KEY

    @property
    def minio_secret_key(self) -> str:
        return self.minio_secret_key_secret.get_secret_value()

    @property
    def data_encryption_key(self) -> str:
        return self.data_encryption_key_secret.get_secret_value()

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.CORS_ORIGINS.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
