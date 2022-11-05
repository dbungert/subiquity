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

import json
import sys

from subiquitycore.tests import SubiTestCase
import subiquity.cmd.schema


class TestSchema(SubiTestCase):
    def test_schema(self):
        def load(filepath):
            with open(filepath) as fp:
                return json.load(fp)

        declared_schema = load('autoinstall-schema.json')

        generated_schema_file = self.tmp_path('schema.json')
        with open(generated_schema_file, 'w') as fp:
            orig_stdout = sys.stdout
            orig_stdout_fd = sys.stdout.fileno()
            sys.stdout = fp
            subiquity.cmd.schema.main()
            sys.stdout = orig_stdout
            self.assertEqual(orig_stdout_fd, sys.stdout.fileno())

        generated_schema = load(generated_schema_file)
        self.assertEqual(generated_schema, declared_schema)
