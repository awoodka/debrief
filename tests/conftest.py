import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from debrief.http import Fetched  # noqa: E402
from debrief.store import raw  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_bytes()


def record(body, url="https://x.test/", *, status=200, meta=None, each=None, seq=0,
           fetched_at="2026-10-22T20:00:00Z", source_id="test", headers=None):
    """A raw record as a live fetch would have stored it."""
    if isinstance(body, str):
        body = body.encode()
    f = Fetched(url=url, final_url=url, status=status, headers=headers or {}, content=body,
                error="" if 200 <= status < 400 else f"HTTP {status}")
    return raw.make_record(source_id, "run", seq, f, fetched_at, each=each, meta=meta)
