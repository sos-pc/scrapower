"""Configuration management — TOML file + environment variables."""

from __future__ import annotations

import os

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Config:
    """Root configuration loaded from TOML and overridden by env vars."""

    # Server
    host: str = "127.0.0.1"
    port: int = 8777

    # Data
    data_dir: str = "data"
    blob_dir: str = "data/blobs"
    db_path: str = "data/scrapower.db"

    # Limits
    max_blob_size_mb: int = 50
    max_task_retries: int = 3
    blob_ttl_days: int = 7
    checkpoint_ttl_days: int = 30

    # Worker gateway
    heartbeat_interval_sec: int = 10
    heartbeat_miss_threshold: int = 3
    task_accept_timeout_sec: int = 5
    scheduler_tick_sec: float = 5.0

    # Security
    enforce_segregation: bool = False
    max_anonymous_workers: int = 100
    pull_rate_limit_per_ip: int = 12  # per minute

    # Keepalive
    keepalive_enabled: bool = True
    keepalive_duration_sec: int = 2

    # Verification
    default_verification_mode: str = "trust"  # "trust" (no check) | "challenge" (10% double-exec) | "redundant" (100% double-exec)

    # Logging
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        self._apply_env_overrides()

    def _apply_env_overrides(self) -> None:
        """Override config values from SCRAPOWER_* environment variables."""
        env_map: dict[str, tuple[str, type[Any]]] = {
            "SCRAPOWER_HOST": ("host", str),
            "SCRAPOWER_PORT": ("port", int),
            "SCRAPOWER_DATA_DIR": ("data_dir", str),
            "SCRAPOWER_DB_PATH": ("db_path", str),
            "SCRAPOWER_MAX_BLOB_SIZE_MB": ("max_blob_size_mb", int),
            "SCRAPOWER_MAX_TASK_RETRIES": ("max_task_retries", int),
            "SCRAPOWER_BLOB_TTL_DAYS": ("blob_ttl_days", int),
            "SCRAPOWER_LOG_LEVEL": ("log_level", str),
            "SCRAPOWER_ENFORCE_SEGREGATION": ("enforce_segregation", bool),
        }
        for env_var, (attr, typ) in env_map.items():
            val = os.environ.get(env_var)
            if val is not None:
                if typ is bool:
                    setattr(self, attr, val.lower() in ("1", "true", "yes"))
                else:
                    setattr(self, attr, typ(val))


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from TOML file, then apply env overrides.

    Looks for config in this order:
    1. Explicit path argument
    2. SCRAPOWER_CONFIG env var
    3. config/coordinator.toml (relative to project root)
    4. Defaults only
    """
    config = Config()

    # Determine config file path
    config_path: Path | None = None
    if path:
        config_path = Path(path)
    elif env_path := os.environ.get("SCRAPOWER_CONFIG"):
        config_path = Path(env_path)
    else:
        candidates = [
            Path("config/coordinator.toml"),
            Path("coordinator.toml"),
        ]
        for c in candidates:
            if c.exists():
                config_path = c
                break

    # Load TOML if found
    if config_path and config_path.exists():
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        _apply_toml(config, data)

    # Create data directories
    for d in [config.data_dir, config.blob_dir]:
        Path(d).mkdir(parents=True, exist_ok=True)

    return config


def _apply_toml(config: Config, data: dict[str, Any]) -> None:
    """Merge TOML data into Config dataclass."""
    if "server" in data:
        s = data["server"]
        config.host = s.get("host", config.host)
        config.port = s.get("port", config.port)

    if "paths" in data:
        p = data["paths"]
        if "data_dir" in p:
            config.data_dir = p["data_dir"]
            config.blob_dir = f"{config.data_dir}/blobs"
            config.db_path = f"{config.data_dir}/scrapower.db"

    if "limits" in data:
        limits = data["limits"]
        config.max_blob_size_mb = limits.get("max_blob_size_mb", config.max_blob_size_mb)
        config.max_task_retries = limits.get("max_task_retries", config.max_task_retries)
        config.blob_ttl_days = limits.get("blob_ttl_days", config.blob_ttl_days)
        config.checkpoint_ttl_days = limits.get("checkpoint_ttl_days", config.checkpoint_ttl_days)

    if "worker_gateway" in data:
        wg = data["worker_gateway"]
        config.heartbeat_interval_sec = wg.get(
            "heartbeat_interval_sec", config.heartbeat_interval_sec
        )
        config.heartbeat_miss_threshold = wg.get(
            "heartbeat_miss_threshold", config.heartbeat_miss_threshold
        )
        config.task_accept_timeout_sec = wg.get(
            "task_accept_timeout_sec", config.task_accept_timeout_sec
        )
        config.scheduler_tick_sec = wg.get("scheduler_tick_sec", config.scheduler_tick_sec)

    if "security" in data:
        sec = data["security"]
        config.enforce_segregation = sec.get("enforce_segregation", config.enforce_segregation)
        config.max_anonymous_workers = sec.get(
            "max_anonymous_workers", config.max_anonymous_workers
        )
        config.pull_rate_limit_per_ip = sec.get(
            "pull_rate_limit_per_ip", config.pull_rate_limit_per_ip
        )

    if "keepalive" in data:
        ka = data["keepalive"]
        config.keepalive_enabled = ka.get("enabled", config.keepalive_enabled)
        config.keepalive_duration_sec = ka.get("duration_sec", config.keepalive_duration_sec)

    if "verification" in data:
        v = data["verification"]
        config.default_verification_mode = v.get("default_mode", config.default_verification_mode)

    if "logging" in data:
        config.log_level = data["logging"].get("level", config.log_level)
