from PyQt6 import QtGui as _QtGui
from PyQt6 import QtWidgets as _QtWidgets
from PyQt6.QtWidgets import *  # noqa: F401,F403


def _alias_attr(obj, name, value):
    if not hasattr(obj, name):
        setattr(obj, name, value)


class _DesktopCompat:
    def geometry(self):
        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        return screen.availableGeometry() if screen is not None else None

    def availableGeometry(self, widget=None):
        app = QApplication.instance()
        if app is None:
            return None
        if widget is not None:
            screen = widget.screen()
            if screen is not None:
                return screen.availableGeometry()
        return app.primaryScreen().availableGeometry()


class _QAppProxy:
    def __getattr__(self, attr):
        app = QApplication.instance()
        if app is None:
            raise AttributeError(attr)
        return getattr(app, attr)


_alias_attr(QDialog, "Accepted", QDialog.DialogCode.Accepted)
_alias_attr(QDialogButtonBox, "Ok", QDialogButtonBox.StandardButton.Ok)
_alias_attr(QDialogButtonBox, "Cancel", QDialogButtonBox.StandardButton.Cancel)
_alias_attr(QFileDialog, "AcceptOpen", QFileDialog.AcceptMode.AcceptOpen)
_alias_attr(QFileDialog, "AcceptSave", QFileDialog.AcceptMode.AcceptSave)
_alias_attr(QFrame, "NoFrame", QFrame.Shape.NoFrame)
_alias_attr(QMessageBox, "Yes", QMessageBox.StandardButton.Yes)
_alias_attr(QMessageBox, "No", QMessageBox.StandardButton.No)
_alias_attr(QSizePolicy, "Expanding", QSizePolicy.Policy.Expanding)
_alias_attr(QSizePolicy, "Maximum", QSizePolicy.Policy.Maximum)
_alias_attr(QSizePolicy, "PushButton", QSizePolicy.ControlType.PushButton)

if not hasattr(QDialog, "exec_"):
    QDialog.exec_ = QDialog.exec

if not hasattr(QMessageBox, "exec_"):
    QMessageBox.exec_ = QMessageBox.exec

if not hasattr(QApplication, "desktop"):
    QApplication.desktop = lambda self=None: _DesktopCompat()

AAction = _QtGui.QAction
AActionGroup = _QtGui.QActionGroup
QAction = AAction
QActionGroup = AActionGroup
qApp = _QAppProxy()
