from youtube_tldw import cli, config


def test_set_get_unset(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    assert config.load() == {}
    config.set_value("output_dir", "/tmp/out")
    assert config.get("output_dir") == "/tmp/out"
    assert config.load() == {"output_dir": "/tmp/out"}
    assert config.unset("output_dir") is True
    assert config.get("output_dir") is None
    assert config.unset("output_dir") is False   # already gone


def test_load_ignores_bad_json(monkeypatch, tmp_path):
    cf = tmp_path / "config.json"
    cf.write_text("{not valid")
    monkeypatch.setattr(config, "CONFIG_FILE", cf)
    assert config.load() == {}


def test_default_output_dir_precedence(monkeypatch, tmp_path):
    monkeypatch.delenv("TLDW_OUTPUT_DIR", raising=False)
    monkeypatch.setattr(cli.config, "get", lambda k, d=None: None)
    assert cli._default_output_dir() == cli.DEFAULT_OUTPUT          # hardcoded fallback

    monkeypatch.setattr(cli.config, "get", lambda k, d=None: str(tmp_path / "cfg"))
    assert cli._default_output_dir() == tmp_path / "cfg"            # saved config

    monkeypatch.setenv("TLDW_OUTPUT_DIR", str(tmp_path / "env"))
    assert cli._default_output_dir() == tmp_path / "env"            # env wins over config


def test_config_cli_set_then_get(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.delenv("TLDW_OUTPUT_DIR", raising=False)
    dest = tmp_path / "renders"
    assert cli.main(["config", "set", "output-dir", str(dest)]) == 0
    assert config.get("output_dir") == str(dest.resolve())          # expanded + resolved
    capsys.readouterr()
    assert cli.main(["config", "get"]) == 0
    out = capsys.readouterr().out
    assert "output-dir" in out and str(dest.resolve()) in out
