import io

from botocore.exceptions import ClientError

from accounting.utils import statement_archive
from accounting.utils.statement_archive import R2Credentials, StatementArchive


class _FakePaginator:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects

    def paginate(self, Bucket, Prefix):  # noqa: N803
        contents = [{"Key": key} for key in self._objects if key.startswith(Prefix)]
        return [{"Contents": contents}]


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body):  # noqa: N803
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):  # noqa: N803
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _FakePaginator(self.objects)


def _configured_credentials() -> R2Credentials:
    return R2Credentials(
        account_id="acct",
        access_key_id="key",
        secret_access_key="secret",  # noqa: S106
        bucket_name="bucket",
        endpoint_url="https://acct.r2.cloudflarestorage.com",
        _env_file=None,
    )


def test_resolve_is_none_when_unconfigured() -> None:
    assert R2Credentials(_env_file=None).resolve() is None


def test_resolve_is_none_when_partially_configured() -> None:
    credentials = R2Credentials(account_id="acct", bucket_name="bucket", _env_file=None)
    assert credentials.resolve() is None


def test_resolve_returns_every_field_when_fully_configured() -> None:
    resolved = _configured_credentials().resolve()
    assert resolved is not None
    assert resolved.bucket_name == "bucket"
    assert resolved.endpoint_url == "https://acct.r2.cloudflarestorage.com"


def test_local_fallback_round_trips_bytes(tmp_path) -> None:
    archive = StatementArchive(tmp_path, "statements/user-1", credentials=R2Credentials(_env_file=None))
    archive.write("Chase/checking:1234/20260101T000000.csv", b"date,amount\n")
    assert archive.read("Chase/checking:1234/20260101T000000.csv") == b"date,amount\n"
    assert (tmp_path / "Chase" / "checking:1234" / "20260101T000000.csv").read_bytes() == b"date,amount\n"


def test_local_fallback_exists_and_list_relative_paths(tmp_path) -> None:
    archive = StatementArchive(tmp_path, "statements/user-1", credentials=R2Credentials(_env_file=None))
    archive.write("Chase/checking:1234/20260101T000000.csv", b"a")
    archive.write("SoFi/statement_pdf/statement.pdf", b"%PDF-fake")
    assert archive.exists("Chase/checking:1234/20260101T000000.csv") is True
    assert archive.exists("Chase/checking:1234/does-not-exist.csv") is False
    assert archive.list_relative_paths("*/*/*.csv") == ["Chase/checking:1234/20260101T000000.csv"]
    assert archive.list_relative_paths("SoFi/statement_pdf/*.pdf") == ["SoFi/statement_pdf/statement.pdf"]


def test_local_fallback_list_relative_paths_empty_when_root_missing(tmp_path) -> None:
    archive = StatementArchive(tmp_path / "missing", "statements/user-1", credentials=R2Credentials(_env_file=None))
    assert archive.list_relative_paths() == []


def test_local_fallback_read_all_yields_every_matching_file(tmp_path) -> None:
    archive = StatementArchive(tmp_path, "statements/user-1", credentials=R2Credentials(_env_file=None))
    archive.write("Chase/checking:1234/20260101T000000.csv", b"a")
    archive.write("SoFi/savings:5678/20260201T000000.csv", b"b")
    assert sorted(archive.read_all("*/*/*.csv")) == [
        ("Chase/checking:1234/20260101T000000.csv", b"a"),
        ("SoFi/savings:5678/20260201T000000.csv", b"b"),
    ]


def test_r2_write_read_exists_and_list_use_the_prefixed_key(monkeypatch, tmp_path) -> None:
    fake_client = _FakeS3Client()
    monkeypatch.setattr(statement_archive.boto3, "client", lambda *args, **kwargs: fake_client)
    archive = StatementArchive(tmp_path, "statements/user-1", credentials=_configured_credentials())

    archive.write("Chase/checking:1234/20260101T000000.csv", b"date,amount\n")

    assert fake_client.objects == {"statements/user-1/Chase/checking:1234/20260101T000000.csv": b"date,amount\n"}
    assert archive.read("Chase/checking:1234/20260101T000000.csv") == b"date,amount\n"
    assert archive.exists("Chase/checking:1234/20260101T000000.csv") is True
    assert archive.exists("Chase/checking:1234/missing.csv") is False
    assert archive.list_relative_paths("*/*/*.csv") == ["Chase/checking:1234/20260101T000000.csv"]
    # Never touches local disk when R2 is configured.
    assert list(tmp_path.rglob("*")) == []
