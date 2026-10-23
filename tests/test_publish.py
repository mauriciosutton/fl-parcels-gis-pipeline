import sys
import types

import pytest

from flparcels import publish
from flparcels.cli import main

ENV = {
    "FLPARCELS_PUBLISH_ENDPOINT": "https://s3.example.test",
    "FLPARCELS_PUBLISH_BUCKET": "tiles",
    "FLPARCELS_PUBLISH_ACCESS_KEY": "ak",
    "FLPARCELS_PUBLISH_SECRET_KEY": "sk",
}


def test_target_from_env_reports_every_missing_var(monkeypatch):
    for var in ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("FLPARCELS_PUBLISH_BUCKET", "tiles")
    with pytest.raises(SystemExit) as e:
        publish.Target.from_env()
    msg = str(e.value)
    assert "FLPARCELS_PUBLISH_ENDPOINT" in msg and "FLPARCELS_PUBLISH_SECRET_KEY" in msg
    assert "FLPARCELS_PUBLISH_BUCKET" not in msg


def test_publish_uploads_under_build_name(tmp_path, monkeypatch):
    calls = {}

    class Client:
        def upload_file(self, filename, bucket, key):
            calls.setdefault("uploads", []).append((bucket, key))

    fake = types.SimpleNamespace(client=lambda service, **kw: calls.update(kw=kw) or Client())
    monkeypatch.setitem(sys.modules, "boto3", fake)
    a, b = tmp_path / "fl_parcels.pmtiles", tmp_path / "lookup.parquet"
    a.write_bytes(b"x")
    b.write_bytes(b"y")
    for var, value in ENV.items():
        monkeypatch.setenv(var, value)

    keys = publish.publish("fl_parcels", [a, b], publish.Target.from_env())

    assert keys == ["fl_parcels/fl_parcels.pmtiles", "fl_parcels/lookup.parquet"]
    assert calls["uploads"] == [("tiles", k) for k in keys]
    assert calls["kw"]["endpoint_url"] == "https://s3.example.test"
    assert calls["kw"]["region_name"] == "us-east-1"


def test_cli_publish_with_missing_outputs_exits_1(tmp_path, monkeypatch):
    for var, value in ENV.items():
        monkeypatch.setenv(var, value)
    assert main(["--data-dir", str(tmp_path), "publish"]) == 1
