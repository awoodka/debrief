"""Load and validate Debrief v2 configuration: config/settings.yml, config/sources.yml,
config/triage.yml (as settings["triage"]), config/brief.yml (as settings["brief"]), and env.

Environment:
  DEBRIEF_DATA_DIR   raw data, state and runs (default: <repo>/data/v2).
  DEBRIEF_CONTACT    contact for the User-Agent, an email or URL (default: settings.contact_default)
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO / "config"

ROLES = {"anchor", "discussion", "reference", "context"}
TIERS = {1, 2, 3}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Source:
    """One entry in sources.yml. `url` may contain `{each}` (expanded over `each`) and the date
    placeholders listed in urls.expand(). `candidates` are unverified alternatives that
    probe-sources tries in order; once one is verified it becomes `url`."""
    id: str
    name: str
    adapter: str
    roles: tuple[str, ...]
    tier: int
    enabled: bool = True
    kind: str | None = None
    org: str | None = None
    url: str | None = None
    each: tuple[str, ...] = ()
    candidates: tuple = ()
    probe: dict = field(default_factory=dict)
    auth: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    notes: str = ""

    def auth_headers(self, env):
        """The auth header this source sends, if its env var is set; {} otherwise."""
        a = self.auth or {}
        if a.get("env") and env.get(a["env"]):
            return {a["header"]: a["format"].format(env[a["env"]])}
        return {}


def _source(raw, seen):
    where = f"sources.yml entry {raw.get('id', '?')!r}"
    for key in ("id", "name", "adapter", "roles", "tier"):
        if key not in raw:
            raise ConfigError(f"{where}: missing {key!r}")
    if raw["id"] in seen:
        raise ConfigError(f"{where}: duplicate id")
    roles = tuple(raw["roles"])
    if not roles or set(roles) - ROLES:
        raise ConfigError(f"{where}: roles must be a non-empty subset of {sorted(ROLES)}")
    if raw["tier"] not in TIERS:
        raise ConfigError(f"{where}: tier must be one of {sorted(TIERS)}")
    if raw["tier"] == 3 and "anchor" in roles:
        raise ConfigError(f"{where}: tier 3 is attach-only and can't be an anchor")
    if "{each}" in (raw.get("url") or "") and not raw.get("each"):
        raise ConfigError(f"{where}: url uses {{each}} but no `each` list is given")
    known = {f for f in Source.__dataclass_fields__}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")
    return Source(**{**raw, "roles": roles, "each": tuple(raw.get("each") or ()),
                     "candidates": tuple(raw.get("candidates") or ())})


@dataclass(frozen=True)
class Config:
    settings: dict
    sources: tuple[Source, ...]
    data_dir: Path
    contact: str

    def source(self, source_id):
        for s in self.sources:
            if s.id == source_id:
                return s
        raise ConfigError(f"no source {source_id!r} in sources.yml")

    @property
    def user_agent(self):
        return self.settings["user_agent"].format(contact=self.contact)


def load(config_dir=CONFIG_DIR, env=os.environ):
    config_dir = Path(config_dir)
    settings = yaml.safe_load((config_dir / "settings.yml").read_text())
    settings["triage"] = yaml.safe_load((config_dir / "triage.yml").read_text())
    settings["brief"] = yaml.safe_load((config_dir / "brief.yml").read_text())
    raw_sources = yaml.safe_load((config_dir / "sources.yml").read_text()) or []
    seen, sources = set(), []
    for raw in raw_sources:
        src = _source(raw, seen)
        seen.add(src.id)
        sources.append(src)

    # A story's section comes only from its source's kind, via settings.sections. So every enabled
    # source that creates stories must name a kind that maps to one; nothing is inferred from content.
    sections = settings.get("sections") or {}
    for src in sources:
        if src.enabled and "anchor" in src.roles and sections.get(src.kind) is None:
            raise ConfigError(f"sources.yml entry {src.id!r}: anchor kind {src.kind!r} has no section "
                              f"in settings.yml (known kinds: {sorted(sections)})")

    data_dir = Path(env.get("DEBRIEF_DATA_DIR") or REPO / "data" / "v2")
    contact = (env.get("DEBRIEF_CONTACT") or "").strip() or settings["contact_default"]
    return Config(settings=settings, sources=tuple(sources), data_dir=data_dir, contact=contact)
