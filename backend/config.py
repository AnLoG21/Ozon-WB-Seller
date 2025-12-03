from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    DEBUG: bool = True
    ENV: str = "sandbox"
    
    OZON_CLIENT_ID: str = ""
    OZON_API_KEY: str = ""
    WB_API_KEY: str = ""
    
    BACKEND_HOST: str = "127.0.0.1"
    BACKEND_PORT: int = 8000
    BACKEND_RELOAD: bool = False
    
    CORS_ORIGINS: List[str] = ["*"]
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
