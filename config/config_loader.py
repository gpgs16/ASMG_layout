import os
from pathlib import Path

import yaml
from dotenv import load_dotenv


class ConfigError(Exception):
    pass


class Config:
    def __init__(self, config_path=None, env_path=None):
        # Load .env for sensitive values
        if env_path is None:
            env_path = Path(__file__).parent.parent / ".env"
        if Path(env_path).exists():
            load_dotenv(dotenv_path=env_path)
        # Load YAML config
        if config_path is None:
            config_path = Path(__file__).parent / "config.yaml"
        with open(config_path, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f)
        self._validate()

    def _validate(self):
        # Example: check required fields
        required_sections = ["plant_simulation", "cmsd_xml", "simtalk", "logging"]
        for section in required_sections:
            if section not in self._config:
                raise ConfigError(f"Missing required config section: {section}")

    # GEMINI_API_KEY is optional for this deployment; do not raise if missing.

    @property
    def plant_simulation(self):
        return self._config["plant_simulation"]

    @property
    def cmsd_xml(self):
        return self._config["cmsd_xml"]

    @property
    def simtalk(self):
        return self._config["simtalk"]

    @property
    def logging(self):
        return self._config["logging"]

    @property
    def gemini_api_key(self):
        return os.getenv("GEMINI_API_KEY")


# Usage:
# config = Config()
# print(config.plant_simulation["prog_id"])
# print(config.gemini_api_key)
