import yaml
from pydantic import BaseModel, Field
from pathlib import Path
from typing import Optional

class ExchangeConfig(BaseModel):
    id: str
    apiKey: str = ""
    secret: str = ""
    taker_fee_percent: Optional[float] = None

class PairsConfig(BaseModel):
    whitelist: list[str] = Field(default_factory=list)
    blacklist: list[str] = Field(default_factory=list)

class Settings(BaseModel):
    mode: str = "paper"
    min_profit_percent: float = 0.5
    scan_interval_seconds: int = 10
    exchanges: list[ExchangeConfig]
    pairs: PairsConfig = Field(default_factory=PairsConfig)

    @classmethod
    def load(cls, path: str = "config.yaml") -> "Settings":
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Файл конфигурации {path} не найден.")
        
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(**data)