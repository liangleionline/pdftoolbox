# -*- coding: utf-8 -*-
"""WebP 解码子进程工具。

在独立进程中用 ctypes 调用系统 libwebp 解码 WebP，结果写入文件。
这样即使 libwebp 因异常输入崩溃（段错误），也只影响本子进程，
不会拖垮主服务进程。

用法：
    python3 webpcli.py decode <in.webp> <out.rgb>

输出文件格式：前 8 字节为小端 uint32 宽、高，其后为 RGB 像素数据。
退出码：0=成功，2=无libwebp，3=WebPGetInfo失败，4=尺寸超限，5=解码失败，其他=异常。
"""
import ctypes
import ctypes.util
import glob
import struct
import sys


def _load_libwebp():
    candidates = []
    for pat in (
        "/usr/lib/x86_64-linux-gnu/libwebp.so*",
        "/usr/lib/aarch64-linux-gnu/libwebp.so*",
        "/usr/lib/arm-linux-gnueabihf/libwebp.so*",
        "/usr/lib64/libwebp.so*",
        "/usr/lib/libwebp.so*",
        "/usr/local/lib/libwebp.so*",
    ):
        candidates += sorted(glob.glob(pat))
    n = ctypes.util.find_library("webp")
    if n and not candidates:
        candidates = [n]
    for path in candidates:
        try:
            lib = ctypes.CDLL(path)
            if hasattr(lib, "WebPGetInfo") and hasattr(lib, "WebPDecodeRGBA"):
                return lib
        except Exception:
            continue
    return None


MAX_PIXELS = 30_000_000   # 约 5000*6000，防内存爆炸
MAX_EDGE = 20000


def decode_webp(src, dst):
    try:
        with open(src, "rb") as f:
            data = f.read()
    except Exception:
        return 6
    lib = _load_libwebp()
    if lib is None:
        return 2
    try:
        buf = ctypes.create_string_buffer(data)
        w = ctypes.c_int(0)
        h = ctypes.c_int(0)
        lib.WebPGetInfo.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                                    ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        lib.WebPGetInfo.restype = ctypes.c_int
        if not lib.WebPGetInfo(buf, len(data), ctypes.byref(w), ctypes.byref(h)):
            return 3
        if w.value <= 0 or h.value <= 0 or w.value > MAX_EDGE or h.value > MAX_EDGE:
            return 4
        if w.value * h.value > MAX_PIXELS:
            return 4
        lib.WebPDecodeRGBA.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                                       ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        lib.WebPDecodeRGBA.restype = ctypes.POINTER(ctypes.c_ubyte)
        ptr = lib.WebPDecodeRGBA(buf, len(data), ctypes.byref(w), ctypes.byref(h))
        if not ptr:
            return 5
        try:
            n = w.value * h.value * 4
            raw = bytes(ptr[:n])
        finally:
            lib.WebPFree(ctypes.cast(ptr, ctypes.c_void_p))
    except Exception:
        return 7
    if len(raw) != w.value * h.value * 4:
        return 8
    rgb = bytearray(w.value * h.value * 3)
    px = w.value * h.value
    for i in range(px):
        rgb[i * 3] = raw[i * 4]
        rgb[i * 3 + 1] = raw[i * 4 + 1]
        rgb[i * 3 + 2] = raw[i * 4 + 2]
    try:
        with open(dst, "wb") as f:
            f.write(struct.pack("<II", w.value, h.value))
            f.write(bytes(rgb))
    except Exception:
        return 9
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 4 or sys.argv[1] != "decode":
        print("usage: webpcli.py decode <in.webp> <out.rgb>", file=sys.stderr)
        sys.exit(1)
    sys.exit(decode_webp(sys.argv[2], sys.argv[3]))
