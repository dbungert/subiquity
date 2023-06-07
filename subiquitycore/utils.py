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

import asyncio
import crypt
import logging
import os
import random
import subprocess
from typing import Any, Dict, List, Sequence

log = logging.getLogger("subiquitycore.utils")


def _clean_env(env, *, locale=True):
    if env is None:
        env = os.environ.copy()
    else:
        env = env.copy()
    if locale:
        env['LC_ALL'] = 'C'
    # Maaaybe want to remove SNAP here too.
    return env


def orig_environ(env):
    """Generate an environment dict that is suitable for use for running
    programs that live outside the snap."""
    if env is None:
        env = os.environ
    ret = env.copy()
    for key, val in env.items():
        if key.endswith('_ORIG'):
            key_to_restore = key[:-len('_ORIG')]
            if val:
                ret[key_to_restore] = val
            else:
                del ret[key_to_restore]
            del ret[key]
    ret.pop('LD_LIBRARY_PATH', None)
    return ret


def run_command(cmd: Sequence[str], *, input=None, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, encoding='utf-8', errors='replace',
                env=None, clean_locale=True,
                **kw) -> subprocess.CompletedProcess:
    """A wrapper around subprocess.run with logging and different defaults.

    We never ever want a subprocess to inherit our file descriptors!
    """
    if input is None:
        kw['stdin'] = subprocess.DEVNULL
    else:
        input = input.encode(encoding)
    log.debug("run_command called: %s", cmd)
    try:
        cp = subprocess.run(cmd, input=input, stdout=stdout, stderr=stderr,
                            env=_clean_env(env, locale=clean_locale), **kw)
        if encoding:
            if isinstance(cp.stdout, bytes):
                cp.stdout = cp.stdout.decode(encoding)
            if isinstance(cp.stderr, bytes):
                cp.stderr = cp.stderr.decode(encoding)
    except subprocess.CalledProcessError as e:
        log.debug("run_command %s", str(e))
        raise
    else:
        log.debug("run_command %s exited with code %s", cp.args, cp.returncode)
        return cp


async def arun_command(cmd: Sequence[str], *,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       encoding='utf-8', input=None, errors='replace',
                       env=None, clean_locale=True, check=False, **kw) \
                       -> subprocess.CompletedProcess:
    if input is None:
        if 'stdin' not in kw:
            kw['stdin'] = subprocess.DEVNULL
    else:
        kw['stdin'] = subprocess.PIPE
        input = input.encode(encoding)
    log.debug("arun_command called: %s", cmd)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=stdout, stderr=stderr,
        env=_clean_env(env, locale=clean_locale), **kw)
    stdout, stderr = await proc.communicate(input=input)
    if encoding:
        if stdout is not None:
            stdout = stdout.decode(encoding)
        if stderr is not None:
            stderr = stderr.decode(encoding)
    log.debug("arun_command %s exited with code %s", cmd, proc.returncode)
    # .communicate() forces returncode to be set to a value
    assert proc.returncode is not None
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd,
                                            stdout, stderr)
    else:
        return subprocess.CompletedProcess(
            cmd, proc.returncode, stdout, stderr)


async def astart_command(cmd: Sequence[str], *, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                         env=None, clean_locale=True,
                         **kw) -> asyncio.subprocess.Process:
    log.debug("astart_command called: %s", cmd)
    return await asyncio.create_subprocess_exec(
        *cmd, stdout=stdout, stderr=stderr, stdin=stdin,
        env=_clean_env(env, locale=clean_locale), **kw)


async def split_cmd_output(cmd: Sequence[str], split_on: str) -> List[str]:
    cp = await arun_command(cmd, check=True)
    return cp.stdout.split(split_on)


def start_command(cmd: Sequence[str], *,
                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                  stderr=subprocess.PIPE, encoding='utf-8', errors='replace',
                  env=None, clean_locale=True, **kw) -> subprocess.Popen:
    """A wrapper around subprocess.Popen with logging and different defaults.

    We never ever want a subprocess to inherit our file descriptors!
    """
    log.debug('start_command called: %s', cmd)
    return subprocess.Popen(cmd, stdin=stdin, stdout=stdout, stderr=stderr,
                            env=_clean_env(env, locale=clean_locale), **kw)


def _log_stream(level: int, stream, name: str):
    if stream:
        log.log(level, f'{name}: ------------------------------------------')
        for line in stream.splitlines():
            log.log(level, line)
    elif stream is None:
        log.log(level, f'<{name} is None>')
    else:
        log.log(level, f'<{name} is empty>')


def log_process_streams(level: int,
                        cpe: subprocess.CalledProcessError,
                        command_msg: str):
    log.log(level, f'{command_msg} exited with result: {cpe.returncode}')
    _log_stream(level, cpe.stdout, 'stdout')
    _log_stream(level, cpe.stderr, 'stderr')
    log.log(level, '--------------------------------------------------')


# FIXME: replace with passlib and update package deps
def crypt_password(passwd, algo='SHA-512', backend='crypt'):
    # encryption algo - id pairs for crypt()
    algos = {'SHA-512': '$6$', 'SHA-256': '$5$', 'MD5': '$1$', 'DES': ''}
    if algo not in algos:
        raise Exception('Invalid algo({}), must be one of: {}. '.format(
            algo, ','.join(algos.keys())))

    salt_set = ('abcdefghijklmnopqrstuvwxyz'
                'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
                '0123456789./')
    salt = 16 * ' '
    salt = ''.join([random.choice(salt_set) for c in salt])
    if backend == 'crypt':
        return crypt.crypt(passwd, algos[algo] + salt)
    if backend == 'passlib':
        # FIXME assumes sha512
        return passlib.hash.sha512_crypt(salt=salt, rounds=5000).hash(passwd)
    raise Exception(f'Invalid backend {backend}')


def disable_console_conf():
    """ Stop console-conf service; which also restores getty service """
    log.info('disabling console-conf service')
    run_command(["systemctl", "stop", "--no-block", "console-conf@*.service",
                 "serial-console-conf@*.service"])
    return


def disable_subiquity():
    """ Stop subiquity service; which also restores getty service """
    log.info('disabling subiquity service')
    run_command(["mkdir", "-p", "/run/subiquity"])
    run_command(["touch", "/run/subiquity/complete"])
    run_command(["systemctl", "start", "--no-block", "getty@tty1.service"])
    run_command(["systemctl", "stop", "--no-block",
                 "snap.subiquity.subiquity-service.service",
                 "serial-subiquity@*.service"])
    return


def matching_dicts(items: Sequence[Dict[Any, Any]], **criteria):
    """Given an input sequence of dictionaries, return a list of dicts where
    the supplied keyword arguments all match those items."""
    return [item for item in items
            if all(k in item and item[k] == v for k, v in criteria.items())]
