"""行尾保真（line-ending fidelity）helpers。

编辑工具（``write_file`` / ``str_replace`` / patch applier）改写文件时
必须**不改动行尾风格**：LF-only 文件编辑后仍是 LF，CRLF 文件编辑后仍是
CRLF。默认的 ``Path.write_text``（``newline=None``）会把 ``\\n`` 翻译成
``os.linesep``——在 Windows 上把 LF-only 文件整体改写成 CRLF，制造
全文件 git diff 噪声。因此所有编辑写盘都必须 ``newline=""``（按字符串
原样写），行尾转换只在这里显式发生。

策略对齐 ZCode 的 edit handler（detectLineEndings + normalize +
applyRequestedLineEndings）：读取时探测多数派行尾，匹配/替换在 LF 归一化
文本上做，写回时把原行尾应用回去；多数派平票归 LF（新文件即 LF）。
"""

from __future__ import annotations

from pathlib import Path

CRLF = "\r\n"
LF = "\n"


def detect_line_endings(text: str) -> str:
    """返回 ``text`` 的多数派行尾（``"\\r\\n"`` 或 ``"\\n"``）。

    只统计 ``\\r\\n`` 对裸 ``\\n`` 的多数（与 ZCode detectLineEndings 一致），
    平票（含空文本）归 LF。
    """
    crlf = text.count(CRLF)
    lf = text.count(LF) - crlf
    return CRLF if crlf > lf else LF


def normalize_to_lf(text: str) -> str:
    """把 CRLF 归一化成 LF（匹配/替换统一在 LF 空间进行）。"""
    return text.replace(CRLF, LF)


def apply_line_endings(text: str, eol: str) -> str:
    """把 ``text``（任意行尾）归一化后应用 ``eol`` 行尾。"""
    normalized = normalize_to_lf(text)
    return normalized.replace(LF, CRLF) if eol == CRLF else normalized


def read_text_raw(path: Path, *, encoding: str = "utf-8") -> str:
    """按字节读入并解码，**不做任何行尾翻译**。

    ``Path.read_text`` 的 ``newline`` 参数 3.13 才加入（项目要求 >=3.10），
    默认的 universal-newlines 会把 CRLF 折叠成 LF，丢失原文行尾信息，
    所以这里走 ``read_bytes().decode()``。
    """
    return path.read_bytes().decode(encoding)


__all__ = [
    "CRLF",
    "LF",
    "apply_line_endings",
    "detect_line_endings",
    "normalize_to_lf",
    "read_text_raw",
]
