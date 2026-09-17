"""Inflating the Chromium layer pack — the part that only ever runs on Lambda.

A fake pack (tiny Brotli archives) proves the layout and environment the real
one relies on, without a 250 MB browser in the test suite.
"""

from __future__ import annotations

import io
import os
import tarfile

import brotli
import pytest

from src.lib import pdf_chromium


def _tar_br(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name); info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return brotli.compress(buf.getvalue())


@pytest.fixture
def pack(tmp_path):
    p = tmp_path / "opt" / "chromium"; p.mkdir(parents=True)
    (p / "chromium.br").write_bytes(brotli.compress(b"#!/bin/sh\necho fake chromium\n"))
    (p / "al2023.tar.br").write_bytes(_tar_br({"lib/libnss3.so": b"lib"}))
    (p / "fonts.tar.br").write_bytes(_tar_br({"fonts.conf": b"<fontconfig/>"}))
    (p / "swiftshader.tar.br").write_bytes(_tar_br({"libGLESv2.so": b"gl"}))
    return p


def test_inflate_lays_out_tmp_the_way_chromium_expects(pack, tmp_path, monkeypatch):
    tmp = tmp_path / "tmp"; tmp.mkdir()
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    monkeypatch.delenv("FONTCONFIG_PATH", raising=False)
    exe = pdf_chromium._inflate(pack, tmp)
    assert exe == str(tmp / "chromium") and os.access(exe, os.X_OK)
    assert (tmp / "al2023" / "lib" / "libnss3.so").exists()
    assert (tmp / "fonts" / "fonts.conf").exists()
    assert (tmp / "libGLESv2.so").exists()
    assert os.environ["LD_LIBRARY_PATH"].startswith(str(tmp / "al2023" / "lib"))
    assert os.environ["FONTCONFIG_PATH"] == str(tmp / "fonts")


def test_a_warm_container_does_not_inflate_twice(pack, tmp_path):
    tmp = tmp_path / "tmp"; tmp.mkdir()
    pdf_chromium._inflate(pack, tmp)
    (tmp / "chromium").write_bytes(b"already here")
    pdf_chromium._inflate(pack, tmp)
    assert (tmp / "chromium").read_bytes() == b"already here"


def test_without_a_pack_playwrights_own_browser_is_used(monkeypatch, tmp_path):
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_PACK", str(tmp_path / "nowhere"))
    monkeypatch.delenv("PLAYWRIGHT_CHROMIUM_PATH", raising=False)
    launch = pdf_chromium._launch_options()
    assert "executable_path" not in launch
    assert "--single-process" not in launch["args"], "Lambda-only flags must not leak into local renders"


def test_an_explicit_binary_beats_the_pack(monkeypatch, pack):
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_PACK", str(pack))
    monkeypatch.setenv("PLAYWRIGHT_CHROMIUM_PATH", "/usr/bin/chromium")
    assert pdf_chromium._launch_options()["executable_path"] == "/usr/bin/chromium"


def test_a_non_executable_node_driver_is_copied_somewhere_it_can_run(tmp_path, monkeypatch):
    node = tmp_path / "pkg" / "node"; node.parent.mkdir(); node.write_bytes(b"node"); node.chmod(0o644)
    monkeypatch.setattr("playwright._impl._driver.compute_driver_executable", lambda: (str(node), "cli.js"))
    monkeypatch.delenv("PLAYWRIGHT_NODEJS_PATH", raising=False)
    tmp = tmp_path / "tmp"; tmp.mkdir()
    pdf_chromium._ensure_node_executable(tmp)
    assert os.environ["PLAYWRIGHT_NODEJS_PATH"] == str(tmp / "pw-node")
    assert os.access(tmp / "pw-node", os.X_OK)


def test_an_executable_node_driver_is_left_alone(tmp_path, monkeypatch):
    node = tmp_path / "node"; node.write_bytes(b"node"); node.chmod(0o755)
    monkeypatch.setattr("playwright._impl._driver.compute_driver_executable", lambda: (str(node), "cli.js"))
    monkeypatch.delenv("PLAYWRIGHT_NODEJS_PATH", raising=False)
    pdf_chromium._ensure_node_executable(tmp_path)
    assert "PLAYWRIGHT_NODEJS_PATH" not in os.environ
