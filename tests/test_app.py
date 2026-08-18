"""Interface logic, exercised without starting a Gradio server."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import fixtures as fx
from imgmetamanager.app import MetaApp, analyse, build_interface, help_markdown
from imgmetamanager.i18n import t


def _value(update):
    """Read the value out of a ``gr.update()`` payload."""
    return update["value"] if isinstance(update, dict) else update


@pytest.fixture()
def app(tmp_path: Path) -> MetaApp:
    return MetaApp(allow_local=True, allowed_roots=[tmp_path])


@pytest.fixture()
def photos(tmp_path: Path):
    folder = tmp_path / "photos"
    folder.mkdir()
    return [fx.make_jpeg(folder / "a.jpg"), fx.make_png(folder / "b.png")]


def test_analyse_produces_previews(photos):
    entries = analyse(photos, "local")
    assert len(entries) == 2
    assert all(entry.meta.ok for entry in entries)
    assert all(entry.preview and Path(entry.preview).exists() for entry in entries)


def test_analyse_reuses_the_cache(photos):
    first = analyse(photos, "local")
    second = analyse(photos, "local", first)
    assert second[0] is first[0], "an unchanged file must not be read twice"


def test_folder_loading(app: MetaApp, photos, tmp_path: Path):
    entries, index, gallery, status = app.load_folder(str(tmp_path / "photos"), False, [])
    assert len(entries) == 2 and index == 0
    assert len(_value(gallery)) == 2
    assert "2" in status


def test_missing_folder(app: MetaApp):
    entries, _, _, status = app.load_folder("/no/such/folder", False, [])
    assert entries == []
    assert "not found" in status


def test_folder_blocked_in_shared_mode(photos, tmp_path: Path):
    restricted = MetaApp(allow_local=False, allowed_roots=[tmp_path])
    entries, _, _, status = restricted.load_folder(str(tmp_path / "photos"), False, [])
    assert entries == []
    assert t("msg_folder_blocked") in status


def test_uploads_and_folder_coexist(app: MetaApp, photos, tmp_path: Path):
    entries, *_ = app.load_uploads([str(photos[0])], [])
    assert len(entries) == 1 and entries[0].origin == "upload"
    entries, *_ = app.load_folder(str(tmp_path / "photos"), False, entries)
    assert len(entries) == 3
    assert {e.origin for e in entries} == {"upload", "local"}


def test_view_rendering(app: MetaApp, photos):
    entries = analyse(photos, "local")
    preview, summary, groups, table, kinds, prov = app.render_view(entries, 0, "", False)
    assert Path(_value(preview)).exists()
    assert "a.jpg" in summary
    assert "gps" in groups["value"]
    assert any(row[1] == "Make" for row in _value(table))
    assert "exif" in kinds["value"] and "icc" not in kinds["value"]
    assert "provenance" in prov.lower() or "provenance" in prov


def test_table_filters(app: MetaApp, photos):
    entries = analyse(photos, "local")
    rows = _value(app.render_table(entries, 0, ["gps"], "", False))
    assert rows and all(row[0].startswith("GPS") for row in rows)
    rows = _value(app.render_table(entries, 0, [], "acme", False))
    assert [row[1] for row in rows] == ["Make"]
    rows = _value(app.render_table(entries, 0, [], "", True))
    assert all(row[1] != "Model" for row in rows)


def test_clipboard_selection(app: MetaApp, photos):
    entries = analyse(photos, "local")

    class Select:
        index = [0, 1]

    buffer, box = app.add_row(Select(), entries, 0, ["exif"], "", False, [], "Tag = value")
    assert len(buffer) == 1
    assert "=" in _value(box)
    buffer, box = app.add_row(Select(), entries, 0, ["exif"], "", False, buffer, "Tag = value")
    assert len(buffer) == 1, "no duplicate"
    buffer, box = app.add_visible(entries, 0, ["gps"], "", False, buffer, "JSON")
    assert len(buffer) > 1
    assert json.loads(_value(box))
    buffer, box = app.clear_buffer()
    assert buffer == [] and _value(box) == ""


def test_export_from_the_interface(app: MetaApp, photos):
    entries = analyse(photos, "local")
    file_update, code, status = app.do_export(
        entries, 0, t("scope_all"), "json", False, [], "", False, False)
    path = Path(_value(file_update))
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["image_count"] == 2
    assert path.suffix == ".json"
    assert _value(code)


def test_export_zip_per_image(app: MetaApp, photos):
    entries = analyse(photos, "local")
    file_update, _, _ = app.do_export(entries, 0, t("scope_all"), "csv", False, [], "", False, True)
    assert Path(_value(file_update)).suffix == ".zip"


def test_export_without_images(app: MetaApp):
    _, _, status = app.do_export([], 0, t("scope_current"), "json", False, [], "", False, False)
    assert t("msg_nothing_to_export") in status


def test_cleaning_writes_a_copy(app: MetaApp, photos):
    entries = analyse(photos, "local")
    pool, table, files, status = app.do_clean(
        entries, 0, t("scope_current"), ["all"], t("dest_copy"), "", True, False, False)
    rows = _value(table)
    assert len(rows) == 1 and rows[0][0] == "a-clean.jpg"
    assert "✅" in rows[0][4]
    assert photos[0].exists(), "the original must stay untouched"
    assert Path(_value(files)[0]).exists()


def test_cleaning_dry_run(app: MetaApp, photos):
    entries = analyse(photos, "local")
    _, table, _, status = app.do_clean(
        entries, 0, t("scope_current"), ["gps"], t("dest_copy"), "", True, False, True)
    assert t("msg_dry_run") in status
    assert _value(table)[0][1].startswith("GPS")
    assert not list(photos[0].parent.glob("*-clean*")), "a dry run writes nothing"


def test_in_place_cleaning_needs_confirmation(app: MetaApp, photos):
    entries = analyse(photos, "local")
    _, _, _, status = app.do_clean(
        entries, 0, t("scope_current"), ["all"], t("dest_inplace"), "", True, False, False)
    assert t("msg_need_confirm") in status

    entries, _, _, status = app.do_clean(
        entries, 0, t("scope_current"), ["all"], t("dest_inplace"), "", True, True, False)
    from imgmetamanager.core import read_metadata
    assert read_metadata(photos[0], with_hash=False).metadata_count == 0
    assert photos[0].with_name("a.jpg.bak").exists()
    assert entries[0].meta.metadata_count == 0, "the session state must be refreshed"


def test_in_place_cleaning_refuses_uploads(app: MetaApp, photos):
    entries, *_ = app.load_uploads([str(photos[0])], [])
    _, _, _, status = app.do_clean(
        entries, 0, t("scope_current"), ["all"], t("dest_inplace"), "", True, True, False)
    assert t("msg_inplace_blocked") in status


def test_cleaning_without_a_category(app: MetaApp, photos):
    entries = analyse(photos, "local")
    _, _, _, status = app.do_clean(
        entries, 0, t("scope_current"), [], t("dest_copy"), "", True, False, False)
    assert t("msg_pick_kinds") in status


def test_batch_cleaning_into_a_folder(app: MetaApp, photos, tmp_path: Path):
    entries = analyse(photos, "local")
    out = tmp_path / "output"
    _, table, files, status = app.do_clean(
        entries, 0, t("scope_all"), ["all"], t("dest_dir"), str(out), True, False, False)
    assert len(_value(table)) == 2
    assert len(list(out.glob("*"))) == 2
    assert len(_value(files)) == 2


def test_destination_fields_follow_the_mode():
    from imgmetamanager.app import toggle_destination

    outdir, backup, confirm = toggle_destination(t("dest_copy"))
    assert not any(u["visible"] for u in (outdir, backup, confirm))
    outdir, backup, confirm = toggle_destination(t("dest_dir"))
    assert outdir["visible"] and not backup["visible"]
    outdir, backup, confirm = toggle_destination(t("dest_inplace"))
    assert backup["visible"] and confirm["visible"] and not outdir["visible"]


def test_interface_builds():
    assert build_interface() is not None
    assert "Lossless removal" in help_markdown(True)
    assert "disabled" in help_markdown(False)
