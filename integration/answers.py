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

import attr
import glob
import os
import subprocess

from parameterized import parameterized

from subiquitycore.tests import SubiTestCase


@attr.s(auto_attribs=True)
class Parameters:
    machine_config: str = attr.ib(default='examples/simple.json')
    source_catalog: str = attr.ib(default='examples/install-sources.yaml')
    serial: bool = attr.ib(default=False)

    @staticmethod
    def from_file(filename):
        kw = {}
        for line in open(filename):
            if line.startswith('#machine-config'):
                k, v = line.split(': ')
                kw['machine_config'] = v.strip()
                continue
            if line.startswith('#source-catalog'):
                k, v = line.split(': ')
                kw['source_catalog'] = v.strip()
                continue
            if line.startswith('#serial'):
                kw['serial'] = True
                continue
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
        expected = Parameters(machine_config='examples/imsm.json')
        actual = Parameters.from_file('examples/answers-imsm.yaml')
        self.assertEqual(expected, actual)

    def test_source_catalog(self):
        expected = Parameters(source_catalog='examples/tpm-sources.yaml')
        actual = Parameters.from_file('examples/answers-tpm.yaml')
        self.assertEqual(expected, actual)


answers_files = [f for f in glob.glob('examples/answers*.yaml')]


class TestAnswers(SubiTestCase):
    @parameterized.expand(answers_files)
    def test_answers(self, answers_relative_path):
        param = Parameters.from_file(answers_relative_path)
        tmpdir = self.tmp_dir(cleanup=False)
        args = [
            'python3', '-m', 'subiquity.cmd.tui',
            '--dry-run',
            '--output-base', tmpdir,
            '--answers', answers_relative_path,
            '--machine-config', param.machine_config,
            '--bootloader', 'uefi',
            '--snaps-from-examples',
            '--source-catalog', param.source_catalog,
        ]
        if param.serial:
            args.append('--serial')
        env = os.environ
        env.update({
            'LANG': 'C.UTF-8',
            'PYTHONTRACEMALLOC': '3',
            'SUBIQUITY_REPLAY_TIMESCALE': '100',
        })
        subprocess.run(args, env=env, check=True, timeout=60)

# origbash = '''
# for answers in examples/answers*.yaml; do
#     # The --foreground is important to avoid subiquity getting SIGTTOU-ed.
#         python3 -m subiquity.cmd.tui < "$tty" \
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
