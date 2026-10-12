# Responsible for: bundled Lucide lookup, theme coloring and bounded Qt icon caches.

from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from utils.paths import get_resource_path


ALIASES = {"work": "briefcase-business", "idea": "lightbulb", "book": "book-open",
           "stop-square": "square", "pencil-sparkles": "wand-sparkles", "trash-2": "trash"}


@lru_cache(maxsize=1)
def available_icons():
    """Return sorted bundled icon names without loading or rendering SVG content."""
    return tuple(sorted(path.stem for path in Path(get_resource_path("assets/icons/lucide")).glob("*.svg")))


def resolve_name(name):
    """Map legacy names and unknown values to a safe bundled icon name."""
    name = ALIASES.get(name, name) if isinstance(name, str) else "folder"
    return name if name in available_icons() else "folder"


@lru_cache(maxsize=256)
def _svg(name):
    return Path(get_resource_path(f"assets/icons/lucide/{name}.svg")).read_bytes()


@lru_cache(maxsize=256)
def _pixmap(name, color, size, dpr):
    data = _svg(name).replace(b"currentColor", color.encode("ascii"))
    pixmap = QPixmap(round(size * dpr), round(size * dpr))
    pixmap.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(data))
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def get_pixmap(name, color=None, theme=None, size=24, dpr=1.0):
    """Return a cached transparent Lucide pixmap; color overrides the theme."""
    selected = QColor(color or (theme or {}).get("DARK_TEXT", "#586e75"))
    if not selected.isValid():
        selected = QColor("#586e75")
    return QPixmap(_pixmap(resolve_name(name), selected.name(), size, dpr))


@lru_cache(maxsize=256)
def _icon(name, color):
    # Include high-density pixmaps without rendering the entire catalog eagerly.
    result = QIcon()
    for size in (16, 24, 32):
        for dpr in (1.0, 2.0):
            result.addPixmap(get_pixmap(name, color=color, size=size, dpr=dpr))
    return result


def get_icon(name, color=None, theme=None):
    """Return a bounded cached QIcon for a Lucide name, legacy alias or fallback."""
    selected = QColor(color or (theme or {}).get("DARK_TEXT", "#586e75"))
    return QIcon(_icon(resolve_name(name), selected.name() if selected.isValid() else "#586e75"))
