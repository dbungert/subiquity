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

from subiquitycore.tests import SubiTestCase


class Parameters:
    pass
    # config = str default 'examples/simple.json'
    # catalog = str default 'examples/install-sources.yaml'
    # serial = bool default False


class TestAnswers(SubiTestCase):
    def test_answers(self):
        pass


# def read_parameters(filename):
#     config = None
#     catalog = None
#     serial = None
#     for line in open(filename):
#         if line.startswith('#machine-config'):
#             k, v = line.split(': ')
#             config = v
#         if line.startswith('#source-catalog'):
#             k, v = line.split(': ')
#             catalog = v
#         if line.startswith('#serial'):
#             k, v = line.split(': ')
#             catalog = bool(v)

#     return Parameters(config=config, catalog=catalog, serial=serial)

# for filename in glob('examples/answers*.yaml'):
#     read_parameters(filename)

# origbash = '''
# for answers in examples/answers*.yaml; do
#     opts=()
#     if [ -n "$serial" ]; then
#         opts+=(--serial)
#     fi
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
