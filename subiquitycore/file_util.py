# Copyright 2018-2022 Canonical, Ltd.
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

import contextlib
import datetime
import grp
import logging
import os
import shutil
import tempfile

import yaml

_DEF_PERMS_FILE = 0o640
_DEF_GROUP = "adm"

log = logging.getLogger("subiquitycore.file_util")


def set_log_perms(target, *, isdir=True, group_write=False, mode=None):
    if os.getuid() != 0:
        log.warning(
            "set_log_perms: running as non-root - not adjusting"
            + " group owner or permissions for "
            + target
        )
        return
    if mode is None:
        mode = _DEF_PERMS_FILE
        if isdir:
            mode |= 0o110
        if group_write:
            mode |= 0o020
    os.chmod(target, mode)
    os.chown(target, -1, grp.getgrnam(_DEF_GROUP).gr_gid)


@contextlib.contextmanager
def open_perms(filename, *, cmode=None):
    if cmode is None:
        cmode = _DEF_PERMS_FILE

    tf = None
    try:
        dirname = os.path.dirname(filename)
        os.makedirs(dirname, exist_ok=True)
        tf = tempfile.NamedTemporaryFile(dir=dirname, delete=False, mode="w")
        yield tf
        tf.close()
        set_log_perms(tf.name, mode=cmode)
        os.rename(tf.name, filename)
    except OSError as e:
        if tf is not None:
            os.unlink(tf.name)
        raise e


def write_file(filename, content, **kwargs):
    with open_perms(filename, **kwargs) as tf:
        tf.write(content)


def generate_timestamped_header() -> str:
    now = datetime.datetime.utcnow()
    return f"# Autogenerated by Subiquity: {now} UTC\n"


def generate_config_yaml(filename, content, **kwargs):
    with open_perms(filename, **kwargs) as tf:
        tf.write(generate_timestamped_header())
        tf.write(yaml.dump(content))


def copy_file_if_exists(source: str, target: str):
    """If source exists, copy to destination.  Ignore error that dest may be a
    duplicate.  Create destination parent dirs as needed."""
    if not os.path.exists(source):
        return
    dirname = os.path.dirname(target)
    os.makedirs(dirname, exist_ok=True)
    try:
        shutil.copyfile(source, target)
    except shutil.SameFileError:
        pass
