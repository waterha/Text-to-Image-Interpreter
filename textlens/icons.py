"""托盘图标：陶土色圆角方块上一枚纸色菱形，纯 Python 构造，不依赖外部文件。

ICO 采用 32 位 BGRA 像素 + 1 位 AND 掩码：方角外的像素由掩码抠掉，
方块与菱形边缘在 32×32 下手工抗锯齿（半覆盖像素混入邻色）。
"""

import struct

from textlens.theme import ACCENT, PAPER


def _rgb(hex_color):
    value = hex_color.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _mix(foreground, background, alpha):
    return tuple(round(foreground[i] * alpha + background[i] * (1 - alpha)) for i in range(3))


def _coverage(distance, edge, core):
    """曼哈顿距离到菱形覆盖度的映射，core..edge 之间线性过渡。"""
    if distance <= core:
        return 1.0
    if distance >= edge:
        return 0.0
    return edge - distance


def pixels(size=32):
    """返回 (32 位 BGRA 像素行, 1 位 AND 掩码字节串)，均为 BMP 行序。"""
    paper, accent = _rgb(PAPER), _rgb(ACCENT)
    edge_paper, edge_accent = _mix(paper, accent, .5), _mix(paper, accent, .12)
    corner = size * .38
    diamond_center = (size - 1) / 2
    diamond_radius = size * .30
    rows, mask_words = [], []
    for y in range(size - 1, -1, -1):
        row, word = b"", 0
        for x in range(size):
            dx = max(corner - x, x - (size - 1 - corner), 0)
            dy = max(corner - y, y - (size - 1 - corner), 0)
            outside = (dx * dx + dy * dy) ** .5
            if outside >= corner:
                word |= 1 << (size - 1 - x)
                row += b"\x00\x00\x00\x00"
                continue
            distance = abs(x - diamond_center) + abs(y - diamond_center)
            coverage = _coverage(distance, diamond_radius, diamond_radius - 1.2)
            color = _mix(paper, accent, coverage)
            # 两种颜色的交界处补一层过渡色，弱化 1 像素宽的锯齿。
            if .05 < coverage < .95 and distance <= diamond_radius:
                color = edge_paper if coverage > .5 else edge_accent
            row += struct.pack("BBBB", color[2], color[1], color[0], 255)
        rows.append(row)
        mask_words.append(word)
    return b"".join(rows), struct.pack(f"<{len(mask_words)}I", *mask_words)


def bitmap(size=32):
    image, _ = pixels(size)
    return image


def and_mask(size=32):
    return pixels(size)[1]


def icon_bytes(size=32):
    """构造一个单幅 32 位 ICO 文件内容。"""
    image, mask = pixels(size)
    data_size = 40 + len(image) + len(mask)
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, data_size, 22)
    bmp = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, data_size, 0, 0, 0, 0)
    return header + entry + bmp + image + mask
