# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

# Copyright 2014 Florian Bruhin (The Compiler) <mail@qutebrowser.org>
#
# This file is part of qutebrowser.
#
# qutebrowser is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# qutebrowser is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with qutebrowser.  If not, see <http://www.gnu.org/licenses/>.

"""The progress bar in the statusbar."""

from PyQt5.QtCore import pyqtSlot
from PyQt5.QtWidgets import QProgressBar, QSizePolicy

from qutebrowser.widgets import webview
from qutebrowser.config import style
from qutebrowser.utils import utils


class Progress(QProgressBar):

    """The progress bar part of the status bar."""

    # FIXME for some reason, margin-left is not shown
    # https://github.com/The-Compiler/qutebrowser/issues/125

    STYLESHEET = """
        QProgressBar {
            border-radius: 0px;
            border: 2px solid transparent;
            margin-left: 1px;
            background-color: transparent;
        }

        QProgressBar::chunk {
            {{ color['statusbar.progress.bg'] }}
        }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        style.set_register_stylesheet(self)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Ignored)
        self.setTextVisible(False)
        self.hide()

    def __repr__(self):
        return utils.get_repr(self, value=self.value())

    @pyqtSlot()
    def on_load_started(self):
        """Clear old error and show progress, used as slot to loadStarted."""
        self.setValue(0)
        self.show()

    @pyqtSlot(int)
    def on_tab_changed(self, tab):
        """Set the correct value when the current tab changed."""
        if self is None:
            # This should never happen, but for some weird reason it does
            # sometimes.
            return
        self.setValue(tab.progress)
        if tab.load_status == webview.LoadStatus.loading:
            self.show()
        else:
            self.hide()
