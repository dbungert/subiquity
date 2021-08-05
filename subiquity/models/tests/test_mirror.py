# Copyright 2019 Canonical, Ltd.
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

import unittest

from subiquity.models.mirror import (
    MirrorModel,
    )


class TestMirrorModel(unittest.TestCase):

    def test_set_country(self):
        model = MirrorModel()
        model.set_country("CC")
        self.assertIn(
            model.get_mirror(),
            [
                "http://CC.archive.ubuntu.com/ubuntu",
                "http://CC.ports.ubuntu.com/ubuntu-ports",
            ])

    def test_set_mirror(self):
        model = MirrorModel()
        model.set_mirror("http://mymirror.invalid/")
        self.assertEqual(model.get_mirror(), "http://mymirror.invalid/")

    def test_set_country_after_set_mirror(self):
        model = MirrorModel()
        model.set_mirror("http://mymirror.invalid/")
        model.set_country("CC")
        self.assertEqual(model.get_mirror(), "http://mymirror.invalid/")

    def test_render(self):
        model = MirrorModel()
        model.set_components(('main',))
#         expected = '''\
# deb $MIRROR $RELEASE main
# deb $SECURITY $RELEASE-security main
# '''
        cfg = model.render()
        # print(cfg['apt']['sources_list'])
        # self.assertEqual(expected, cfg['apt']['sources_list'])
