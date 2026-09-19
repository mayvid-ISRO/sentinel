"""Config loader + redaction + credential store tests.
These three modules form the Phase 0 'no secrets in code, no secrets in
logs' guarantee."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import iris_config
from agent.redact import clear_cache, redact
import tools.credentials as cred


class TestConfigLoader:
    def test_defaults_load_without_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(iris_config, "CONFIG_PATH", tmp_path / "missing.json")
        cfg = iris_config._Config().load(tmp_path / "missing.json")
        assert cfg["llm"]["url"].startswith("http://127.0.0.1")
        assert cfg["agent"]["error_budget"] == 3

    def test_file_overrides_defaults(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"llm": {"model": "test-model:1b"}}))
        monkeypatch.setattr(iris_config, "CONFIG_PATH", cfg_file)
        cfg = iris_config._Config().load(cfg_file)
        assert cfg["llm"]["model"] == "test-model:1b"
        # deep merge keeps untouched siblings
        assert "timeout_seconds" in cfg["llm"]

    def test_env_overrides_file(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text(json.dumps({"backend": {"port": 8000}}))
        monkeypatch.setattr(iris_config, "CONFIG_PATH", cfg_file)
        monkeypatch.setenv("IRIS_BACKEND_PORT", "9999")
        cfg = iris_config._Config().load(cfg_file)
        assert cfg["backend"]["port"] == 9999

    def test_broken_file_falls_back_to_defaults(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{ not valid json !!")
        monkeypatch.setattr(iris_config, "CONFIG_PATH", cfg_file)
        cfg = iris_config._Config().load(cfg_file)
        assert cfg["llm"]["model"]  # defaults survived


class TestRedaction:
    def setup_method(self):
        clear_cache()

    def test_password_pattern_redacted(self):
        out = redact("login with password: test-pass-123 please")
        assert "mayvid" not in out
        assert "REDACTED" in out

    def test_api_pass_pattern_redacted(self):
        out = redact("api_pass=hunter2 cluster=10.0.0.1")
        assert "hunter2" not in out

    def test_normal_text_untouched(self):
        assert redact("list all volumes under svm itnd") == "list all volumes under svm itnd"

    def test_empty_safe(self):
        assert redact("") == ""


class TestCredentialStore:
    def test_save_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cred, "path", tmp_path / "cred.json")
        cred.save("netapp", {"cluster": "10.0.0.1", "api_user": "admin", "api_pass": "pw"})
        data = cred.load("netapp")
        assert data["cluster"] == "10.0.0.1"
        assert data["api_pass"] == "pw"

    def test_merge_keeps_old_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cred, "path", tmp_path / "cred.json")
        cred.save("netapp", {"cluster": "a", "api_user": "u", "api_pass": "p"})
        cred.save("netapp", {"svm_name": "itnd"})  # partial update
        data = cred.load("netapp")
        assert data["cluster"] == "a" and data["svm_name"] == "itnd"

    def test_missing_domain_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cred, "path", tmp_path / "cred.json")
        assert cred.load("nothing") == {}

    def test_clear_single_domain(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cred, "path", tmp_path / "cred.json")
        cred.save("netapp", {"cluster": "a"})
        cred.save("ssh", {"host": "b"})
        cred.clear("netapp")
        assert cred.load("netapp") == {}
        assert cred.load("ssh") == {"host": "b"}

    def test_broken_file_returns_empty(self, tmp_path, monkeypatch):
        p = tmp_path / "cred.json"
        p.write_text("{broken")
        monkeypatch.setattr(cred, "path", p)
        assert cred.load("netapp") == {}
