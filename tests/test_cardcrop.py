"""Card cropper: finds the card in a photo, straightens it, and fits it under the scan API's size limit."""
import cv2
import numpy as np
import pytest
from PIL import Image

from app import cardcrop

rng = np.random.default_rng(7)


def make_card(w=630, h=880):
    """A fake card: yellow border, blue art box with a red circle, dark text lines."""
    img = np.full((h, w, 3), (232, 200, 64), np.uint8)
    cv2.rectangle(img, (40, 90), (w - 40, 520), (60, 120, 200), -1)
    cv2.circle(img, (w // 2, 300), 110, (220, 80, 60), -1)
    for y in range(560, 840, 28):
        cv2.line(img, (50, y), (w - 50 - int(rng.integers(0, 200)), y), (40, 40, 40), 6)
    return img


def photo(quad, bg=(70, 100, 80), size=(1200, 900), noise=12, card=None):
    """A fake phone photo: the card warped onto `quad` over a noisy background."""
    card = make_card() if card is None else card
    W, H = size
    shot = np.clip(np.full((H, W, 3), bg) + rng.normal(0, noise, (H, W, 3)), 0, 255).astype(np.uint8)
    ch, cw = card.shape[:2]
    m = cv2.getPerspectiveTransform(np.float32([[0, 0], [cw, 0], [cw, ch], [0, ch]]), np.float32(quad))
    warped = cv2.warpPerspective(card, m, (W, H))
    mask = cv2.warpPerspective(np.full((ch, cw), 255, np.uint8), m, (W, H))
    shot[mask > 0] = warped[mask > 0]
    return Image.fromarray(shot)


STRAIGHT = [[350, 100], [850, 100], [850, 800], [350, 800]]
ROTATED = [[420, 90], [880, 230], [700, 830], [250, 640]]
TILTED = [[330, 120], [880, 160], [820, 800], [400, 760]]


@pytest.mark.parametrize("quad", [STRAIGHT, ROTATED, TILTED], ids=["straight", "rotated", "tilted"])
def test_card_is_found_and_straightened(quad):
    crop = cardcrop.crop_card(photo(quad))
    assert crop.found and crop.image.size == (cardcrop.OUT_WIDTH, cardcrop.OUT_HEIGHT)
    px = np.asarray(crop.image)
    assert px[10, 10, 0] > 200 and px[10, 10, 2] < 120                 # top-left is the yellow border
    cy, cx = round(300 / 880 * cardcrop.OUT_HEIGHT), cardcrop.OUT_WIDTH // 2
    assert px[cy, cx, 0] > 180 and px[cy, cx, 2] < 120                 # the red circle is in the middle: upright


def test_works_on_a_light_table_and_a_noisy_background():
    assert cardcrop.crop_card(photo(STRAIGHT, bg=(225, 225, 220))).found
    assert cardcrop.crop_card(photo(ROTATED, noise=45)).found


def test_card_lying_on_its_side_becomes_portrait():
    crop = cardcrop.crop_card(photo([[900, 150], [900, 780], [200, 780], [200, 150]]))
    assert crop.found and crop.image.size == (cardcrop.OUT_WIDTH, cardcrop.OUT_HEIGHT)


def test_no_card_falls_back_to_the_whole_photo_turned_portrait():
    blank = Image.fromarray(np.clip(np.full((900, 1200, 3), 120) + rng.normal(0, 25, (900, 1200, 3)), 0, 255)
                            .astype(np.uint8))
    crop = cardcrop.crop_card(blank)
    assert not crop.found and crop.image.size == (900, 1200) and crop.whole is crop.image


def test_whole_photo_is_kept_as_a_second_attempt_when_a_card_is_found():
    crop = cardcrop.crop_card(photo(STRAIGHT))
    assert crop.found and crop.whole.size == (900, 1200)               # landscape photo, rotated upright


def test_without_opencv_the_whole_photo_is_used(monkeypatch):
    monkeypatch.setattr(cardcrop, "cv2", None)
    crop = cardcrop.crop_card(photo(STRAIGHT))
    assert not crop.found and crop.image.size == (900, 1200)


def test_encode_stays_under_the_api_limit_even_for_noise():
    noise = Image.fromarray(rng.integers(0, 255, (2200, 1600, 3), dtype=np.uint8))
    data = cardcrop.encode_jpeg(noise)
    assert len(data) <= cardcrop.MAX_JPEG_BYTES and data[:2] == b"\xff\xd8"


def test_encode_gives_up_clearly_when_nothing_fits():
    with pytest.raises(cardcrop.CropError):
        cardcrop.encode_jpeg(Image.new("RGB", (400, 560), "red"), max_bytes=50)
