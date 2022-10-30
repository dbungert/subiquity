# Copyright 2022 Canonical, Ltd.
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

from subiquity.common.types import AutoinstallValue
from subiquity.server.controller import NonInteractiveController

log = logging.getLogger("subiquity.server.controllers.geoip")


class GeoipController(NonInteractiveController):

    autoinstall_key = 'geoip'
    autoinstall_schema = {'type': 'boolean'}
    autoinstall_default = True

    def __init__(self, *args, **kwargs):
        self.has_autoinstall = False
        self.use_geoip = self.autoinstall_default
        super().__init__(*args, **kwargs)

    def start(self):
        self.app.geoip.enable(self.use_geoip)

    def load_autoinstall_data(self, data):
        self.has_autoinstall = True
        self.use_geoip = data

    def make_autoinstall(self):
        return AutoinstallValue(
                ok=self.has_autoinstall,
                value=self.use_geoip)
