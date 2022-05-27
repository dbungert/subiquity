import aiohttp
from aiohttp.client_exceptions import ClientResponseError
import async_timeout
import asyncio
import contextlib
from functools import wraps
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote

from subiquitycore.tests import SubiTestCase
from subiquitycore.utils import astart_command

default_timeout = 10


def match(items, **kwargs):
    return [item for item in items
            if all(item.get(k) == v for k, v in kwargs.items())]


def match_type(items, value):
    """special case to matching items for key = '$type'"""
    return [item for item in items if item.get('$type') == value]


def timeout(multiplier=1):
    def wrapper(coro):
        @wraps(coro)
        async def run(*args, **kwargs):
            with async_timeout.timeout(default_timeout * multiplier):
                return await coro(*args, **kwargs)
        return run
    return wrapper


def json_print(json_data):
    print(json.dumps(json_data, indent=4))


class Client:
    def __init__(self, session):
        self.session = session

    def loads(self, data):
        if data == '' or data is None:  # json.loads likes neither of these
            return None
        return json.loads(data)

    def dumps(self, data):
        # if the data we're dumping is literally False,
        # we want that to be 'false'
        if data or isinstance(data, bool):
            return json.dumps(data, separators=(',', ':'))
        elif data is not None:
            return '""'
        else:
            return data

    async def get(self, query, **kwargs):
        return await self.request('GET', query, **kwargs)

    async def post(self, query, data=None, **kwargs):
        return await self.request('POST', query, data, **kwargs)

    async def request(self, method, query, data=None, **kwargs):
        params = {k: self.dumps(v) for k, v in kwargs.items()}
        data = self.dumps(data)
        async with self.session.request(method, f'http://a{query}',
                                        data=data, params=params) as resp:
            print(unquote(str(resp.url)))
            content = await resp.content.read()
            content = content.decode()
            if 400 <= resp.status:
                print(content)
                resp.raise_for_status()
            return self.loads(content)

    async def poll_startup(self):
        for _ in range(default_timeout * 10):
            try:
                resp = await self.get('/meta/status')
                if resp["state"] in ('STARTING_UP', 'CLOUD_INIT_WAIT',
                                     'EARLY_COMMANDS'):
                    await asyncio.sleep(.5)
                    continue
                if resp["state"] == 'ERROR':
                    raise Exception('server in error state')
                return
            except aiohttp.client_exceptions.ClientConnectorError:
                await asyncio.sleep(.5)
        raise Exception('timeout on server startup')


class Server(Client):
    async def server_shutdown(self, immediate=True):
        try:
            await self.post('/shutdown', mode='POWEROFF', immediate=immediate)
        except aiohttp.client_exceptions.ServerDisconnectedError:
            return

    async def spawn(self, output_base, socket, machine_config,
                    bootloader='uefi', extra_args=None):
        env = os.environ.copy()
        env['SUBIQUITY_REPLAY_TIMESCALE'] = '100'
        cmd = ['python3', '-m', 'subiquity.cmd.server',
               '--dry-run',
               '--bootloader', bootloader,
               '--socket', socket,
               '--output-base', output_base,
               '--machine-config', machine_config]
        if extra_args is not None:
            cmd.extend(extra_args)
        self.proc = await astart_command(cmd, env=env)

    async def close(self):
        try:
            await asyncio.wait_for(self.server_shutdown(), timeout=5.0)
            await asyncio.wait_for(self.proc.communicate(), timeout=5.0)
        except asyncio.exceptions.TimeoutError:
            pass
        finally:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass


class TestAPI(unittest.IsolatedAsyncioTestCase, SubiTestCase):
    def assertDictSubset(self, expected, actual):
        """All keys in dictionary expected, and matching values, must match
        keys and values in actual.  Actual may contain additional keys and
        values that don't appear in expected, and this is not a failure."""

        for k, v in expected.items():
            self.assertEqual(v, actual[k], k)


async def poll_for_socket_exist(socket_path):
    for _ in range(default_timeout * 5):
        # test level timeout will trigger first, this loop is just a fallback
        if os.path.exists(socket_path):
            return
        await asyncio.sleep(.1)
    raise Exception('timeout looking for socket to exist')


@contextlib.contextmanager
def tempdirs(*args, **kwargs):
    # This does the following:
    # * drop in replacement for TemporaryDirectory that doesn't cleanup, so
    #   that the log files can be examined later
    # * make it an otherwise-unnecessary contextmanager so that the indentation
    #   of the caller can be preserved
    prefix = '/tmp/testapi/'
    os.makedirs(prefix, exist_ok=True)
    tempdir = tempfile.mkdtemp(prefix=prefix)
    print(tempdir)
    yield tempdir


@contextlib.asynccontextmanager
async def start_server(*args, **kwargs):
    with tempfile.TemporaryDirectory() as tempdir:
        socket_path = f'{tempdir}/socket'
        conn = aiohttp.UnixConnector(path=socket_path)
        async with aiohttp.ClientSession(connector=conn) as session:
            server = Server(session)
            try:
                await server.spawn(tempdir, socket_path, *args, **kwargs)
                await poll_for_socket_exist(socket_path)
                await server.poll_startup()
                yield server
            finally:
                await server.close()


@contextlib.asynccontextmanager
async def connect_server(*args, **kwargs):
    # This is not used by the tests directly, but can be convenient when
    # wanting to debug the server process.  Change a test's start_server
    # to connect_server, disable the test timeout, and run just that test.
    socket_path = '.subiquity/socket'
    conn = aiohttp.UnixConnector(path=socket_path)
    async with aiohttp.ClientSession(connector=conn) as session:
        yield Client(session)


class TestBitlocker(TestAPI):
    @timeout()
    async def test_has_bitlocker(self):
        async with start_server('examples/win10.json') as inst:
            resp = await inst.get('/storage/has_bitlocker')
            self.assertEqual(1, len(resp))

    @timeout()
    async def test_not_bitlocker(self):
        async with start_server('examples/simple.json') as inst:
            resp = await inst.get('/storage/has_bitlocker')
            self.assertEqual(0, len(resp))


class TestFlow(TestAPI):
    @timeout(2)
    async def test_server_flow(self):
        async with start_server('examples/simple.json') as inst:
            await inst.post('/locale', 'en_US.UTF-8')
            keyboard = {
                'layout': 'us',
                'variant': '',
                'toggle': None
            }
            await inst.post('/keyboard', keyboard)
            await inst.post('/source',
                            source_id='ubuntu-server', search_drivers=True)
            await inst.post('/network')
            await inst.post('/proxy', '')
            await inst.post('/mirror', 'http://us.archive.ubuntu.com/ubuntu')
            resp = await inst.get('/storage/guided')
            [d1] = resp['disks']
            disk_id = d1['id']
            choice = {"disk_id": disk_id}
            await inst.post('/storage/v2/guided', choice)
            await inst.post('/storage/v2')
            await inst.get('/meta/status', cur='WAITING')
            await inst.post('/meta/confirm', tty='/dev/tty1')
            await inst.get('/meta/status', cur='NEEDS_CONFIRMATION')
            identity = {
                'realname': 'ubuntu',
                'username': 'ubuntu',
                'hostname': 'ubuntu-server',
                'crypted_password': '$6$exDY1mhS4KUYCE/2$zmn9ToZwTKLhCw.b4/'
                                    + 'b.ZRTIZM30JZ4QrOQ2aOXJ8yk96xpcCof0kx'
                                    + 'KwuX1kqLG/ygbJ1f8wxED22bTL4F46P0'
            }
            await inst.post('/identity', identity)
            ssh = {
                'install_server': False,
                'allow_pw': False,
                'authorized_keys': []
            }
            await inst.post('/ssh', ssh)
            await inst.post('/snaplist', [])
            ua_params = {
                "token": "a1b2c3d4e6f7g8h9I0K1",
            }
            await inst.post('/ubuntu_pro', ua_params)
            for state in 'RUNNING', 'POST_WAIT', 'POST_RUNNING', 'UU_RUNNING':
                await inst.get('/meta/status', cur=state)

    @timeout()
    async def test_v2_flow(self):
        async with start_server('examples/win10.json') as inst:
            orig_resp = await inst.get('/storage/v2')
            [d1] = orig_resp['disks']
            disk_id = d1['id']

            data = {'disk_id': disk_id}
            resp = await inst.post('/storage/v2/reformat_disk', data)
            [d1] = resp['disks']
            [gap] = d1['partitions']

            data = {
                'disk_id': disk_id,
                'gap': gap,
                'partition': {
                    'format': 'ext3',
                    'mount': '/',
                }
            }
            add_resp = await inst.post('/storage/v2/add_partition', data)
            [add_d1] = add_resp['disks']
            [add_p2] = match(add_d1['partitions'], number=2)
            self.assertEqual('ext3', add_p2['format'])

            data = {
                'disk_id': disk_id,
                'partition': {
                    'number': 2,
                    'format': 'ext4',
                }
            }
            edit_resp = await inst.post('/storage/v2/edit_partition', data)
            [edit_d1] = edit_resp['disks']
            [edit_p2] = match(edit_d1['partitions'], number=2)

            for key in 'size', 'number', 'mount', 'boot':
                self.assertEqual(add_p2[key], edit_p2[key], key)
            self.assertEqual('ext4', edit_p2['format'])

            del_resp = await inst.post('/storage/v2/delete_partition', data)
            [d1] = del_resp['disks']
            [p1, g1] = d1['partitions']
            self.assertEqual('Partition', p1['$type'])
            self.assertEqual('Gap', g1['$type'])

            reset_resp = await inst.post('/storage/v2/reset')
            self.assertEqual(orig_resp, reset_resp)

            choice = {'disk_id': disk_id}
            guided_resp = await inst.post('/storage/v2/guided', choice)
            post_resp = await inst.post('/storage/v2')
            # posting to the endpoint shouldn't change the answer
            self.assertEqual(guided_resp, post_resp)


class TestGuided(TestAPI):
    @timeout()
    async def test_guided_v2(self):
        async with start_server('examples/simple.json') as inst:
            resp = await inst.get('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']

            choice = {'disk_id': disk_id}
            resp = await inst.post('/storage/v2/guided', choice)
            [d1] = resp['disks']
            gaps = match_type(d1['partitions'], 'Gap')
            self.assertEqual(0, len(gaps))


class TestAdd(TestAPI):
    @timeout()
    async def test_v2_add_boot_partition(self):
        async with start_server('examples/simple.json') as inst:
            resp = await inst.post('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            [g1] = d1['partitions']

            data = {
                'disk_id': disk_id,
                'gap': g1,
                'partition': {
                    'format': 'ext4',
                    'mount': '/',
                }
            }
            single_add = await inst.post('/storage/v2/add_partition', data)
            [d1] = single_add['disks']
            parts = d1['partitions']
            self.assertEqual(2, len(parts))
            self.assertTrue(d1['boot_device'])

            await inst.post('/storage/v2/reset')

            # these manual steps are expected to be equivalent to just adding
            # the single partition and getting the automatic boot partition
            resp = await inst.post(
                '/storage/v2/add_boot_partition', disk_id=disk_id)
            [d1] = resp['disks']
            [g1] = match_type(d1['partitions'], 'Gap')
            data = {
                'disk_id': disk_id,
                'gap': g1,
                'partition': {
                    'format': 'ext4',
                    'mount': '/',
                }
            }
            manual_add = await inst.post('/storage/v2/add_partition', data)

            self.assertEqual(single_add, manual_add)

    @timeout()
    async def test_v2_deny_multiple_add_boot_partition(self):
        async with start_server('examples/simple.json') as inst:
            resp = await inst.post('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            await inst.post('/storage/v2/add_boot_partition', disk_id=disk_id)
            with self.assertRaises(ClientResponseError):
                await inst.post('/storage/v2/add_boot_partition',
                                disk_id=disk_id)

    @timeout()
    async def test_v2_deny_multiple_add_boot_partition_BIOS(self):
        async with start_server('examples/simple.json', 'bios') as inst:
            resp = await inst.post('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            await inst.post('/storage/v2/add_boot_partition', disk_id=disk_id)
            with self.assertRaises(ClientResponseError):
                await inst.post('/storage/v2/add_boot_partition',
                                disk_id=disk_id)

    @timeout()
    async def test_add_format_required(self):
        async with start_server('examples/simple.json') as inst:
            resp = await inst.post('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            [g1] = d1['partitions']
            bad_requests = [
                {},
                {'mount': '/'},
            ]
            for partition in bad_requests:
                data = {
                    'disk_id': disk_id,
                    'gap': g1,
                    'partition': partition
                }
                with self.assertRaises(ClientResponseError, msg=data):
                    await inst.post('/storage/v2/add_partition', data)

    @timeout()
    async def test_add_default_size_handling(self):
        async with start_server('examples/simple.json') as inst:
            resp = await inst.get('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            [g1] = d1['partitions']

            data = {
                'disk_id': disk_id,
                'gap': g1,
                'partition': {
                    'format': 'ext4',
                    'mount': '/',
                }
            }
            resp = await inst.post('/storage/v2/add_partition', data)
            [d1] = match(resp['disks'], id=disk_id)
            [p1, p2] = d1['partitions']
            self.assertEqual(g1['size'], p1['size'] + p2['size'])

    @timeout()
    async def test_v2_add_boot_BIOS(self):
        async with start_server('examples/simple.json', 'bios') as inst:
            resp = await inst.post('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            resp = await inst.post('/storage/v2/add_boot_partition',
                                   disk_id=disk_id)
            [d1] = resp['disks']
            [p1, _] = d1['partitions']
            self.assertTrue(d1['boot_device'])
            self.assertTrue(p1['boot'])

    @timeout()
    async def test_v2_blank_is_not_boot(self):
        async with start_server('examples/simple.json', 'bios') as inst:
            resp = await inst.get('/storage/v2')
            [d1] = resp['disks']
            self.assertFalse(d1['boot_device'])

    @timeout()
    async def test_v2_multi_disk_multi_boot(self):
        async with start_server('examples/many-nics-and-disks.json') as inst:
            resp = await inst.get('/storage/v2')
            [d1, d2] = resp['disks'][:2]
            await inst.post('/storage/v2/reformat_disk', {'disk_id': d1['id']})
            await inst.post('/storage/v2/reformat_disk', {'disk_id': d2['id']})
            await inst.post('/storage/v2/add_boot_partition', disk_id=d1['id'])
            await inst.post('/storage/v2/add_boot_partition', disk_id=d2['id'])
            # should allow both disks to get a boot partition with no Exception


class TestDelete(TestAPI):
    @timeout()
    async def test_v2_delete_without_reformat(self):
        cfg = 'examples/win10.json'
        extra = ['--storage-version', '1']
        async with start_server(cfg, extra_args=extra) as inst:
            resp = await inst.post('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            data = {
                'disk_id': disk_id,
                'partition': {'number': 1}
            }
            with self.assertRaises(ClientResponseError):
                await inst.post('/storage/v2/delete_partition', data)

    @timeout()
    async def test_v2_delete_without_reformat_is_ok_with_sv2(self):
        cfg = 'examples/win10.json'
        extra = ['--storage-version', '2']
        async with start_server(cfg, extra_args=extra) as inst:
            resp = await inst.post('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            data = {
                'disk_id': disk_id,
                'partition': {'number': 1}
            }
            await inst.post('/storage/v2/delete_partition', data)

    @timeout()
    async def test_v2_delete_with_reformat(self):
        async with start_server('examples/win10.json') as inst:
            resp = await inst.post('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            resp = await inst.post('/storage/v2/reformat_disk',
                                   {'disk_id': disk_id})
            [d1] = resp['disks']
            [g1] = d1['partitions']
            data = {
                'disk_id': disk_id,
                'gap': g1,
                'partition': {
                    'mount': '/',
                    'format': 'ext4',
                }
            }
            await inst.post('/storage/v2/add_partition', data)
            data = {
                'disk_id': disk_id,
                'partition': {'number': 1}
            }
            await inst.post('/storage/v2/delete_partition', data)

    @timeout()
    async def test_delete_nonexistant(self):
        async with start_server('examples/win10.json') as inst:
            resp = await inst.post('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            await inst.post('/storage/v2/reformat_disk', {'disk_id': disk_id})
            data = {
                'disk_id': disk_id,
                'partition': {'number': 1}
            }
            with self.assertRaises(ClientResponseError):
                await inst.post('/storage/v2/delete_partition', data)


class TestEdit(TestAPI):
    @timeout()
    async def test_edit_no_change_size(self):
        async with start_server('examples/win10.json') as inst:
            # FIXME v1 vs v2
            resp = await inst.get('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            [p3] = match(d1['partitions'], number=3)
            p3['size'] -= 1
            data = {'disk_id': disk_id, 'partition': p3}
            with self.assertRaises(ClientResponseError):
                await inst.post('/storage/v2/edit_partition', data)

    @timeout()
    async def test_edit_no_change_grub(self):
        async with start_server('examples/win10.json') as inst:
            resp = await inst.get('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            [p3] = match(d1['partitions'], number=3)
            p3['boot'] = True
            data = {'disk_id': disk_id, 'partition': p3}
            with self.assertRaises(ClientResponseError):
                await inst.post('/storage/v2/edit_partition', data)

    @timeout()
    async def test_edit_format(self):
        async with start_server('examples/win10.json') as inst:
            resp = await inst.get('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            [p3] = match(d1['partitions'], number=3)
            p3['format'] = 'btrfs'
            data = {'disk_id': disk_id, 'partition': p3}
            resp = await inst.post('/storage/v2/edit_partition', data)

            [d1] = resp['disks']
            [p3] = match(d1['partitions'], number=3)
            self.assertEqual('btrfs', p3['format'])

    @timeout()
    async def test_edit_mount(self):
        async with start_server('examples/win10.json') as inst:
            resp = await inst.get('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            [p3] = match(d1['partitions'], number=3)
            p3['mount'] = '/'
            data = {'disk_id': disk_id, 'partition': p3}
            resp = await inst.post('/storage/v2/edit_partition', data)

            [d1] = resp['disks']
            [p3] = match(d1['partitions'], number=3)
            self.assertEqual('/', p3['mount'])

    @timeout()
    async def test_edit_format_and_mount(self):
        async with start_server('examples/win10.json') as inst:
            resp = await inst.get('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            [p3] = match(d1['partitions'], number=3)
            p3['format'] = 'btrfs'
            p3['mount'] = '/'
            data = {'disk_id': disk_id, 'partition': p3}
            resp = await inst.post('/storage/v2/edit_partition', data)

            [d1] = resp['disks']
            [p3] = match(d1['partitions'], number=3)
            self.assertEqual('btrfs', p3['format'])
            self.assertEqual('/', p3['mount'])

    @timeout()
    async def test_v2_reuse(self):
        async with start_server('examples/win10.json') as inst:
            resp = await inst.get('/storage/v2')
            [d1] = resp['disks']
            [_, orig_p2, _, orig_p4] = d1['partitions']
            disk_id = d1['id']

            data = {
                'disk_id': disk_id,
                'partition': {
                    'number': 3,
                    'format': 'ext4',
                    'mount': '/',
                }
            }
            resp = await inst.post('/storage/v2/edit_partition', data)
            [d1] = resp['disks']
            [p1, p2, p3, p4] = d1['partitions']
            self.assertIsNone(p1['wipe'])
            self.assertEqual('/boot/efi', p1['mount'])
            self.assertEqual('vfat', p1['format'])
            self.assertTrue(p1['boot'])

            self.assertEqual(orig_p2, p2)

            self.assertEqual('superblock', p3['wipe'])
            self.assertEqual('/', p3['mount'])
            self.assertEqual('ext4', p3['format'])
            self.assertFalse(p3['boot'])

            self.assertEqual(orig_p4, p4)


class TestReformat(TestAPI):
    @timeout()
    async def test_reformat_msdos(self):
        cfg = 'examples/simple.json'
        async with start_server(cfg) as inst:
            resp = await inst.get('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            data = {'disk_id': disk_id, 'ptable': 'msdos'}
            resp = await inst.post('/storage/v2/reformat_disk', data)
            [d1] = resp['disks']
            self.assertEqual('msdos', d1['ptable'])


class TestPartitionTableTypes(TestAPI):
    @timeout()
    async def test_ptable_gpt(self):
        async with start_server('examples/win10.json') as inst:
            resp = await inst.get('/storage/v2')
            [d1] = resp['disks']
            self.assertEqual('gpt', d1['ptable'])

    @timeout()
    async def test_ptable_msdos(self):
        async with start_server('examples/many-nics-and-disks.json') as inst:
            resp = await inst.get('/storage/v2')
            [sda] = match(resp['disks'], id='disk-sda')
            self.assertEqual('msdos', sda['ptable'])

    @timeout()
    async def test_ptable_none(self):
        async with start_server('examples/simple.json') as inst:
            resp = await inst.get('/storage/v2')
            [d1] = resp['disks']
            self.assertEqual(None, d1['ptable'])


class TestTodos(TestAPI):  # server indicators of required client actions
    @timeout()
    async def test_todos_simple(self):
        async with start_server('examples/simple.json') as inst:
            resp = await inst.get('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            resp = await inst.post('/storage/v2/reformat_disk',
                                   {'disk_id': disk_id})
            self.assertTrue(resp['need_root'])
            self.assertTrue(resp['need_boot'])

            [d1] = resp['disks']
            [g1] = d1['partitions']
            data = {
                'disk_id': disk_id,
                'gap': g1,
                'partition': {
                    'format': 'ext4',
                    'mount': '/',
                }
            }
            resp = await inst.post('/storage/v2/add_partition', data)
            self.assertFalse(resp['need_root'])
            self.assertFalse(resp['need_boot'])

    @timeout()
    async def test_todos_manual(self):
        async with start_server('examples/simple.json') as inst:
            resp = await inst.get('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            resp = await inst.post('/storage/v2/reformat_disk',
                                   {'disk_id': disk_id})
            self.assertTrue(resp['need_root'])
            self.assertTrue(resp['need_boot'])

            resp = await inst.post('/storage/v2/add_boot_partition',
                                   disk_id=disk_id)
            self.assertTrue(resp['need_root'])
            self.assertFalse(resp['need_boot'])

            [d1] = resp['disks']
            [g1] = match_type(d1['partitions'], 'Gap')
            data = {
                'disk_id': disk_id,
                'gap': g1,
                'partition': {
                    'format': 'ext4',
                    'mount': '/',
                }
            }
            resp = await inst.post('/storage/v2/add_partition', data)
            self.assertFalse(resp['need_root'])
            self.assertFalse(resp['need_boot'])

    @timeout()
    async def test_todos_guided(self):
        async with start_server('examples/simple.json') as inst:
            resp = await inst.get('/storage/v2')
            [d1] = resp['disks']
            disk_id = d1['id']
            resp = await inst.post('/storage/v2/reformat_disk',
                                   {'disk_id': disk_id})
            self.assertTrue(resp['need_root'])
            self.assertTrue(resp['need_boot'])

            choice = {'disk_id': disk_id}
            resp = await inst.post('/storage/v2/guided', choice)
            self.assertFalse(resp['need_root'])
            self.assertFalse(resp['need_boot'])


class TestInfo(TestAPI):
    @timeout()
    async def test_path(self):
        async with start_server('examples/simple.json') as inst:
            disk_id = 'disk-sda'
            resp = await inst.get('/storage/v2')
            [sda] = match(resp['disks'], id=disk_id)
            self.assertEqual('/dev/sda', sda['path'])

    async def test_model_and_vendor(self):
        async with start_server('examples/simple.json') as inst:
            disk_id = 'disk-sda'
            resp = await inst.get('/storage/v2')
            [sda] = match(resp['disks'], id=disk_id)
            self.assertEqual('QEMU HARDDISK', sda['model'])
            self.assertEqual('ATA', sda['vendor'])

    async def test_no_vendor(self):
        async with start_server('examples/many-nics-and-disks.json') as inst:
            disk_id = 'disk-sda'
            resp = await inst.get('/storage/v2')
            [sda] = match(resp['disks'], id=disk_id)
            self.assertEqual('QEMU HARDDISK', sda['model'])
            self.assertEqual(None, sda['vendor'])


class TestFree(TestAPI):
    @timeout()
    async def test_free_only(self):
        async with start_server('examples/simple.json') as inst:
            await inst.post('/meta/free_only', enable=True)
            components = await inst.get('/mirror/disable_components')
            components.sort()
            self.assertEqual(['multiverse', 'restricted'], components)

    @timeout()
    async def test_not_free_only(self):
        async with start_server('examples/simple.json') as inst:
            comps = ['universe', 'multiverse']
            await inst.post('/mirror/disable_components', comps)
            await inst.post('/meta/free_only', enable=False)
            components = await inst.get('/mirror/disable_components')
            self.assertEqual(['universe'], components)


class TestOSProbe(TestAPI):
    @timeout()
    async def test_win10(self):
        async with start_server('examples/win10.json') as inst:
            resp = await inst.get('/storage/v2')
            [sda] = match(resp['disks'], id='disk-sda')
            [sda1] = match(sda['partitions'], number=1)
            expected = {
                'label': 'Windows',
                'long': 'Windows Boot Manager',
                'subpath': '/efi/Microsoft/Boot/bootmgfw.efi',
                'type': 'efi',
                'version': None
            }

            self.assertEqual(expected, sda1['os'])


class TestPartitionTableEditing(TestAPI):
    @timeout()
    async def test_use_free_space_after_existing(self):
        cfg = 'examples/ubuntu-and-free-space.json'
        extra = ['--storage-version', '2']
        async with start_server(cfg, extra_args=extra) as inst:
            # Disk has 3 existing partitions and free space.  Add one to end.
            # sda1 is an ESP, so that should get implicitly picked up.
            resp = await inst.get('/storage/v2')
            [sda] = resp['disks']
            [e1, e2, e3, gap] = sda['partitions']
            self.assertEqual('Gap', gap['$type'])

            data = {
                'disk_id': 'disk-sda',
                'gap': gap,
                'partition': {
                    'format': 'ext4',
                    'mount': '/',
                }
            }
            resp = await inst.post('/storage/v2/add_partition', data)
            [sda] = resp['disks']
            [p1, p2, p3, p4] = sda['partitions']
            e1.pop('annotations')
            e1.update({'mount': '/boot/efi', 'grub_device': True})
            self.assertDictSubset(e1, p1)
            self.assertEqual(e2, p2)
            self.assertEqual(e3, p3)
            e4 = {
                '$type': 'Partition',
                'number': 4,
                'size': gap['size'],
                'offset': gap['offset'],
                'format': 'ext4',
                'mount': '/',
            }
            self.assertDictSubset(e4, p4)

    @timeout()
    async def test_resize(self):
        # load config, edit size, use that for server
        with open('examples/ubuntu-and-free-space.json', 'r') as fp:
            data = json.load(fp)

        # expand sda3 to use the rest of the disk
        def get_size(key):
            return int(data['storage']['blockdev'][key]['attrs']['size'])

        sda_size = get_size('/dev/sda')
        sda1_size = get_size('/dev/sda1')
        sda2_size = get_size('/dev/sda2')
        sda3_size = sda_size - sda1_size - sda2_size - (2 << 20)
        data['storage']['blockdev']['/dev/sda3']['attrs']['size'] = \
            str(sda3_size)
        cfg = self.tmp_path('machine-config.json')
        with open(cfg, 'w') as fp:
            json.dump(data, fp)

        extra = ['--storage-version', '2']
        async with start_server(cfg, extra_args=extra) as inst:
            # Disk has 3 existing partitions and no free space.
            resp = await inst.get('/storage/v2')
            [sda] = resp['disks']
            [orig_p1, orig_p2, orig_p3] = sda['partitions']

            p3 = orig_p3.copy()
            p3['size'] = 10 << 30
            data = {
                'disk_id': 'disk-sda',
                'partition': p3,
            }
            resp = await inst.post('/storage/v2/edit_partition', data)
            [sda] = resp['disks']
            [_, _, actual_p3, g1] = sda['partitions']
            self.assertEqual(10 << 30, actual_p3['size'])
            self.assertEqual(True, actual_p3['resize'])
            self.assertIsNone(actual_p3['wipe'])
            end_size = orig_p3['size'] - (10 << 30)
            self.assertEqual(end_size, g1['size'])

            expected_p1 = orig_p1.copy()
            expected_p1.pop('annotations')
            expected_p1.update({'mount': '/boot/efi', 'grub_device': True})
            expected_p3 = actual_p3
            data = {
                'disk_id': 'disk-sda',
                'gap': g1,
                'partition': {
                    'format': 'ext4',
                    'mount': '/srv',
                }
            }
            resp = await inst.post('/storage/v2/add_partition', data)
            [sda] = resp['disks']
            [actual_p1, actual_p2, actual_p3, actual_p4] = sda['partitions']
            self.assertDictSubset(expected_p1, actual_p1)
            self.assertEqual(orig_p2, actual_p2)
            self.assertEqual(expected_p3, actual_p3)
            self.assertEqual(end_size, actual_p4['size'])
            self.assertEqual('Partition', actual_p4['$type'])

            v1resp = await inst.get('/storage')
            config = v1resp['config']
            [sda3] = match(config, type='partition', number=3)
            [sda3_format] = match(config, type='format', volume=sda3['id'])
            self.assertTrue(sda3['preserve'])
            self.assertTrue(sda3['resize'])
            self.assertTrue(sda3_format['preserve'])


class TestGap(TestAPI):
    async def test_blank_disk_is_one_big_gap(self):
        async with start_server('examples/simple.json') as inst:
            resp = await inst.get('/storage/v2')
            [sda] = match(resp['disks'], id='disk-sda')
            [gap] = sda['partitions']
            expected = (10 << 30) - (2 << 20)
            self.assertEqual(expected, gap['size'])

    async def test_gap_at_end(self):
        async with start_server('examples/simple.json') as inst:
            resp = await inst.get('/storage/v2')
            [sda] = resp['disks']
            [gap] = match_type(sda['partitions'], 'Gap')
            data = {
                'disk_id': 'disk-sda',
                'gap': gap,
                'partition': {
                    'format': 'ext4',
                    'mount': '/',
                    'size': 4 << 30,
                }
            }
            resp = await inst.post('/storage/v2/add_partition', data)
            [sda] = match(resp['disks'], id='disk-sda')
            [boot] = match(sda['partitions'], number=1)
            gap = sda['partitions'][2]
            expected = (10 << 30) - boot['size'] - (4 << 30) - (2 << 20)
            self.assertEqual(expected, gap['size'])

    async def SKIP_test_two_gaps(self):
        async with start_server('examples/simple.json') as inst:
            disk_id = 'disk-sda'
            resp = await inst.post('/storage/v2/add_boot_partition',
                                   disk_id=disk_id)
            json_print(resp)
            boot_size = resp['disks'][0]['partitions'][0]['size']
            root_size = 4 << 30
            data = {
                'disk_id': disk_id,
                'partition': {
                    'format': 'ext4',
                    'mount': '/',
                    'size': root_size,
                }
            }
            await inst.post('/storage/v2/add_partition', data)
            data = {
                'disk_id': disk_id,
                'partition': {'number': 1}
            }
            resp = await inst.post('/storage/v2/delete_partition', data)
            [sda] = match(resp['disks'], id=disk_id)
            self.assertEqual(3, len(sda['partitions']))

            boot_gap = sda['partitions'][0]
            self.assertEqual(boot_size, boot_gap['size'])
            self.assertEqual('Gap', boot_gap['$type'])

            root = sda['partitions'][1]
            self.assertEqual(root_size, root['size'])
            self.assertEqual('Partition', root['$type'])

            end_gap = sda['partitions'][2]
            end_size = (10 << 30) - boot_size - root_size - (2 << 20)
            self.assertEqual(end_size, end_gap['size'])
            self.assertEqual('Gap', end_gap['$type'])


class TestRegression(TestAPI):
    @timeout()
    async def test_edit_not_trigger_boot_device(self):
        async with start_server('examples/simple.json') as inst:
            disk_id = 'disk-sda'
            resp = await inst.get('/storage/v2')
            [sda] = resp['disks']
            [gap] = sda['partitions']
            data = {
                'disk_id': disk_id,
                'gap': gap,
                'partition': {
                    'format': 'ext4',
                    'mount': '/foo',
                }
            }
            resp = await inst.post('/storage/v2/add_partition', data)
            [sda] = match(resp['disks'], id=disk_id)
            [sda2] = match(sda['partitions'], number=2)
            sda2.update({'format': 'ext3', 'mount': '/bar'})
            data['partition'] = sda2
            data.pop('gap')
            await inst.post('/storage/v2/edit_partition', data)
            # should not throw an exception complaining about boot


class TestCancel(TestAPI):
    @timeout()
    async def test_cancel_drivers(self):
        with patch.dict(os.environ, {'SUBIQUITY_DEBUG': 'has-drivers'}):
            async with start_server('examples/simple.json') as inst:
                await inst.post('/source', source_id="dummy",
                                search_drivers=True)
                # /drivers?wait=true is expected to block until APT is
                # configured.
                # Let's make sure we cancel it.
                with self.assertRaises(asyncio.TimeoutError):
                    await asyncio.wait_for(inst.get('/drivers', wait=True),
                                           0.1)
                names = ['locale', 'keyboard', 'source', 'network', 'proxy',
                         'mirror', 'storage']
                await inst.post('/meta/mark_configured', endpoint_names=names)
                await inst.get('/meta/status', cur='WAITING')
                await inst.post('/meta/confirm', tty='/dev/tty1')
                await inst.get('/meta/status', cur='NEEDS_CONFIRMATION')

                # should not raise ServerDisconnectedError
                resp = await inst.get('/drivers', wait=True)
                self.assertEqual(['nvidia-driver-470-server'], resp['drivers'])


class TestSource(TestAPI):
    async def test_optional_search_drivers(self):
        async with start_server('examples/simple.json') as inst:
            await inst.post('/source', source_id='ubuntu-server')
            resp = await inst.get('/source')
            self.assertFalse(resp['search_drivers'])

            await inst.post('/source', source_id='ubuntu-server',
                            search_drivers=True)
            resp = await inst.get('/source')
            self.assertTrue(resp['search_drivers'])

            await inst.post('/source', source_id='ubuntu-server',
                            search_drivers=False)
            resp = await inst.get('/source')
            self.assertFalse(resp['search_drivers'])


class TestIdentityValidation(TestAPI):
    async def test_username_validation(self):
        async with start_server('examples/simple.json') as inst:
            resp = await inst.get('/identity/validate_username',
                                  username='plugdev')
            self.assertEqual(resp, 'SYSTEM_RESERVED')

            resp = await inst.get('/identity/validate_username',
                                  username='root')
            self.assertEqual(resp, 'ALREADY_IN_USE')

            resp = await inst.get('/identity/validate_username',
                                  username='r'*33)
            self.assertEqual(resp, 'TOO_LONG')

            resp = await inst.get('/identity/validate_username',
                                  username='01root')
            self.assertEqual(resp, 'INVALID_CHARS')

            resp = await inst.get('/identity/validate_username',
                                  username='o#$%^&')
            self.assertEqual(resp, 'INVALID_CHARS')
