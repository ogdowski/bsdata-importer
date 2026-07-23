import io
import tarfile

import bsdata_importer
import pytest
from bsdata_importer import fetch_repo_dir


def _make_tarball_bytes(root_name: str, filename: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=f"{root_name}/{filename}")
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, data: bytes):
        self.content = data

    def raise_for_status(self):
        pass


def test_fetch_repo_dir_multi_ref_coexistence_and_cache(tmp_path, monkeypatch):
    repo = "BSData/wh40k-10e"
    dest_dir = tmp_path / "cache"
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        ref = url.rsplit("/", 1)[-1]
        data = _make_tarball_bytes(
            f"wh40k-10e-{ref}", "x.cat", f"content-{ref}".encode())
        return _FakeResponse(data)

    monkeypatch.setattr(bsdata_importer.requests, "get", fake_get)

    path_aaa = fetch_repo_dir(repo, "aaa", dest_dir)
    path_bbb = fetch_repo_dir(repo, "bbb", dest_dir)

    assert path_aaa == dest_dir / "wh40k-10e-aaa"
    assert path_bbb == dest_dir / "wh40k-10e-bbb"
    assert (path_aaa / "x.cat").read_bytes() == b"content-aaa"
    assert (path_bbb / "x.cat").read_bytes() == b"content-bbb"
    assert len(calls) == 2

    # ponowne pobranie znanego ref trafia w cache, nie w sieć
    assert fetch_repo_dir(repo, "aaa", dest_dir) == path_aaa
    assert len(calls) == 2


def test_fetch_repo_dir_rejects_path_traversal(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name="../outside.txt")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"bad"))

    monkeypatch.setattr(
        bsdata_importer.requests,
        "get",
        lambda url, timeout=None: _FakeResponse(buf.getvalue()),
    )

    with pytest.raises(RuntimeError, match="unsafe tar member path"):
        fetch_repo_dir("BSData/wh40k-10e", "unsafe", tmp_path / "cache")

    assert not (tmp_path / "outside.txt").exists()
