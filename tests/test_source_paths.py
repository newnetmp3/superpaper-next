"""Source picker validation and duplicate-handling regression tests."""

from superpaper.source_paths import is_valid_source, source_identity


def test_source_type_and_extension_validation(tmp_path):
    folder = tmp_path / "wallpapers"
    folder.mkdir()
    image = folder / "SKY.PNG"
    image.write_bytes(b"placeholder")
    non_image = folder / "notes.txt"
    non_image.write_text("not an image")

    assert is_valid_source(str(folder))
    assert is_valid_source(str(image))
    assert not is_valid_source(str(non_image))
    assert not is_valid_source(str(folder / "missing.jpg"))


def test_source_id_normalizes_path_but_preserves_monitor(tmp_path):
    folder = tmp_path / "backgrounds"
    folder.mkdir()
    image = folder / "sky.jpg"
    image.write_bytes(b"placeholder")
    dot_path = str(folder / "." / "sky.jpg")

    assert source_identity(str(image), "0") == source_identity(dot_path, "0")
    assert source_identity(str(image), "0") != source_identity(str(image), "1")
