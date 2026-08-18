"""Command line."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import fixtures as fx
from imgmetamanager.cli import collect_paths, main
from imgmetamanager.core import read_metadata


@pytest.fixture()
def folder(tmp_path: Path) -> Path:
    photos = tmp_path / "photos"
    (photos / "subfolder").mkdir(parents=True)
    fx.make_jpeg(photos / "a.jpg")
    fx.make_png(photos / "b.png")
    fx.make_jpeg(photos / "subfolder" / "c.jpg")
    (photos / "notes.txt").write_text("not an image", encoding="utf-8")
    return photos


def test_collect_paths(folder: Path):
    assert [p.name for p in collect_paths([str(folder)])] == ["a.jpg", "b.png"]
    assert len(collect_paths([str(folder)], recursive=True)) == 3
    assert collect_paths([str(folder / "a.jpg")])[0].name == "a.jpg"


def test_show_txt(folder: Path, capsys):
    assert main(["--lang", "en", "show", str(folder / "a.jpg"), "--no-hash"]) == 0
    out = capsys.readouterr().out
    assert "a.jpg" in out and "ACME" in out and "GPS" in out


def test_show_json_with_filter(folder: Path, capsys):
    assert main(["show", str(folder), "-f", "json", "-g", "gps", "--no-hash"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["image_count"] == 2
    assert set(payload["images"][0]["groups"]) == {"gps"}


def test_show_in_french(folder: Path, capsys):
    assert main(["--lang", "fr", "show", str(folder / "a.jpg"), "-f", "markdown",
                 "--no-hash"]) == 0
    assert "### GPS / Localisation" in capsys.readouterr().out


def test_export(folder: Path, tmp_path: Path, capsys):
    target = tmp_path / "meta.json"
    assert main(["--lang", "en", "export", str(folder), "-o", str(target), "--no-hash"]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["image_count"] == 2
    assert "exported" in capsys.readouterr().out


def test_strip_dry_run(folder: Path, capsys):
    assert main(["strip", str(folder), "--dry-run"]) == 0
    assert "🔍" in capsys.readouterr().out
    assert not list(folder.glob("*-clean*"))


def test_strip_into_a_folder(folder: Path, tmp_path: Path):
    out = tmp_path / "clean"
    assert main(["strip", str(folder), "-r", "--out", str(out)]) == 0
    produced = sorted(p.name for p in out.glob("*"))
    assert produced == ["a-clean.jpg", "b-clean.png", "c-clean.jpg"]
    for path in out.glob("*"):
        assert read_metadata(path, with_hash=False).metadata_count == 0


def test_strip_in_place(folder: Path):
    assert main(["strip", str(folder / "a.jpg"), "--in-place"]) == 0
    assert read_metadata(folder / "a.jpg", with_hash=False).metadata_count == 0
    assert (folder / "a.jpg.bak").exists()


def test_strip_one_category(folder: Path, tmp_path: Path):
    assert main(["strip", str(folder / "a.jpg"), "--remove", "gps", "--out",
                 str(tmp_path / "out")]) == 0
    meta = read_metadata(tmp_path / "out" / "a-clean.jpg", with_hash=False)
    assert "gps" not in meta.group_names()
    assert meta.find("Make", "exif") is not None


def test_missing_input_file(capsys):
    assert main(["show", "/no/such/file.jpg"]) == 2


def test_no_subcommand_starts_the_interface(monkeypatch):
    """Running ``imgmetamanager`` on its own opens the web interface."""
    from imgmetamanager import cli

    called = {}
    monkeypatch.setattr(cli, "cmd_ui", lambda args: called.setdefault("args", args) and 0)
    assert cli.main([]) == 0
    assert called["args"].host == "127.0.0.1"
    assert called["args"].port is None, "no port means pick a free one"

    called.clear()
    assert cli.main(["--lang", "en"]) == 0
    assert called["args"].lang == "en"


def test_ui_options(monkeypatch):
    from imgmetamanager import cli

    seen = {}
    monkeypatch.setattr(cli, "cmd_ui", lambda args: seen.update(vars(args)) or 0)
    assert cli.main(["ui", "--port", "9000", "--share", "--no-browser"]) == 0
    assert seen["port"] == 9000 and seen["share"] is True and seen["no_browser"] is True


# --------------------------------------------------------------------- ports
def test_port_probe_detects_a_busy_port():
    import socket

    from imgmetamanager.cli import port_is_free

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        busy = holder.getsockname()[1]
        assert not port_is_free("127.0.0.1", busy)
    assert port_is_free("127.0.0.1", busy)


def test_auto_port_skips_the_busy_default(capsys):
    """A Gradio app already on 7860 must not stop this one from starting."""
    import socket

    from imgmetamanager.cli import DEFAULT_PORT, resolve_port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        try:
            holder.bind(("127.0.0.1", DEFAULT_PORT))
        except OSError:
            pytest.skip(f"port {DEFAULT_PORT} is already taken on this machine")
        holder.listen(1)
        port = resolve_port("127.0.0.1", None)
    assert port is not None and port != DEFAULT_PORT
    assert str(port) in capsys.readouterr().out


def test_explicit_busy_port_is_reported(capsys):
    """An explicit --port is honoured as given, so a clash is an error."""
    import socket

    from imgmetamanager.cli import resolve_port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        busy = holder.getsockname()[1]
        assert resolve_port("127.0.0.1", busy) is None
    assert "already in use" in capsys.readouterr().err


def test_no_free_port_at_all(capsys, monkeypatch):
    from imgmetamanager import cli

    monkeypatch.setattr(cli, "port_is_free", lambda host, port: False)
    assert cli.resolve_port("127.0.0.1", None) is None
    assert "No free port" in capsys.readouterr().err


def test_ui_stops_cleanly_when_the_port_is_taken(monkeypatch, capsys):
    from imgmetamanager import cli

    monkeypatch.setattr(cli, "resolve_port", lambda host, port: None)
    args = cli.build_parser().parse_args(["ui", "--port", "7860"])
    assert cli.cmd_ui(args) == 1
