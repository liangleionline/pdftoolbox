# -*- coding: utf-8 -*-
"""
把 RGB 像素编码为 JPEG —— 零 Python 依赖（纯标准库）。

有损照片类图片（WebP 等）嵌入 PDF 时若用 RGB+zlib 会膨胀数倍，
改用 JPEG(DCTDecode) 可把 PDF 体积降到与源图相当。

编码顺序：
  1. ffmpeg（系统命令，快）
  2. ImageMagick convert（系统命令）
  3. 纯 Python baseline JPEG 编码器（零依赖兜底，质量可选）

输出为标准 JPEG（JFIF，YUV 4:2:0）。
"""
import os
import subprocess
import struct
import sys
import tempfile


# ============ 标准量化表（T.81 K.1） ============
LUM_Q = [
    16, 11, 10, 16, 24, 40, 51, 61,
    12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56,
    14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77,
    24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101,
    72, 92, 95, 98, 112, 100, 103, 99,
]
CHR_Q = [
    17, 18, 24, 47, 99, 99, 99, 99,
    18, 21, 26, 66, 99, 99, 99, 99,
    24, 26, 56, 99, 99, 99, 99, 99,
    47, 66, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
    99, 99, 99, 99, 99, 99, 99, 99,
]

# 标准 zigzag 序（T.81 图 F.1）
ZIGZAG = [
    0, 1, 8, 16, 9, 2, 3, 10,
    17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34,
    27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36,
    29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46,
    53, 60, 61, 54, 47, 55, 62, 63,
]

# ============ 标准 Huffman 表（T.81 K.3~K.6，BITS+HUFFVAL） ============
# 每表：(BITS 16项, HUFFVAL)
STD_HDC_L = ([0,1,5,1,1,1,1,1,1,0,0,0,0,0,0,0],
             [0,1,2,3,4,5,6,7,8,9,10,11])
STD_HAC_L = ([0,2,1,3,3,2,4,3,5,5,4,4,0,0,1,125],
             [1,2,3,0,4,17,5,18,33,49,65,6,19,81,97,7,34,113,20,50,129,145,161,8,35,66,177,193,21,82,209,240,36,51,98,114,130,9,10,22,23,24,25,26,37,38,39,40,41,42,52,53,54,55,56,57,58,67,68,69,70,71,72,73,74,83,84,85,86,87,88,89,90,99,100,101,102,103,104,105,106,115,116,117,118,119,120,121,122,131,132,133,134,135,136,137,138,146,147,148,149,150,151,152,153,154,162,163,164,165,166,167,168,169,170,178,179,180,181,182,183,184,185,186,194,195,196,197,198,199,200,201,202,210,211,212,213,214,215,216,217,218,225,226,227,228,229,230,231,232,233,234,241,242,243,244,245,246,247,248,249,250])
STD_HDC_C = ([0,3,1,1,1,1,1,1,1,1,1,0,0,0,0,0],
             [0,1,2,3,4,5,6,7,8,9,10,11])
STD_HAC_C = ([0,2,1,2,4,4,3,4,7,5,4,4,0,1,2,119],
             [0,1,2,3,17,4,5,33,49,6,18,65,81,7,97,113,19,34,50,129,8,20,66,145,161,177,193,9,35,51,82,240,21,98,114,209,10,22,36,52,225,37,241,23,24,25,26,38,39,40,41,42,53,54,55,56,57,58,67,68,69,70,71,72,73,74,83,84,85,86,87,88,89,90,99,100,101,102,103,104,105,106,115,116,117,118,119,120,121,122,130,131,132,133,134,135,136,137,138,146,147,148,149,150,151,152,153,154,162,163,164,165,166,167,168,169,170,178,179,180,181,182,183,184,185,186,194,195,196,197,198,199,200,201,202,210,211,212,213,214,215,216,217,218,226,227,228,229,230,231,232,233,234,242,243,244,245,246,247,248,249,250])


class _BitWriter:
    __slots__ = ("data", "acc", "n")

    def __init__(self):
        self.data = bytearray()
        self.acc = 0
        self.n = 0

    def write(self, code, length):
        self.acc = (self.acc << length) | code
        self.n += length
        while self.n >= 8:
            self.n -= 8
            b = (self.acc >> self.n) & 0xFF
            self.data.append(b)
            if b == 0xFF:
                self.data.append(0x00)   # 字节填充

    def finish(self):
        if self.n > 0:
            b = (self.acc << (8 - self.n)) & 0xFF
            self.data.append(b)
            if b == 0xFF:
                self.data.append(0x00)
        return bytes(self.data)


def _build_codes(bits, vals):
    """按 JPEG 规则由 BITS/HUFFVAL 生成 {symbol: (code, length)}。"""
    code = 0
    k = 0
    table = {}
    for length in range(1, 17):
        for _ in range(bits[length - 1]):
            table[vals[k]] = (code, length)
            k += 1
            code += 1
        code <<= 1
    return table


_TBL = {}
def _tables():
    if not _TBL:
        _TBL["dc_l"] = _build_codes(*STD_HDC_L)
        _TBL["ac_l"] = _build_codes(*STD_HAC_L)
        _TBL["dc_c"] = _build_codes(*STD_HDC_C)
        _TBL["ac_c"] = _build_codes(*STD_HAC_C)
    return _TBL


def _quality_scale(quality):
    quality = max(1, min(100, quality))
    if quality < 50:
        return 5000 // quality
    return 200 - quality * 2


def _qtable(base, scale):
    q = []
    for v in base:
        s = (v * scale + 50) // 100
        q.append(max(1, min(255, s)))
    return q


def _rgb_to_ycbcr(rgb, w, h):
    """RGB → Y(全分辨率), Cb, Cr(4:2:0 半分辨率)。返回三个 float list。"""
    Y = [0.0] * (w * h)
    CbW = (w + 1) // 2
    CbH = (h + 1) // 2
    Cb = [0.0] * (CbW * CbH)
    Cr = [0.0] * (CbW * CbH)
    for y in range(h):
        row = y * w
        for x in range(w):
            i = row + x
            r = rgb[i * 3]
            g = rgb[i * 3 + 1]
            b = rgb[i * 3 + 2]
            Y[i] = 0.299 * r + 0.587 * g + 0.114 * b - 128.0
    # 2x2 平均采样 Cb/Cr
    for y in range(0, h, 2):
        cb_row = (y // 2) * CbW
        y0 = y
        y1 = min(y + 1, h - 1)
        for x in range(0, w, 2):
            x1 = min(x + 1, w - 1)
            cb = cr = 0.0
            for yy in (y0, y1):
                for xx in (x, x1):
                    i = yy * w + xx
                    r = rgb[i * 3]
                    g = rgb[i * 3 + 1]
                    b = rgb[i * 3 + 2]
                    cb += -0.168736 * r - 0.331264 * g + 0.5 * b
                    cr += 0.5 * r - 0.418688 * g - 0.081312 * b
            cb /= 4.0
            cr /= 4.0
            Cb[cb_row + x // 2] = cb
            Cr[cb_row + x // 2] = cr
    return Y, Cb, Cr


_COS = {}
def _fdct(block):
    """8x8 浮点 DCT-II（输入已减 128）。返回 8x8 系数。"""
    # 预计算 cos 表（标准 DCT-II：cos((2x+1)uπ/16)，无额外 0.5 因子）
    cosc = _COS.get("c")
    if cosc is None:
        cosc = [[0.0] * 8 for _ in range(8)]
        for u in range(8):
            for x in range(8):
                cosc[u][x] = __import__("math").cos((2 * x + 1) * u * 3.141592653589793 / 16)
        _COS["c"] = cosc
    out = [[0.0] * 8 for _ in range(8)]
    for u in range(8):
        cu = 0.7071067811865476 if u == 0 else 1.0
        for v in range(8):
            cv = 0.7071067811865476 if v == 0 else 1.0
            s = 0.0
            for y in range(8):
                for x in range(8):
                    s += block[y][x] * cosc[u][x] * cosc[v][y]
            out[u][v] = 0.25 * cu * cv * s
    return out


def _encode_block(samples, width, height, bx, by, qtable, dc_tbl, ac_tbl, bw, prev_dc):
    """编码单个 8x8 块，返回新的 DC 预测器。"""
    block = [[0.0] * 8 for _ in range(8)]
    for yy in range(8):
        y = by * 8 + yy
        if y >= height:
            sy = height - 1
        else:
            sy = y
        for xx in range(8):
            x = bx * 8 + xx
            if x >= width:
                sx = width - 1
            else:
                sx = x
            block[yy][xx] = samples[sy * width + sx]
    coef = _fdct(block)
    # 量化 + zigzag（ZIGZAG[z] 是块内线性位置，对应 行=idx//8、列=idx%8）
    zz = []
    for z in range(64):
        u, v = divmod(ZIGZAG[z], 8)
        c = coef[u][v]
        zz.append(int(round(c / qtable[ZIGZAG[z]])))
    # DC 差分
    dc = zz[0]
    diff = dc - prev_dc
    prev_dc = dc
    cat, mag = _category(diff)
    bw.write(dc_tbl[cat][0], dc_tbl[cat][1])
    if cat:
        bw.write(mag, cat)
    # AC
    run = 0
    for k in range(1, 64):
        if zz[k] == 0:
            run += 1
            continue
        while run >= 16:
            bw.write(ac_tbl[0xF0][0], ac_tbl[0xF0][1])   # ZRL
            run -= 16
        cat, mag = _category(zz[k])
        sym = (run << 4) | cat
        bw.write(ac_tbl[sym][0], ac_tbl[sym][1])
        if cat:
            bw.write(mag, cat)
        run = 0
    if run > 0:
        bw.write(ac_tbl[0][0], ac_tbl[0][1])   # EOB
    return prev_dc


def _category(val):
    """返回 (类别, 幅值编码)。JPEG 标准：正=值；负=(2^cat - 1) + 值。"""
    if val == 0:
        return 0, 0
    if val > 0:
        cat = val.bit_length()
        return cat, val
    else:
        av = -val
        cat = av.bit_length()
        return cat, (1 << cat) - 1 + val


def encode_jpeg_pure(rgb, w, h, quality=82):
    """纯 Python baseline JPEG（YUV 4:2:0）。"""
    scale = _quality_scale(quality)
    ql = _qtable(LUM_Q, scale)
    qc = _qtable(CHR_Q, scale)
    Y, Cb, Cr = _rgb_to_ycbcr(rgb, w, h)
    cw = (w + 1) // 2
    ch = (h + 1) // 2

    bw = _BitWriter()
    t = _tables()

    # DQT
    dqt = bytearray()
    dqt += bytes([0xFF, 0xDB, 0x00, 0x43, 0x00]) + bytes(ql)
    dqt += bytes([0xFF, 0xDB, 0x00, 0x43, 0x01]) + bytes(qc)
    # SOF0
    sof = bytes([0xFF, 0xC0, 0x00, 0x11, 0x08]) + struct.pack(">HH", h, w)
    sof += bytes([0x03]) + bytes([0x01, 0x22, 0x00, 0x02, 0x11, 0x01, 0x03, 0x11, 0x01])
    # DHT
    dht = bytearray()
    for tc, th, bits, vals in ((0, 0, STD_HDC_L[0], STD_HDC_L[1]),
                               (1, 0, STD_HAC_L[0], STD_HAC_L[1]),
                               (0, 1, STD_HDC_C[0], STD_HDC_C[1]),
                               (1, 1, STD_HAC_C[0], STD_HAC_C[1])):
        n = sum(bits)
        dht += bytes([0xFF, 0xC4, 0x00, 3 + 16 + n, (tc << 4) | th])
        dht += bytes(bits)
        dht += bytes(vals)
    # SOS
    sos = bytes([0xFF, 0xDA, 0x00, 0x0C, 0x03,
                 0x01, 0x00, 0x02, 0x11, 0x03, 0x11, 0x00, 0x3F, 0x00])

    # 熵编码（MCU 交错：4:2:0 下每个 MCU = 2x2 Y 块 + 1 Cb + 1 Cr）
    # 注意：Y 块网格必须与 MCU 对齐（每 MCU 固定 2x2），边缘块由 _encode_block 内部填充，
    # 否则 Y 块数 < 解码器期望数会导致熵流错位。
    c_nbx = (cw + 7) // 8
    c_nby = (ch + 7) // 8
    prev_y = 0
    prev_cb = 0
    prev_cr = 0
    for mcu_row in range(c_nby):
        for mcu_col in range(c_nbx):
            for by in range(2):
                for bx in range(2):
                    prev_y = _encode_block(Y, w, h, mcu_col * 2 + bx, mcu_row * 2 + by,
                                           ql, t["dc_l"], t["ac_l"], bw, prev_y)
            prev_cb = _encode_block(Cb, cw, ch, mcu_col, mcu_row,
                                    qc, t["dc_c"], t["ac_c"], bw, prev_cb)
            prev_cr = _encode_block(Cr, cw, ch, mcu_col, mcu_row,
                                    qc, t["dc_c"], t["ac_c"], bw, prev_cr)
    entropy = bw.finish()

    jfif = bytes([0xFF, 0xE0, 0x00, 0x10,
                  0x4A, 0x46, 0x49, 0x46, 0x00, 0x01, 0x01, 0x00,
                  0x00, 0x01, 0x00, 0x01, 0x00, 0x00])
    out = b"\xFF\xD8" + jfif + bytes(dqt) + sof + bytes(dht) + sos + entropy + b"\xFF\xD9"
    return out


def _resize_dim(w, h, max_dim):
    """若任一边超过 max_dim，等比缩小到长边 = max_dim；返回 (nw, nh, scale)。"""
    if not max_dim or max_dim <= 0:
        return w, h, 1.0
    m = max(w, h)
    if m <= max_dim:
        return w, h, 1.0
    scale = max_dim / float(m)
    nw = max(1, round(w * scale))
    nh = max(1, round(h * scale))
    return nw, nh, scale


def _via_ffmpeg(rgb, w, h, quality, workdir, max_dim):
    try:
        nw, nh, _ = _resize_dim(w, h, max_dim)
        raw = os.path.join(workdir, "in.rgb")
        jpg = os.path.join(workdir, "out.jpg")
        with open(raw, "wb") as f:
            f.write(rgb)
        q = max(2, min(31, int(31 - quality * 0.29)))   # q:v 与质量近似换算
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-i", raw]
        if (nw, nh) != (w, h):
            cmd += ["-vf", f"scale={nw}:{nh}:flags=fast_bilinear"]
        cmd += ["-q:v", str(q), jpg]
        p = subprocess.run(cmd, capture_output=True, timeout=180)
        if p.returncode != 0:
            return None, w, h
        with open(jpg, "rb") as f:
            d = f.read()
        if d.startswith(b"\xFF\xD8"):
            return d, nw, nh
        return None, w, h
    except Exception:
        return None, w, h


def _via_convert(rgb, w, h, quality, workdir, max_dim):
    try:
        nw, nh, _ = _resize_dim(w, h, max_dim)
        raw = os.path.join(workdir, "in.rgb")
        jpg = os.path.join(workdir, "out.jpg")
        with open(raw, "wb") as f:
            f.write(rgb)
        cmd = ["convert", "-size", f"{w}x{h}", "-depth", "8", "rgb:" + raw]
        if (nw, nh) != (w, h):
            cmd += ["-resize", f"{nw}x{nh}!"]
        cmd += ["-quality", str(quality), jpg]
        p = subprocess.run(cmd, capture_output=True, timeout=180)
        if p.returncode != 0:
            return None, w, h
        with open(jpg, "rb") as f:
            d = f.read()
        if d.startswith(b"\xFF\xD8"):
            return d, nw, nh
        return None, w, h
    except Exception:
        return None, w, h


def encode_jpeg(rgb, w, h, quality=82, max_dim=None):
    """把 RGB 编码为 JPEG。优先系统命令（快，可顺带缩放），fallback 纯 Python（零依赖）。
    返回 (jpeg_bytes, 输出宽, 输出高)。max_dim>0 时超过该长边的图会等比缩小。"""
    with tempfile.TemporaryDirectory(prefix="jpeg_") as td:
        d, ow, oh = _via_ffmpeg(rgb, w, h, quality, td, max_dim)
        if d:
            return d, ow, oh
        d, ow, oh = _via_convert(rgb, w, h, quality, td, max_dim)
        if d:
            return d, ow, oh
    return encode_jpeg_pure(rgb, w, h, quality), w, h


if __name__ == "__main__":
    # 自测：读一个 webp → RGB → 编码 jpeg
    import io
    from PIL import Image
    for p in sys.argv[1:]:
        im = Image.open(p).convert("RGB")
        j, ow, oh = encode_jpeg(im.tobytes(), im.width, im.height, 82)
        open(p + ".jpg", "wb").write(j)
        print(f"{p}: {im.width}x{im.height} -> {ow}x{oh} jpeg={len(j)/1e6:.2f}MB")
