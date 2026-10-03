# -*- coding: utf-8 -*-
"""
WebP 解码器 —— 零 Python 依赖（纯标准库）。

所有解码器都运行在独立子进程中，即使某个解码器（如 libwebp 原生库）遇到
异常输入导致崩溃（段错误），也只会影响子进程，不会拖垮主服务进程。
这避免了"后端进程死亡 → 应用网关返回 502"的问题。

解码顺序：
  1. ffmpeg（系统命令）
  2. ImageMagick convert（系统命令）
  3. libwebp（经 webpcli.py 在子进程中 ctypes 调用，官方解码器，全格式支持）
均有尺寸上限与超时保护。全部失败返回 None，由上层回退 Pillow / PyMuPDF / 报错。
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_WEBPCLI = os.path.join(_THIS_DIR, "webpcli.py")

MAX_PIXELS = 30_000_000   # 与 webpcli 保持一致
MAX_EDGE = 20000
_TMO_CMD = 120            # ffmpeg / convert 超时（秒）
_TMO_LIB = 60             # libwebp 子进程超时（秒）


def libwebp_available():
    """系统是否有可用的 libwebp（保守探测）。"""
    try:
        p = subprocess.run(
            [sys.executable, _WEBPCLI, "decode", os.devnull, os.devnull],
            capture_output=True, timeout=10)
        # 退出码 6（读文件失败）说明已成功加载 libwebp
        return p.returncode in (0, 6)
    except Exception:
        return False


def _decode_via_ffmpeg(data, workdir):
    try:
        src = os.path.join(workdir, "in.webp")
        with open(src, "wb") as f:
            f.write(data)
        # 先探测尺寸，超限则不解码
        pv = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", src],
            capture_output=True, timeout=60)
        if pv.returncode != 0:
            return None
        dims = pv.stdout.decode("utf-8", errors="replace").strip().split(",")
        if len(dims) != 2:
            return None
        w, h = int(dims[0]), int(dims[1])
        if w <= 0 or h <= 0 or w > MAX_EDGE or h > MAX_EDGE or w * h > MAX_PIXELS:
            return None
        p = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src,
             "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
            capture_output=True, timeout=_TMO_CMD)
        if p.returncode != 0 or len(p.stdout) < w * h * 3:
            return None
        return w, h, p.stdout[:w * h * 3]
    except Exception:
        return None


def _decode_via_convert(data, workdir):
    try:
        src = os.path.join(workdir, "in.webp")
        with open(src, "wb") as f:
            f.write(data)
        dims = subprocess.run(
            ["identify", "-format", "%w %h", src],
            capture_output=True, timeout=60)
        if dims.returncode != 0:
            return None
        wh = dims.stdout.decode().strip().split()
        if len(wh) != 2:
            return None
        w, h = int(wh[0]), int(wh[1])
        if w <= 0 or h <= 0 or w > MAX_EDGE or h > MAX_EDGE or w * h > MAX_PIXELS:
            return None
        p = subprocess.run(
            ["convert", src, "rgb:-"],
            capture_output=True, timeout=_TMO_CMD)
        if p.returncode != 0 or len(p.stdout) < w * h * 3:
            return None
        return w, h, p.stdout[:w * h * 3]
    except Exception:
        return None


def _decode_via_libwebp_sub(data, workdir):
    """在子进程中用 ctypes 调用 libwebp，隔离段错误崩溃。"""
    try:
        src = os.path.join(workdir, "in.webp")
        out = os.path.join(workdir, "out.rgb")
        with open(src, "wb") as f:
            f.write(data)
        p = subprocess.run(
            [sys.executable, _WEBPCLI, "decode", src, out],
            capture_output=True, timeout=_TMO_LIB)
        if p.returncode != 0:
            return None
        with open(out, "rb") as f:
            head = f.read(8)
            if len(head) < 8:
                return None
            w, h = struct.unpack("<II", head)
            rgb = f.read()
        if w <= 0 or h <= 0 or len(rgb) < w * h * 3:
            return None
        return w, h, rgb[:w * h * 3]
    except Exception:
        return None


def decode_webp_rgb(data):
    """把 WebP 解码为 RGB。返回 (w, h, rgb_bytes)，全部失败返回 None。

    全部解码器均运行于子进程（崩溃隔离），并带尺寸上限与超时保护。
    """
    try:
        with tempfile.TemporaryDirectory(prefix="webp_") as td:
            r = _decode_via_ffmpeg(data, td)
            if r:
                return r
            r = _decode_via_convert(data, td)
            if r:
                return r
            r = _decode_via_libwebp_sub(data, td)
            if r:
                return r
    except Exception:
        return None
    return None


def webp_supported_natively():
    """本机是否能在无 Pillow/PyMuPDF 的情况下解码 WebP。"""
    return bool(shutil.which("ffmpeg") or shutil.which("convert") or libwebp_available())


if __name__ == "__main__":
    with open(sys.argv[1], "rb") as f:
        d = f.read()
    r = decode_webp_rgb(d)
    if r:
        w, h, rgb = r
        print(f"OK {w}x{h} rgb={len(rgb)}B")
    else:
        print("FAIL decode")
