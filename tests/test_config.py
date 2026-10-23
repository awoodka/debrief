import pytest
import yaml

from debrief import config


def write(tmp_path, sources, settings=None):
    (tmp_path / "settings.yml").write_text(yaml.safe_dump(settings or {
        "user_agent": "debrief/2.0 ({contact})", "contact_default": "+https://example.test",
        "sections": {"announcement": "announcements"}}))
    (tmp_path / "sources.yml").write_text(yaml.safe_dump(sources))
    (tmp_path / "triage.yml").write_text("{}\n")
    return tmp_path


SRC = {"id": "a", "name": "A", "adapter": "rss", "roles": ["anchor"], "tier": 1, "kind": "announcement",
       "url": "https://a.test/feed"}


def test_repo_config_loads():
    cfg = config.load(env={"DEBRIEF_DATA_DIR": "/tmp/debrief-test-data"})
    assert len(cfg.sources) > 50
    assert cfg.source("hn").roles == ("discussion",)


def test_contact_goes_into_user_agent(tmp_path):
    d = write(tmp_path, [SRC])
    assert config.load(d, env={}).user_agent == "debrief/2.0 (+https://example.test)"
    assert config.load(d, env={"DEBRIEF_CONTACT": "me@example.test"}).user_agent == "debrief/2.0 (me@example.test)"


@pytest.mark.parametrize("change, message", [
    ({"tier": 3}, "attach-only"),
    ({"roles": ["bogus"]}, "roles"),
    ({"tier": 4}, "tier"),
    ({"url": "https://x.test/{each}"}, "each"),
    ({"surprise": 1}, "unknown keys"),
])
def test_invalid_sources_are_rejected(tmp_path, change, message):
    with pytest.raises(config.ConfigError, match=message):
        config.load(write(tmp_path, [{**SRC, **change}]), env={})


def test_duplicate_ids_are_rejected(tmp_path):
    with pytest.raises(config.ConfigError, match="duplicate"):
        config.load(write(tmp_path, [SRC, SRC]), env={})


def test_refuses_the_v1_data_dir(tmp_path):
    (tmp_path / "data" / "debriefs").mkdir(parents=True)
    with pytest.raises(config.ConfigError, match="v1"):
        config.load(write(tmp_path, [SRC]), env={"DEBRIEF_DATA_DIR": str(tmp_path / "data")})


def test_anchor_kind_must_map_to_a_section(tmp_path):
    d = write(tmp_path, [{**SRC, "kind": "mystery"}],
              {"user_agent": "x ({contact})", "contact_default": "c", "sections": {"announcement": "announcements"}})
    with pytest.raises(config.ConfigError, match="no section"):
        config.load(d, env={})


def test_every_enabled_anchor_in_the_repo_config_has_a_section():
    cfg = config.load(env={"DEBRIEF_DATA_DIR": "/tmp/debrief-test-data"})
    sections = cfg.settings["sections"]
    assert all(sections.get(s.kind) for s in cfg.sources if s.enabled and "anchor" in s.roles)
