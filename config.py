from typing import Any, Callable, Set
from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
import pathlib
import os

# env_name = os.getenv("ACTIVE_ENVIRONMENT")
env_name = "dev"


class Settings(BaseSettings):
    WEBAPP: str
    PORT: int
    SOCKET_PORT: int
    DEBUG: bool
    DYNAMODB: str
    DYNAMODB_REGION: str
    LAMBDA_REGION: str
    LOCAL_ENV: bool
    DB_API: AnyHttpUrl

    class Config:
        env_file = pathlib.Path.joinpath(pathlib.Path(__file__).resolve().parents[0], f"../ocpp-backend/config/{env_name}.env" )


if __name__ == "__main__":
    settings = Settings()
