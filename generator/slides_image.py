"""Native Slides.com image-block serialization and editor-readback rules."""
from __future__ import annotations

import html
import os
from struct import unpack
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class ImageBlockError(ValueError):
    pass


def absolute_https_url(value, asset_origin=None):
    """Resolve an image value to the public HTTPS URL Slides must save."""
    if not isinstance(value, str) or not value.strip():
        raise ImageBlockError('Image block needs a non-empty value URL.')
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme == 'https' and parsed.netloc:
        return value
    if parsed.scheme or parsed.netloc:
        raise ImageBlockError('Image value must be an absolute HTTPS URL.')
    origin = (asset_origin or os.environ.get('PRESENTATION_ASSET_ORIGIN', '')).rstrip('/')
    origin_parts = urlsplit(origin)
    if origin_parts.scheme != 'https' or not origin_parts.netloc:
        raise ImageBlockError('Relative image values require an absolute HTTPS PRESENTATION_ASSET_ORIGIN.')
    return origin + '/' + value.lstrip('/')


def _png_dimensions(data):
    if len(data) >= 24 and data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16] == b'IHDR':
        return unpack('>II', data[16:24])
    return None


def _gif_dimensions(data):
    if len(data) >= 10 and data[:6] in (b'GIF87a', b'GIF89a'):
        return unpack('<HH', data[6:10])
    return None


def _jpeg_dimensions(data):
    if not data.startswith(b'\xff\xd8'):
        return None
    offset = 2
    while offset + 9 <= len(data):
        if data[offset] != 0xff:
            offset += 1
            continue
        marker = data[offset + 1]
        offset += 2
        if marker in (0xd8, 0xd9) or 0xd0 <= marker <= 0xd7:
            continue
        if offset + 2 > len(data):
            break
        length = unpack('>H', data[offset:offset + 2])[0]
        if length < 2 or offset + length > len(data):
            break
        if 0xc0 <= marker <= 0xc3 or 0xc5 <= marker <= 0xc7 or 0xc9 <= marker <= 0xcb or 0xcd <= marker <= 0xcf:
            return unpack('>HH', data[offset + 3:offset + 7])[::-1]
        offset += length
    return None


def probe_image_dimensions(url):
    """Read only the image header; citation assets are required to be public."""
    request = Request(url, headers={'Range': 'bytes=0-65535', 'User-Agent': 'SlidesImageMetadata/1.0'})
    try:
        with urlopen(request, timeout=20) as response:
            data = response.read(65536)
    except Exception as error:
        raise ImageBlockError('Could not read image metadata from the public image URL.') from error
    dimensions = _png_dimensions(data) or _gif_dimensions(data) or _jpeg_dimensions(data)
    if not dimensions or min(dimensions) <= 0:
        raise ImageBlockError('Image URL did not provide supported, nonzero PNG/GIF/JPEG dimensions.')
    return dimensions


def image_dimensions(block, url):
    width = block.get('natural-width', block.get('naturalWidth'))
    height = block.get('natural-height', block.get('naturalHeight'))
    if width is None or height is None:
        width, height = probe_image_dimensions(url)
    try:
        width, height = int(width), int(height)
    except (TypeError, ValueError) as error:
        raise ImageBlockError('Image natural dimensions must be integers.') from error
    if width <= 0 or height <= 0:
        raise ImageBlockError('Image natural dimensions must be nonzero.')
    return width, height


def serialize_rest_image_block(block, block_id, asset_origin=None):
    """Emit a complete native editor image block, never a lazy placeholder."""
    url = absolute_https_url(block.get('value', block.get('src')), asset_origin)
    natural_width, natural_height = image_dimensions(block, url)
    x, y, width, height = (float(block[key]) for key in ('x', 'y', 'width', 'height'))
    if width <= 0 or height <= 0:
        raise ImageBlockError('Image block width and height must be nonzero.')
    escaped = html.escape(url, quote=True)
    return (
        f'<div class="sl-block" data-block-type="image" data-block-id="{block_id}" '
        f'style="left:{x:g}px;top:{y:g}px;width:{width:g}px;height:{height:g}px;z-index:1;">'
        '<div class="sl-block-style" style="z-index:1;transform:rotate(0deg);">'
        '<div class="sl-block-content">'
        f'<img src="{escaped}" data-src="{escaped}" data-natural-width="{natural_width}" '
        f'data-natural-height="{natural_height}" style="width:100%;height:100%;object-fit:contain;" />'
        '</div></div></div>'
    )
