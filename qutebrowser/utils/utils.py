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

"""Other utilities which don't fit anywhere else. """

import os
import io
import re
import sys
import enum
import shlex
import inspect
import os.path
import urllib.request
import urllib.parse
import collections
import functools
import contextlib

from PyQt5.QtCore import QCoreApplication, QStandardPaths, Qt
from PyQt5.QtGui import QKeySequence, QColor
import pkg_resources

import qutebrowser
from qutebrowser.utils import qtutils, log, usertypes


def elide(text, length):
    """Elide text so it uses a maximum of length chars."""
    if length < 1:
        raise ValueError("length must be >= 1!")
    if len(text) <= length:
        return text
    else:
        return text[:length - 1] + '\u2026'


def compact_text(text, elidelength=None):
    """Remove leading whitespace and newlines from a text and maybe elide it.

    FIXME: Add tests.

    Args:
        text: The text to compact.
        elidelength: To how many chars to elide.
    """
    out = []
    for line in text.splitlines():
        out.append(line.strip())
    out = ''.join(out)
    if elidelength is not None:
        out = elide(out, elidelength)
    return out


def read_file(filename):
    """Get the contents of a file contained with qutebrowser.

    Args:
        filename: The filename to open as string.

    Return:
        The file contents as string.
    """
    if hasattr(sys, 'frozen'):
        # cx_Freeze doesn't support pkg_resources :(
        fn = os.path.join(os.path.dirname(sys.executable), filename)
        with open(fn, 'r', encoding='utf-8') as f:
            return f.read()
    else:
        data = pkg_resources.resource_string(qutebrowser.__name__, filename)
        return data.decode('UTF-8')


def dotted_getattr(obj, path):
    """getattr supporting the dot notation.

    Args:
        obj: The object where to start.
        path: A dotted object path as a string.

    Return:
        The object at path.
    """
    return functools.reduce(getattr, path.split('.'), obj)


def _get_lexer(s):
    """Get an shlex lexer for safe_shlex_split."""
    if s is None:
        raise TypeError("Refusing to create a lexer with s=None!")
    lexer = shlex.shlex(s, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ''
    return lexer


def safe_shlex_split(s):
    r"""Split a string via shlex safely (don't bail out on unbalanced quotes).

    We split while the user is typing (for completion), and as
    soon as ", ' or \ is typed, the string is invalid for shlex,
    because it encounters EOF while in quote/escape state.

    Here we fix this error temporarily so shlex doesn't blow up,
    and then retry splitting again.

    Since shlex raises ValueError in both cases we unfortunately
    have to parse the exception string...

    We try 3 times so multiple errors can be fixed.
    """
    orig_s = s
    for i in range(3):
        lexer = _get_lexer(s)
        try:
            tokens = list(lexer)
        except ValueError as e:
            if str(e) not in ("No closing quotation", "No escaped character"):
                raise
            # eggs "bacon ham -> eggs "bacon ham"
            # eggs\ -> eggs\\
            if lexer.state not in lexer.escape + lexer.quotes:
                raise AssertionError(
                    "Lexer state is >{}< while parsing >{}< (attempted fixup: "
                    ">{}<)".format(lexer.state, orig_s, s))
            s += lexer.state
        else:
            return tokens
    # We should never arrive here.
    raise AssertionError(
        "Gave up splitting >{}< after {} tries. Attempted fixup: >{}<.".format(
            orig_s, i, s))  # pylint: disable=undefined-loop-variable


def pastebin(text):
    """Paste the text into a pastebin and return the URL."""
    api_url = 'http://paste.the-compiler.org/api/'
    data = {
        'text': text,
        'title': "qutebrowser crash",
        'name': "qutebrowser",
    }
    encoded_data = urllib.parse.urlencode(data).encode('utf-8')
    create_url = urllib.parse.urljoin(api_url, 'create')
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded;charset=utf-8'
    }
    request = urllib.request.Request(create_url, encoded_data, headers)
    response = urllib.request.urlopen(request)
    url = response.read().decode('utf-8').rstrip()
    if not url.startswith('http'):
        raise ValueError("Got unexpected response: {}".format(url))
    return url


def get_standard_dir(typ):
    """Get the directory where files of the given type should be written to.

    Args:
        typ: A member of the QStandardPaths::StandardLocation enum,
             see http://qt-project.org/doc/qt-5/qstandardpaths.html#StandardLocation-enum
    """
    qapp = QCoreApplication.instance()
    orgname = qapp.organizationName()
    # We need to temporarily unset the organisationname here since the
    # webinspector wants it to be set to store its persistent data correctly,
    # but we don't want that to happen.
    qapp.setOrganizationName(None)
    try:
        path = QStandardPaths.writableLocation(typ)
        if not path:
            raise ValueError("QStandardPaths returned an empty value!")
        # Qt seems to use '/' as path separator even on Windows...
        path = path.replace('/', os.sep)
        appname = qapp.applicationName()
        if (typ == QStandardPaths.ConfigLocation and
                path.split(os.sep)[-1] != appname):
            # WORKAROUND - see
            # https://bugreports.qt-project.org/browse/QTBUG-38872
            path = os.path.join(path, appname)
        if not os.path.exists(path):
            os.makedirs(path)
        return path
    finally:
        qapp.setOrganizationName(orgname)


def actute_warning():
    """Display a warning about the dead_actute issue if needed."""
    # WORKAROUND (remove this when we bump the requirements to 5.3.0)
    # Non linux OS' aren't affected
    if not sys.platform.startswith('linux'):
        return
    # If no compose file exists for some reason, we're not affected
    if not os.path.exists('/usr/share/X11/locale/en_US.UTF-8/Compose'):
        return
    # Qt >= 5.3 doesn't seem to be affected
    try:
        if qtutils.version_check('5.3.0'):
            return
    except ValueError:
        pass
    with open('/usr/share/X11/locale/en_US.UTF-8/Compose', 'r',
              encoding='utf-8') as f:
        for line in f:
            if '<dead_actute>' in line:
                if sys.stdout is not None:
                    sys.stdout.flush()
                print("Note: If you got a 'dead_actute' warning above, that "
                      "is not a bug in qutebrowser! See "
                      "https://bugs.freedesktop.org/show_bug.cgi?id=69476 for "
                      "details.")
                break


def _get_color_percentage(a_c1, a_c2, a_c3, b_c1, b_c2, b_c3, percent):
    """Get a color which is percent% interpolated between start and end.

    Args:
        a_c1, a_c2, a_c3: Start color components (R, G, B / H, S, V / H, S, L)
        b_c1, b_c2, b_c3: End color components (R, G, B / H, S, V / H, S, L)
        percent: Percentage to interpolate, 0-100.
                 0: Start color will be returned.
                 100: End color will be returned.

    Return:
        A (c1, c2, c3) tuple with the interpolated color components.

    Raise:
        ValueError if the percentage was invalid.
    """
    if not 0 <= percent <= 100:
        raise ValueError("percent needs to be between 0 and 100!")
    out_c1 = round(a_c1 + (b_c1 - a_c1) * percent / 100)
    out_c2 = round(a_c2 + (b_c2 - a_c2) * percent / 100)
    out_c3 = round(a_c3 + (b_c3 - a_c3) * percent / 100)
    return (out_c1, out_c2, out_c3)


def interpolate_color(start, end, percent, colorspace=QColor.Rgb):
    """Get an interpolated color value.

    Args:
        start: The start color.
        end: The end color.
        percent: Which value to get (0 - 100)
        colorspace: The desired interpolation colorsystem,
                    QColor::{Rgb,Hsv,Hsl} (from QColor::Spec enum)

    Return:
        The interpolated QColor, with the same spec as the given start color.

    Raise:
        ValueError if invalid parameters are passed.
    """
    qtutils.ensure_valid(start)
    qtutils.ensure_valid(end)
    out = QColor()
    if colorspace == QColor.Rgb:
        a_c1, a_c2, a_c3, _alpha = start.getRgb()
        b_c1, b_c2, b_c3, _alpha = end.getRgb()
        components = _get_color_percentage(a_c1, a_c2, a_c3, b_c1, b_c2, b_c3,
                                           percent)
        out.setRgb(*components)
    elif colorspace == QColor.Hsv:
        a_c1, a_c2, a_c3, _alpha = start.getHsv()
        b_c1, b_c2, b_c3, _alpha = end.getHsv()
        components = _get_color_percentage(a_c1, a_c2, a_c3, b_c1, b_c2, b_c3,
                                           percent)
        out.setHsv(*components)
    elif colorspace == QColor.Hsl:
        a_c1, a_c2, a_c3, _alpha = start.getHsl()
        b_c1, b_c2, b_c3, _alpha = end.getHsl()
        components = _get_color_percentage(a_c1, a_c2, a_c3, b_c1, b_c2, b_c3,
                                           percent)
        out.setHsl(*components)
    else:
        raise ValueError("Invalid colorspace!")
    out = out.convertTo(start.spec())
    qtutils.ensure_valid(out)
    return out


def format_seconds(total_seconds):
    """Format a count of seconds to get a [H:]M:SS string."""
    prefix = '-' if total_seconds < 0 else ''
    hours, rem = divmod(abs(round(total_seconds)), 3600)
    minutes, seconds = divmod(rem, 60)
    chunks = []
    if hours:
        chunks.append(str(hours))
        min_format = '{:02}'
    else:
        min_format = '{}'
    chunks.append(min_format.format(minutes))
    chunks.append('{:02}'.format(seconds))
    return prefix + ':'.join(chunks)


def format_size(size, base=1024, suffix=''):
    """Format a byte size so it's human readable.

    Inspired by http://stackoverflow.com/q/1094841
    """
    prefixes = ['', 'k', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y']
    if size is None:
        return '?.??' + suffix
    for p in prefixes:
        if -base < size < base:
            return '{:.02f}{}{}'.format(size, p, suffix)
        size /= base
    return '{:.02f}{}{}'.format(size, prefixes[-1], suffix)


def key_to_string(key):
    """Convert a Qt::Key member to a meaningful name.

    Args:
        key: A Qt::Key member.

    Return:
        A name of the key as a string.
    """
    special_names_str = {
        # Some keys handled in a weird way by QKeySequence::toString.
        # See https://bugreports.qt-project.org/browse/QTBUG-40030
        # Most are unlikely to be ever needed, but you never know ;)
        # For dead/combining keys, we return the corresponding non-combining
        # key, as that's easier to add to the config.
        'Key_Blue': 'Blue',
        'Key_Calendar': 'Calendar',
        'Key_ChannelDown': 'Channel Down',
        'Key_ChannelUp': 'Channel Up',
        'Key_ContrastAdjust': 'Contrast Adjust',
        'Key_Dead_Abovedot': '˙',
        'Key_Dead_Abovering': '˚',
        'Key_Dead_Acute': '´',
        'Key_Dead_Belowdot': 'Belowdot',
        'Key_Dead_Breve': '˘',
        'Key_Dead_Caron': 'ˇ',
        'Key_Dead_Cedilla': '¸',
        'Key_Dead_Circumflex': '^',
        'Key_Dead_Diaeresis': '¨',
        'Key_Dead_Doubleacute': '˝',
        'Key_Dead_Grave': '`',
        'Key_Dead_Hook': 'Hook',
        'Key_Dead_Horn': 'Horn',
        'Key_Dead_Iota': 'Iota',
        'Key_Dead_Macron': '¯',
        'Key_Dead_Ogonek': '˛',
        'Key_Dead_Semivoiced_Sound': 'Semivoiced Sound',
        'Key_Dead_Tilde': '~',
        'Key_Dead_Voiced_Sound': 'Voiced Sound',
        'Key_Exit': 'Exit',
        'Key_Green': 'Green',
        'Key_Guide': 'Guide',
        'Key_Info': 'Info',
        'Key_LaunchG': 'LaunchG',
        'Key_LaunchH': 'LaunchH',
        'Key_MediaLast': 'MediaLast',
        'Key_Memo': 'Memo',
        'Key_MicMute': 'Mic Mute',
        'Key_Mode_switch': 'Mode switch',
        'Key_Multi_key': 'Multi key',
        'Key_PowerDown': 'Power Down',
        'Key_Red': 'Red',
        'Key_Settings': 'Settings',
        'Key_SingleCandidate': 'Single Candidate',
        'Key_ToDoList': 'Todo List',
        'Key_TouchpadOff': 'Touchpad Off',
        'Key_TouchpadOn': 'Touchpad On',
        'Key_TouchpadToggle': 'Touchpad toggle',
        'Key_Yellow': 'Yellow',
    }
    # We now build our real special_names dict from the string mapping above.
    # The reason we don't do this directly is that certain Qt versions don't
    # have all the keys, so we want to ignore AttributeErrors.
    special_names = {}
    for k, v in special_names_str.items():
        try:
            special_names[getattr(Qt, k)] = v
        except AttributeError:
            pass
    # Now we check if the key is any special one - if not, we use
    # QKeySequence::toString.
    try:
        return special_names[key]
    except KeyError:
        name = QKeySequence(key).toString()
        morphings = {
            'Backtab': 'Tab',
            'Esc': 'Escape',
        }
        if name in morphings:
            return morphings[name]
        else:
            return name


def keyevent_to_string(e):
    """Convert a QKeyEvent to a meaningful name.

    Args:
        e: A QKeyEvent.

    Return:
        A name of the key (combination) as a string or
        None if only modifiers are pressed..
    """
    modmask2str = collections.OrderedDict([
        (Qt.ControlModifier, 'Ctrl'),
        (Qt.AltModifier, 'Alt'),
        (Qt.MetaModifier, 'Meta'),
        (Qt.ShiftModifier, 'Shift'),
    ])
    modifiers = (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta,
                 Qt.Key_AltGr, Qt.Key_Super_L, Qt.Key_Super_R,
                 Qt.Key_Hyper_L, Qt.Key_Hyper_R, Qt.Key_Direction_L,
                 Qt.Key_Direction_R)
    if e.key() in modifiers:
        # Only modifier pressed
        return None
    mod = e.modifiers()
    parts = []
    for (mask, s) in modmask2str.items():
        if mod & mask:
            parts.append(s)
    parts.append(key_to_string(e.key()))
    return '+'.join(parts)


def normalize_keystr(keystr):
    """Normalize a keystring like Ctrl-Q to a keystring like Ctrl+Q.

    Args:
        keystr: The key combination as a string.

    Return:
        The normalized keystring.
    """
    replacements = (
        ('Control', 'Ctrl'),
        ('Windows', 'Meta'),
        ('Mod1', 'Alt'),
        ('Mod4', 'Meta'),
    )
    for (orig, repl) in replacements:
        keystr = keystr.replace(orig, repl)
    for mod in ('Ctrl', 'Meta', 'Alt', 'Shift'):
        keystr = keystr.replace(mod + '-', mod + '+')
    return keystr.lower()


class FakeIOStream(io.TextIOBase):

    """A fake file-like stream which calls a function for write-calls."""

    def __init__(self, write_func):
        self.write = write_func

    def flush(self):
        """This is only here to satisfy pylint."""
        return super().flush()

    def isatty(self):
        """This is only here to satisfy pylint."""
        return super().isatty()


@contextlib.contextmanager
def fake_io(write_func):
    """Run code with stdout and stderr replaced by FakeIOStreams.

    Args:
        write_func: The function to call when write is called.
    """
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    fake_stderr = FakeIOStream(write_func)
    fake_stdout = FakeIOStream(write_func)
    sys.stderr = fake_stderr
    sys.stdout = fake_stdout
    yield
    # If the code we did run did change sys.stdout/sys.stderr, we leave it
    # unchanged. Otherwise, we reset it.
    if sys.stdout is fake_stdout:
        sys.stdout = old_stdout
    if sys.stderr is fake_stderr:
        sys.stderr = old_stderr


@contextlib.contextmanager
def disabled_excepthook():
    """Run code with the exception hook temporarily disabled."""
    old_excepthook = sys.excepthook
    sys.excepthook = sys.__excepthook__
    yield
    # If the code we did run did change sys.excepthook, we leave it
    # unchanged. Otherwise, we reset it.
    if sys.excepthook is sys.__excepthook__:
        sys.excepthook = old_excepthook


class prevent_exceptions:  # pylint: disable=invalid-name

    """Decorator to ignore and log exceptions.

    This needs to be used for some places where PyQt segfaults on exceptions or
    silently ignores them.

    We used to re-raise the exception with a single-shot QTimer in a similiar
    case, but that lead to a strange proble with a KeyError with some random
    jinja template stuff as content. For now, we only log it, so it doesn't
    pass 100% silently.

    This could also be a function, but as a class (with a "wrong" name) it's
    much cleaner to implement.

    Attributes:
        retval: The value to return in case of an exception.
        predicate: The condition which needs to be True to prevent exceptions
    """

    def __init__(self, retval, predicate=True):
        """Save decorator arguments.

        Gets called on parse-time with the decorator arguments.

        Args:
            See class attributes.
        """
        self.retval = retval
        self.predicate = predicate

    def __call__(self, func):
        """Gets called when a function should be decorated.

        Args:
            func: The function to be decorated.

        Return:
            The decorated function.
        """
        if not self.predicate:
            return func

        retval = self.retval

        @functools.wraps(func)
        def wrapper(*args, **kwargs):  # pylint: disable=missing-docstring
            try:
                return func(*args, **kwargs)
            except BaseException:
                log.misc.exception("Error in {}".format(func.__qualname__))
                return retval

        return wrapper


def is_enum(obj):
    """Check if a given object is an enum."""
    try:
        return issubclass(obj, enum.Enum)
    except TypeError:
        return False


def is_git_repo():
    """Check if we're running from a git repository."""
    gitfolder = os.path.join(qutebrowser.basedir, os.path.pardir, '.git')
    return os.path.isdir(gitfolder)


def docs_up_to_date(path):
    """Check if the generated html documentation is up to date.

    Args:
        path: The path of the document to check.

    Return:
        True if they are up to date or we couldn't check.
        False if they are outdated.
    """
    if hasattr(sys, 'frozen') or not is_git_repo():
        return True
    html_path = os.path.join(qutebrowser.basedir, 'html', 'doc', path)
    filename = os.path.splitext(path)[0]
    asciidoc_path = os.path.join(qutebrowser.basedir, os.path.pardir,
                                 'doc', 'help', filename + '.asciidoc')
    try:
        html_time = os.path.getmtime(html_path)
        asciidoc_time = os.path.getmtime(asciidoc_path)
    except FileNotFoundError:
        return True
    return asciidoc_time <= html_time


class DocstringParser:

    """Generate documentation based on a docstring of a command handler.

    The docstring needs to follow the format described in HACKING.
    """

    State = usertypes.enum('State', 'short', 'desc', 'desc_hidden',
                           'arg_start', 'arg_inside', 'misc')

    def __init__(self, func):
        """Constructor.

        Args:
            func: The function to parse the docstring for.
        """
        self.state = self.State.short
        self.short_desc = []
        self.long_desc = []
        self.arg_descs = collections.OrderedDict()
        self.cur_arg_name = None
        self.handlers = {
            self.State.short: self._parse_short,
            self.State.desc: self._parse_desc,
            self.State.desc_hidden: self._skip,
            self.State.arg_start: self._parse_arg_start,
            self.State.arg_inside: self._parse_arg_inside,
            self.State.misc: self._skip,
        }
        doc = inspect.getdoc(func)
        for line in doc.splitlines():
            handler = self.handlers[self.state]
            stop = handler(line)
            if stop:
                break
        for k, v in self.arg_descs.items():
            self.arg_descs[k] = ' '.join(v).replace(', or None', '')
        self.long_desc = ' '.join(self.long_desc)
        self.short_desc = ' '.join(self.short_desc)

    def _process_arg(self, line):
        """Helper method to process a line like 'fooarg: Blah blub'."""
        self.cur_arg_name, argdesc = line.split(':', maxsplit=1)
        self.cur_arg_name = self.cur_arg_name.strip().lstrip('*')
        self.arg_descs[self.cur_arg_name] = [argdesc.strip()]

    def _skip(self, line):
        """Handler to ignore everything until we get 'Args:'."""
        if line.startswith('Args:'):
            self.state = self.State.arg_start

    def _parse_short(self, line):
        """Parse the short description (first block) in the docstring."""
        if not line:
            self.state = self.State.desc
        else:
            self.short_desc.append(line.strip())

    def _parse_desc(self, line):
        """Parse the long description in the docstring."""
        if line.startswith('Args:'):
            self.state = self.State.arg_start
        elif line.startswith('Emit:') or line.startswith('Raise:'):
            self.state = self.State.misc
        elif line.strip() == '//':
            self.state = self.State.desc_hidden
        elif line.strip():
            self.long_desc.append(line.strip())

    def _parse_arg_start(self, line):
        """Parse first argument line."""
        self._process_arg(line)
        self.state = self.State.arg_inside

    def _parse_arg_inside(self, line):
        """Parse subsequent argument lines."""
        argname = self.cur_arg_name
        if re.match(r'^[A-Z][a-z]+:$', line):
            if not self.arg_descs[argname][-1].strip():
                self.arg_descs[argname] = self.arg_descs[argname][:-1]
                return True
        elif not line.strip():
            self.arg_descs[argname].append('\n\n')
        elif line[4:].startswith(' '):
            self.arg_descs[argname].append(line.strip() + '\n')
        else:
            self._process_arg(line)


def get_object(name):
    """Helper function to get an object."""
    return QCoreApplication.instance().obj[name]


def register_object(name, obj):
    """Helper function to register an object."""
    QCoreApplication.instance().obj[name] = obj
