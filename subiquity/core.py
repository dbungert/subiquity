# Copyright 2015 Canonical, Ltd.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging
import urwid
import urwid.curses_display
from tornado.ioloop import IOLoop
from tornado.util import import_object
from subiquity.signals import Signal
from subiquity.palette import STYLES, STYLES_MONO
from subiquity.prober import Prober

log = logging.getLogger('subiquity.core')


class CoreControllerError(Exception):
    """ Basecontroller exception """
    pass


class Controller:
    def __init__(self, ui, opts):
        self.common = {
            "ui": ui,
            "opts": opts,
            "signal": Signal(),
            "prober": Prober(opts),
            "loop": None
        }
        self.controllers = {
            "Welcome": None,
            "Installpath": None,
            "Network": None,
            "Filesystem": None,
            "Identity": None,
            "InstallProgress": None,
        }

    def _connect_base_signals(self):
        """ Connect signals used in the core controller
        """
        signals = []

        # Add quit signal
        signals.append(('quit', self.exit))
        signals.append(('refresh', self.redraw_screen))
        self.common['signal'].connect_signals(signals)

        # Registers signals from each controller
        for controller, controller_class in self.controllers.items():
            controller_class.register_signals()
        log.debug(self.common['signal'])

# EventLoop -------------------------------------------------------------------
    def redraw_screen(self):
        if hasattr(self, 'loop'):
            try:
                self.common['loop'].draw_screen()
            except AssertionError as e:
                log.critical("Redraw screen error: {}".format(e))

    def set_alarm_in(self, interval, cb):
        self.common['loop'].set_alarm_in(interval, cb)
        return

    def update(self, *args, **kwds):
        """ Update loop """
        pass

    def exit(self):
        raise urwid.ExitMainLoop()

    def header_hotkeys(self, key):
        if key in ['q', 'Q', 'ctrl c']:
            self.exit()

    def run(self):
        if not hasattr(self, 'loop'):
            palette = STYLES
            additional_opts = {
                'screen': urwid.raw_display.Screen(),
                'unhandled_input': self.header_hotkeys,
                'handle_mouse': False
            }
            if self.common['opts'].run_on_serial:
                palette = STYLES_MONO
                additional_opts['screen'] = urwid.curses_display.Screen()
            else:
                additional_opts['screen'].set_terminal_properties(colors=256)
                additional_opts['screen'].reset_default_terminal_palette()

            evl = urwid.TornadoEventLoop(IOLoop())
            self.common['loop'] = urwid.MainLoop(
                self.common['ui'], palette, event_loop=evl, **additional_opts)
            log.debug("Running event loop: {}".format(
                self.common['loop'].event_loop))

        try:
            self.set_alarm_in(0.05, self.welcome)
            for k in self.controllers.keys():
                log.debug("Importing controller: {}".format(k))
                klass = import_object(
                    "subiquity.controllers.{}Controller".format(
                        k))
                self.controllers[k] = klass(self.common)

            self._connect_base_signals()
            self.common['loop'].run()
        except:
            log.exception("Exception in controller.run():")
            raise

    # Welcome Mode ------------------------------------------------------------
    #
    # Starts the initial UI view.
    def welcome(self, *args, **kwargs):
        self.controllers['Welcome'].welcome()
