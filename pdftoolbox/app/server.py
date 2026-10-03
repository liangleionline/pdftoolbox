#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF 转换工具箱 - fnOS FPK 应用后端
仅依赖 Python 标准库；可选依赖 PyMuPDF / Pillow / 外部命令（LibreOffice、Calibre、7z）。
"""
import argparse
import io
import json
import mimetypes
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# 确保本模块（webpcodec 等）可被导入
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
import webpcodec
import jpegcodec

# ---------- 全局配置 ----------
WEB_DIR = os.path.join(_APP_DIR, "web")
ETC_DIR = os.environ.get("TRIM_PKGETC", "/etc/pdftoolbox")

DATA_DIR = "/var/tmp/pdftoolbox-data"
TMP_DIR = "/var/tmp/pdftoolbox-tmp"
HOME_DIR = "/var/tmp/pdftoolbox-home"

# 异步任务表：耗时转换放后台线程，POST 立即返回 task_id，
# 前端轮询 /api/task/<id> 取结果，避免长时间占用连接被网关掐断（Failed to fetch）。
TASKS = {}


def _submit_task(job):
    """job() 返回 (ok, payload)；ok=True 时 payload 合并进结果。"""
    tid = uuid.uuid4().hex
    TASKS[tid] = {"status": "running"}
    def run():
        try:
            ok, payload = job()
            if ok:
                TASKS[tid] = {"status": "done", **(payload or {})}
            else:
                TASKS[tid] = {"status": "error", "error": (payload or {}).get("error", "转换失败")}
        except Exception as e:
            TASKS[tid] = {"status": "error", "error": str(e)}
    threading.Thread(target=run, daemon=True).start()
    return tid


JPEG_MIME = {"image/jpeg", "image/jpg"}
PNG_MIME = {"image/png"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
OFFICE_EXTS = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".txt"}
EBOOK_EXTS = {".epub", ".mobi", ".azw3", ".azw", ".txt", ".fb2", ".lit"}
DOC_EXTS = OFFICE_EXTS | EBOOK_EXTS
ARCHIVE_EXTS = {".zip", ".rar", ".7z"}
IGNORE_NAMES = {"__MACOSX", ".ds_store"}

# 尝试导入 PyMuPDF（用于 PDF 转图片，自包含）
try:
    import fitz  # PyMuPDF
    HAS_FITZ = True
except Exception:
    HAS_FITZ = False

# 尝试导入 Pillow（用于 WebP 转 PDF）
try:
    from PIL import Image as _PILImage
    HAS_PIL = True
except Exception:
    HAS_PIL = False


# ---------- 工具函数 ----------
def shutil_which(cmd):
    return shutil.which(cmd)


def load_tools():
    """读取 install_callback 探测到的外部工具"""
    path = os.path.join(ETC_DIR, "tools.json")
    raw = {
        "libreoffice": False,
        "ebook_convert": False,
        "pdftoppm": False,
        "mutool": False,
        "7z": False,
        "unrar": False,
        "pymupdf": HAS_FITZ,
    }
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw.update(json.load(f))
    except Exception:
        for k, cmd in [
            ("libreoffice", "soffice"),
            ("ebook_convert", "ebook-convert"),
            ("pdftoppm", "pdftoppm"),
            ("mutool", "mutool"),
            ("7z", "7z"),
            ("unrar", "unrar"),
        ]:
            raw[k] = bool(shutil_which(cmd))

    raw["pillow"] = HAS_PIL

    # WebP 解码：优先零依赖（libwebp/ffmpeg/ImageMagick），libwebp 几乎所有 Linux 自带
    webp_ok = webpcodec.webp_supported_natively()
    raw["webp"] = webp_ok

    meta = {
        "libreoffice":   ("LibreOffice（Word/Excel/PPT/TXT转PDF）", "sudo apt install -y libreoffice"),
        "ebook_convert": ("Calibre（EPUB/MOBI转PDF）",              "sudo apt install -y calibre"),
        "pdftoppm":      ("poppler-utils（PDF转图片）",             "sudo apt install -y poppler-utils"),
        "mutool":        ("MuPDF（PDF转图片备选）",                 "sudo apt install -y mupdf-tools"),
        "7z":            ("p7zip（rar/7z压缩包解压）",              "sudo apt install -y p7zip-full"),
        "unrar":         ("unrar（rar压缩包解压备选）",             "sudo apt install -y unrar"),
        "pillow":        ("Pillow（WebP转PDF备选）",                "" if webp_ok else "python3 -m pip install pillow"),
        "webp":          ("内置 WebP 解码（系统 libwebp）",          "" if webp_ok else "sudo apt install -y libwebp-dev"),
        "pymupdf":       ("内置 PDF 渲染（PDF转图片）",             ""),
    }
    tools = []
    for key, (name, cmd) in meta.items():
        tools.append({
            "key": key,
            "name": name,
            "available": bool(raw.get(key, False)),
            "install_cmd": cmd,
        })
    return tools


def run_convert_cmd(cmd_list, workdir, timeout=600):
    """运行外部命令，返回 (ok, output)"""
    try:
        p = subprocess.run(cmd_list, cwd=workdir, capture_output=True, timeout=timeout)
        out = (p.stdout or b"").decode("utf-8", errors="replace")
        err = (p.stderr or b"").decode("utf-8", errors="replace")
        return p.returncode == 0, out + "\n" + err
    except FileNotFoundError:
        return False, f"命令未找到: {cmd_list[0]}"
    except subprocess.TimeoutExpired:
        return False, "命令执行超时"


def detect_soffice():
    for c in ("soffice", "libreoffice"):
        if shutil_which(c):
            return c
    return None


def detect_7z():
    for c in ("7z", "7za"):
        if shutil_which(c):
            return c
    return None


# ---------- 图片解码（纯 Python） ----------
def jpeg_info(data):
    """解析 JPEG SOF，返回 (width, height, components)"""
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        raise ValueError("不是有效的 JPEG 文件")
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height = int.from_bytes(data[i + 5:i + 7], "big")
            width = int.from_bytes(data[i + 7:i + 9], "big")
            comps = data[i + 9]
            return width, height, comps
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        i += 2 + seg_len
    raise ValueError("JPEG 中未找到 SOF 标记")


def png_info(data):
    """解析 PNG，返回 (width, height, color_type, bit_depth, raw_scanlines)"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是有效的 PNG 文件")
    pos = 8
    width = height = bit_depth = color_type = None
    compressed = b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = bytes(data[pos + 4:pos + 8])
        chunk_data = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width = struct.unpack(">I", chunk_data[0:4])[0]
            height = struct.unpack(">I", chunk_data[4:8])[0]
            bit_depth = chunk_data[8]
            color_type = chunk_data[9]
        elif ctype == b"IDAT":
            compressed += chunk_data
        elif ctype == b"IEND":
            break
        pos += 8 + length + 4
    if width is None:
        raise ValueError("PNG IHDR 缺失")
    raw = zlib.decompress(compressed)
    return width, height, color_type, bit_depth, raw


def png_to_pdf_image(raw, width, height, color_type, bit_depth):
    """PNG 原始数据转 PDF FlateDecode samples。返回 (samples, colorspace, bpc)"""
    if bit_depth != 8:
        raise ValueError("仅支持 8-bit PNG")
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if ch is None:
        raise ValueError(f"不支持的 PNG color_type={color_type}")
    rowbytes = width * ch
    out = bytearray()
    for y in range(height):
        start = y * (rowbytes + 1)
        f = raw[start]
        line = raw[start + 1:start + 1 + rowbytes]
        if f == 0:
            out += line
        elif f == 1:
            new = bytearray(rowbytes)
            for i in range(rowbytes):
                a = new[i - ch] if i >= ch else 0
                new[i] = (line[i] + a) & 0xFF
            out += new
        elif f == 2:
            new = bytearray(rowbytes)
            for i in range(rowbytes):
                new[i] = (line[i] + new[i]) & 0xFF  # Up: 用上一行；此处简化为直接
            # Up 需要上一行数据，改为正确实现
            prev = bytearray(rowbytes)
            new2 = bytearray(rowbytes)
            for i in range(rowbytes):
                new2[i] = (line[i] + prev[i]) & 0xFF
            out += new2
        elif f == 3:
            new = bytearray(rowbytes)
            prev = bytearray(rowbytes)
            for i in range(rowbytes):
                a = new[i - ch] if i >= ch else 0
                new[i] = (line[i] + (a + prev[i]) // 2) & 0xFF
            out += new
        elif f == 4:
            new = bytearray(rowbytes)
            prev = bytearray(rowbytes)
            for i in range(rowbytes):
                a = new[i - ch] if i >= ch else 0
                b = prev[i]
                c = new[i - ch] if i >= ch else 0
                p = a + b - c
                pa = abs(p - a); pb = abs(p - b); pc = abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                new[i] = (line[i] + pr) & 0xFF
            out += new
        else:
            out += line
    out = bytes(out)
    if color_type == 0:
        return out, "/DeviceGray", 8
    if color_type == 2:
        return out, "/DeviceRGB", 8
    if color_type == 3:
        raise ValueError("不支持索引色 PNG（color_type=3），请转换为 RGB 或灰度")
    if color_type == 4:
        return bytes(out[i] for i in range(0, len(out), 2)), "/DeviceGray", 8
    if color_type == 6:
        s = bytearray()
        for i in range(0, len(out), 4):
            s += out[i:i + 3]
        return bytes(s), "/DeviceRGB", 8
    raise ValueError(f"不支持的 PNG color_type={color_type}")


def webp_to_rgb(data):
    """WebP 解码为 RGB。

    优先使用零依赖方案（ctypes 调系统 libwebp，或 ffmpeg / ImageMagick），
    其次回退 Pillow / PyMuPDF。libwebp 几乎在所有 Linux 上自带，故通常无需安装。
    """
    r = webpcodec.decode_webp_rgb(data)
    if r is not None:
        return r
    if HAS_PIL:
        im = _PILImage.open(io.BytesIO(data)).convert("RGB")
        return im.width, im.height, im.tobytes()
    if HAS_FITZ:
        pix = fitz.Pixmap(data)
        if pix.alpha:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        return pix.width, pix.height, bytes(pix.samples)
    raise ValueError("WebP 转换需要系统 libwebp（一般系统自带），或安装 Pillow：python3 -m pip install pillow")


def images_to_pdf(image_list):
    """image_list: [(filename, mime, bytes), ...] → PDF bytes"""
    objects = []
    obj_counter = [2]
    page_infos = []
    for fname, mime, data in image_list:
        ext = os.path.splitext(fname)[1].lower()
        if mime in JPEG_MIME or ext in (".jpg", ".jpeg"):
            w, h, comps = jpeg_info(data)
            cs = "/DeviceRGB" if comps == 3 else ("/DeviceGray" if comps == 1 else "/DeviceCMYK")
            img_obj_id = obj_counter[0]; obj_counter[0] += 1
            img_header = (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} "
                          f"/ColorSpace {cs} /BitsPerComponent 8 /Filter /DCTDecode "
                          f"/Length {len(data)} >>").encode("ascii")
            objects.append((img_obj_id, img_header, data))
            page_infos.append((w, h, img_obj_id))
        elif mime in PNG_MIME or ext == ".png":
            w, h, ct, bd, raw = png_info(data)
            samples, cs, bpc = png_to_pdf_image(raw, w, h, ct, bd)
            comp = zlib.compress(samples)
            img_obj_id = obj_counter[0]; obj_counter[0] += 1
            img_header = (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} "
                          f"/ColorSpace {cs} /BitsPerComponent {bpc} /Filter /FlateDecode "
                          f"/Length {len(comp)} >>").encode("ascii")
            objects.append((img_obj_id, img_header, comp))
            page_infos.append((w, h, img_obj_id))
        elif ext == ".webp":
            w, h, rgb = webp_to_rgb(data)
            # WebP 为有损格式：转回 RGB 再用 zlib 嵌入会膨胀约 8 倍（4.7MB→48MB 的根因）。
            # 改用 JPEG 编码（/DCTDecode）嵌入；超过 4096 长边的大图等比缩小，降低计算量。
            jpg, ow, oh = jpegcodec.encode_jpeg(rgb, w, h, 82, max_dim=4096)
            img_obj_id = obj_counter[0]; obj_counter[0] += 1
            img_header = (f"<< /Type /XObject /Subtype /Image /Width {ow} /Height {oh} "
                          f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
                          f"/Length {len(jpg)} >>").encode("ascii")
            objects.append((img_obj_id, img_header, jpg))
            page_infos.append((ow, oh, img_obj_id))
        else:
            raise ValueError(f"暂不支持的图片格式：{fname}（仅支持 JPG/PNG/WebP）")

    n_pages = len(page_infos)
    pages_obj_id = obj_counter[0]; obj_counter[0] += 1
    page_obj_ids = []
    content_obj_ids = []
    for w, h, img_obj_id in page_infos:
        page_obj_ids.append(obj_counter[0]); obj_counter[0] += 1
        content_obj_ids.append(obj_counter[0]); obj_counter[0] += 1
        content = f"q {w} 0 0 {h} 0 0 cm /Im0 Do Q\n".encode("ascii")
        objects.append((content_obj_ids[-1], f"<< /Length {len(content)} >>".encode("ascii"), content))

    catalog_obj_id = 1
    kids = " ".join(f"{pid} 0 R" for pid in page_obj_ids)
    objects.append((pages_obj_id, b"", f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode("ascii")))
    for i, (w, h, img_obj_id) in enumerate(page_infos):
        pid = page_obj_ids[i]
        cid = content_obj_ids[i]
        page_body = (f"<< /Type /Page /Parent {pages_obj_id} 0 R "
                     f"/MediaBox [0 0 {w} {h}] "
                     f"/Resources << /XObject << /Im0 {img_obj_id} 0 R >> >> "
                     f"/Contents {cid} 0 R >>").encode("ascii")
        objects.append((pid, b"", page_body))
    objects.append((catalog_obj_id, b"", f"<< /Type /Catalog /Pages {pages_obj_id} 0 R >>".encode("ascii")))

    objects.sort(key=lambda x: x[0])
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for obj_id, header, body in objects:
        offsets[obj_id] = len(out)
        out += f"{obj_id} 0 obj\n".encode("ascii")
        if header:
            out += header + b"\nstream\n" + body + b"\nendstream\n"
        else:
            out += body + b"\n"
        out += b"endobj\n"
    xref_pos = len(out)
    max_obj = max(offsets.keys())
    out += f"xref\n0 {max_obj + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for i in range(1, max_obj + 1):
        out += f"{offsets.get(i, 0):010d} 00000 n \n".encode("ascii")
    out += (f"trailer\n<< /Size {max_obj + 1} /Root {catalog_obj_id} 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode("ascii")
    return bytes(out)


def _image_to_single_pdf(fname, mime, data):
    """单张图片转一个 PDF"""
    return images_to_pdf([(fname, mime, data)])


# ---------- 纯 Python TXT -> PDF ----------
def _decode_text(data):
    for enc in ("utf-8-sig", "utf-16", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _char_width(ch):
    o = ord(ch)
    if o < 128:
        return 0.5
    if (0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0xA4CF or
        0xAC00 <= o <= 0xD7A3 or 0xF900 <= o <= 0xFAFF or
        0xFE30 <= o <= 0xFE4F or 0xFF00 <= o <= 0xFF60 or
        0xFFE0 <= o <= 0xFFE6):
        return 1.0
    return 0.5


def _wrap_line(line, max_units):
    lines = []
    cur = ""
    cur_w = 0.0
    for ch in line:
        w = _char_width(ch)
        if cur_w + w > max_units and cur:
            lines.append(cur)
            cur = ch
            cur_w = w
        else:
            cur += ch
            cur_w += w
    if cur:
        lines.append(cur)
    return lines


def txt_to_pdf_pure(data):
    """纯 Python TXT 转 PDF，使用 STSong-Light 中文 CID 字体"""
    text = _decode_text(data)
    lines_raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    page_w = 595.0; page_h = 842.0
    margin_l = 50.0; margin_r = 50.0; margin_t = 60.0; margin_b = 60.0
    font_size = 12.0; leading = 18.0
    usable_w = page_w - margin_l - margin_r
    max_units = usable_w / font_size
    wrapped = []
    for ln in lines_raw:
        if ln == "":
            wrapped.append("")
        else:
            wrapped.extend(_wrap_line(ln, max_units))
    lines_per_page = int((page_h - margin_t - margin_b) / leading)
    pages = [wrapped[i:i + lines_per_page] for i in range(0, len(wrapped), lines_per_page)]
    if not pages:
        pages = [[""]]

    objects = []
    catalog_id = 1; pages_id = 2; font_id = 3; cidfont_id = 4; fd_id = 5
    page_ids = [6 + i * 2 for i in range(len(pages))]
    content_ids = [p + 1 for p in page_ids]
    n_pages = len(pages)
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append((pages_id, b"", f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode("ascii")))
    objects.append((font_id, b"", b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
                                b"/Encoding /UniGB-UCS2-H /DescendantFonts [4 0 R] >>"))
    objects.append((cidfont_id, b"", b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
                                    b"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> "
                                    b"/FontDescriptor 5 0 R /DW 1000 >>"))
    objects.append((fd_id, b"", b"<< /Type /FontDescriptor /FontName /STSong-Light /Flags 6 "
                                b"/BBox [0 -120 1000 880] /ItalicAngle 0 /Ascent 880 /Descent -120 "
                                b"/CapHeight 800 /StemV 80 >>"))
    for i, page_lines in enumerate(pages):
        y = page_h - margin_t
        stream_lines = [f"BT /F1 {font_size:.1f} Tf {margin_l:.1f} {y:.1f} Td".encode("ascii")]
        for j, ln in enumerate(page_lines):
            if j > 0:
                stream_lines.append(b"T*")
            if ln:
                hex_str = ln.encode("utf-16-be").hex()
                stream_lines.append(f"<{hex_str}> Tj".encode("ascii"))
        stream_lines.append(b"ET")
        content = b"\n".join(stream_lines)
        objects.append((content_ids[i], f"<< /Length {len(content)} >>".encode("ascii"), content))
        page_body = (f"<< /Type /Page /Parent {pages_id} 0 R "
                     f"/MediaBox [0 0 {page_w:.1f} {page_h:.1f}] "
                     f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                     f"/Contents {content_ids[i]} 0 R >>").encode("ascii")
        objects.append((page_ids[i], b"", page_body))
    objects.append((catalog_id, b"", f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("ascii")))

    objects.sort(key=lambda x: x[0])
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for obj_id, header, body in objects:
        offsets[obj_id] = len(out)
        out += f"{obj_id} 0 obj\n".encode("ascii")
        if header:
            out += header + b"\nstream\n" + body + b"\nendstream\n"
        else:
            out += body + b"\n"
        out += b"endobj\n"
    xref_pos = len(out)
    max_obj = max(offsets.keys())
    out += f"xref\n0 {max_obj + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for i in range(1, max_obj + 1):
        out += f"{offsets.get(i, 0):010d} 00000 n \n".encode("ascii")
    out += (f"trailer\n<< /Size {max_obj + 1} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode("ascii")
    return bytes(out)


# ---------- Office / 电子书转 PDF ----------
def office_to_pdf(fname, data):
    """Office 转 PDF；.txt 走纯 Python 实现"""
    ext = os.path.splitext(fname)[1].lower()
    if ext == ".txt":
        try:
            return txt_to_pdf_pure(data), None
        except Exception as e:
            return None, f"TXT 转换失败：{e}"
    soffice = detect_soffice()
    if not soffice:
        return None, "未检测到 LibreOffice（soffice），请先在系统中安装 LibreOffice。"
    workdir = tempfile.mkdtemp(prefix="office_", dir=TMP_DIR)
    src = os.path.join(workdir, fname)
    with open(src, "wb") as f:
        f.write(data)
    ok, log = run_convert_cmd([soffice, "--headless", "--norestore", "--convert-to", "pdf",
                               "--outdir", workdir, src], workdir)
    out_path = os.path.join(workdir, os.path.splitext(fname)[0] + ".pdf")
    if ok and os.path.exists(out_path):
        with open(out_path, "rb") as f:
            return f.read(), None
    return None, f"LibreOffice 转换失败：{log}"


def ebook_to_pdf(fname, data):
    """电子书转 PDF"""
    ext = os.path.splitext(fname)[1].lower()
    workdir = tempfile.mkdtemp(prefix="ebook_", dir=TMP_DIR)
    src = os.path.join(workdir, fname)
    with open(src, "wb") as f:
        f.write(data)
    out_pdf = os.path.join(workdir, os.path.splitext(fname)[0] + ".pdf")
    if ext == ".txt":
        return office_to_pdf(fname, data)
    ec = shutil_which("ebook-convert")
    if not ec:
        return None, "未检测到 Calibre（ebook-convert），无法转换 EPUB/MOBI 等电子书。请先安装 Calibre。"
    ok, log = run_convert_cmd([ec, src, out_pdf], workdir, timeout=600)
    if ok and os.path.exists(out_pdf):
        with open(out_pdf, "rb") as f:
            return f.read(), None
    return None, f"ebook-convert 转换失败：{log}"


# ---------- PDF 转图片 ----------
def pdf_to_images(fname, data, fmt="png", dpi=150):
    """PDF 转图片 ZIP。优先 PyMuPDF，fallback pdftoppm/mutool。"""
    workdir = tempfile.mkdtemp(prefix="pdf2img_", dir=TMP_DIR)
    stem = os.path.splitext(fname)[0]

    if HAS_FITZ:
        try:
            doc = fitz.open(stream=data, filetype="pdf")
            ext = "png" if fmt == "png" else "jpg"
            for i, page in enumerate(doc):
                zoom = dpi / 72.0
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                pix.save(os.path.join(workdir, f"{stem}-{i+1}.{ext}"))
            doc.close()
            return _zip_images(workdir, fname), None
        except Exception as e:
            sys.stderr.write(f"[pdf2img] PyMuPDF failed: {e}\n")

    src = os.path.join(workdir, fname)
    with open(src, "wb") as f:
        f.write(data)
    out_prefix = os.path.join(workdir, stem)
    ok = False
    pdftoppm = shutil_which("pdftoppm")
    mutool = shutil_which("mutool")
    if pdftoppm:
        ext = "png" if fmt == "png" else "jpeg"
        ok, log = run_convert_cmd([pdftoppm, "-r", str(dpi), "-" + ext, src, out_prefix], workdir)
    elif mutool:
        ok, log = run_convert_cmd([mutool, "convert", "-o", out_prefix + "-%d.png", "-r", str(dpi), src], workdir)
    else:
        return None, "未检测到 PDF 渲染引擎（PyMuPDF 未安装，且系统无 pdftoppm/mutool）。"
    if not ok:
        return None, f"PDF 转图片失败：{log}"
    return _zip_images(workdir, fname), None


def _zip_images(workdir, fname):
    buf = io.BytesIO()
    images = [os.path.join(workdir, fn) for fn in sorted(os.listdir(workdir))
              if fn.lower().endswith((".png", ".jpg", ".jpeg"))]
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for img in images:
            zf.write(img, arcname=os.path.basename(img))
    return buf.getvalue()


# ---------- PDF 合并（纯 Python） ----------
_REF_RE = re.compile(rb'/([\w.%+-]+)\s+(\d+)\s+0\s+R')
_OBJ_RE = re.compile(rb'(\d+)\s+0\s+obj\b')
_STREAM_RE = re.compile(rb'(<<.*?>>)\s*\r?\nstream\r?\n', re.S)
_TYPE_PAGE_RE = re.compile(rb'/Type\s*/Page\b')


def _parse_pdf_objects(data):
    """拆 PDF 为 {objnum: body_bytes}，body 是 obj 头之后、endobj 之前的内容"""
    objs = {}
    for m in _OBJ_RE.finditer(data):
        num = int(m.group(1))
        end = data.find(b"endobj", m.end())
        if end == -1:
            continue
        objs[num] = data[m.end():end]
    return objs


def _split_stream(block):
    """把对象 body 拆成 (dict部分, stream数据 或 b'')"""
    m = _STREAM_RE.search(block)
    if m:
        dict_part = m.group(1)
        rest = block[m.end():]
        if rest.endswith(b"endstream"):
            rest = rest[:-len(b"endstream")]
        rest = rest.rstrip(b"\r\n")
        return dict_part, rest
    return block, b""


def _find_root(data):
    m = re.search(rb'/Root\s+(\d+)\s+0\s+R', data)
    if m:
        return int(m.group(1))
    return None


def _is_page_block(block):
    d, _ = _split_stream(block)
    return bool(_TYPE_PAGE_RE.search(d))


def merge_pdfs(pdf_list):
    """把多个 PDF 合并为一个 PDF。pdf_list: [bytes, ...] → bytes（纯 Python）"""
    next_num = [3]  # 1=Catalog, 2=Pages 预留
    out_objs = {}
    all_pages_new = []

    for data in pdf_list:
        if not data.startswith(b"%PDF"):
            raise ValueError("存在不是有效 PDF 的文件")
        objs = _parse_pdf_objects(data)
        root = _find_root(data)
        if root is None or root not in objs:
            # 从对象里找 Catalog
            root = None
            for n, b in objs.items():
                if b" /Type /Catalog" in b or b"/Type/Catalog" in b:
                    root = n
                    break
            if root is None:
                raise ValueError("无法解析 PDF 目录")

        mapping = {}

        def copy(num):
            if num in mapping:
                return mapping[num]
            new = next_num[0]; next_num[0] += 1
            mapping[num] = new
            block = objs.get(num)
            if block is None:
                out_objs[new] = b"null"
                return new
            d, stream = _split_stream(block)
            nd = _REF_RE.sub(lambda m: b"/" + m.group(1) + b" " + str(copy(int(m.group(2)))).encode() + b" 0 R", d)
            if stream:
                out_objs[new] = nd + b"\nstream\n" + stream + b"\nendstream"
            else:
                out_objs[new] = nd
            return new

        copy(root)  # 深拷贝整棵可达对象（含页、资源）
        for n, b in objs.items():
            if _is_page_block(b):
                all_pages_new.append(copy(n))

    if not all_pages_new:
        raise ValueError("合并的 PDF 中没有找到页面")

    kids = b" ".join(b"%d 0 R" % n for n in all_pages_new)
    out_objs[2] = b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % len(all_pages_new)
    out_objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"

    # 写文件
    items = sorted(out_objs.items())
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num, body in items:
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode("ascii")
        out += body + b"\nendobj\n"
    xref_pos = len(out)
    max_obj = max(offsets.keys())
    out += f"xref\n0 {max_obj + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for i in range(1, max_obj + 1):
        out += f"{offsets.get(i, 0):010d} 00000 n \n".encode("ascii")
    out += (f"trailer\n<< /Size {max_obj + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n").encode("ascii")
    return bytes(out)


# ---------- 压缩包处理 ----------
def _safe_extract_zip(zf, dest):
    """安全解压 zip（防路径穿越）"""
    dest = os.path.realpath(dest)
    for info in zf.infolist():
        name = info.filename
        target = os.path.realpath(os.path.join(dest, name))
        if not target.startswith(dest + os.sep):
            raise ValueError(f"压缩包存在非法路径：{name}")
        if info.is_dir():
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)


def extract_archive(fname, data):
    """解压压缩包到临时目录，返回 (目录路径, 错误或 None)。
    支持 zip（标准库）、rar/7z（外部 7z/unrar）。"""
    ext = os.path.splitext(fname)[1].lower()
    workdir = tempfile.mkdtemp(prefix="arch_", dir=TMP_DIR)
    arch_path = os.path.join(workdir, fname)
    with open(arch_path, "wb") as f:
        f.write(data)

    if ext == ".zip":
        try:
            with zipfile.ZipFile(arch_path) as zf:
                _safe_extract_zip(zf, workdir)
            return workdir, None
        except zipfile.BadZipFile:
            # 可能是伪装成 zip 的 rar/7z，尝试 7z
            return _extract_with_7z(arch_path, workdir, ext)
        except Exception as e:
            return None, f"ZIP 解压失败：{e}"

    # rar / 7z
    return _extract_with_7z(arch_path, workdir, ext)


def _extract_with_7z(arch_path, workdir, ext):
    exe = detect_7z()
    if exe:
        ok, log = run_convert_cmd([exe, "x", "-y", "-o" + workdir, arch_path], workdir, timeout=300)
        if ok:
            return workdir, None
        return None, f"压缩包解压失败（7z）：{log}"
    unrar = shutil_which("unrar")
    if ext == ".rar" and unrar:
        ok, log = run_convert_cmd([unrar, "x", "-y", arch_path, workdir + os.sep], workdir, timeout=300)
        if ok:
            return workdir, None
        return None, f"压缩包解压失败（unrar）：{log}"
    return None, "解压 rar/7z 需要 p7zip（7z 命令），请先安装：sudo apt install -y p7zip-full"


def _kind_of(fname):
    ext = os.path.splitext(fname)[1].lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in DOC_EXTS:
        return "doc"
    return "other"


def list_archive_files(rootdir):
    """递归列出解压目录中的文件，跳过系统/隐藏文件。返回 [(name, relpath)]"""
    items = []
    for dirpath, dirnames, filenames in os.walk(rootdir):
        dirnames[:] = [d for d in dirnames if d.lower() not in IGNORE_NAMES and not d.startswith(".")]
        for fn in filenames:
            if fn.lower().startswith("._") or fn.lower() in IGNORE_NAMES or fn.startswith("."):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, rootdir)
            items.append((fn, rel))
    items.sort(key=lambda x: x[0].lower())
    return items


def _sanitize_entry(name):
    """判断压缩包条目是否需要保留。跳过目录、系统/隐藏文件。返回保留则 True。"""
    if name.endswith("/"):
        return False
    parts = name.replace("\\", "/").split("/")
    if any(p in IGNORE_NAMES or p.startswith(".") for p in parts):
        return False
    if os.path.basename(name).startswith("._"):
        return False
    return True


def list_archive_entries(fname, data):
    """不解压，仅列出压缩包内文件。返回 ([(basename, relpath)], 错误或 None)。
    这是 analyze 的核心：zip 用标准库读中央目录，rar/7z 用 7z l 列出，
    全程不落地解压，弱机上也能秒级返回。"""
    ext = os.path.splitext(fname)[1].lower()
    names = []
    if ext == ".zip":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for n in zf.namelist():
                    if _sanitize_entry(n):
                        names.append(n)
        except Exception as e:
            return None, f"ZIP 读取失败：{e}"
    else:
        workdir = tempfile.mkdtemp(prefix="archlst_", dir=TMP_DIR)
        arch = os.path.join(workdir, fname)
        try:
            with open(arch, "wb") as f:
                f.write(data)
            exe = detect_7z()
            cmd = None
            if exe:
                cmd = [exe, "l", "-slt", arch]
            elif ext == ".rar":
                unrar = shutil_which("unrar")
                if unrar:
                    cmd = [unrar, "lb", arch]
            if not cmd:
                return None, "列出 rar/7z 需要 p7zip（7z 命令），请先安装：sudo apt install -y p7zip-full"
            p = subprocess.run(cmd, capture_output=True, timeout=120)
            if p.returncode != 0:
                return None, f"压缩包读取失败：{p.stderr.decode('utf-8', 'ignore')[:200]}"
            # 解析 7z -slt 的 Path= 字段；unrar lb 是每行一个路径
            if exe:
                pending, is_folder = None, False
                for line in p.stdout.decode("utf-8", "ignore").splitlines():
                    line = line.strip()
                    if line.startswith("Path = "):
                        if pending is not None and not is_folder and _sanitize_entry(pending):
                            names.append(pending)
                        pending = line[len("Path = "):]
                        is_folder = False
                    elif line.startswith("Folder = +"):
                        is_folder = True
                if pending is not None and not is_folder and _sanitize_entry(pending):
                    names.append(pending)
            else:
                for line in p.stdout.decode("utf-8", "ignore").splitlines():
                    line = line.strip()
                    if line and _sanitize_entry(line):
                        names.append(line)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    return [(os.path.basename(n), n) for n in names], None


def classify_archive(fname, data):
    """分析压缩包：返回 {ok, type, count, items:[{name,kind}], error?}
    type: image | doc | mixed | unsupported | empty。
    只列压缩包条目（不解压），因此分析秒级返回。"""
    files, err = list_archive_entries(fname, data)
    if err:
        return {"ok": False, "error": err}
    if not files:
        return {"ok": True, "type": "empty", "count": 0, "items": []}

    kinds = [_kind_of(n) for n, _ in files]
    has_img = "image" in kinds
    has_doc = "doc" in kinds
    has_other = "other" in kinds

    relevant = [(n, k) for (n, _), k in zip(files, kinds) if k in ("image", "doc")]
    if not relevant:
        return {"ok": True, "type": "unsupported", "count": 0,
                "items": [{"name": n, "kind": _kind_of(n)} for n, _ in files], "error": "压缩包内没有可转换为 PDF 的文件。"}
    if has_img and has_doc:
        return {"ok": True, "type": "mixed", "count": len(relevant),
                "items": [{"name": n, "kind": k} for n, k in relevant],
                "error": "压缩包中同时包含图片和文档，请先将内容整理为同一类型后再上传。"}
    typ = "image" if has_img else "doc"
    return {"ok": True, "type": typ, "count": len(relevant),
            "items": [{"name": n, "kind": k} for n, k in relevant]}


def convert_archive(fname, data, mode):
    """按 mode 转换压缩包。mode: merge|separate。
    merge → 单个 PDF；separate → 多个 PDF 打包 zip。返回 (output_bytes, is_zip, filename, error)"""
    try:
        return _convert_archive_impl(fname, data, mode)
    except Exception as e:
        return None, False, None, f"压缩包转换失败：{e}"


def _convert_archive_impl(fname, data, mode):
    cls = classify_archive(fname, data)
    if not cls["ok"]:
        return None, False, None, cls["error"]
    typ = cls["type"]
    if typ in ("mixed", "unsupported", "empty"):
        return None, False, None, cls.get("error", "压缩包内容无法转换")

    rootdir, err = extract_archive(fname, data)
    if err:
        return None, False, None, err
    files = list_archive_files(rootdir)

    # 只处理相关文件
    rels = []
    for fn, rel in files:
        if _kind_of(fn) in ("image", "doc"):
            rels.append((fn, rel))
    rels.sort(key=lambda x: x[0].lower())

    base = os.path.splitext(os.path.basename(fname))[0]

    if mode == "separate":
        # 每个文件单独 PDF → zip
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fn, rel in rels:
                full = os.path.join(rootdir, rel)
                with open(full, "rb") as f:
                    item_data = f.read()
                mime = mimetypes.guess_type(fn)[0] or "application/octet-stream"
                pdf, cerr = _convert_one(fn, mime, item_data)
                if cerr:
                    return None, False, None, f"{fn} 转换失败：{cerr}"
                out_name = os.path.splitext(fn)[0] + ".pdf"
                zf.writestr(out_name, pdf)
        return buf.getvalue(), True, base + "_分文件.zip", None

    # merge：按文件名顺序合并为一个 PDF
    pdfs = []
    for fn, rel in rels:
        full = os.path.join(rootdir, rel)
        with open(full, "rb") as f:
            item_data = f.read()
        mime = mimetypes.guess_type(fn)[0] or "application/octet-stream"
        pdf, cerr = _convert_one(fn, mime, item_data)
        if cerr:
            return None, False, None, f"{fn} 转换失败：{cerr}"
        pdfs.append(pdf)
    merged = merge_pdfs(pdfs)
    return merged, False, base + ".pdf", None


def _convert_one(fname, mime, data):
    """把一个文件转成 PDF（按类型分派）"""
    ext = os.path.splitext(fname)[1].lower()
    if ext in IMAGE_EXTS:
        return images_to_pdf([(fname, mime, data)]), None
    if ext in OFFICE_EXTS:
        return office_to_pdf(fname, data)
    if ext in EBOOK_EXTS:
        return ebook_to_pdf(fname, data)
    return None, "不支持的文件类型"


# ---------- multipart 解析 ----------
def parse_multipart(body, boundary):
    results = []
    delim = b"--" + boundary
    parts = body.split(delim)
    for part in parts:
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        header_block, content = part.split(b"\r\n\r\n", 1)
        headers = {}
        for line in header_block.split(b"\r\n"):
            if b":" in line:
                k, v = line.split(b":", 1)
                headers[k.strip().decode("latin1").lower()] = v.strip().decode("latin1")
        cd = headers.get("content-disposition", "")
        fname = None
        field = None
        if ";" in cd:
            for seg in cd.split(";"):
                seg = seg.strip()
                if seg.startswith("name="):
                    field = seg[5:].strip('"')
                elif seg.startswith("filename="):
                    fname = seg[9:].strip('"')
        mime = headers.get("content-type", "application/octet-stream")
        if fname is not None:
            results.append((field, fname, mime, content))
    return results


# ---------- HTTP Handler ----------
class Handler(BaseHTTPRequestHandler):
    server_version = "PDFToolbox/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, data, filename, mime="application/octet-stream"):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        try:
            filename.encode("latin1")
            dispo = f'attachment; filename="{filename}"'
        except UnicodeEncodeError:
            from urllib.parse import quote
            dispo = f"attachment; filename=\"download\"; filename*=UTF-8''{quote(filename)}"
        self.send_header("Content-Disposition", dispo)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_static(self, path):
        if path == "/":
            path = "/index.html"
        fp = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
        if not fp.startswith(WEB_DIR):
            self.send_error(403)
            return
        if not os.path.exists(fp) or os.path.isdir(fp):
            self.send_error(404)
            return
        mime, _ = mimetypes.guess_type(fp)
        with open(fp, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _get_parts(self, body, ct):
        if "multipart/form-data" in ct:
            boundary = None
            for seg in ct.split(";"):
                seg = seg.strip()
                if seg.startswith("boundary="):
                    boundary = seg[len("boundary="):].strip('"')
            if not boundary:
                raise ValueError("缺少 boundary")
            return parse_multipart(body, boundary.encode())
        return []

    def _save_download(self, filename, data):
        """把转换结果落盘到默认输出目录，返回 (下载URL, 服务器绝对路径)。"""
        ddir = os.path.join(DATA_DIR, "downloads")
        os.makedirs(ddir, exist_ok=True)
        base = os.path.basename(filename) or "output"
        name = f"{int(time.time())}_{uuid.uuid4().hex[:6]}_{base}"
        path = os.path.join(ddir, name)
        with open(path, "wb") as f:
            f.write(data)
        return "/downloads/" + name, path

    def _handle_download(self, name):
        """提供 /downloads/<name> 的文件下载（供 App WebView 直接打开/复制链接）。"""
        ddir = os.path.join(DATA_DIR, "downloads")
        safe = os.path.basename(name)   # 只允许文件名，防目录穿越
        fp = os.path.normpath(os.path.join(ddir, safe))
        if not fp.startswith(ddir) or not os.path.isfile(fp):
            self._send_json({"ok": False, "error": "文件不存在或已过期，请重新转换"}, 404)
            return
        mime = mimetypes.guess_type(fp)[0] or "application/octet-stream"
        with open(fp, "rb") as f:
            data = f.read()
        self._send_file(data, os.path.basename(fp), mime)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/tools":
            self._send_json({"tools": load_tools()})
            return
        if parsed.path.startswith("/api/task/"):
            self._handle_task(parsed.path[len("/api/task/"):])
            return
        if parsed.path.startswith("/api/"):
            # 未知 API 一律返回 JSON，绝不返回 HTML 页面
            self._send_json({"ok": False, "error": "未知接口"}, 404)
            return
        if parsed.path.startswith("/downloads/"):
            self._handle_download(parsed.path[len("/downloads/"):])
            return
        self._send_static(parsed.path)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        ct = self.headers.get("Content-Type", "")
        try:
            route = parsed.path
            if route == "/api/convert/images-to-pdf":
                self._handle_images_to_pdf(body, ct)
            elif route == "/api/convert/pdf-to-images":
                self._handle_pdf_to_images(body, ct, parsed.query)
            elif route == "/api/convert/office-to-pdf":
                self._handle_office(body, ct)
            elif route == "/api/convert/ebook-to-pdf":
                self._handle_ebook(body, ct)
            elif route == "/api/archive/analyze":
                self._handle_archive_analyze(body, ct)
            elif route == "/api/archive/convert":
                self._handle_archive_convert(body, ct, parsed.query)
            elif route == "/api/merge/pdf":
                self._handle_merge_pdf(body, ct)
            else:
                self._send_json({"ok": False, "error": "未知接口"}, 404)
        except Exception as e:
            self._send_json({"ok": False, "error": f"服务器错误：{e}"}, 500)

    def _handle_images_to_pdf(self, body, ct):
        parts = self._get_parts(body, ct)
        images = []
        for field, fname, mime, data in parts:
            ext = os.path.splitext(fname)[1].lower()
            if ext in IMAGE_EXTS:
                images.append((fname, mime, data))
        if not images:
            self._send_json({"ok": False, "error": "未收到图片文件，请上传 JPG / PNG / WebP。"}, 400)
            return
        def job():
            try:
                pdf = images_to_pdf(images)
                url, path = self._save_download("merged.pdf", pdf)
                return True, {"filename": "merged.pdf", "path": path, "url": url}
            except Exception as e:
                return False, {"error": str(e)}
        tid = _submit_task(job)
        self._send_json({"ok": True, "task_id": tid})

    def _handle_pdf_to_images(self, body, ct, query):
        parts = self._get_parts(body, ct)
        if not parts:
            self._send_json({"ok": False, "error": "未收到 PDF 文件"}, 400)
            return
        _, fname, mime, data = parts[0]
        if not fname.lower().endswith(".pdf"):
            self._send_json({"ok": False, "error": "请上传 .pdf 文件"}, 400)
            return
        params = parse_qs(query)
        fmt = params.get("fmt", ["png"])[0]
        dpi = int(params.get("dpi", ["150"])[0])
        def job():
            try:
                zip_data, err = pdf_to_images(fname, data, fmt=fmt, dpi=dpi)
                if err:
                    return False, {"error": err}
                out_name = os.path.splitext(fname)[0] + "_images.zip"
                url, path = self._save_download(out_name, zip_data)
                return True, {"filename": out_name, "path": path, "url": url}
            except Exception as e:
                return False, {"error": str(e)}
        tid = _submit_task(job)
        self._send_json({"ok": True, "task_id": tid})

    def _handle_office(self, body, ct):
        parts = self._get_parts(body, ct)
        if not parts:
            self._send_json({"ok": False, "error": "未收到文件"}, 400)
            return
        _, fname, mime, data = parts[0]
        ext = os.path.splitext(fname)[1].lower()
        if ext not in OFFICE_EXTS:
            self._send_json({"ok": False, "error": f"不支持的格式：{ext}"}, 400)
            return
        def job():
            try:
                pdf, err = office_to_pdf(fname, data)
                if err:
                    return False, {"error": err}
                out_name = os.path.splitext(fname)[0] + ".pdf"
                url, path = self._save_download(out_name, pdf)
                return True, {"filename": out_name, "path": path, "url": url}
            except Exception as e:
                return False, {"error": str(e)}
        tid = _submit_task(job)
        self._send_json({"ok": True, "task_id": tid})

    def _handle_ebook(self, body, ct):
        parts = self._get_parts(body, ct)
        if not parts:
            self._send_json({"ok": False, "error": "未收到文件"}, 400)
            return
        _, fname, mime, data = parts[0]
        ext = os.path.splitext(fname)[1].lower()
        if ext not in EBOOK_EXTS:
            self._send_json({"ok": False, "error": f"不支持的电子书格式：{ext}"}, 400)
            return
        def job():
            try:
                pdf, err = ebook_to_pdf(fname, data)
                if err:
                    return False, {"error": err}
                out_name = os.path.splitext(fname)[0] + ".pdf"
                url, path = self._save_download(out_name, pdf)
                return True, {"filename": out_name, "path": path, "url": url}
            except Exception as e:
                return False, {"error": str(e)}
        tid = _submit_task(job)
        self._send_json({"ok": True, "task_id": tid})

    def _handle_archive_analyze(self, body, ct):
        parts = self._get_parts(body, ct)
        if not parts:
            self._send_json({"ok": False, "error": "未收到压缩包文件"}, 400)
            return
        _, fname, mime, data = parts[0]
        ext = os.path.splitext(fname)[1].lower()
        if ext not in ARCHIVE_EXTS:
            self._send_json({"ok": False, "error": f"请上传 zip / rar / 7z 压缩包（收到 {ext}）"}, 400)
            return
        self._send_json(classify_archive(fname, data))

    def _handle_archive_convert(self, body, ct, query):
        parts = self._get_parts(body, ct)
        if not parts:
            self._send_json({"ok": False, "error": "未收到压缩包文件"}, 400)
            return
        _, fname, mime, data = parts[0]
        ext = os.path.splitext(fname)[1].lower()
        if ext not in ARCHIVE_EXTS:
            self._send_json({"ok": False, "error": f"请上传 zip / rar / 7z 压缩包"}, 400)
            return
        params = parse_qs(query)
        mode = params.get("mode", ["merge"])[0]
        def job():
            try:
                out, is_zip, out_name, err = convert_archive(fname, data, mode)
                if err:
                    return False, {"error": err}
                url, path = self._save_download(out_name, out)
                return True, {"filename": out_name, "path": path, "url": url}
            except Exception as e:
                return False, {"error": str(e)}
        tid = _submit_task(job)
        self._send_json({"ok": True, "task_id": tid})

    def _handle_merge_pdf(self, body, ct):
        parts = self._get_parts(body, ct)
        pdfs = []
        for field, fname, mime, data in parts:
            if fname.lower().endswith(".pdf"):
                pdfs.append(data)
        if not pdfs:
            self._send_json({"ok": False, "error": "未收到 PDF 文件"}, 400)
            return
        def job():
            try:
                merged = merge_pdfs(pdfs)
                url, path = self._save_download("merged.pdf", merged)
                return True, {"filename": "merged.pdf", "path": path, "url": url}
            except Exception as e:
                return False, {"error": f"PDF 整合失败：{e}"}
        tid = _submit_task(job)
        self._send_json({"ok": True, "task_id": tid})

    def _handle_task(self, tid):
        t = TASKS.get(tid)
        if not t:
            self._send_json({"ok": False, "error": "任务不存在或已过期"}, 404)
            return
        self._send_json({"ok": True, **t})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5860)
    ap.add_argument("--data-dir", default="/var/tmp/pdftoolbox-data")
    ap.add_argument("--tmp-dir", default="/var/tmp/pdftoolbox-tmp")
    ap.add_argument("--home-dir", default="/var/tmp/pdftoolbox-home")
    args = ap.parse_args()

    global DATA_DIR, TMP_DIR, HOME_DIR
    DATA_DIR = args.data_dir
    TMP_DIR = args.tmp_dir
    HOME_DIR = args.home_dir
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(TMP_DIR, exist_ok=True)
    os.makedirs(HOME_DIR, exist_ok=True)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[PDFToolbox] listening on http://{args.host}:{args.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
