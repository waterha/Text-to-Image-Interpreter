import asyncio
import io

from PIL import Image, ImageGrab

from til.diagnostics import exception as log_exception


def recognize_region(bounds, max_chars):
    left, top, right, bottom = map(int, bounds)
    if right - left < 8 or bottom - top < 8:
        return None
    try:
        image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
        scale = max(1, min(3, 1200 // max(image.width, image.height)))
        if scale > 1:
            image = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
        encoded = io.BytesIO()
        image.save(encoded, format='PNG')
        text = asyncio.run(_recognize_png(encoded.getvalue()))
        text = ' '.join(text.split())[:max_chars].strip() if text else ''
        return text if len(text) >= 2 else None
    except Exception as error:
        log_exception("screen OCR failed", error)
        return None


async def _recognize_png(data):
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(data)
    await writer.store_async()
    await writer.flush_async()
    writer.detach_stream()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_user_profile_languages()
    if not engine:
        return ''
    result = await engine.recognize_async(bitmap)
    return result.text
