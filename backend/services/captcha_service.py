"""Stateless image captcha.

The answer is never stored server-side: the token is an HMAC of
"answer:timestamp" signed with JWT_SECRET, so verification re-derives the
signature from whatever the user typed. No Redis, no session table.
"""
import io
import hmac
import hashlib
import base64
import random
import string
import time
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from core.config import get_settings

settings = get_settings()
_CHARSET = string.ascii_uppercase.replace("O", "").replace("I", "") + string.digits.replace("0", "")

# "arial.ttf" only resolves on Windows. On Linux / the Docker image it raises,
# and the old fallback (ImageFont.load_default() with no size) is a ~8px bitmap
# font, which rendered a 140x50 captcha as four unreadable specks.
_FONT_CANDIDATES = (
    "arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
_FONT_SIZE = 32


def _load_font():
    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, _FONT_SIZE)
        except Exception:
            continue
    try:
        # Pillow >= 10.1 can scale the built-in font — no system font needed.
        return ImageFont.load_default(size=_FONT_SIZE)
    except TypeError:
        return ImageFont.load_default()


def _make_token(text: str, timestamp: int) -> str:
    """Sign captcha text + timestamp with JWT secret so it can't be forged."""
    msg = f"{text.upper()}:{timestamp}"
    sig = hmac.new(settings.jwt_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return f"{timestamp}:{sig}"


def generate_captcha() -> tuple[str, str]:
    """
    Returns (base64_png_image, signed_token).
    The token encodes the correct answer and a timestamp (valid for 5 min).
    """
    text = "".join(random.choices(_CHARSET, k=4))
    timestamp = int(time.time())
    token = _make_token(text, timestamp)

    # ── Draw image ──────────────────────────────────────────────────────────
    width, height = 140, 50
    img = Image.new("RGB", (width, height), color=(15, 15, 20))
    draw = ImageDraw.Draw(img)

    # Background noise lines
    for _ in range(6):
        x1, y1 = random.randint(0, width), random.randint(0, height)
        x2, y2 = random.randint(0, width), random.randint(0, height)
        draw.line([(x1, y1), (x2, y2)], fill=(random.randint(60, 120),) * 3, width=1)

    # Characters — spaced evenly across the image and vertically centred using
    # the glyph's real bounding box, so it stays readable with any font.
    font = _load_font()
    slot = width / (len(text) + 1)
    for i, ch in enumerate(text, start=1):
        color = (
            random.randint(180, 255),
            random.randint(180, 255),
            random.randint(180, 255),
        )
        try:
            box = draw.textbbox((0, 0), ch, font=font)
            ch_w, ch_h = box[2] - box[0], box[3] - box[1]
            off_x, off_y = box[0], box[1]
        except Exception:
            ch_w, ch_h, off_x, off_y = _FONT_SIZE // 2, _FONT_SIZE, 0, 0
        x = slot * i - ch_w / 2 - off_x + random.randint(-3, 3)
        y = (height - ch_h) / 2 - off_y + random.randint(-3, 3)
        draw.text((x, y), ch, fill=color, font=font)

    # Dots noise
    for _ in range(60):
        x, y = random.randint(0, width - 1), random.randint(0, height - 1)
        draw.point((x, y), fill=(random.randint(80, 160),) * 3)

    img = img.filter(ImageFilter.SMOOTH)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    image_data = f"data:image/png;base64,{b64}"

    return image_data, token


def verify_captcha(user_input: str, token: str, max_age_seconds: int = 300) -> bool:
    """Verify user-supplied text against the signed token."""
    try:
        parts = token.split(":", 1)
        if len(parts) != 2:
            return False
        timestamp_str, sig = parts
        timestamp = int(timestamp_str)

        if int(time.time()) - timestamp > max_age_seconds:
            return False  # expired

        # We don't store the original text — we re-derive expected sig using the input
        expected_msg = f"{user_input.upper()}:{timestamp}"
        expected_sig = hmac.new(
            settings.jwt_secret.encode(), expected_msg.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(sig, expected_sig)
    except Exception:
        return False
