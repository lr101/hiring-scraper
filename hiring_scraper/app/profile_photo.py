"""Validate profile portraits before storing them."""
import binascii
from io import BytesIO
import struct

from PIL import Image, UnidentifiedImageError


def validate_png(data: bytes) -> None:
    """Check PNG framing, dimensions and decoded pixels."""
    if (len(data) < 45 or len(data) > 2 * 1024 * 1024 or not data.startswith(b'\x89PNG\r\n\x1a\n')
            or data[8:16] != b'\x00\x00\x00\rIHDR'):
        raise ValueError('Choose a valid PNG photo under 2 MiB.')
    width, height = struct.unpack('!II', data[16:24])
    if not (1 <= width <= 4096 and 1 <= height <= 4096):
        raise ValueError('Use a PNG photo no larger than 4096 pixels per side.')
    # Validate complete chunk framing and checksums before saving it.
    offset, image_data, ended = 8, False, False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError('The PNG photo is truncated or corrupt.')
        size = struct.unpack('!I', data[offset:offset + 4])[0]
        end = offset + size + 12
        if end > len(data):
            raise ValueError('The PNG photo is truncated or corrupt.')
        kind = data[offset + 4:offset + 8]
        checksum = struct.unpack('!I', data[end - 4:end])[0]
        if binascii.crc32(data[offset + 4:end - 4]) != checksum:
            raise ValueError('The PNG photo is truncated or corrupt.')
        if kind == b'IHDR' and offset != 8:
            raise ValueError('The PNG photo has an invalid image header.')
        if kind == b'IDAT' and size:
            image_data = True
        if kind == b'IEND':
            ended = size == 0 and end == len(data)
            break
        offset = end
    if not image_data or not ended:
        raise ValueError('The PNG photo is truncated or corrupt.')
    try:
        with Image.open(BytesIO(data), formats=['PNG']) as image:
            image.verify()
        with Image.open(BytesIO(data), formats=['PNG']) as image:
            image.load()  # Verify compressed pixels as well as chunk checksums.
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise ValueError('The PNG photo is truncated or corrupt.') from None
