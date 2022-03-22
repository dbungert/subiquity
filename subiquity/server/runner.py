# Copyright 2021 Canonical, Ltd.
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

import asyncio
from contextlib import suppress
import os
import subprocess
from typing import List, Optional

from subiquitycore.utils import astart_command


class LoggedCommandRunner:
    """ Class that executes commands using systemd-run. """
    def __init__(self, ident,
                 *, use_systemd_user: Optional[bool] = None) -> None:
        self.ident = ident
        self.env_whitelist = [
            "PATH", "PYTHONPATH",
            "PYTHON",
            "TARGET_MOUNT_POINT",
            "SNAP",
        ]
        if use_systemd_user is not None:
            self.use_systemd_user = use_systemd_user
        else:
            self.use_systemd_user = os.geteuid() != 0

    def _forge_systemd_cmd(self, cmd: List[str], private_mounts: bool) \
            -> List[str]:
        """ Return the supplied command prefixed with the systemd-run stuff.
        """
        prefix = [
            "systemd-run",
            "--wait", "--same-dir",
            "--property", f"SyslogIdentifier={self.ident}",
        ]
        if private_mounts:
            prefix.extend(("--property", "PrivateMounts=yes"))
        if self.use_systemd_user:
            prefix.append("--user")
        for key in self.env_whitelist:
            with suppress(KeyError):
                prefix.extend(("--setenv", f"{key}={os.environ[key]}"))

        prefix.append("--")

        return prefix + cmd

    async def start(self, cmd, *, capture=False):
        if not capture:
            cmd = [
                'systemd-cat',
                '--level-prefix=false',
                '--identifier='+self.ident,
                ] + cmd
        proc = await astart_command(cmd)
        proc.args = cmd
        return proc

    async def wait(self, proc):
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, proc.args)
        else:
            return subprocess.CompletedProcess(
                proc.args, proc.returncode, stdout=stdout, stderr=stderr)

    async def run(self, cmd, **opts):
        proc = await self.start(cmd, **opts)
        return await self.wait(proc)


class DryRunCommandRunner(LoggedCommandRunner):

    def __init__(self, ident, delay,
                 *, use_systemd_user: Optional[bool] = None) -> None:
        super().__init__(ident, use_systemd_user=use_systemd_user)
        self.delay = delay

    async def start(self, cmd, *, capture=False):
        if 'scripts/replay-curtin-log.py' in cmd:
            return 0
        elif 'unattended-upgrades' in cmd:
            return 3 * self.delay
        else:
            cmd = ['echo', 'not running:'] + cmd
            if 'unattended-upgrades' in cmd:
                delay = 3*self.delay
            else:
                delay = self.delay
        proc = await super().start(cmd, capture=capture)
        await asyncio.sleep(delay)
        return proc


def get_command_runner(app):
    if app.opts.dry_run:
        return DryRunCommandRunner(
            app.log_syslog_id, 2/app.scale_factor)
    else:
        return LoggedCommandRunner(app.log_syslog_id)
