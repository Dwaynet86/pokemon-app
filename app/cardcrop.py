"""Find the card in a photo, straighten it, and shrink it for the scan API.

The TCG Tracking scan endpoint does no border detection or cropping: it expects an upright,
mostly full-card image of at most 100 KB. A phone photo of a card on a table has to be prepared
first, which is what this module does:

  1. look for the card's outline (a roughly 63:88 quadrilateral) with edge detection and two
     thresholding passes, and keep the best one;
  2. warp it flat (corrects angle and perspective) into a portrait image;
  3. encode it as a small JPEG.

If no outline is found (or the card already fills the whole frame) the whole photo is used
instead, turned upright if it is landscape. OpenCV is optional at import time: without it the
whole-photo fallback is used.
"""
import io
from dataclasses import dataclass

from PIL import Image

try:  # opencv-python-headless
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = np = None

CARD_ASPECT = 63 / 88          # width / height of a Pokemon card
OUT_WIDTH = 420                # size of the straightened card
OUT_HEIGHT = round(OUT_WIDTH / CARD_ASPECT)
DETECT_EDGE = 1000             # longest edge used while searching (speed)
MIN_AREA = 0.12                # the card must cover at least this share of the photo
MAX_AREA = 0.96                # ...and a "card" covering everything is just the frame
ASPECT_TOLERANCE = 0.10        # accepted distance from CARD_ASPECT after straightening
MAX_JPEG_BYTES = 95_000        # the API's limit is 100 KB of decoded image

# (width in px, JPEG quality), tried in order until the file is small enough
ENCODE_STEPS = [(420, 85), (380, 80), (340, 75), (300, 70), (260, 65), (220, 60)]


class CropError(Exception):
    """The photo couldn't be prepared. The message is safe to show."""


@dataclass
class Crop:
    image: Image.Image   # what to scan first: the straightened card, or the whole photo if none was found
    found: bool          # False: no card outline was found, so `image` is the whole photo
    whole: Image.Image   # the whole photo, upright: the second attempt when the crop doesn't match


def _order(points):
    """Corners as top-left, top-right, bottom-right, bottom-left."""
    pts = np.asarray(points, dtype="float32").reshape(4, 2)
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype="float32")


def _sides(quad):
    tl, tr, br, bl = quad
    width = (np.linalg.norm(tr - tl) + np.linalg.norm(br - bl)) / 2
    height = (np.linalg.norm(bl - tl) + np.linalg.norm(br - tr)) / 2
    return float(width), float(height)


def _score(quad, image_area: float, shape) -> float | None:
    """How good a card outline `quad` is (None = not a card)."""
    width, height = _sides(quad)
    if min(width, height) < 20:
        return None
    aspect = min(width, height) / max(width, height)
    if abs(aspect - CARD_ASPECT) > ASPECT_TOLERANCE:
        return None
    area = float(cv2.contourArea(quad.astype("float32")))
    share = area / image_area
    if not MIN_AREA <= share <= MAX_AREA:
        return None
    h, w = shape[:2]
    margin = 0.01 * max(h, w)
    on_border = sum(1 for x, y in quad if x < margin or y < margin or x > w - margin or y > h - margin)
    if on_border >= 3:         # hugging the photo's edge: that's the frame, not a card
        return None
    return share * (1 - abs(aspect - CARD_ASPECT))


def _quads(gray):
    """Candidate quadrilaterals from three different views of the photo."""
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(blurred, 40, 120), np.ones((3, 3), np.uint8), iterations=2)
    _, otsu = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    close = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    for mask in (edges, cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, close),
                 cv2.morphologyEx(255 - otsu, cv2.MORPH_CLOSE, close)):
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
            hull = cv2.convexHull(contour)
            perimeter = cv2.arcLength(hull, True)
            for eps in (0.02, 0.04, 0.06):
                approx = cv2.approxPolyDP(hull, eps * perimeter, True)
                if len(approx) == 4:
                    yield _order(approx)
                    break
            else:   # no clean 4-corner polygon: use the tightest rotated rectangle instead
                yield _order(cv2.boxPoints(cv2.minAreaRect(hull)))


def find_card(rgb):
    """Corners (in `rgb`'s own coordinates) of the best card outline, or None."""
    h, w = rgb.shape[:2]
    scale = min(1.0, DETECT_EDGE / max(h, w))
    small = cv2.resize(rgb, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else rgb
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    area = float(gray.shape[0] * gray.shape[1])
    best, best_score = None, 0.0
    for quad in _quads(gray):
        score = _score(quad, area, gray.shape)
        if score is not None and score > best_score:
            best, best_score = quad, score
    return None if best is None else best / scale


def _straighten(rgb, quad) -> Image.Image:
    width, height = _sides(quad)
    if width > height:                       # lying on its side: make the short edges the top/bottom
        quad = np.roll(quad, -1, axis=0)
    centre = quad.mean(axis=0)
    quad = centre + (quad - centre) * 0.99   # trim the hairline of background along the edges
    target = np.array([[0, 0], [OUT_WIDTH - 1, 0], [OUT_WIDTH - 1, OUT_HEIGHT - 1], [0, OUT_HEIGHT - 1]], "float32")
    matrix = cv2.getPerspectiveTransform(quad.astype("float32"), target)
    flat = cv2.warpPerspective(rgb, matrix, (OUT_WIDTH, OUT_HEIGHT), flags=cv2.INTER_AREA,
                               borderMode=cv2.BORDER_REPLICATE)
    return Image.fromarray(flat)


def _whole_photo(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    if img.width > img.height:               # a card is portrait
        img = img.rotate(90, expand=True)
    return img


def crop_card(img: Image.Image) -> Crop:
    """The card in `img`, straightened. Falls back to the whole photo when no outline is found."""
    img = img.convert("RGB")
    whole = _whole_photo(img)
    if cv2 is not None:
        try:
            quad = find_card(np.asarray(img))
            if quad is not None:
                return Crop(_straighten(np.asarray(img), quad), True, whole)
        except cv2.error:
            pass
    return Crop(whole, False, whole)


def encode_jpeg(img: Image.Image, max_bytes: int = MAX_JPEG_BYTES) -> bytes:
    """A JPEG under `max_bytes`, shrinking the image until it fits."""
    img = img.convert("RGB")
    data = b""
    for width, quality in ENCODE_STEPS:
        step = img.copy()
        if step.width > width:
            step = step.resize((width, max(1, round(step.height * width / step.width))), Image.LANCZOS)
        buf = io.BytesIO()
        step.save(buf, "JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            return data
    raise CropError("That photo couldn't be shrunk enough to scan. Try a simpler background.")
