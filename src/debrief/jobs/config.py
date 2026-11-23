"""Load and check the jobs config: config/jobs/settings.yml and config/jobs/sources.yml.

Jobs shares Debrief's data dir (DEBRIEF_DATA_DIR) and contact (DEBRIEF_CONTACT); its own files
live under <data>/jobs/.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import REPO, ConfigError

CONFIG_DIR = REPO / "config" / "jobs"
FAMILIES = {"list", "board", "bigtech", "api", "discussion", "email", "check", "universe", "watchlist", "detail"}
DERIVED = {"watchlist", "detail"}     # work from the profile and stored postings, not a url of their own


@dataclass(frozen=True)
class JobSource:
    id: str
    name: str
    family: str
    enabled: bool = True
    url: str | None = None
    each: tuple = ()
    method: str = "GET"
    body: dict | None = None
    candidates: tuple = ()
    probe: dict = field(default_factory=dict)
    auth: dict = field(default_factory=dict)
    adapter: str | None = None      # the collector that reads it (jobs/adapters); None = probe only
    options: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)     # extra headers; "{contact}" is DEBRIEF_CONTACT
    terms: str = ""
    notes: str = ""

    def auth_env(self):
        """Every env var this source's auth needs."""
        a = self.auth or {}
        names = list(a.get("env") or [])
        names += list((a.get("headers") or {}).values()) + list((a.get("params") or {}).values())
        return names

    def missing_keys(self, env):
        return [n for n in self.auth_env() if not (env.get(n) or "").strip()]

    def auth_parts(self, env):
        """(headers, params) to send. Call only when missing_keys() is empty."""
        a = self.auth or {}
        headers = {h: env[v].strip() for h, v in (a.get("headers") or {}).items()}
        params = {p: env[v].strip() for p, v in (a.get("params") or {}).items()}
        return headers, params


@dataclass(frozen=True)
class JobsConfig:
    settings: dict
    sources: tuple
    data_dir: Path        # <debrief data>/jobs
    contact: str

    @property
    def user_agent(self):
        return self.settings["user_agent"].format(contact=self.contact)

    def source(self, source_id):
        for s in self.sources:
            if s.id == source_id:
                return s
        raise ConfigError(f"no source {source_id!r} in config/jobs/sources.yml")


def _source(raw, seen):
    where = f"config/jobs/sources.yml entry {raw.get('id', '?')!r}"
    for key in ("id", "name", "family"):
        if key not in raw:
            raise ConfigError(f"{where}: missing {key!r}")
    if raw["id"] in seen:
        raise ConfigError(f"{where}: duplicate id")
    if raw["family"] not in FAMILIES:
        raise ConfigError(f"{where}: family must be one of {sorted(FAMILIES)}")
    unknown = set(raw) - set(JobSource.__dataclass_fields__)
    if unknown:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")
    if "{each}" in (raw.get("url") or "") and not raw.get("each"):
        raise ConfigError(f"{where}: url uses {{each}} but no `each` list is given")
    if raw.get("enabled", True) and raw["family"] not in DERIVED and not (raw.get("url") or raw.get("candidates")):
        raise ConfigError(f"{where}: an enabled source needs a url or candidates")
    if raw.get("enabled", True) and raw.get("adapter") and raw["family"] not in DERIVED and not (
            raw.get("url") or (raw.get("options") or {}).get("endpoint")):
        raise ConfigError(f"{where}: its collector needs a url (or options.endpoint); candidates are for the probe only")
    method = raw.get("method", "GET").upper()
    if method not in ("GET", "POST"):
        raise ConfigError(f"{where}: method must be GET or POST")
    return JobSource(**{**raw, "method": method, "each": tuple(raw.get("each") or ()),
                        "candidates": tuple(raw.get("candidates") or ())})


def load(config_dir=CONFIG_DIR, env=os.environ):
    config_dir = Path(config_dir)
    settings = yaml.safe_load((config_dir / "settings.yml").read_text())
    seen, sources = set(), []
    for raw in yaml.safe_load((config_dir / "sources.yml").read_text()) or []:
        s = _source(raw, seen)
        seen.add(s.id)
        sources.append(s)
    data_dir = Path(env.get("DEBRIEF_DATA_DIR") or REPO / "data" / "v2") / "jobs"
    contact = (env.get("DEBRIEF_CONTACT") or "").strip() or settings["contact_default"]
    return JobsConfig(settings=settings, sources=tuple(sources), data_dir=data_dir, contact=contact)
