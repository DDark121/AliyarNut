from pathlib import Path
from typing import Optional 
from pydantic import BaseModel, PostgresDsn
from pydantic_settings import (
    BaseSettings, 
    SettingsConfigDict, 
    YamlConfigSettingsSource
)

BASE_DIR = Path(__file__).parent 
PROJECT_ROOT = BASE_DIR.parent   
YAML_FILE_PATH = BASE_DIR / "settings.yaml"


class ModelSettings(BaseModel):
    name: str = "openai/gpt-4.1"
    base_url: str = "https://openrouter.ai/api/v1"
    temperature: float = 0.0

class BackUpModelSettings(BaseModel):
    name: str = "gpt-4o" 
    temperature: float = 0.0
    base_url: Optional[str] = None 

class PostgresSettings(BaseModel):
    db_uri: PostgresDsn
    max_size: int = 10

class PromptSettings(BaseModel):
    system_prompt_path: Optional[str] = None
    fallback: str = "You are a helpful AI assistant your name masha"
    memory_enabled: bool = True

    @property
    def content(self) -> str:
        if not self.system_prompt_path or not self.system_prompt_path.strip():
            return self.fallback
        full_path = PROJECT_ROOT / self.system_prompt_path
        if not full_path.exists() or not full_path.is_file():
            return self.fallback
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()

class SecuritySettings(BaseModel):

    banned_words_path: Optional[str] = None

    @property
    def banned_keywords(self) -> list[str]:
        """Читает файл и возвращает список слов."""
        if not self.banned_words_path or not self.banned_words_path.strip():
            return []

        full_path = PROJECT_ROOT / self.banned_words_path
        
        if not full_path.exists() or not full_path.is_file():
            print(f"⚠️ Warning: Файл стоп-слов не найден: {full_path}")
            return []

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                return [line.strip().lower() for line in f if line.strip()]
        except Exception as e:
            print(f"❌ Ошибка чтения стоп-слов: {e}")
            return []


class Settings(BaseSettings):

    openrouter_api_key: Optional[str] = None
    openai_api_key: Optional[str] = None
    
    model: ModelSettings = ModelSettings()
    backup_model: BackUpModelSettings = BackUpModelSettings()
    prompts: PromptSettings = PromptSettings()
    security: SecuritySettings = SecuritySettings()
    postgres: PostgresSettings 

    model_config = SettingsConfigDict(
        env_file=".env",           
        env_nested_delimiter="__",  
        extra="ignore"              
    )

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, **kwargs):
        yaml_settings = None
        if YAML_FILE_PATH.exists():
            yaml_settings = YamlConfigSettingsSource(settings_cls, yaml_file=YAML_FILE_PATH)
        sources = [init_settings, env_settings, dotenv_settings]
        if yaml_settings:
            sources.insert(0, yaml_settings) 
        return tuple(sources)

settings = Settings()