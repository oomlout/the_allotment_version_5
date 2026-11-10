"""One fixed, packaged payload for every platform test."""
import hashlib
from pathlib import Path


SAMPLE_TEXT = "The Allotment test post.\nImage and URL: https://example.com/"
SAMPLE_IMAGE = Path(__file__).parent / "static" / "test-image.png"


def sample_signature():
    return hashlib.sha256(SAMPLE_TEXT.encode("utf-8") + SAMPLE_IMAGE.read_bytes()).hexdigest()
