"""Config storage for transmymodels.

Config lives at ~/.transmymodels/config.json and is the single source of truth
for the local server and the CLI. It is loaded fresh on every server request so
that `add` / `remove` / `refresh` take effect without a restart.
"""
from __future__ import annotations

import json
import random
import secrets
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional


CONFIG_DIR = Path.home() / ".transmymodels"
CONFIG_PATH = CONFIG_DIR / "config.json"


def _gen_local_key() -> str:
    return "sk-tmm-" + secrets.token_urlsafe(24)


@dataclass
class ModelMapping:
    upstream_model: str          # the model id as the upstream knows it
    routed_name: str             # the name we expose on localhost


@dataclass
class ApiKey:
    key: str
    weight: int = 1


@dataclass
class Upstream:
    name: str                    # nickname, e.g. "openai", "deepseek"
    base_url: str                # e.g. https://api.openai.com/v1
    api_keys: List[ApiKey] = field(default_factory=list)
    models: List[ModelMapping] = field(default_factory=list)

    def pick_key(self) -> str:
        """Weighted-random pick among this upstream's keys."""
        if not self.api_keys:
            raise RuntimeError(f"upstream '{self.name}' has no API keys configured")
        keys = [k.key for k in self.api_keys]
        weights = [max(1, int(k.weight)) for k in self.api_keys]
        return random.choices(keys, weights=weights, k=1)[0]


@dataclass
class LocalConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    api_key: str = field(default_factory=_gen_local_key)


@dataclass
class Config:
    local: LocalConfig = field(default_factory=LocalConfig)
    upstreams: List[Upstream] = field(default_factory=list)


# ---------- (de)serialization ----------

def _upstream_from_dict(d: dict) -> Upstream:
    # Backward compat: old single-key config uses "api_key".
    raw_keys = d.get("api_keys")
    if not raw_keys:
        single = d.get("api_key")
        raw_keys = [{"key": single, "weight": 1}] if single else []
    api_keys = [
        ApiKey(key=k["key"], weight=int(k.get("weight", 1)))
        for k in raw_keys
    ]
    return Upstream(
        name=d["name"],
        base_url=d["base_url"].rstrip("/"),
        api_keys=api_keys,
        models=[ModelMapping(**m) for m in d.get("models", [])],
    )


def load_config() -> Config:
    if not CONFIG_PATH.exists():
        cfg = Config()
        save_config(cfg)
        return cfg
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    local = LocalConfig(**raw.get("local", {}))
    if not local.api_key:
        local.api_key = _gen_local_key()
    upstreams = [_upstream_from_dict(u) for u in raw.get("upstreams", [])]
    return Config(local=local, upstreams=upstreams)


def save_config(cfg: Config) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "local": asdict(cfg.local),
        "upstreams": [
            {
                "name": u.name,
                "base_url": u.base_url,
                "api_keys": [asdict(k) for k in u.api_keys],
                "models": [asdict(m) for m in u.models],
            }
            for u in cfg.upstreams
        ],
    }
    CONFIG_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


# ---------- helpers ----------

def build_routing_table(cfg: Config) -> Dict[str, Upstream]:
    table: Dict[str, Upstream] = {}
    for u in cfg.upstreams:
        for m in u.models:
            table[m.routed_name] = u
    return table


def find_upstream(cfg: Config, name: str) -> Optional[Upstream]:
    for u in cfg.upstreams:
        if u.name == name:
            return u
    return None
