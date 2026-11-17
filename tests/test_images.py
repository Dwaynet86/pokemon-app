import io

import pytest
from PIL import Image

from app import images


def upload(name="c.png", fmt="PNG", size=(30, 40)):
    buf = io.BytesIO()
    Image.new("RGB", size, "blue").save(buf, fmt)
    buf.seek(0)
    return type("F", (), {"filename": name, "stream": buf})()


def test_pending_photo_is_temporary_and_nothing_else_is_stored(app, tmp_path):
    app.config["UPLOAD_DIR"] = tmp_path
    with app.app_context():
        name = images.store_pending(upload())
        assert images.pending_path(name)
        images.discard_pending(name)
        assert images.pending_path(name) is None
        assert not [p for p in tmp_path.rglob("*.webp")]            # never copied anywhere permanent


def test_abandoned_pending_photos_expire_after_an_hour(app, tmp_path):
    import os, time
    app.config["UPLOAD_DIR"] = tmp_path
    with app.app_context():
        old, fresh = images.store_pending(upload()), images.store_pending(upload())
        stale = time.time() - 2 * 3600
        os.utime(tmp_path / "pending" / old, (stale, stale))
        images.store_pending(upload())                               # any new upload sweeps the folder
        assert images.pending_path(old) is None and images.pending_path(fresh)


def test_rejects_bad_files(app, tmp_path):
    app.config["UPLOAD_DIR"] = tmp_path
    with app.app_context():
        with pytest.raises(images.ImageError):
            images.store_pending(upload("c.exe"))
        bad = type("F", (), {"filename": "c.png", "stream": io.BytesIO(b"not an image")})()
        with pytest.raises(images.ImageError):
            images.store_pending(bad)


def test_names_and_tokens(app):
    assert images.valid_name("a" * 32 + ".webp")
    assert not any(images.valid_name(n) for n in ("../x.webp", "x.webp", None, "a" * 32 + ".png"))
    with app.app_context():
        tok = images.make_token(1, "a" * 32 + ".webp")
        assert images.read_token(tok, 1) == "a" * 32 + ".webp"
        assert images.read_token(tok, 2) is None and images.read_token("junk", 1) is None
