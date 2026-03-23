from PyQt6 import QtGui as _QtGui
from PyQt6.QtGui import *  # noqa: F401,F403


def _alias_attr(obj, name, value):
    if not hasattr(obj, name):
        setattr(obj, name, value)


_alias_attr(QPalette, "Active", QPalette.ColorGroup.Active)
_alias_attr(QPalette, "Disabled", QPalette.ColorGroup.Disabled)

for _name in (
    "AlternateBase",
    "Base",
    "BrightText",
    "Button",
    "ButtonText",
    "Highlight",
    "HighlightedText",
    "Light",
    "Link",
    "Text",
    "ToolTipBase",
    "ToolTipText",
    "Window",
    "WindowText",
):
    _alias_attr(QPalette, _name, getattr(QPalette.ColorRole, _name))

_alias_attr(QPainter, "Antialiasing", QPainter.RenderHint.Antialiasing)
_alias_attr(QFont, "TypeWriter", QFont.StyleHint.TypeWriter)
_alias_attr(QFontDatabase, "FixedFont", QFontDatabase.SystemFont.FixedFont)
