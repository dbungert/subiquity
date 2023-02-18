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
import inspect

from subiquitycore.async_helpers import run_bg_task


class CoreChannels:
    NETWORK_UP = 'network-up'


class MessageHub:

    def __init__(self):
        self.subscriptions = {}

    def subscribe(self, channel, method):
        self.subscriptions.setdefault(channel, []).append(method)

    async def abroadcast(self, channel, *args, **kwargs):
        for m in self.subscriptions.get(channel, []):
            v = m(*args, **kwargs)
            if inspect.iscoroutine(v):
                await v

    def broadcast(self, channel, *args, bg_task=True, **kwargs):
        coro = self.abroadcast(channel, *args, **kwargs)
        if bg_task:
            run_bg_task(coro)
            return None
        else:
            return asyncio.create_task(coro)
