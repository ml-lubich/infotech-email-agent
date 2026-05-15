"""Tests for the externalized configuration cascade.

Verifies that ``invoice_agent.config.load_settings`` correctly merges:
  defaults → global TOML → project TOML → env → explicit overrides
and that the model allow-list still aborts on bad input.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from invoice_agent import config as config_mod
from invoice_agent.config import (
    GLOBAL_CONFIG_FILENAME,
    PROJECT_CONFIG_FILENAME,
    Settings,
    load_settings,
    project_config_paths,
)


# --------------------------------------------------------------------- defaults


class TestDefaults:
    def test_defaults_when_nothing_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No env vars, no TOML in CWD, no global file.
        for name in [
            "INFOTECH_AGENT_MODEL", "INVOICE_AGENT_MODEL",
            "INFOTECH_EXTRACT_MODEL", "INVOICE_EXTRACT_MODEL",
            "INFOTECH_CRITIC_MODEL", "INVOICE_CRITIC_MODEL",
            "INFOTECH_WEB_HOST", "INVOICE_WEB_HOST",
            "INFOTECH_WEB_PORT", "INVOICE_WEB_PORT",
            "INFOTECH_WEB_RUNS_DIR", "INVOICE_WEB_RUNS_DIR",
            "INFOTECH_PIPELINE_LLM_DISABLED", "INVOICE_PIPELINE_LLM_DISABLED",
        ]:
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(
            config_mod, "global_config_path", lambda: tmp_path / "missing.toml"
        )
        s = load_settings(project_start=tmp_path)
        assert s.web_host == "127.0.0.1"
        assert s.web_port == 8000
        assert s.agent_model == "gpt-5-mini"
        assert s.llm_disabled is False


# --------------------------------------------------------------------- env layer


class TestEnvLayer:
    def test_env_overrides_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("INFOTECH_WEB_PORT", "9001")
        monkeypatch.setenv("INFOTECH_WEB_HOST", "0.0.0.0")
        monkeypatch.setattr(
            config_mod, "global_config_path", lambda: tmp_path / "missing.toml"
        )
        s = load_settings(project_start=tmp_path)
        assert s.web_port == 9001
        assert s.web_host == "0.0.0.0"

    def test_legacy_env_still_works(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("INFOTECH_WEB_PORT", raising=False)
        monkeypatch.setenv("INVOICE_WEB_PORT", "9002")
        monkeypatch.setattr(
            config_mod, "global_config_path", lambda: tmp_path / "missing.toml"
        )
        s = load_settings(project_start=tmp_path)
        assert s.web_port == 9002

    def test_canonical_env_wins_over_legacy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("INFOTECH_WEB_PORT", "9003")
        monkeypatch.setenv("INVOICE_WEB_PORT", "9004")
        monkeypatch.setattr(
            config_mod, "global_config_path", lambda: tmp_path / "missing.toml"
        )
        s = load_settings(project_start=tmp_path)
        assert s.web_port == 9003

    def test_bad_port_env_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("INFOTECH_WEB_PORT", "not-a-number")
        monkeypatch.setattr(
            config_mod, "global_config_path", lambda: tmp_path / "missing.toml"
        )
        with pytest.raises(ValueError, match="not an integer"):
            load_settings(project_start=tmp_path)


# --------------------------------------------------------------------- TOML layers


class TestTomlLayers:
    def test_global_toml_loaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("INFOTECH_WEB_PORT", "INVOICE_WEB_PORT"):
            monkeypatch.delenv(name, raising=False)
        gconf = tmp_path / "global" / GLOBAL_CONFIG_FILENAME
        gconf.parent.mkdir(parents=True)
        gconf.write_text('web_port = 7777\nweb_host = "1.2.3.4"\n', encoding="utf-8")
        monkeypatch.setattr(config_mod, "global_config_path", lambda: gconf)
        s = load_settings(project_start=tmp_path)
        assert s.web_port == 7777
        assert s.web_host == "1.2.3.4"

    def test_project_toml_overrides_global(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("INFOTECH_WEB_PORT", "INVOICE_WEB_PORT"):
            monkeypatch.delenv(name, raising=False)
        gconf = tmp_path / "global" / GLOBAL_CONFIG_FILENAME
        gconf.parent.mkdir(parents=True)
        gconf.write_text("web_port = 7777\n", encoding="utf-8")
        monkeypatch.setattr(config_mod, "global_config_path", lambda: gconf)

        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / PROJECT_CONFIG_FILENAME).write_text(
            "web_port = 8888\n", encoding="utf-8"
        )
        s = load_settings(project_start=proj)
        assert s.web_port == 8888

    def test_env_overrides_project_toml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / PROJECT_CONFIG_FILENAME).write_text(
            "web_port = 8888\n", encoding="utf-8"
        )
        monkeypatch.setenv("INFOTECH_WEB_PORT", "9999")
        monkeypatch.setattr(
            config_mod, "global_config_path", lambda: tmp_path / "missing.toml"
        )
        s = load_settings(project_start=proj)
        assert s.web_port == 9999

    def test_pyproject_table_loaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("INFOTECH_WEB_HOST", "INVOICE_WEB_HOST"):
            monkeypatch.delenv(name, raising=False)
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "pyproject.toml").write_text(
            '[tool.infotech-email-agent]\nweb_host = "10.0.0.5"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            config_mod, "global_config_path", lambda: tmp_path / "missing.toml"
        )
        s = load_settings(project_start=proj)
        assert s.web_host == "10.0.0.5"

    def test_unreadable_toml_skipped_not_raised(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / PROJECT_CONFIG_FILENAME).write_text(
            "this is not = valid toml [[", encoding="utf-8"
        )
        monkeypatch.setattr(
            config_mod, "global_config_path", lambda: tmp_path / "missing.toml"
        )
        # Should fall through to defaults, not crash.
        s = load_settings(project_start=proj)
        assert s.web_port == 8000


# --------------------------------------------------------------------- overrides


class TestOverrides:
    def test_explicit_overrides_win(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("INFOTECH_WEB_PORT", "8001")
        monkeypatch.setattr(
            config_mod, "global_config_path", lambda: tmp_path / "missing.toml"
        )
        s = load_settings(project_start=tmp_path, overrides={"web_port": 4242})
        assert s.web_port == 4242

    def test_none_overrides_are_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("INFOTECH_WEB_PORT", "8002")
        monkeypatch.setattr(
            config_mod, "global_config_path", lambda: tmp_path / "missing.toml"
        )
        s = load_settings(project_start=tmp_path, overrides={"web_port": None})
        assert s.web_port == 8002


# --------------------------------------------------------------------- allow-list


class TestModelAllowList:
    def test_bad_model_in_toml_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in ("INFOTECH_AGENT_MODEL", "INVOICE_AGENT_MODEL"):
            monkeypatch.delenv(name, raising=False)
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / PROJECT_CONFIG_FILENAME).write_text(
            'agent_model = "gpt-9-ultra"\n', encoding="utf-8"
        )
        monkeypatch.setattr(
            config_mod, "global_config_path", lambda: tmp_path / "missing.toml"
        )
        with pytest.raises(ValueError, match="not allow-listed"):
            load_settings(project_start=proj)

    def test_settings_constructor_validates(self) -> None:
        with pytest.raises(ValueError, match="not allow-listed"):
            Settings(extract_model="claude-sonnet")


# --------------------------------------------------------------------- discovery


class TestProjectConfigDiscovery:
    def test_walks_up_to_find_flat_toml(self, tmp_path: Path) -> None:
        (tmp_path / PROJECT_CONFIG_FILENAME).write_text("", encoding="utf-8")
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        paths = project_config_paths(nested)
        assert len(paths) == 1
        assert paths[0].name == PROJECT_CONFIG_FILENAME

    def test_returns_empty_when_no_match(self, tmp_path: Path) -> None:
        # pyproject.toml without our table must not be picked up.
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'unrelated'\n", encoding="utf-8"
        )
        assert project_config_paths(tmp_path) == []

    def test_discovers_config_subfolder(self, tmp_path: Path) -> None:
        # config/config.toml at the repo root is the recommended layout
        # and must be discovered ahead of a flat ./infotech-email-agent.toml.
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "config.toml").write_text(
            'web_port = 9876\n', encoding="utf-8"
        )
        # Also drop a flat file to prove subfolder wins.
        (tmp_path / PROJECT_CONFIG_FILENAME).write_text(
            'web_port = 1111\n', encoding="utf-8"
        )
        paths = project_config_paths(tmp_path)
        assert len(paths) == 1
        assert paths[0] == cfg_dir / "config.toml"

    def test_config_subfolder_values_load(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        for name in (
            "INFOTECH_WEB_PORT", "INVOICE_WEB_PORT",
            "INFOTECH_AGENT_MODEL", "INVOICE_AGENT_MODEL",
        ):
            monkeypatch.delenv(name, raising=False)
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "config.toml").write_text(
            'web_port = 9876\nagent_model = "gpt-5-nano"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            config_mod, "global_config_path", lambda: tmp_path / "missing.toml"
        )
        s = load_settings(project_start=tmp_path)
        assert s.web_port == 9876
        assert s.agent_model == "gpt-5-nano"
