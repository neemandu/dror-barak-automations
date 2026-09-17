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


def test_without_a_pack_playwright_renders(monkeypatch, tmp_path):
    monkeypatch.setenv("CHROMIUM_PACK_DIR", str(tmp_path / "nowhere"))
    assert pdf_chromium.engine() == "playwright"
    called = {}
    monkeypatch.setattr(pdf_chromium, "_render_playwright", lambda html, fmt: called.setdefault("pw", b"%PDF-pw"))
    assert pdf_chromium.render("<html></html>") == b"%PDF-pw"


def test_with_the_layer_chromium_prints_by_itself(pack, tmp_path, monkeypatch):
    # A stand-in binary that does what headless_shell does with --print-to-pdf:
    # proves the command line, the @page injection and the cleanup without a browser.
    fake = tmp_path / "fake-chromium"
    fake.write_text("#!/bin/sh\nfor a in \"$@\"; do case \"$a\" in --print-to-pdf=*) out=\"${a#--print-to-pdf=}\";; "
                    "file://*) src=\"${a#file://}\";; esac; done\n"
                    "printf '%%PDF-1.4 ' > \"$out\"; cat \"$src\" >> \"$out\"\n")
    fake.chmod(0o755)
    tmp = tmp_path / "tmp"; tmp.mkdir()
    monkeypatch.setenv("CHROMIUM_PACK_DIR", str(pack))
    monkeypatch.setattr(pdf_chromium, "TMP", tmp)
    monkeypatch.setattr(pdf_chromium, "_inflate", lambda p, t=None: str(fake))
    assert pdf_chromium.engine() == "cli"
    pdf = pdf_chromium.render("<html><head></head><body>שלום</body></html>")
    assert pdf.startswith(b"%PDF")
    assert b"@page{size:A4;margin:0}" in pdf, "a document without @page gets a full-bleed page box"
    assert "שלום".encode() in pdf
    assert list(tmp.glob("pdf-*")) == [], "the work dir must not pile up in /tmp on a warm container"


def test_a_document_with_its_own_page_box_is_left_alone():
    html = "<html><head><style>@page{size:A3}</style></head></html>"
    assert pdf_chromium._with_page_css(html, "A4") == html


def test_chromium_failing_to_print_is_a_clear_error(pack, tmp_path, monkeypatch):
    broken = tmp_path / "broken"; broken.write_text("#!/bin/sh\necho 'libnss3.so: cannot open' >&2\nexit 127\n"); broken.chmod(0o755)
    tmp = tmp_path / "tmp"; tmp.mkdir()
    monkeypatch.setattr(pdf_chromium, "TMP", tmp)
    with pytest.raises(pdf_chromium.ChromiumError, match="libnss3"):
        pdf_chromium._render_cli(str(broken), "<html></html>", "A4")
