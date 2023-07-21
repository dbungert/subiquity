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

import logging

import attr

log = logging.getLogger("subiquity.models.identity")


@attr.s
class User(object):
    realname = attr.ib()
    username = attr.ib()
    password = attr.ib()


class IdentityModel(object):
    """Model representing user identity"""

    def __init__(self):
        self._user = None
        self._hostname = None

    def add_user(self, identity_data):
        self._hostname = identity_data.hostname
        d = {}
        d["realname"] = identity_data.realname
        d["username"] = identity_data.username
        d["password"] = identity_data.crypted_password
        if not d["realname"]:
            d["realname"] = identity_data.username
        self._user = User(**d)

    @property
    def hostname(self):
        return self._hostname

    @property
    def user(self):
        return self._user

    def __repr__(self):
        return "<LocalUser: {} {}>".format(self.user, self.hostname)
