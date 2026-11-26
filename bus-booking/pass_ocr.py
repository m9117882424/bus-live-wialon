import io
import re
import subprocess
from threading import BoundedSemaphore

from PIL import Image, ImageFilter, ImageOps

MAX_FRAME_BYTES = 512 * 1024
_ocr_slots = BoundedSemaphore(2)


def recognize_pass(frame: bytes) -> str | None:
    with Image.open(io.BytesIO(frame), formats=['PNG']) as image:
        width, height = image.size
        if not (1 <= width <= 640 and 1 <= height <= 240):
            raise ValueError('Invalid frame dimensions')
        pixels = image.convert('RGB')

    # Keep the red print and discard dark text such as the ID number.
    mask = Image.new('L', pixels.size)
    mask.putdata([
        0 if red - max(green, blue) >= 28 and red >= max(green, blue) * 1.2 else 255
        for red, green, blue in pixels.getdata()
    ])
    bounds = ImageOps.invert(mask).getbbox()
    if bounds is None or bounds[3] - bounds[1] < 12:
        return None
    digits = mask.crop(bounds)
    scale = max(1, 70 // digits.height)
    digits = digits.resize((digits.width * scale, digits.height * scale), Image.Resampling.LANCZOS)
    prepared = io.BytesIO()
    ImageOps.expand(digits.filter(ImageFilter.MinFilter(3)), border=20, fill=255).save(prepared, 'PNG')

    if not _ocr_slots.acquire(blocking=False):
        raise BlockingIOError('OCR is busy')
    try:
        result = subprocess.run(
            ['tesseract', 'stdin', 'stdout', '--psm', '7', '-c', 'tessedit_char_whitelist=0123456789'],
            input=prepared.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=3, check=True,
        )
    finally:
        _ocr_slots.release()
    number = result.stdout.decode('ascii', errors='replace').strip()
    return number if re.fullmatch(r'[0-9]{6}', number) else None
