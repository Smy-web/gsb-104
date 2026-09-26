import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()


class Settings(BaseSettings):
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    HUGGINGFACE_TOKEN: str = os.getenv("HUGGINGFACE_TOKEN", "")
    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.example.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    EMERGENCY_ADMIN_EMAIL: str = os.getenv("EMERGENCY_ADMIN_EMAIL", "admin@emergency.gov.cn")
    
    WHISPER_MODEL_SIZE: str = "base"
    SPACY_MODEL: str = "zh_core_web_sm"
    
    class Config:
        env_file = ".env"


settings = Settings()
