"""User-customisable UI preferences — persisted at ``~/.modelbridge/ui.json``.

Every knob is tunable through the ``/ui`` slash command mid-session (applied
live to the full-screen TUI) and survives restarts via the JSON file. The
store is deliberately dumb: flat fields, strict coercion per type, unknown
keys rejected — bad hand-edited values fall back to defaults instead of
breaking the REPL.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from ..utils import get_app_dir

UI_FILE_NAME = "ui.json"

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

#: Ordered spec driving both ``/ui`` listing and value coercion:
#: key → (类别, 说明, 类型标签). Types: "float", "int", "bool", "enum:right|left",
#: "hex". Only fields listed here are settable.
_SPEC: dict[str, tuple[str, str, str]] = {
    "rail_ratio": ("布局", "右栏宽度占全屏比例", "float"),
    "rail_max": ("布局", "右栏最大列数", "int"),
    "show_status": ("分组", "右栏：目录/模型/上下文/缓存", "bool"),
    "show_tasks": ("分组", "右栏：任务清单", "bool"),
    "show_bg": ("分组", "右栏：后台任务", "bool"),
    "show_tools": ("分组", "右栏：本轮工具调用", "bool"),
    "footer_hint": ("布局", "右栏底部按键提示行", "bool"),
    "user_align": ("对话流", "用户消息对齐方向", "enum:right|left"),
    "stream_thinking": ("对话流", "流式时实时显示思考内容", "bool"),
    "color_accent": ("配色", "强调色（模型名/组标题）", "hex"),
    "color_good": ("配色", "成功色（工具成功/低占用）", "hex"),
    "color_bad": ("配色", "错误色（失败/审批）", "hex"),
    "color_warn": ("配色", "警示色（中等占用/按键提示）", "hex"),
    "color_tool": ("配色", "工具名颜色", "hex"),
    "color_dim": ("配色", "次要文字灰", "hex"),
}


def _coerce(kind: str, raw: Any):
    """Coerce ``raw`` for ``kind``; raise ValueError with a 中文 message."""
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("on", "true", "1", "yes", "开"):
            return True
        if text in ("off", "false", "0", "no", "关"):
            return False
        raise ValueError("布尔值请用 on/off")
    if kind == "float":
        v = float(raw)
        return v
    if kind == "int":
        return int(raw)
    if kind.startswith("enum:"):
        choices = kind.split(":", 1)[1].split("|")
        text = str(raw).strip().lower()
        if text not in choices:
            raise ValueError(f"可选值: {' / '.join(choices)}")
        return text
    if kind == "hex":
        text = str(raw).strip()
        if not _HEX_RE.match(text):
            raise ValueError("颜色用十六进制，如 #58a6ff")
        return text.lower()
    raise ValueError(f"未知类型 {kind}")


@dataclass
class UiPrefs:
    """Flat preference bag. Defaults mirror the shipped GitHub-dark look."""

    # 布局
    rail_ratio: float = 0.4          # of full screen width (0.22–0.55)
    rail_max: int = 72               # hard cap in columns
    footer_hint: bool = True
    # 右栏分组开关
    show_status: bool = True         # 目录 / 模型 / 上下文 / 缓存
    show_tasks: bool = True
    show_bg: bool = True
    show_tools: bool = True
    # 对话流
    user_align: str = "right"        # 用户消息靠右（经典模式语言）
    stream_thinking: bool = True     # 流式时实时显示思考 delta
    # 配色
    color_accent: str = "#58a6ff"
    color_good: str = "#3fb950"
    color_bad: str = "#f85149"
    color_warn: str = "#d29922"
    color_tool: str = "#d2a8ff"
    color_dim: str = "#8a8f98"

    #: Test/diagnostic hook — override the storage path (default ~/.modelbridge/ui.json).
    path: Path | None = field(default=None, repr=False, compare=False)
    #: mtime captured by load()/maybe_reload(); drives cheap cross-process
    #: hot-reload (the browser panel edits ui.json → terminal TUI picks it up).
    _loaded_mtime: float = field(default=-1.0, repr=False, compare=False)

    # -- persistence ---------------------------------------------------------

    @classmethod
    def _default_path(cls) -> Path:
        return get_app_dir() / UI_FILE_NAME

    @classmethod
    def load(cls, path: Path | None = None) -> "UiPrefs":
        """Read ``ui.json``; any corruption/unknown field degrades to defaults.

        ``path`` overrides the storage location (tests / MBRIDGE_HOME-less
        diagnostics); the returned instance keeps it as its save target.
        """
        prefs = cls()
        if path is not None:
            prefs.path = path
        try:
            src = json.loads((prefs.path or cls._default_path()).read_text("utf-8"))
            known = {f.name: f for f in fields(cls) if f.name != "path"}
            for key, raw in dict(src or {}).items():
                fobj = known.get(key)
                if fobj is None:
                    continue
                spec = _SPEC.get(key)
                try:
                    coerced = _coerce(spec[2], raw) if spec else raw
                    setattr(prefs, key, coerced)
                except (ValueError, TypeError):
                    continue  # 单个坏值静默回落默认
        except Exception:
            pass  # 文件不存在/损坏 → 全默认
        prefs.clamp()  # 手改文件写出越界数值时兜住几何范围
        try:
            prefs._loaded_mtime = (prefs.path or cls._default_path()).stat().st_mtime
        except OSError:
            pass
        return prefs

    def maybe_reload(self) -> bool:
        """Re-read the file when its mtime moved (cheap: one stat per call).

        Returns True when the in-memory bag changed. Callers should treat the
        object as read-fresh afterwards; renders pick up new values naturally.
        """
        target = self.path or self._default_path()
        try:
            mtime = target.stat().st_mtime
        except OSError:
            return False
        if mtime == self._loaded_mtime:
            return False
        fresh = type(self).load(path=self.path)
        self._loaded_mtime = fresh._loaded_mtime
        for f in fields(self):
            if f.name not in ("path", "_loaded_mtime"):
                setattr(self, f.name, getattr(fresh, f.name))
        return True

    def save(self) -> None:
        target = self.path or self._default_path()
        payload = {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if f.name != "path" and f.name in _SPEC
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
        try:
            self._loaded_mtime = target.stat().st_mtime
        except OSError:
            pass

    # -- mutation ------------------------------------------------------------

    def clamp(self) -> None:
        """Enforce numeric ranges after load/set (rail geometry keeps sane)."""
        self.rail_ratio = min(0.55, max(0.22, float(self.rail_ratio)))
        self.rail_max = min(96, max(40, int(self.rail_max)))

    def set_key(self, key: str, raw: Any) -> str:
        """Set one knob by name; returns a 中文 confirmation message.

        Raises ``KeyError`` for unknown keys and ``ValueError`` for bad values.
        """
        spec = _SPEC.get(key)
        if spec is None:
            raise KeyError(key)
        _category, desc, kind = spec
        value = _coerce(kind, raw)
        setattr(self, key, value)
        self.clamp()
        self.save()
        return f"{desc} → {getattr(self, key)}"

    # -- introspection -------------------------------------------------------

    @classmethod
    def spec_items(cls) -> list[tuple[str, str, str]]:
        """/ui listing rows: (key, 类别说明文本, 当前值格式化在调用侧拼)。"""
        return [
            (key, f"{cat} · {desc}", kind)
            for key, (cat, desc, kind) in _SPEC.items()
        ]

    def format_value(self, key: str) -> str:
        return str(getattr(self, key))


__all__ = ["UiPrefs"]
