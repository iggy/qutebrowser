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

"""Initialization of qutebrowser and application-wide things."""

import os
import sys
import subprocess
import faulthandler
import configparser
import getpass
from bdb import BdbQuit
from functools import partial
from signal import signal, SIGINT
from base64 import b64encode

from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox
from PyQt5.QtCore import (pyqtSlot, QTimer, QEventLoop, Qt, QStandardPaths,
                          qInstallMessageHandler, QObject, QUrl)
from PyQt5.QtNetwork import QLocalSocket, QLocalServer

import qutebrowser
import qutebrowser.commands.utils as cmdutils
import qutebrowser.config.style as style
import qutebrowser.config.config as config
import qutebrowser.network.qutescheme as qutescheme
import qutebrowser.config.websettings as websettings
import qutebrowser.network.proxy as proxy
import qutebrowser.browser.quickmarks as quickmarks
import qutebrowser.utils.log as log
import qutebrowser.utils.version as version
import qutebrowser.utils.url as urlutils
import qutebrowser.utils.message as message
from qutebrowser.network.networkmanager import NetworkManager
from qutebrowser.config.config import ConfigManager
from qutebrowser.keyinput.modeman import ModeManager
from qutebrowser.widgets.mainwindow import MainWindow
from qutebrowser.widgets.crash import (ExceptionCrashDialog, FatalCrashDialog,
                                       ReportDialog)
from qutebrowser.keyinput.modeparsers import (NormalKeyParser, HintKeyParser,
                                              PromptKeyParser)
from qutebrowser.keyinput.keyparser import PassthroughKeyParser
from qutebrowser.commands.managers import CommandManager, SearchManager
from qutebrowser.config.iniparsers import ReadWriteConfigParser
from qutebrowser.config.lineparser import LineConfigParser
from qutebrowser.browser.cookies import CookieJar
from qutebrowser.browser.downloads import DownloadManager
from qutebrowser.utils.misc import get_standard_dir, actute_warning
from qutebrowser.utils.qt import get_qt_args
from qutebrowser.utils.readline import ReadlineBridge
from qutebrowser.utils.usertypes import Timer


class Application(QApplication):

    """Main application instance.

    Attributes:
        mainwindow: The MainWindow QWidget.
        commandmanager: The main CommandManager instance.
        searchmanager: The main SearchManager instance.
        config: The main ConfigManager
        stateconfig: The "state" ReadWriteConfigParser instance.
        cmd_history: The "cmd_history" LineConfigParser instance.
        messagebridge: The global MessageBridge instance.
        modeman: The global ModeManager instance.
        networkmanager: The global NetworkManager instance.
        cookiejar: The global CookieJar instance.
        rl_bridge: The ReadlineBridge being used.
        args: ArgumentParser instance.
        _keyparsers: A mapping from modes to keyparsers.
        _timers: List of used QTimers so they don't get GCed.
        _shutting_down: True if we're currently shutting down.
        _quit_status: The current quitting status.
        _crashdlg: The crash dialog currently open.
        _crashlogfile: A file handler to the fatal crash logfile.
    """

    # This also holds all our globals, so we're a bit over the top here.
    # pylint: disable=too-many-instance-attributes

    def __init__(self, args):
        """Constructor.

        Args:
            Argument namespace from argparse.
        """
        # pylint: disable=too-many-statements
        qt_args = get_qt_args(args)
        log.init.debug("Qt arguments: {}, based on {}".format(qt_args, args))
        super().__init__(get_qt_args(args))
        self._quit_status = {
            'crash': True,
            'tabs': False,
            'networkmanager': False,
            'main': False,
        }
        self._timers = []
        self._shutting_down = False
        self._keyparsers = None
        self._crashdlg = None
        self._crashlogfile = None
        self.rl_bridge = None
        self.messagebridge = None
        self.stateconfig = None
        self.modeman = None
        self.cmd_history = None
        self.config = None

        sys.excepthook = self._exception_hook

        self.args = args
        self.socket = QLocalSocket(self)
        self.socket.connectToServer('qutebrowser-{}'.format(getpass.getuser()))
        is_running = self.socket.waitForConnected(500)
        if is_running:
            print("qutebrowser is already running!")
            sys.exit(0)

        self.server = QLocalServer()
        ok = self.server.listen('qutebrowser-{}'.format(getpass.getuser()))
        if not ok:
            log.init.error("Error while listening: {}".format(self.server.errorString()))
        #self.server.newConnection.connect(self.on_localsocket_connection)

        log.init.debug("Starting init...")
        self._init_misc()
        actute_warning()
        log.init.debug("Initializing config...")
        self._init_config()
        log.init.debug("Initializing crashlog...")
        self._handle_segfault()
        log.init.debug("Initializing modes...")
        self._init_modes()
        log.init.debug("Initializing websettings...")
        websettings.init()
        log.init.debug("Initializing quickmarks...")
        quickmarks.init()
        log.init.debug("Initializing proxy...")
        proxy.init()
        log.init.debug("Initializing cookies...")
        self.cookiejar = CookieJar(self)
        log.init.debug("Initializing NetworkManager...")
        self.networkmanager = NetworkManager(self.cookiejar)
        log.init.debug("Initializing commands...")
        self.commandmanager = CommandManager()
        log.init.debug("Initializing search...")
        self.searchmanager = SearchManager(self)
        log.init.debug("Initializing downloads...")
        self.downloadmanager = DownloadManager(self)
        log.init.debug("Initializing main window...")
        self.mainwindow = MainWindow()

        self.modeman.mainwindow = self.mainwindow
        log.init.debug("Initializing eventfilter...")
        self.installEventFilter(self.modeman)
        self.setQuitOnLastWindowClosed(False)

        log.init.debug("Connecting signals...")
        self._connect_signals()
        self.modeman.enter('normal', 'init')

        log.init.debug("Showing mainwindow...")
        self.mainwindow.show()
        log.init.debug("Applying python hacks...")
        self._python_hacks()
        timer = QTimer.singleShot(0, self._process_init_args)
        self._timers.append(timer)

        log.init.debug("Init done!")

        if self._crashdlg is not None:
            self._crashdlg.raise_()

    def _init_config(self):
        """Inizialize and read the config."""
        if self.args.confdir is None:
            confdir = get_standard_dir(QStandardPaths.ConfigLocation)
        elif self.args.confdir == '':
            confdir = None
        else:
            confdir = self.args.confdir
        try:
            self.config = ConfigManager(confdir, 'qutebrowser.conf', self)
        except (config.ValidationError,
                config.NoOptionError,
                configparser.InterpolationError,
                configparser.DuplicateSectionError,
                configparser.DuplicateOptionError,
                configparser.ParsingError,
                ValueError) as e:
            errstr = "Error while reading config:"
            if hasattr(e, 'section') and hasattr(e, 'option'):
                errstr += "\n\n{} -> {}:".format(e.section, e.option)
            errstr += "\n{}".format(e)
            msgbox = QMessageBox(QMessageBox.Critical,
                                 "Error while reading config!", errstr)
            msgbox.exec_()
            # We didn't really initialize much so far, so we just quit hard.
            sys.exit(1)
        self.stateconfig = ReadWriteConfigParser(confdir, 'state')
        self.cmd_history = LineConfigParser(confdir, 'cmd_history',
                                            ('completion', 'history-length'))

    def _init_modes(self):
        """Inizialize the mode manager and the keyparsers."""
        self._keyparsers = {
            'normal': NormalKeyParser(self),
            'hint': HintKeyParser(self),
            'insert': PassthroughKeyParser('keybind.insert', self),
            'passthrough': PassthroughKeyParser('keybind.passthrough', self),
            'command': PassthroughKeyParser('keybind.command', self),
            'prompt': PassthroughKeyParser('keybind.prompt', self, warn=False),
            'yesno': PromptKeyParser(self),
        }
        self.modeman = ModeManager(self)
        self.modeman.register('normal', self._keyparsers['normal'].handle)
        self.modeman.register('hint', self._keyparsers['hint'].handle)
        self.modeman.register('insert', self._keyparsers['insert'].handle,
                              passthrough=True)
        self.modeman.register('passthrough',
                              self._keyparsers['passthrough'].handle,
                              passthrough=True)
        self.modeman.register('command', self._keyparsers['command'].handle,
                              passthrough=True)
        self.modeman.register('prompt', self._keyparsers['prompt'].handle,
                              passthrough=True)
        self.modeman.register('yesno', self._keyparsers['yesno'].handle)

    def _init_misc(self):
        """Initialize misc things."""
        if self.args.version:
            print(version.version())
            print()
            print()
            print(qutebrowser.__copyright__)
            print()
            print(version.GPL_BOILERPLATE.strip())
            sys.exit(0)
        self.setOrganizationName("qutebrowser")
        self.setApplicationName("qutebrowser")
        self.setApplicationVersion(qutebrowser.__version__)
        self.messagebridge = message.MessageBridge(self)
        self.rl_bridge = ReadlineBridge()

    def _handle_segfault(self):
        """Handle a segfault from a previous run."""
        # FIXME If an empty logfile exists, we log to stdout instead, which is
        # the only way to not break multiple instances.
        # However this also means if the logfile is there for some weird
        # reason, we'll *always* log to stderr, but that's still better than no
        # dialogs at all.
        logname = os.path.join(get_standard_dir(QStandardPaths.DataLocation),
                               'crash.log')
        # First check if an old logfile exists.
        if os.path.exists(logname):
            with open(logname, 'r') as f:
                data = f.read()
            if data:
                # Crashlog exists and has data in it, so something crashed
                # previously.
                try:
                    os.remove(logname)
                except PermissionError:
                    log.init.warning("Could not remove crash log!")
                else:
                    self._init_crashlogfile()
                self._crashdlg = FatalCrashDialog(data)
                self._crashdlg.show()
            else:
                # Crashlog exists but without data.
                # This means another instance is probably still running and
                # didn't remove the file. As we can't write to the same file,
                # we just leave faulthandler as it is and log to stderr.
                log.init.warning("Empty crash log detected. This means either "
                                 "another instance is running (then ignore "
                                 "this warning) or the file is lying here "
                                 "because of some earlier crash (then delete "
                                 "{}).".format(logname))
                self._crashlogfile = None
        else:
            # There's no log file, so we can use this to display crashes to the
            # user on the next start.
            self._init_crashlogfile()

    def _init_crashlogfile(self):
        """Start a new logfile and redirect faulthandler to it."""
        logname = os.path.join(get_standard_dir(QStandardPaths.DataLocation),
                               'crash.log')
        self._crashlogfile = open(logname, 'w')
        faulthandler.enable(self._crashlogfile)
        if (hasattr(faulthandler, 'register') and
                hasattr(signal, 'SIGUSR1')):
            # If available, we also want a traceback on SIGUSR1.
            # pylint: disable=no-member
            faulthandler.register(signal.SIGUSR1)

    def _process_init_args(self):
        """Process initial positional args.

        URLs to open have no prefix, commands to execute begin with a colon.
        """
        # QNetworkAccessManager::createRequest will hang for over a second, so
        # we make sure the GUI is refreshed here, so the start seems faster.
        self.processEvents(QEventLoop.ExcludeUserInputEvents |
                           QEventLoop.ExcludeSocketNotifiers)

        for cmd in self.args.command:
            if cmd.startswith(':'):
                log.init.debug("Startup cmd {}".format(cmd))
                self.commandmanager.run_safely_init(cmd.lstrip(':'))
            else:
                log.init.debug("Startup URL {}".format(cmd))
                try:
                    url = urlutils.fuzzy_url(cmd)
                except urlutils.FuzzyUrlError as e:
                    message.error("Error in startup argument '{}': {}".format(
                        cmd, e))
                else:
                    self.mainwindow.tabs.tabopen(url)

        if self.mainwindow.tabs.count() == 0:
            log.init.debug("Opening startpage")
            for urlstr in self.config.get('general', 'startpage'):
                try:
                    url = urlutils.fuzzy_url(urlstr)
                except urlutils.FuzzyUrlError as e:
                    message.error("Error when opening startpage: {}".format(e))
                else:
                    self.mainwindow.tabs.tabopen(url)

    def _python_hacks(self):
        """Get around some PyQt-oddities by evil hacks.

        This sets up the uncaught exception hook, quits with an appropriate
        exit status, and handles Ctrl+C properly by passing control to the
        Python interpreter once all 500ms.
        """
        signal(SIGINT, lambda *args: self.exit(128 + SIGINT))
        timer = Timer(self, 'python_hacks')
        timer.start(500)
        timer.timeout.connect(lambda: None)
        self._timers.append(timer)

    def _connect_signals(self):
        """Connect all signals to their slots."""
        # pylint: disable=too-many-statements
        # syntactic sugar
        kp = self._keyparsers
        status = self.mainwindow.status
        completion = self.mainwindow.completion
        tabs = self.mainwindow.tabs
        cmd = self.mainwindow.status.cmd
        completer = self.mainwindow.completion.completer

        # misc
        self.lastWindowClosed.connect(self.shutdown)
        tabs.quit.connect(self.shutdown)

        # status bar
        self.modeman.entered.connect(status.on_mode_entered)
        self.modeman.left.connect(status.on_mode_left)
        self.modeman.left.connect(status.cmd.on_mode_left)
        self.modeman.left.connect(status.prompt.on_mode_left)

        # commands
        cmd.got_cmd.connect(self.commandmanager.run_safely)
        cmd.got_search.connect(self.searchmanager.search)
        cmd.got_search_rev.connect(self.searchmanager.search_rev)
        cmd.returnPressed.connect(tabs.setFocus)
        self.searchmanager.do_search.connect(tabs.search)
        kp['normal'].keystring_updated.connect(status.keystring.setText)
        tabs.got_cmd.connect(self.commandmanager.run_safely)

        # hints
        kp['hint'].fire_hint.connect(tabs.fire_hint)
        kp['hint'].filter_hints.connect(tabs.filter_hints)
        kp['hint'].keystring_updated.connect(tabs.handle_hint_key)
        tabs.hint_strings_updated.connect(kp['hint'].on_hint_strings_updated)

        # messages
        self.messagebridge.error.connect(status.disp_error)
        self.messagebridge.info.connect(status.disp_temp_text)
        self.messagebridge.text.connect(status.set_text)
        self.messagebridge.set_cmd_text.connect(cmd.set_cmd_text)
        self.messagebridge.question.connect(status.prompt.ask_question,
                                            Qt.DirectConnection)

        # config
        self.config.style_changed.connect(style.invalidate_caches)
        for obj in (tabs, completion, self.mainwindow, self.cmd_history,
                    websettings, kp['normal'], self.modeman, status,
                    status.txt):
            self.config.changed.connect(obj.on_config_changed)

        # statusbar
        # FIXME some of these probably only should be triggered on mainframe
        # loadStarted.
        tabs.current_tab_changed.connect(status.prog.on_tab_changed)
        tabs.cur_progress.connect(status.prog.setValue)
        tabs.cur_load_finished.connect(status.prog.hide)
        tabs.cur_load_started.connect(status.prog.on_load_started)

        tabs.current_tab_changed.connect(status.percentage.on_tab_changed)
        tabs.cur_scroll_perc_changed.connect(status.percentage.set_perc)

        tabs.current_tab_changed.connect(status.txt.on_tab_changed)
        tabs.cur_statusbar_message.connect(status.txt.on_statusbar_message)
        tabs.cur_load_started.connect(status.txt.on_load_started)

        tabs.current_tab_changed.connect(status.url.on_tab_changed)
        tabs.cur_url_text_changed.connect(status.url.set_url)
        tabs.cur_link_hovered.connect(status.url.set_hover_url)
        tabs.cur_load_status_changed.connect(status.url.on_load_status_changed)

        # command input / completion
        self.modeman.left.connect(tabs.on_mode_left)
        cmd.clear_completion_selection.connect(
            completion.on_clear_completion_selection)
        cmd.hide_completion.connect(completion.hide)
        cmd.update_completion.connect(completer.on_update_completion)
        completer.change_completed_part.connect(cmd.on_change_completed_part)

        # downloads
        tabs.start_download.connect(self.downloadmanager.fetch)
        tabs.download_get.connect(self.downloadmanager.get)

    def get_all_widgets(self):
        """Get a string list of all widgets."""
        lines = []
        widgets = self.allWidgets()
        widgets.sort(key=lambda e: repr(e))
        lines.append("{} widgets".format(len(widgets)))
        for w in widgets:
            lines.append(repr(w))
        return '\n'.join(lines)

    def get_all_objects(self, obj=None, depth=0, lines=None):
        """Get all children of an object recursively as a string."""
        if lines is None:
            lines = []
        if obj is None:
            obj = self
        for kid in obj.findChildren(QObject):
            lines.append('    ' * depth + repr(kid))
            self.get_all_objects(kid, depth + 1, lines)
        if depth == 0:
            lines.insert(0, '{} objects:'.format(len(lines)))
        return '\n'.join(lines)

    def _recover_pages(self):
        """Try to recover all open pages.

        Called from _exception_hook, so as forgiving as possible.

        Return:
            A list of open pages, or an empty list.
        """
        pages = []
        if self.mainwindow is None:
            return pages
        if self.mainwindow.tabs is None:
            return pages
        for tab in self.mainwindow.tabs.widgets:
            try:
                url = tab.url().toString()
                if url:
                    pages.append(url)
            except Exception as e:  # pylint: disable=broad-except
                log.destroy.debug("Error while recovering tab: {}: {}".format(
                    e.__class__.__name__, e))
        return pages

    def _save_geometry(self):
        """Save the window geometry to the state config."""
        geom = b64encode(bytes(self.mainwindow.saveGeometry())).decode('ASCII')
        try:
            self.stateconfig.add_section('geometry')
        except configparser.DuplicateSectionError:
            pass
        self.stateconfig['geometry']['mainwindow'] = geom

    def _destroy_crashlogfile(self):
        """Clean up the crash log file and delete it."""
        if self._crashlogfile is None:
            return
        if sys.stderr is not None:
            faulthandler.enable()
        else:
            faulthandler.disable()
        self._crashlogfile.close()
        try:
            os.remove(self._crashlogfile.name)
        except PermissionError as e:
            log.destroy.warning("Could not remove crash log ({})!".format(e))

    def _exception_hook(self, exctype, excvalue, tb):
        """Handle uncaught python exceptions.

        It'll try very hard to write all open tabs to a file, and then exit
        gracefully.
        """
        # pylint: disable=broad-except

        if exctype is BdbQuit or not issubclass(exctype, Exception):
            # pdb exit, KeyboardInterrupt, ...
            try:
                self.shutdown()
                return
            except Exception as e:
                log.init.debug("Error while shutting down: {}: {}".format(
                    e.__class__.__name__, e))
                self.quit()
                return

        exc = (exctype, excvalue, tb)
        sys.__excepthook__(*exc)

        self._quit_status['crash'] = False

        try:
            pages = self._recover_pages()
        except Exception as e:
            log.destroy.debug("Error while recovering pages: {}: {}".format(
                e.__class__.__name__, e))
            pages = []

        try:
            history = self.mainwindow.status.cmd.history[-5:]
        except Exception as e:
            log.destroy.debug("Error while getting history: {}: {}".format(
                e.__class__.__name__, e))
            history = []

        try:
            widgets = self.get_all_widgets()
        except Exception as e:
            log.destroy.debug("Error while getting widgets: {}: {}".format(
                e.__class__.__name__, e))
            widgets = ""

        try:
            objects = self.get_all_objects()
        except Exception as e:
            log.destroy.debug("Error while getting objects: {}: {}".format(
                e.__class__.__name__, e))
            objects = ""

        try:
            self.lastWindowClosed.disconnect(self.shutdown)
        except TypeError as e:
            log.destroy.debug("Error while preventing shutdown: {}: {}".format(
                e.__class__.__name__, e))
        QApplication.closeAllWindows()
        self._crashdlg = ExceptionCrashDialog(pages, history, exc, widgets,
                                              objects)
        ret = self._crashdlg.exec_()
        if ret == QDialog.Accepted:  # restore
            self.restart(shutdown=False, pages=pages)
        # We might risk a segfault here, but that's better than continuing to
        # run in some undefined state, so we only do the most needed shutdown
        # here.
        qInstallMessageHandler(None)
        self._destroy_crashlogfile()
        sys.exit(1)

    def _maybe_quit(self, sender):
        """Maybe quit qutebrowser.

        This only quits if both the ExceptionCrashDialog was ready to quit AND
        the shutdown is complete.

        Args:
            The sender of the quit signal (string)
        """
        self._quit_status[sender] = True
        log.destroy.debug("maybe_quit called from {}, quit status {}".format(
            sender, self._quit_status))
        if all(self._quit_status.values()):
            log.destroy.debug("maybe_quit quitting.")
            self.quit()

    @cmdutils.register(instance='', nargs=0)
    def restart(self, shutdown=True, pages=None):
        """Restart qutebrowser while keeping existing tabs open."""
        # We don't use _recover_pages here as it's too forgiving when
        # exceptions occur.
        if pages is None:
            pages = []
            for tab in self.mainwindow.tabs.widgets:
                urlstr = tab.url().toString()
                if urlstr:
                    pages.append(urlstr)
        log.destroy.debug("sys.executable: {}".format(sys.executable))
        log.destroy.debug("sys.path: {}".format(sys.path))
        log.destroy.debug("sys.argv: {}".format(sys.argv))
        log.destroy.debug("frozen: {}".format(hasattr(sys, 'frozen')))
        if hasattr(sys, 'frozen'):
            args = [sys.executable]
            cwd = os.path.abspath(os.path.dirname(sys.executable))
        else:
            args = [sys.executable, '-m', 'qutebrowser']
            cwd = os.path.join(os.path.abspath(os.path.dirname(
                               qutebrowser.__file__)), '..')
        for arg in sys.argv[1:]:
            if arg.startswith('-'):
                # We only want to preserve options on a restart.
                args.append(arg)
        # Add all open pages so they get reopened.
        args += pages
        log.destroy.debug("args: {}".format(args))
        log.destroy.debug("cwd: {}".format(cwd))
        # Open a new process and immediately shutdown the existing one
        subprocess.Popen(args, cwd=cwd)
        if shutdown:
            self.shutdown()

    @cmdutils.register(instance='', split=False, debug=True)
    def debug_pyeval(self, s):
        """Evaluate a python string and display the results as a webpage.

        We have this here rather in utils.debug so the context of eval makes
        more sense and because we don't want to import much stuff in the utils.

        Args:
            s: The string to evaluate.
        """
        try:
            r = eval(s)  # pylint: disable=eval-used
            out = repr(r)
        except Exception as e:  # pylint: disable=broad-except
            out = ': '.join([e.__class__.__name__, str(e)])
        qutescheme.pyeval_output = out
        self.mainwindow.tabs.openurl(QUrl('qute:pyeval'), newtab=True)

    @cmdutils.register(instance='')
    def report(self):
        """Report a bug in qutebrowser."""
        pages = self._recover_pages()
        history = self.mainwindow.status.cmd.history[-5:]
        widgets = self.get_all_widgets()
        objects = self.get_all_objects()
        self._crashdlg = ReportDialog(pages, history, widgets, objects)
        self._crashdlg.show()

    @pyqtSlot()
    def shutdown(self):
        """Try to shutdown everything cleanly.

        For some reason lastWindowClosing sometimes seem to get emitted twice,
        so we make sure we only run once here.
        """
        if self._shutting_down:
            return
        self._shutting_down = True
        log.destroy.debug("Shutting down...")
        to_save = []
        # Save everything
        if self.config.get('general', 'auto-save-config'):
            to_save.append(("config", self.config.save))
        to_save += [("command history", self.cmd_history.save),
                    ("window geometry", self._save_geometry),
                    ("window geometry", self.stateconfig.save),
                    ("cookies", self.cookiejar.save),
                    ("quickmarks", quickmarks.save)]
        for what, handler in to_save:
            log.destroy.debug("Saving {} (handler: {})".format(
                what, handler.__qualname__))
            try:
                handler()
            except AttributeError as e:
                log.destroy.warning("Could not save {}.".format(what))
                log.destroy.debug(e)
        # Shut down tabs
        try:
            self.mainwindow.tabs.shutdown_complete.connect(partial(
                self._maybe_quit, 'tabs'))
            self.mainwindow.tabs.shutdown()
        except AttributeError as e:  # mainwindow or tabs could still be None
            log.destroy.warning("No mainwindow/tabs to shut down ({}).".format(
                e))
            self._maybe_quit('tabs')
        # Shut down networkmanager
        try:
            self.networkmanager.abort_requests()
            self.networkmanager.destroyed.connect(partial(
                self._maybe_quit, 'networkmanager'))
            self.networkmanager.deleteLater()
        except AttributeError as e:
            log.destroy.warning("No networkmanager to shut down ({}).".format(
                e))
            self._maybe_quit('networkmanager')
        # Re-enable faulthandler to stdout, then remove crash log
        self._destroy_crashlogfile()
        # If we don't kill our custom handler here we might get segfaults
        qInstallMessageHandler(None)
        self._maybe_quit('main')

    @pyqtSlot()
    def on_tab_shutdown_complete(self):
        """Quit application after a shutdown.

        Gets called when all tabs finished shutting down after shutdown().
        """
        log.destroy.debug("Shutdown complete, quitting.")
        self.quit()
