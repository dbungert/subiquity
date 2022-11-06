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

# import json
# from unittest import mock
# import sys

import attr

from subiquitycore.tests import SubiTestCase

@attr.s(auto_attribs=True)
class Parameters:
    config: str = attr.ib(default='examples/simple.json')
    catalog: str = attr.ib(default='examples/install-sources.yaml')
    serial: bool = attr.ib(default=False)

    @staticmethod
    def from_file(filename):
        kw = {}
        for line in open(filename):
            if line.startswith('#machine-config'):
                k, v = line.split(': ')
                kw['config'] = v.strip()
            if line.startswith('#source-catalog'):
                k, v = line.split(': ')
                kw['catalog'] = v.strip()
            if line.startswith('#serial'):
                kw['serial'] = True

        return Parameters(**kw)


class TestParameters(SubiTestCase):
    def test_defaults(self):
        expected = Parameters()
        actual = Parameters.from_file('examples/answers.yaml')
        self.assertEqual(expected, actual)

    def test_serial(self):
        expected = Parameters(serial=True)
        actual = Parameters.from_file('examples/answers-serial.yaml')
        self.assertEqual(expected, actual)

    def test_machine_config(self):
        expected = Parameters(config='examples/imsm.json')
        actual = Parameters.from_file('examples/answers-imsm.yaml')
        self.assertEqual(expected, actual)

    def test_source_catalog(self):
        expected = Parameters(catalog='examples/tpm-sources.yaml')
        actual = Parameters.from_file('examples/answers-tpm.yaml')
        self.assertEqual(expected, actual)

# origbash = '''
# for answers in examples/answers*.yaml; do
#     # The --foreground is important to avoid subiquity getting SIGTTOU-ed.
#     LANG=C.UTF-8 timeout --foreground 60 \
#         python3 -m subiquity.cmd.tui < "$tty" \
#         --dry-run \
#         --output-base "$tmpdir" \
#         --answers "$answers" \
#         "${opts[@]}" \
#         --machine-config "$config" \
#         --bootloader uefi \
#         --snaps-from-examples \
#         --source-catalog $catalog
#     if [ "$answers" = examples/answers-tpm.yaml ]; then
#         validate skip
#     else
#         validate install
#     fi
#     grep -q '
#     finish: subiquity/Install/install/postinstall/run_unattended_upgrades:
#     SUCCESS: downloading and installing security updates'
#     $tmpdir/subiquity-server-debug.log
#     clean
# done
# '''
