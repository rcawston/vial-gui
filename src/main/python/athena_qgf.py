import io
import math
from colorsys import rgb_to_hsv

from PIL import Image, ImageChops


RGB565_FORMAT = {
    "image_format": "IMAGE_FORMAT_RGB565",
    "bpp": 16,
    "has_palette": False,
    "num_colors": 65536,
    "image_format_byte": 0x08,
}


def _o8(i):
    return bytes((i & 0xFF,))


def _o16(i):
    return int(i & 0xFFFF).to_bytes(2, byteorder="little")


def _o24(i):
    return int(i & 0xFFFFFF).to_bytes(3, byteorder="little")


def _o32(i):
    return int(i & 0xFFFFFFFF).to_bytes(4, byteorder="little")


class _QGFBlockHeader:
    def __init__(self, type_id, length):
        self.type_id = type_id
        self.length = length

    def write(self, fp):
        fp.write(_o8(self.type_id))
        fp.write(_o8((~self.type_id) & 0xFF))
        fp.write(_o24(self.length))


class _QGFGraphicsDescriptor:
    TYPE_ID = 0x00
    MAGIC = 0x464751

    def __init__(self, width, height, frame_count):
        self.width = width
        self.height = height
        self.frame_count = frame_count
        self.total_file_size = 0

    def write(self, fp):
        _QGFBlockHeader(self.TYPE_ID, 18).write(fp)
        fp.write(_o24(self.MAGIC))
        fp.write(_o8(1))
        fp.write(_o32(self.total_file_size))
        fp.write(_o32((~self.total_file_size) & 0xFFFFFFFF))
        fp.write(_o16(self.width))
        fp.write(_o16(self.height))
        fp.write(_o16(self.frame_count))


class _QGFFrameOffsets:
    TYPE_ID = 0x01

    def __init__(self, frame_count):
        self.offsets = [0xFFFFFFFF] * frame_count

    def write(self, fp):
        _QGFBlockHeader(self.TYPE_ID, len(self.offsets) * 4).write(fp)
        for offset in self.offsets:
            fp.write(_o32(offset))


class _QGFFrameDescriptor:
    TYPE_ID = 0x02

    def __init__(self, *, is_delta, compression, delay_ms):
        self.is_delta = is_delta
        self.compression = compression
        self.delay_ms = delay_ms

    def write(self, fp):
        _QGFBlockHeader(self.TYPE_ID, 6).write(fp)
        flags = 0x02 if self.is_delta else 0x00
        fp.write(_o8(RGB565_FORMAT["image_format_byte"]))
        fp.write(_o8(flags))
        fp.write(_o8(self.compression))
        fp.write(_o8(0xFF))
        fp.write(_o16(self.delay_ms))


class _QGFDeltaDescriptor:
    TYPE_ID = 0x04

    def __init__(self, bbox):
        self.left, self.top, self.right, self.bottom = bbox

    def write(self, fp):
        _QGFBlockHeader(self.TYPE_ID, 8).write(fp)
        fp.write(_o16(self.left))
        fp.write(_o16(self.top))
        fp.write(_o16(self.right))
        fp.write(_o16(self.bottom))


class _QGFDataDescriptor:
    TYPE_ID = 0x05

    def __init__(self, data):
        self.data = bytes(data)

    def write(self, fp):
        _QGFBlockHeader(self.TYPE_ID, len(self.data)).write(fp)
        fp.write(self.data)


def _rgb_to565(r, g, b):
    msb = ((r >> 3 & 0x1F) << 3) + (g >> 5 & 0x07)
    lsb = ((g >> 2 & 0x07) << 5) + (b >> 3 & 0x1F)
    return msb, lsb


def _convert_rgb565(im):
    im = im.convert("RGB")
    red = im.tobytes("raw", "R")
    green = im.tobytes("raw", "G")
    blue = im.tobytes("raw", "B")
    out = []
    for r, g, b in zip(red, green, blue):
        out.extend(_rgb_to565(r, g, b))
    return out


def _compress_bytes_qmk_rle(data):
    output = []
    temp = []
    repeat = False

    def append_byte(c):
        output.append(c)

    def append_range(r):
        append_byte(127 + len(r))
        output.extend(r)

    for idx in range(0, len(data) + 1):
        end = idx == len(data)
        if not end:
            temp.append(data[idx])
            if len(temp) <= 1:
                continue

        if repeat:
            if temp[-1] != temp[-2]:
                repeat = False
            if (not repeat) or len(temp) == 128 or end:
                append_byte(len(temp) if end else len(temp) - 1)
                append_byte(temp[0])
                temp = [temp[-1]]
                repeat = False
        else:
            if len(temp) >= 2 and temp[-1] == temp[-2]:
                repeat = True
                if len(temp) > 2:
                    append_range(temp[0:len(temp) - 2])
                    temp = [temp[-1], temp[-1]]
                continue
            if len(temp) == 128 or end:
                append_range(temp)
                temp = []
                repeat = False
    return output


def _compress_frame(frame, previous_frame, use_rle, use_deltas):
    graphic_data = _convert_rgb565(frame)
    raw_data = graphic_data
    rle_data = _compress_bytes_qmk_rle(graphic_data) if use_rle else None
    use_raw = (not use_rle) or len(raw_data) <= len(rle_data)
    image_data = raw_data if use_raw else rle_data

    use_delta = False
    bbox = None

    if use_deltas and previous_frame is not None:
        diff = ImageChops.difference(frame, previous_frame)
        bbox = diff.getbbox()
        if bbox:
            delta_frame = frame.crop(bbox)
            delta_raw_data = _convert_rgb565(delta_frame)
            delta_rle_data = _compress_bytes_qmk_rle(delta_raw_data) if use_rle else None
            delta_use_raw = (not use_rle) or len(delta_raw_data) <= len(delta_rle_data)
            delta_image_data = delta_raw_data if delta_use_raw else delta_rle_data
            if len(delta_image_data) + 8 < len(image_data):
                raw_data = delta_raw_data
                rle_data = delta_rle_data
                use_raw = delta_use_raw
                image_data = delta_image_data
                use_delta = True

        bbox = bbox or (0, 0, frame.size[0], frame.size[1])
        bbox = [bbox[0], bbox[1], bbox[2] - 1, bbox[3] - 1]

    return {
        "image_data": image_data,
        "use_raw": use_raw,
        "use_delta": use_delta,
        "bbox": bbox,
    }


def encode_qgf(frames, delays_ms, *, use_rle=True, use_deltas=True):
    if not frames:
        raise ValueError("at least one frame is required")

    width, height = frames[0].size
    if any(frame.size != (width, height) for frame in frames):
        raise ValueError("all frames must have the same size")

    buf = io.BytesIO()
    graphics = _QGFGraphicsDescriptor(width, height, len(frames))
    graphics_pos = buf.tell()
    graphics.write(buf)

    offsets = _QGFFrameOffsets(len(frames))
    offsets_pos = buf.tell()
    offsets.write(buf)

    previous = None
    for idx, frame in enumerate(frames):
        compressed = _compress_frame(frame, previous, use_rle, use_deltas)
        offsets.offsets[idx] = buf.tell()

        descriptor = _QGFFrameDescriptor(
            is_delta=compressed["use_delta"],
            compression=0x00 if compressed["use_raw"] else 0x01,
            delay_ms=max(1, int(delays_ms[idx])),
        )
        descriptor.write(buf)

        if compressed["use_delta"]:
            _QGFDeltaDescriptor(compressed["bbox"]).write(buf)

        _QGFDataDescriptor(compressed["image_data"]).write(buf)
        previous = frame

    graphics.total_file_size = buf.tell()
    buf.seek(graphics_pos)
    graphics.write(buf)
    buf.seek(offsets_pos)
    offsets.write(buf)
    return buf.getvalue()


def _i16(data, offset):
    return int.from_bytes(data[offset:offset + 2], byteorder="little")


def _i24(data, offset):
    return int.from_bytes(data[offset:offset + 3], byteorder="little")


def _i32(data, offset):
    return int.from_bytes(data[offset:offset + 4], byteorder="little")


def _validate_block(data, offset, expected_type):
    if offset + 5 > len(data):
        raise ValueError("truncated QGF block")
    type_id = data[offset]
    neg_type_id = data[offset + 1]
    if type_id != expected_type or neg_type_id != ((~expected_type) & 0xFF):
        raise ValueError("unexpected QGF block")
    return _i24(data, offset + 2)


def parse_qgf_header(data):
    if len(data) < 23:
        raise ValueError("QGF too small")
    length = _validate_block(data, 0, _QGFGraphicsDescriptor.TYPE_ID)
    if length != 18:
        raise ValueError("invalid QGF graphics descriptor")
    if _i24(data, 5) != _QGFGraphicsDescriptor.MAGIC or data[8] != 1:
        raise ValueError("invalid QGF header")
    total_size = _i32(data, 9)
    if (_i32(data, 13) ^ 0xFFFFFFFF) != total_size:
        raise ValueError("invalid QGF size negation")
    return {
        "total_size": total_size,
        "width": _i16(data, 17),
        "height": _i16(data, 19),
        "frame_count": _i16(data, 21),
    }


def _decode_qmk_rle(data, expected_length):
    out = bytearray()
    idx = 0
    while idx < len(data) and len(out) < expected_length:
        control = data[idx]
        idx += 1
        if control <= 127:
            if idx >= len(data):
                raise ValueError("truncated QGF RLE repeat")
            out.extend([data[idx]] * control)
            idx += 1
        else:
            count = control - 127
            out.extend(data[idx:idx + count])
            idx += count
    if len(out) != expected_length:
        raise ValueError("decoded QGF RLE length mismatch")
    return bytes(out)


def _rgb565_bytes_to_image(data, width, height):
    if len(data) != width * height * 2:
        raise ValueError("invalid RGB565 payload length")
    pixels = bytearray()
    for idx in range(0, len(data), 2):
        msb = data[idx]
        lsb = data[idx + 1]
        r = ((msb >> 3) & 0x1F) * 255 // 31
        g = (((msb & 0x07) << 3) | ((lsb >> 5) & 0x07)) * 255 // 63
        b = (lsb & 0x1F) * 255 // 31
        pixels.extend((r, g, b))
    return Image.frombytes("RGB", (width, height), bytes(pixels))


def decode_qgf_first_frame(data):
    header = parse_qgf_header(data)
    offset_table_length = _validate_block(data, 23, _QGFFrameOffsets.TYPE_ID)
    frame_count = header["frame_count"]
    if offset_table_length != frame_count * 4:
        raise ValueError("invalid QGF frame offset table")
    frame0_offset = _i32(data, 28)

    frame_length = _validate_block(data, frame0_offset, _QGFFrameDescriptor.TYPE_ID)
    if frame_length != 6:
        raise ValueError("invalid QGF frame descriptor")
    format_byte = data[frame0_offset + 5]
    flags = data[frame0_offset + 6]
    compression = data[frame0_offset + 7]
    if format_byte != RGB565_FORMAT["image_format_byte"]:
        raise ValueError("unsupported QGF image format")
    if flags & 0x02:
        raise ValueError("delta first frame is unsupported")

    data_offset = frame0_offset + 11
    payload_length = _validate_block(data, data_offset, _QGFDataDescriptor.TYPE_ID)
    payload = data[data_offset + 5:data_offset + 5 + payload_length]
    expected_size = header["width"] * header["height"] * 2
    if compression == 0x00:
        decoded = payload
    elif compression == 0x01:
        decoded = _decode_qmk_rle(payload, expected_size)
    else:
        raise ValueError("unsupported QGF compression")
    return _rgb565_bytes_to_image(decoded, header["width"], header["height"]), header


def _parse_qgf_offsets(data, header):
    offset_table_length = _validate_block(data, 23, _QGFFrameOffsets.TYPE_ID)
    frame_count = header["frame_count"]
    if offset_table_length != frame_count * 4:
        raise ValueError("invalid QGF frame offset table")
    offsets_start = 28
    return [_i32(data, offsets_start + idx * 4) for idx in range(frame_count)]


def _decode_qgf_frame(data, header, offset, previous_frame):
    frame_length = _validate_block(data, offset, _QGFFrameDescriptor.TYPE_ID)
    if frame_length != 6:
        raise ValueError("invalid QGF frame descriptor")
    format_byte = data[offset + 5]
    flags = data[offset + 6]
    compression = data[offset + 7]
    delay_ms = _i16(data, offset + 9)
    if format_byte != RGB565_FORMAT["image_format_byte"]:
        raise ValueError("unsupported QGF image format")

    is_delta = bool(flags & 0x02)
    data_offset = offset + 11
    bbox = None
    if is_delta:
        delta_length = _validate_block(data, data_offset, _QGFDeltaDescriptor.TYPE_ID)
        if delta_length != 8:
            raise ValueError("invalid QGF delta descriptor")
        bbox = (
            _i16(data, data_offset + 5),
            _i16(data, data_offset + 7),
            _i16(data, data_offset + 9),
            _i16(data, data_offset + 11),
        )
        data_offset += 13

    payload_length = _validate_block(data, data_offset, _QGFDataDescriptor.TYPE_ID)
    payload = data[data_offset + 5:data_offset + 5 + payload_length]

    if is_delta:
        if previous_frame is None:
            raise ValueError("delta first frame is unsupported")
        left, top, right, bottom = bbox
        width = right - left + 1
        height = bottom - top + 1
    else:
        width = header["width"]
        height = header["height"]

    expected_size = width * height * 2
    if compression == 0x00:
        decoded = payload
    elif compression == 0x01:
        decoded = _decode_qmk_rle(payload, expected_size)
    else:
        raise ValueError("unsupported QGF compression")

    image = _rgb565_bytes_to_image(decoded, width, height)
    if is_delta:
        composited = previous_frame.copy()
        composited.paste(image, (left, top))
        image = composited
    return image, delay_ms


def decode_qgf_frames(data):
    header = parse_qgf_header(data)
    offsets = _parse_qgf_offsets(data, header)
    frames = []
    delays = []
    previous = None
    for offset in offsets:
        frame, delay_ms = _decode_qgf_frame(data, header, offset, previous)
        frames.append(frame)
        delays.append(max(1, int(delay_ms)))
        previous = frame
    return frames, delays, header


def qgf_first_frame_region_length(data):
    header = parse_qgf_header(data)
    offset_table_length = _validate_block(data, 23, _QGFFrameOffsets.TYPE_ID)
    frame_count = header["frame_count"]
    if offset_table_length != frame_count * 4:
        raise ValueError("invalid QGF frame offset table")
    frame0_offset = _i32(data, 28)
    frame_length = _validate_block(data, frame0_offset, _QGFFrameDescriptor.TYPE_ID)
    if frame_length != 6:
        raise ValueError("invalid QGF frame descriptor")
    data_offset = frame0_offset + 11
    payload_length = _validate_block(data, data_offset, _QGFDataDescriptor.TYPE_ID)
    return data_offset + 5 + payload_length


UF2_FAMILY_ID_RP2040 = 0xE48BFF56
UF2_MAGIC_START0 = 0x0A324655
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
UF2_FLAG_FAMILY_ID = 0x00002000


def encode_uf2(payload, target_addr):
    block_size = 256
    num_blocks = (len(payload) + block_size - 1) // block_size
    out = bytearray()

    for block_no in range(num_blocks):
        chunk = payload[block_no * block_size:(block_no + 1) * block_size]
        chunk = chunk + b"\x00" * (block_size - len(chunk))
        header = (
            _o32(UF2_MAGIC_START0)
            + _o32(UF2_MAGIC_START1)
            + _o32(UF2_FLAG_FAMILY_ID)
            + _o32(target_addr + block_no * block_size)
            + _o32(block_size)
            + _o32(block_no)
            + _o32(num_blocks)
            + _o32(UF2_FAMILY_ID_RP2040)
        )
        padding = bytes(476 - len(header) - block_size)
        out.extend(header)
        out.extend(chunk)
        out.extend(padding)
        out.extend(_o32(UF2_MAGIC_END))

    return bytes(out)
