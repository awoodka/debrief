import pytest
from conftest import record

from debrief.store import Store, raw


def test_migrations_apply_once(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    version = s.schema_version
    s.close()
    assert version >= 1 and Store(tmp_path / "s.sqlite").schema_version == version


def test_transaction_rolls_back_on_error(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    with pytest.raises(RuntimeError):
        with s.transaction():
            s.insert_item("src", "k", "seen", None, None, "2026-10-22T00:00:00Z", "h", "raw/x#0")
            raise RuntimeError("boom")
    assert s.item("src", "k") is None


def test_raw_roundtrip_keeps_bytes_exact(tmp_path):
    recs = [record("héllo", seq=0), record(b"\xff\xfe not utf-8", seq=1)]
    refs = raw.write(tmp_path, "2026-10-22", "src", "20261022T200000Z", recs)
    assert refs == ["raw/2026-10-22/src/20261022T200000Z.jsonl.gz#0", "raw/2026-10-22/src/20261022T200000Z.jsonl.gz#1"]
    back = raw.runs_for_day(tmp_path, "2026-10-22")["20261022T200000Z"]["src"][1]
    assert [raw.body(r) for r in back] == ["héllo".encode(), b"\xff\xfe not utf-8"]
    assert "body_b64" in back[1] and "body" in back[0]


def test_prune_removes_only_old_days(tmp_path):
    for d in ("2026-08-01", "2026-10-01", "2026-10-22"):
        raw.write(tmp_path, d, "src", "r", [record("x")])
    assert raw.prune(tmp_path, 60, "2026-10-22") == ["2026-08-01"]
    assert sorted(p.name for p in (tmp_path / "raw").iterdir()) == ["2026-10-01", "2026-10-22"]
