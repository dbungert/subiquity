# Copyright 2018 Canonical, Ltd.
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

import copy
import logging
import re
from urllib import parse

from curtin.commands.apt_config import (
    get_arch_mirrorconfig,
    get_mirror,
    PRIMARY_ARCHES,
    )
from curtin.config import merge_config
try:
    from curtin.distro import get_architecture
except ImportError:
    from curtin.util import get_architecture

from subiquity.templates.sources_list import template

log = logging.getLogger('subiquitycore.models.mirror')


DEFAULT = {
    "preserve_sources_list": False,
    "primary": [
        {
            "arches": PRIMARY_ARCHES,
            "uri": "http://archive.ubuntu.com/ubuntu",
        },
        {
            "arches": ["default"],
            "uri": "http://ports.ubuntu.com/ubuntu-ports",
        },
        ],
}


class MirrorModel(object):

    def __init__(self):
        self.config = copy.deepcopy(DEFAULT)
        self.architecture = get_architecture()
        self.default_mirror = self.get_mirror()
        self.components = []

    def mirror_is_default(self):
        return self.get_mirror() == self.default_mirror

    def set_country(self, cc):
        if not self.mirror_is_default():
            return
        uri = self.get_mirror()
        parsed = parse.urlparse(uri)
        new = parsed._replace(netloc=cc + '.' + parsed.netloc)
        self.set_mirror(parse.urlunparse(new))

    def get_mirror(self):
        return get_mirror(self.config, "primary", self.architecture)

    def set_mirror(self, mirror):
        config = get_arch_mirrorconfig(
            self.config, "primary", self.architecture)
        config["uri"] = mirror

    def set_components(self, components):
        self.components = components

    def components_config(self):
        if not self.components:
            return {}
        output = []
        for line in template.splitlines():
            # the template contains commented and uncommented lines
            # * commented lines stay that way and can be ignored
            # * uncommented lines may become commented if they contain
            #   an affected component
            # * some lines will be split into commented and uncommeted,
            #   to address cases where some of the listed components are
            #   enabled but not all
            p = re.compile('^(deb \$(?:MIRROR|SECURITY) \$RELEASE(?:-\w+)? )([ \w]+)$')
            m = p.match(line)
            if not m:
                output.append(line)
                continue
            # components actually found in line
            components_of_line = m.groups()[1].split(' ')
            # components in line that we want
            desired_components_of_line = []
            comment_added = False
            for comp in components_of_line:
                if comp in self.components:
                    # this component is one of the ones we want
                    desired_components_of_line.append(comp)
                elif not comment_added:
                    # this one isn't, so comment out the line
                    output.append('# ' + line)
                    comment_added = True
            if desired_components_of_line:
                # one+ components we want were detected, so output a
                # possibly modified line reflecting that
                to_add = ' '.join(desired_components_of_line)
                output.append(m.groups()[0] + to_add)
        print('\n'.join(output) + '\n')
        return {'sources_list': template}

    def render(self):
        config = copy.deepcopy(self.config)
        merge_config(config, self.components_config())
        return {'apt': config}
