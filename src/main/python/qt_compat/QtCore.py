from PyQt6 import QtCore as _QtCore
from PyQt6.QtCore import *  # noqa: F401,F403


def _alias_attr(obj, name, value):
    if not hasattr(obj, name):
        setattr(obj, name, value)


Qt = _QtCore.Qt
pyqtSignal = _QtCore.pyqtSignal
pyqtSlot = _QtCore.pyqtSlot
pyqtProperty = _QtCore.pyqtProperty
QT_VERSION_STR = _QtCore.QT_VERSION_STR
PYQT_VERSION_STR = _QtCore.PYQT_VERSION_STR

_QT_ALIASES = {
    "AlignCenter": Qt.AlignmentFlag.AlignCenter,
    "AlignHCenter": Qt.AlignmentFlag.AlignHCenter,
    "ClickFocus": Qt.FocusPolicy.ClickFocus,
    "CustomizeWindowHint": Qt.WindowType.CustomizeWindowHint,
    "Dialog": Qt.WindowType.Dialog,
    "FramelessWindowHint": Qt.WindowType.FramelessWindowHint,
    "Horizontal": Qt.Orientation.Horizontal,
    "KeepAspectRatio": Qt.AspectRatioMode.KeepAspectRatio,
    "LeftButton": Qt.MouseButton.LeftButton,
    "NoFocus": Qt.FocusPolicy.NoFocus,
    "NoPen": Qt.PenStyle.NoPen,
    "OddEvenFill": Qt.FillRule.OddEvenFill,
    "RichText": Qt.TextFormat.RichText,
    "ScrollBarAlwaysOff": Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
    "ScrollBarAlwaysOn": Qt.ScrollBarPolicy.ScrollBarAlwaysOn,
    "SmoothTransformation": Qt.TransformationMode.SmoothTransformation,
    "SolidPattern": Qt.BrushStyle.SolidPattern,
    "ToolButtonTextOnly": Qt.ToolButtonStyle.ToolButtonTextOnly,
    "Vertical": Qt.Orientation.Vertical,
    "WindowContextHelpButtonHint": Qt.WindowType.WindowContextHelpButtonHint,
    "WindowStaysOnTopHint": Qt.WindowType.WindowStaysOnTopHint,
    "WindowTitleHint": Qt.WindowType.WindowTitleHint,
    "X11BypassWindowManagerHint": Qt.WindowType.X11BypassWindowManagerHint,
}

for _name, _value in _QT_ALIASES.items():
    _alias_attr(Qt, _name, _value)

for _name in ("Key_Control", "Key_Delete", "Key_Escape", "Key_O", "Key_S"):
    _alias_attr(Qt, _name, getattr(Qt.Key, _name))

if not hasattr(Qt, "Orientations"):
    Qt.Orientations = lambda value: value

_alias_attr(QStandardPaths, "AppLocalDataLocation", QStandardPaths.StandardLocation.AppLocalDataLocation)
_alias_attr(QStandardPaths, "CacheLocation", QStandardPaths.StandardLocation.CacheLocation)

_alias_attr(QEvent, "LayoutRequest", QEvent.Type.LayoutRequest)
_alias_attr(QEvent, "MouseButtonDblClick", QEvent.Type.MouseButtonDblClick)
_alias_attr(QEvent, "ToolTip", QEvent.Type.ToolTip)

_alias_attr(QIODevice, "WriteOnly", _QtCore.QIODeviceBase.OpenModeFlag.WriteOnly)
_alias_attr(QProcess, "ReadWrite", _QtCore.QIODeviceBase.OpenModeFlag.ReadWrite)
_alias_attr(QProcess, "Unbuffered", _QtCore.QIODeviceBase.OpenModeFlag.Unbuffered)
