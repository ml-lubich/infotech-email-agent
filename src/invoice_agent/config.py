"""Externalized configuration with a documented precedence cascade.

Precedence (highest wins last; later entries override earlier ones):

1. Hardcoded defaults (lowest)
2. Global / user config:
   ``${platformdirs.user_config_dir('infotech-email-agent')}/config.toml``
   - macOS:   ``~/Library/Application Support/infotech-email-agent/config.toml``
   - Linux:   ``~/.config/infotech-email-agent/config.toml`` (XDG_CONFIG_HOME)
   - Windows: ``%APPDATA%\\infotech-email-agent\\config.toml``
3. Project config (whichever is found first, in order, walking up
   from the CWD):
   - ``./config/config.toml``         (recommended; visible in repo + Docker mount)
   - ``./config/infotech-email-agent.toml``
   - ``./infotech-email-agent.toml``  (flat keys at top level)
   - ``./pyproject.toml`` table ``[tool.infotech-email-agent]``
4. Environment variables (12-factor; canonical prefix ``INFOTECH_…``;
   legacy ``INVOICE_…`` names are still honored for back-compat).
5. Command-line arguments (highest) — applied by the calling CLI by
   passing explicit kwargs to ``Settings(...)`` after ``load_settings()``.

Secrets (``OPENAI_API_KEY``) come from env or ``.env`` only — never from
TOML files. TOML is for non-secret toggles.

This module is *additive*: existing ``os.getenv("INVOICE_*")`` callsites
keep working unchanged. Use ``get_settings()`` when you want the merged
view; use raw env lookups when you only care about a single knob.
"""

from __future__ import annotations

import logging
import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from platformdirs import user_config_dir
from pydantic import BaseModel, Field, field_validator

from invoice_agent.models import (
    ALLOWED_MODELS,
    DEFAULT_AGENT_MODEL,
    DEFAULT_CRITIC_MODEL,
    DEFAULT_EXTRACT_MODEL,
)

log = logging.getLogger(__name__)

APP_NAME: Final[str] = "infotech-email-agent"
GLOBAL_CONFIG_FILENAME: Final[str] = "config.toml"
PROJECT_CONFIG_FILENAME: Final[str] = "infotech-email-agent.toml"
PYPROJECT_FILENAME: Final[str] = "pyproject.toml"
PYPROJECT_TABLE: Final[str] = "tool.infotech-email-agent"

# Env var names. INFOTECH_* is canonical; INVOICE_* stays as legacy alias
# so existing deployments + tests don't break. Order in each tuple:
# canonical first, legacy second — first non-empty wins.
_ENV_AGENT_MODEL: Final[tuple[str, ...]] = ("INFOTECH_AGENT_MODEL", "INVOICE_AGENT_MODEL")
_ENV_EXTRACT_MODEL: Final[tuple[str, ...]] = ("INFOTECH_EXTRACT_MODEL", "INVOICE_EXTRACT_MODEL")
_ENV_CRITIC_MODEL: Final[tuple[str, ...]] = ("INFOTECH_CRITIC_MODEL", "INVOICE_CRITIC_MODEL")
_ENV_WEB_HOST: Final[tuple[str, ...]] = ("INFOTECH_WEB_HOST", "INVOICE_WEB_HOST")
_ENV_WEB_PORT: Final[tuple[str, ...]] = ("INFOTECH_WEB_PORT", "INVOICE_WEB_PORT")
_ENV_WEB_RUNS_DIR: Final[tuple[str, ...]] = ("INFOTECH_WEB_RUNS_DIR", "INVOICE_WEB_RUNS_DIR")
_ENV_LLM_DISABLED: Final[tuple[str, ...]] = (
    "INFOTECH_PIPELINE_LLM_DISABLED",
    "INVOICE_PIPELINE_LLM_DISABLED",
)


class Settings(BaseModel):
    """Resolved, validated configuration view.

    Models are constrained to the assignment allow-list. Validation runs
    at construction so a bad TOML override aborts startup with a clear
    error rather than silently degrading.
    """

    agent_model: str = DEFAULT_AGENT_MODEL
    extract_model: str = DEFAULT_EXTRACT_MODEL
    critic_model: str = DEFAULT_CRITIC_MODEL
    web_host: str = "127.0.0.1"
    web_port: int = Field(default=8000, ge=1, le=65535)
    web_runs_dir: str | None = None
    llm_disabled: bool = False

    @field_validator("agent_model", "extract_model", "critic_model")
    @classmethod
    def _check_model_allowlist(cls, v: str) -> str:
        if v not in ALLOWED_MODELS:
            raise ValueError(
                f"model {v!r} is not allow-listed; allowed: {sorted(ALLOWED_MODELS)}"
            )
        return v


# --------------------------------------------------------------------- paths


def global_config_path() -> Path:
    """Return the OS-appropriate user config file path (no I/O)."""
    return Path(user_config_dir(APP_NAME)) / GLOBAL_CONFIG_FILENAME


def project_config_paths(start: Path | None = None) -> list[Path]:
    """Return candidate project-config paths in precedence order.

    Walks up from ``start`` (or the CWD). At each level we look for the
    first match in this order:

    1. ``<dir>/config/config.toml``                 (recommended layout)
    2. ``<dir>/config/infotech-email-agent.toml``
    3. ``<dir>/infotech-email-agent.toml``          (flat repo root)
    4. ``<dir>/pyproject.toml`` table ``[tool.infotech-email-agent]``

    Returns the first match wrapped in a single-element list, or [].
    """
    cur = (start or Path.cwd()).resolve()
    for parent in [cur, *cur.parents]:
        in_config_dir = parent / "config"
        for candidate in (
            in_config_dir / GLOBAL_CONFIG_FILENAME,         # config/config.toml
            in_config_dir / PROJECT_CONFIG_FILENAME,        # config/infotech-email-agent.toml
            parent / PROJECT_CONFIG_FILENAME,               # ./infotech-email-agent.toml
        ):
            if candidate.is_file():
                return [candidate]
        pyproj = parent / PYPROJECT_FILENAME
        if pyproj.is_file():
            try:
                data = tomllib.loads(pyproj.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                log.warning("config: skipping unreadable %s: %s", pyproj, exc)
                continue
            if _read_table(data, PYPROJECT_TABLE) is not None:
                return [pyproj]
    return []


# --------------------------------------------------------------------- merge


def _read_table(data: dict[str, Any], dotted: str) -> dict[str, Any] | None:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur if isinstance(cur, dict) else None


def _load_toml_layer(path: Path) -> dict[str, Any]:
    """Load a TOML file. For pyproject.toml return only our table."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("config: skipping unreadable %s: %s", path, exc)
        return {}
    if path.name == PYPROJECT_FILENAME:
        return _read_table(data, PYPROJECT_TABLE) or {}
    # Flat layout: keys at the top of the file. Strip nested tables to
    # avoid surprising the caller with structured config they didn't ask for.
    return {k: v for k, v in data.items() if not isinstance(v, dict)}


def _first_env(*names: str) -> str | None:
    for name in names:
        v = os.environ.get(name)
        if v not in (None, ""):
            return v
    return None


def _env_layer() -> dict[str, Any]:
    layer: dict[str, Any] = {}
    if (v := _first_env(*_ENV_AGENT_MODEL)) is not None:
        layer["agent_model"] = v
    if (v := _first_env(*_ENV_EXTRACT_MODEL)) is not None:
        layer["extract_model"] = v
    if (v := _first_env(*_ENV_CRITIC_MODEL)) is not None:
        layer["critic_model"] = v
    if (v := _first_env(*_ENV_WEB_HOST)) is not None:
        layer["web_host"] = v
    if (v := _first_env(*_ENV_WEB_PORT)) is not None:
        try:
            layer["web_port"] = int(v)
        except ValueError as exc:
            raise ValueError(
                f"web port env override is not an integer: {v!r}"
            ) from exc
    if (v := _first_env(*_ENV_WEB_RUNS_DIR)) is not None:
        layer["web_runs_dir"] = v
    if (v := _first_env(*_ENV_LLM_DISABLED)) is not None:
        layer["llm_disabled"] = v == "1"
    return layer


def load_settings(
    *,
    project_start: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> Settings:
    """Build a ``Settings`` instance by merging all configuration layers.

    Layers are merged in precedence order (lowest → highest):
    defaults → global TOML → project TOML → env → explicit overrides.

    Args:
        project_start: Where to begin searching for project config (defaults
            to CWD). Useful in tests.
        overrides: CLI-supplied kwargs that always win.
    """
    merged: dict[str, Any] = {}

    gpath = global_config_path()
    if gpath.is_file():
        merged.update(_load_toml_layer(gpath))

    for ppath in project_config_paths(project_start):
        merged.update(_load_toml_layer(ppath))

    merged.update(_env_layer())

    if overrides:
        merged.update({k: v for k, v in overrides.items() if v is not None})

    return Settings(**merged)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached view of merged settings.

    Call ``get_settings.cache_clear()`` in tests when you mutate env vars.
    """
    return load_settings()
