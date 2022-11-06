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
from pathlib import Path
import subprocess

from parameterized import parameterized

from subiquitycore.tests import SubiTestCase


@attr.s(auto_attribs=True)
class Parameters:
    """In the answers file are a simplistic and optional key-value set of
    configurations for the answers-based test.  Load those values, and use
    sensible defaults if not overwritten with a more specific value.

    Also triggers off of the filename to adjust validation mode.
    """
    filename: str
    machine_config: str = attr.ib(default='examples/simple.json')
    source_catalog: str = attr.ib(default='examples/install-sources.yaml')
    serial: bool = attr.ib(default=False)
    validate_mode: str = attr.ib(default='install')

    @staticmethod
    def from_file(filename):
        param = Parameters(filename)
        if 'tpm' in filename:
            param.validate_mode = 'tpm'
        for line in open(filename):
            if line.startswith('#machine-config'):
                param.machine_config = line.split(': ')[1].strip()
                continue
            if line.startswith('#source-catalog'):
                param.source_catalog = line.split(': ')[1].strip()
                continue
            if line.startswith('#serial'):
                param.serial = True
                continue
        return param


class TestParameters(SubiTestCase):
    def test_defaults(self):
        expected = Parameters('examples/answers.yaml')
        actual = Parameters.from_file('examples/answers.yaml')
        self.assertEqual(expected, actual)

    def test_serial(self):
        expected = Parameters('examples/answers-serial.yaml', serial=True)
        actual = Parameters.from_file('examples/answers-serial.yaml')
        self.assertEqual(expected, actual)

    def test_machine_config(self):
        expected = Parameters('examples/answers-imsm.yaml',
                              machine_config='examples/imsm.json')
        actual = Parameters.from_file('examples/answers-imsm.yaml')
        self.assertEqual(expected, actual)

    def test_source_catalog(self):
        expected = Parameters('examples/answers-tpm.yaml',
                              source_catalog='examples/tpm-sources.yaml',
                              validate_mode='tpm')
        actual = Parameters.from_file('examples/answers-tpm.yaml')
        self.assertEqual(expected, actual)


answers_files = [f for f in glob.glob('examples/answers*.yaml')
                 if 'system-setup' not in f]


class TestAnswers(SubiTestCase):
    def assertExists(self, filepath):
        if not os.path.exists(filepath):
            raise AssertionError(f'expected file {filepath} not found')

    @parameterized.expand(answers_files)
    def test_answers(self, answers_relative_path):
        print(answers_relative_path)
        param = Parameters.from_file(answers_relative_path)
        tmpdir = Path(self.tmp_dir(cleanup=False))
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

        if glob.glob(str(tmpdir / 'var/crash/*')):
            self.fail('testcase crash')
            # FIXME show?

        if os.path.getsize(tmpdir / 'server-stderr') > 0:
            with open(tmpdir / 'server-stderr') as fp:
                print(fp.read())
            self.fail('has output on stderr')

        # if param.mode == 'install':
        self.assertExists(tmpdir / 'subiquity-client-debug.log')
        self.assertExists(tmpdir / 'subiquity-server-debug.log')
            # python3 scripts/validate-yaml.py "$tmpdir"/var/log/installer/curtin-install/subiquity-partitioning.conf
            # python3 scripts/validate-autoinstall-user-data.py < $tmpdir/var/log/installer/autoinstall-user-data
            # if grep passw0rd $tmpdir/subiquity-client-debug.log $tmpdir/subiquity-server-debug.log | grep -v "Loaded answers" | grep -v "answers_action"; then
            #   echo "password leaked into log file"
            #   exit 1
            # fi
            # netplan generate --root $tmpdir

#     # The --foreground is important to avoid subiquity getting SIGTTOU-ed.
#     python3 -m subiquity.cmd.tui < "$tty" \
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
