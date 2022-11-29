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
import yaml

from parameterized import parameterized

from subiquitycore.tests import SubiTestCase

installing_security = (
    'finish: '
    'subiquity/Install/install/postinstall/run_unattended_upgrades: '
    'SUCCESS: downloading and installing security updates')


@attr.s(auto_attribs=True)
class Parameters:
    """In the answers file are a simplistic and optional key-value set of
    configurations for the answers-based tests.  Load those values, and use
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
            param.validate_mode = 'skip'
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
                              validate_mode='skip')
        actual = Parameters.from_file('examples/answers-tpm.yaml')
        self.assertEqual(expected, actual)


# answers_files = [f for f in glob.glob('examples/answers*.yaml')
#                  if 'system-setup' not in f]
# answers_files = ['examples/answers.yaml']
answers_files = ['examples/answers-tpm.yaml']
# answers_files = [
#     'examples/answers.yaml',
    # 'examples/answers-imsm.yaml'
# ]


def readFile(filename):
    with open(filename) as fp:
        return fp.read()


def wait(timeout, pidfile, exitfile):
    import time
    time.sleep(1)
    pid = int(readFile(pidfile))
    for i in range(timeout):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        else:
            time.sleep(1)
    return int(readFile(exitfile))


class TestAnswers(SubiTestCase):
    def assertExists(self, filepath):
        if not os.path.exists(filepath):
            raise AssertionError(f'expected file {filepath} not found')

    def fromYamlFile(self, filepath):
        with open(filepath) as fp:
            return yaml.safe_load(fp)

    def validate(self, mode='install'):
        if glob.glob(str(self.cur_tmpdir / 'var/crash/*')):
            self.fail('testcase crash')
            # FIXME show?

        server_stderr = self.cur_tmpdir / 'server-stderr'
        if server_stderr.exists() and os.path.getsize(server_stderr) > 0:
            with open(server_stderr) as fp:
                print(fp.read())
            self.fail('has output on stderr')

        if mode == 'install':
            # actually OK for tpm
            client_debug = self.cur_tmpdir / 'subiquity-client-debug.log'
            self.assertExists(client_debug)
            # actually OK for tpm
            server_debug = self.cur_tmpdir / 'subiquity-server-debug.log'
            self.assertExists(server_debug)

            partitioning_conf = self.cur_tmpdir / \
                'var/log/installer/curtin-install/subiquity-partitioning.conf'
            subprocess.run([
                'python3',
                './scripts/validate-yaml.py',
                str(partitioning_conf),
            ], check=True, timeout=60)

            ai_user_data = self.cur_tmpdir / \
                'var/log/installer/autoinstall-user-data'
            subprocess.run([
                'python3',
                './scripts/validate-autoinstall-user-data.py',
                ai_user_data
            ], check=True, timeout=60)

            with open(client_debug) as fp:
                for line in fp:
                    if 'Loaded answers' in line:
                        continue
                    if 'answers_action' in line:
                        continue
                    self.assertNotIn('passw0rd', line)

            subprocess.run([
                'netplan', 'generate', '--root', self.cur_tmpdir
            ], check=True, timeout=60)

            with open(server_debug) as fp:
                for line in fp:
                    if installing_security in line:
                        break

    @parameterized.expand(answers_files)
    def test_answers(self, answers_relative_path):
        param = Parameters.from_file(answers_relative_path)
        self.cur_tmpdir = Path(self.tmp_dir(cleanup=False))
        print(self.cur_tmpdir)
        #  runs our real program finally
        pidfile = self.tmp_path(path='pid', dir=self.cur_tmpdir)
        exitfile = self.tmp_path(path='exitcode', dir=self.cur_tmpdir)
        args = [
            'screen', '-d', '-m',
            './process_monitor', pidfile, exitfile,
            'python3', '-m', 'subiquity.cmd.tui',
            '--dry-run',
            '--output-base', self.cur_tmpdir,
            '--answers', answers_relative_path,
            '--machine-config', param.machine_config,
            '--bootloader', 'uefi',
            '--snaps-from-examples',
            '--source-catalog', param.source_catalog,
        ]
        # FIXME redirect tty input?
        if param.serial:
            args.append('--serial')
        env = os.environ
        # FIXME patch
        env.update({
            'LANG': 'C.UTF-8',
            'PYTHONTRACEMALLOC': '3',
            'SUBIQUITY_REPLAY_TIMESCALE': '100',
        })
        subprocess.run(args, env=env, check=True, timeout=60)
        ec = wait(timeout=60, pidfile=pidfile, exitfile=exitfile)
        self.assertEqual(0, ec, 'program had non-zero exit')
        self.validate(param.validate_mode)

    def test_autoinstall(self):
        self.cur_tmpdir = Path(self.tmp_dir())
        args = [
            'python3', '-m', 'subiquity.cmd.tui',
            '--dry-run',
            '--output-base', self.cur_tmpdir,
            '--autoinstall', 'examples/autoinstall.yaml',
            '--machine-config', 'examples/existing-partitions.json',
            '--bootloader', 'bios',
            '--snaps-from-examples',
            '--source-catalog', 'examples/install-sources.yaml',
            '--kernel-cmdline', 'autoinstall',
        ]
        env = os.environ
        # FIXME patch
        env.update({
            'LANG': 'C.UTF-8',
            'PYTHONTRACEMALLOC': '3',
            'SUBIQUITY_REPLAY_TIMESCALE': '100',
        })
        subprocess.run(args, env=env, check=True, timeout=60)
        self.validate()
        s_c_a_conf = self.cur_tmpdir / \
                'var/log/installer/subiquity-curtin-apt.conf'
        curtin_apt_yaml = self.fromYamlFile(s_c_a_conf)
        apt = curtin_apt_yaml['apt']
        self.assertEqual(['non-free', 'restricted'], apt['disable_components'])
        self.assertEqual(200, apt['preferences'][0]['pin-priority'])
        self.assertEqual("origin *ubuntu.com*", apt['preferences'][0]['pin'])
        self.assertEqual(-1, apt['preferences'][1]['pin-priority'])
        self.assertEqual("python-*", apt['preferences'][1]['package'])

        subiquity_curthooks_conf = self.cur_tmpdir / \
                'var/log/installer/curtin-install/subiquity-curthooks.conf'
        curthooks = self.fromYamlFile(subiquity_curthooks_conf)
        self.assertEqual("eek", curthooks['debconf_selections']['subiquity'])
        self.assertEqual("errors=remount-ro",
                         curthooks['storage']['config'][-1]['options'])

        cloud_cfg = self.cur_tmpdir / \
                'etc/cloud/cloud.cfg.d/99-installer.cfg'
        ccdata = self.fromYamlFile(cloud_cfg)
        userdata_raw = ccdata['datasource']['None']['userdata_raw']
        userdata = yaml.safe_load(userdata_raw)
        self.assertEqual('en_GB.UTF-8', userdata['locale'])
        self.assertEqual('Pacific/Guam', userdata['timezone'])
        self.assertEqual('C1NWcZTHLteJXGVMM6YhvHDpGrhyy7',
                         userdata['ubuntu_advantage']['token'])
        self.assertEqual(['snap install --channel=3.2/stable etcd'],
                         userdata['snap']['commands'])

        # FIXME system-setup
