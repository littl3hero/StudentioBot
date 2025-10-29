from pydantic_settings import BaseSettings
from typing import List
import os

class Settings(BaseSettings):
    OPENAI_API_KEY: str
    ALLOWED_ORIGINS: str = "http://localhost:3000"
    DATABASE_URL: str

    @property
    def origins(self) -> List[str]:
        raw = self.ALLOWED_ORIGINS.strip()
        return [o.strip() for o in raw.split(",") if o.strip()]

settings = Settings()