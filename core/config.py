import json
from pathlib import Path
from typing import Dict, Any

def load_config(config_path: str = "config.json") -> Dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл конфигурации {config_path} не найден.")
    
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)