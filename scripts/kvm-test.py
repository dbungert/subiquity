#!/usr/bin/env python3

'''kvm-test - boot a kvm with a test iso, possibly building that test iso first

kvm-test -q --install -o --boot
   slimy build, install, overwrite existing image if it exists,
   and boot the result after install

See kvm-test -h for options and more examples.
'''

import argparse
import contextlib
import copy
import crypt
import os
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
from typing import List, Tuple
import yaml


livefs_editor = os.environ['LIVEFS_EDITOR']


cfg = '''
default_mem: '8G'
iso:
    basedir: /srv/iso
    release:
        edge: jammy/subiquity-edge/jammy-live-server-subiquity-edge-amd64.iso
        canary: jammy/jammy-desktop-canary-amd64.iso
        jammy: jammy/jammy-live-server-amd64.iso
        desktop: jammy/jammy-desktop-amd64.iso
        impish: impish/ubuntu-21.10-live-server-amd64.iso
        hirsute: hirsute/ubuntu-21.04-live-server-amd64.iso
        groovy: groovy/ubuntu-20.10-live-server-amd64.iso
        focal: focal/ubuntu-20.04.3-live-server-amd64.iso
        bionic: bionic/bionic-live-server-amd64.iso
    default: edge
'''


def salted_crypt(plaintext_password):
    # match subiquity documentation
    salt = '$6$exDY1mhS4KUYCE/2'
    return crypt.crypt(plaintext_password, salt)


class Tap:
    def __init__(self, ifname: str) -> None:
        self.ifname = ifname


def get_project():
    for filename in ('snapcraft.yaml', 'snap/snapcraft.yaml'):
        if os.path.exists(filename):
            with open(filename) as fp:
                return yaml.safe_load(fp)['name']
    return os.path.basename(os.getcwd())


class Context:
    def __init__(self, args):
        self.config = self.load_config()
        self.args = args
        self.release = args.release
        self.workdir = self.config.get('workdir', '/tmp/kvm-test')
        self.project = args.project or get_project()
        self.default_mem = self.config.get('default_mem', '8G')
        self.default_disk = self.config.get('default_disk', '12G')
        if not self.release:
            self.release = self.config["iso"]["default"]
        iso = self.config["iso"]
        try:
            self.baseiso = os.path.join(iso["basedir"],
                                        iso["release"][self.release])
        except KeyError:
            pass
        self.curdir = os.getcwd()
        self.iso = f'{self.workdir}/{self.release}-test.iso'
        self.hostname = f'{self.release}-test'
        self.target = f'{self.workdir}/{self.hostname}.img'
        self.password = salted_crypt('ubuntu')
        self.cloudconfig = f'''\
#cloud-config
autoinstall:
    version: 1
    locale:
        en_US.UTF-8
    ssh:
        install-server: true
        allow-pw: true
    identity:
        hostname: {self.hostname}
        password: "{self.password}"
        username: ubuntu
'''

    def merge(self, a, b):
        '''Take a pair of dictionaries, and provide the merged result.
           Assumes that any key conflicts have values that are themselves
           dictionaries and raises TypeError if found otherwise.'''
        result = copy.deepcopy(a)

        for key in b:
            if key in result:
                left = result[key]
                right = b[key]
                if type(left) is not dict or type(right) is not dict:
                    result[key] = right
                else:
                    result[key] = self.merge(left, right)
            else:
                result[key] = b[key]

        return result

    def load_config(self):
        result = yaml.safe_load(cfg)
        homecfg = f'{os.environ["HOME"]}/.kvm-test.yaml'
        if os.path.exists(homecfg):
            with open(homecfg, 'r') as f:
                result = self.merge(result, yaml.safe_load(f))

        return result


parser = argparse.ArgumentParser(
    formatter_class=argparse.RawDescriptionHelpFormatter,
    description='''\
Test isos and images written to /tmp/kvm-test by default.

Sample usage:
    kvm-test --build -q --install -o -a --boot
        slimy build, run install, overwrite existing image, use autoinstall,
        boot final resulting image

    kvm-test --install -bo -rfocal
        boot the focal base iso unmodified and run install manually

If DEBOOTSTRAP_PROXY is set, that will be passed to snapcraft to pick up
packages from a cache.

See 'cfg' in script for expected layout of iso files,
which can be managed with ~/.kvm-test.yaml''')
parser.add_argument('-b', '--base', default=False, action='store_true',
                    help='use base iso')
parser.add_argument('--basesnap', default=None, action='store',
                    help='use slimy-update-snap on this snap')
parser.add_argument('--snap', default=None, action='store',
                    help='inject this snap into the ISO')
parser.add_argument('-B', '--bios', action='store_true', default=False,
                    help='boot in BIOS mode (default mode is UEFI)')
parser.add_argument('-c', '--channel', action='store',
                    help='build iso with snap from channel')
parser.add_argument('-d', '--disksize', default=None, action='store',
                    help='size of disk to create (12G default)')
parser.add_argument('-i', '--img', action='store', help='use this img')
parser.add_argument('-n', '--nets', action='store', default=1, type=int,
                    help='''number of network interfaces.
                    0=no network, -1=deadnet''')
parser.add_argument('--nic-user', action="append_const", dest="nics",
                    const=None,
                    help='pass user host -nic to QEMU'
                         ' - overrides --nets')
parser.add_argument('--nic-tap', action="append", dest="nics", type=Tap,
                    metavar="ifname",
                    help='TAP interface to be passed as -nic to QEMU'
                         ' - overrides --nets')
parser.add_argument('--nic', action="append", dest="nics",
                    metavar="argument",
                    help='pass custom -nic argument to QEMU'
                         ' - overrides --nets')
parser.add_argument('-o', '--overwrite', default=False, action='store_true',
                    help='allow overwrite of the target image')
parser.add_argument('--project', action='store', help='project name')
parser.add_argument('-q', '--quick', default=False, action='store_true',
                    help='build iso with quick-test-this-branch')
parser.add_argument('-r', '--release', action='store', help='target release')
parser.add_argument('-s', '--serial', default=False, action='store_true',
                    help='attach to serial console')
parser.add_argument('-S', '--sound', default=False, action='store_true',
                    help='enable sound')
parser.add_argument('--iso', action='store', help='use this iso')
parser.add_argument('-u', '--update', action='store',
                    help='subiquity-channel argument')
parser.add_argument('-m', '--memory', action='store',
                    help='memory for VM')
parser.add_argument('--save', action='store_true',
                    help='preserve built snap')
parser.add_argument('--reuse', action='store_true',
                    help='reuse previously saved snap.  Implies --save')
parser.add_argument('--build', default=False, action='store_true',
                    help='build test iso')
parser.add_argument('--install', default=False, action='store_true',
                    help='''install from iso - one must either build a test
                    iso, use a base iso, or reuse previous test iso''')
parser.add_argument('--video', default=None, action='store')
parser.add_argument('--boot', default=False, action='store_true',
                    help='boot test image')
parser.add_argument('--force-autoinstall', default=None,
                    action='store_true', dest="autoinstall",
                    help='pass autoinstall on the kernel command line')
parser.add_argument('--force-no-autoinstall', default=None,
                    action='store_false', dest="autoinstall",
                    help='do not pass autoinstall on the kernel command line')
parser.add_argument('--with-tpm2', action='store_true',
                    help='''emulate a TPM 2.0 interface (requires swtpm
                    package)''')


cc_group = parser.add_mutually_exclusive_group()
cc_group.add_argument('--cloud-config', action='store',
                      type=argparse.FileType(),
                      help='specify the cloud-config file to use (it may'
                           ' contain an autoinstall section or not)')
cc_group.add_argument('--cloud-config-default',
                      action="store_true",
                      help='use hardcoded cloud-config template')


def parse_args():
    ctx = Context(parser.parse_args())
    if ctx.args.quick or ctx.args.basesnap or ctx.args.snap \
            or ctx.args.channel or ctx.args.reuse:
        ctx.args.build = True
    if ctx.args.reuse:
        ctx.args.save = True

    ctx.livefs_editor = os.environ.get('LIVEFS_EDITOR')
    if not ctx.livefs_editor and ctx.args.build:
        raise Exception('Obtain a copy of livefs-editor and point ' +
                        'LIVEFS_EDITOR to it\n'
                        'https://github.com/mwhudson/livefs-editor')

    return ctx


def run(cmd):
    if isinstance(cmd, str):
        cmd_str = cmd
        cmd_array = shlex.split(cmd)
    else:
        cmd_str = shlex.join(cmd)
        cmd_array = cmd
    # semi-simulate "bash -x"
    print(f'+ {cmd_str}', file=sys.stderr)
    subprocess.run(cmd_array, check=True)


def assert_exists(path):
    if not os.path.exists(path):
        raise Exception(f'Expected file {path} not found')


def remove_if_exists(path):
    if os.path.exists(path):
        os.remove(path)


@contextlib.contextmanager
def delete_later(path):
    try:
        yield path
    finally:
        remove_if_exists(path)


@contextlib.contextmanager
def noop(path):
    yield path


@contextlib.contextmanager
def mounter(src, dest):
    run(["fuseiso", src, dest])
    try:
        yield
    finally:
        run(["fusermount", "-u", dest])


def livefs_edit(ctx, *args):
    livefs_edit = shutil.which('livefs-edit')
    if livefs_edit is None:
        raise Exception('Failed to find livefs-edit in PATH')
    run(['sudo', livefs_edit, ctx.baseiso, ctx.iso, *args])


def build(ctx):
    remove_if_exists(ctx.iso)

    snapargs = '--debug'
    # http_proxy = os.environ.get('DEBOOTSTRAP_PROXY')
    # if http_proxy:
    #     snapargs += f' --http-proxy={http_proxy}'

    snap_manager = noop if ctx.args.save else delete_later
    if ctx.project == 'subiquity':
        if ctx.args.quick:
            run(['sudo',
                 f'LIVEFS_EDITOR={livefs_editor}',
                 './scripts/quick-test-this-branch.sh',
                 ctx.baseiso,
                 ctx.iso])
        elif ctx.args.basesnap:
            with snap_manager('subiquity_test.snap') as snap:
                run(f'sudo ./scripts/slimy-update-snap.sh {ctx.args.basesnap} \
                    {snap}')
                run(f'sudo ./scripts/inject-subiquity-snap.sh {ctx.baseiso} \
                    {snap} {ctx.iso}')
        elif ctx.args.snap:
            run(f'sudo ./scripts/inject-subiquity-snap.sh {ctx.baseiso} \
                {ctx.args.snap} {ctx.iso}')
        elif ctx.args.channel:
            livefs_edit(ctx, '--add-snap-from-store', 'subiquity',
                        ctx.args.channel)
        else:
            with snap_manager('subiquity_test.snap') as snap:
                if not ctx.args.reuse:
                    run('snapcraft clean --use-lxd')
                    run(f'snapcraft pack --use-lxd --output {snap} {snapargs}')
                assert_exists(snap)
                livefs_edit(ctx, '--inject-snap', snap)
    elif ctx.project == 'ubuntu-desktop-installer':
        if ctx.args.quick:
            run(f'sudo ../quick-test-this-branch-udi.sh {ctx.baseiso} \
                {ctx.iso} ubuntu-desktop-installer')
        else:
            with snap_manager('udi_test.snap') as snap:
                if not ctx.args.reuse:
                    run('snapcraft clean --use-lxd')
                    run(f'snapcraft pack --use-lxd --output {snap} '
                        f'{snapargs}')
                assert_exists(snap)
                run(f'sudo ./scripts/inject-snap {ctx.baseiso} {ctx.iso} '
                    f'{snap}')
    else:
        raise Exception(f'do not know how to build {ctx.project}')

    assert_exists(ctx.iso)


def write(dest, data):
    with open(dest, 'w') as destfile:
        destfile.write(data)


def touch(dest):
    with open(dest, 'w'):
        pass


def create_seed(cloudconfig, tempdir):
    write(f'{tempdir}/user-data', cloudconfig)
    touch(f'{tempdir}/meta-data')
    seed = f'{tempdir}/seed.iso'
    run(f'cloud-localds {seed} {tempdir}/user-data {tempdir}/meta-data')
    return seed


def drive(path, format='qcow2') -> Tuple[str, str]:
    """ Return a tuple (-drive, <options>) that can be passed to kvm """
    kwargs = []
    serial = None
    cparam = 'writethrough'
    kwargs.append(f'file={path}')
    kwargs.append(f'format={format}')
    kwargs.append(f'cache={cparam}')
    kwargs.append('if=virtio')
    if serial:
        kwargs.append(f'serial={serial}')

    return ('-drive', ','.join(kwargs))


class PortFinder:
    def __init__(self):
        self.finder = self.port_generator()

    def port_generator(self):
        for port in range(2222, 8000):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                res = sock.connect_ex(('localhost', port))
                if res != 0:
                    yield port

    def get(self):
        return next(self.finder)


class NetFactory:
    """ Generate -nic options for QEMU. """
    ports_finder = PortFinder()

    def user(self) -> Tuple[str, ...]:
        """ User host network with SSH forwarding """
        port = self.ports_finder.get()
        return ('-nic', f'user,model=virtio-net-pci,hostfwd=tcp::{port}-:22')

    def tap(self, ifname: str) -> Tuple[str, ...]:
        """ Network using an existing TAP interface. """
        tap_props = {
            "id": ifname,
            "ifname": ifname,
            "script": "no",
            "downscript": "no",
            "model": "e1000",
        }

        nic = ",".join(["tap"] + [f"{k}={v}" for k, v in tap_props.items()])

        return ('-nic', nic)

    def deadnet(self) -> Tuple[str, ...]:
        """ NIC present but restricted - simulate deadnet environment """
        return ('-nic', 'user,model=virtio-net-pci,restrict=on')

    def nonet(self) -> Tuple[str, ...]:
        """ No network """
        return ('-nic', 'none')


def nets(ctx) -> List[str]:
    nics: List[str] = []
    factory = NetFactory()

    if ctx.args.nics:
        for nic in ctx.args.nics:
            if nic is None:
                nics.extend(factory.user())
            elif isinstance(nic, Tap):
                nics.extend(factory.tap(nic.ifname))
            else:
                nics.extend(('-nic', nic))
    elif ctx.args.nets > 0:
        for _ in range(ctx.args.nets):
            nics.extend(factory.user())
    elif ctx.args.nets == 0:
        nics.extend(factory.nonet())
    else:
        nics.extend(factory.deadnet())
    return nics


@dataclasses.dataclass(frozen=True)
class TPMEmulator:
    socket: pathlib.Path
    logfile: pathlib.Path
    tpmstate: pathlib.Path


def tpm(emulator: Optional[TPMEmulator]) -> List[str]:
    if emulator is None:
        return []

    return ['-chardev', f'socket,id=chrtpm,path={emulator.socket}',
            '-tpmdev', 'emulator,id=tpm0,chardev=chrtpm',
            '-device', 'tpm-tis,tpmdev=tpm0']


def bios(ctx):
    ret = []
    # https://help.ubuntu.com/community/UEFI
    if not ctx.args.bios:
        ret = ['-bios', '/usr/share/qemu/OVMF.fd']
    return ret


def memory(ctx):
    return ['-m', ctx.args.memory or ctx.default_mem]


@contextlib.contextmanager
def kvm_prepare_common(ctx):
    '''Spawn needed background processes and return the CLI options for QEMU'''
    ret = ['kvm', '-no-reboot']
    appends = []
    ret.extend(memory(ctx))
    ret.extend(bios(ctx))
    ret.extend(nets(ctx))
    if ctx.args.sound:
        ret.extend(('-device', 'AC97', '-device', 'usb-ehci'))
    if ctx.args.serial:
        ret.append('-nographic')
        appends = ['console=ttyS0']
    elif ctx.args.video == 'qxl':
        ret.extend(('-device', 'qxl'))
    elif ctx.args.video is not None:
        ret.extend(('-vga', ctx.args.video))
    else:
        ret.extend(('-vga', 'virtio'))

    if ctx.args.with_tpm2:
        tpm_emulator_context = tpm_emulator()
    else:
        tpm_emulator_context = contextlib.nullcontext()

    with tpm_emulator_context as tpm_emulator_cm:
        ret.extend(tpm(tpm_emulator_cm))
        yield ret, appends


def get_initrd(mntdir):
    for initrd in ('initrd', 'initrd.lz', 'initrd.lz4'):
        path = f'{mntdir}/casper/{initrd}'
        if os.path.exists(path):
            return path
    raise Exception('initrd not found')


def install(ctx):
    if os.path.exists(ctx.target):
        if ctx.args.overwrite:
            os.remove(ctx.target)
        else:
            raise Exception('refusing to overwrite existing image, use the ' +
                            '-o option to allow overwriting')

    with tempfile.TemporaryDirectory() as tempdir:
        mntdir = f'{tempdir}/mnt'
        os.mkdir(mntdir)
        appends = []

        with kvm_prepare_common(ctx) as kvm:

            if ctx.args.iso:
                iso = ctx.args.iso
            elif ctx.args.base:
                iso = ctx.baseiso
            else:
                iso = ctx.iso

            kvm.extend(('-cdrom', iso))

            if ctx.args.serial:
                kvm.append('-nographic')
                appends.append('console=ttyS0')

            if ctx.args.cloud_config is not None or ctx.args.cloud_config_default:
                if ctx.args.cloud_config is not None:
                    ctx.cloudconfig = ctx.args.cloud_config.read()
                kvm.extend(drive(create_seed(ctx.cloudconfig, tempdir), 'raw'))
                if ctx.args.autoinstall is None:
                    # Let's inspect the yaml and check if there is an autoinstall
                    # section.
                    autoinstall = "autoinstall" in yaml.safe_load(ctx.cloudconfig)
                else:
                    autoinstall = ctx.args.autoinstall

                if autoinstall:
                    appends.append('autoinstall')


            if ctx.args.update:
                appends.append('subiquity-channel=' + ctx.args.update)

            kvm.extend(drive(ctx.target))
            if not os.path.exists(ctx.target) or ctx.args.overwrite:
                run(f'qemu-img create -f qcow2 {ctx.target} {ctx.args.disksize}')

            if len(appends) > 0:
                with mounter(iso, mntdir):
                    # if we're passing kernel args, we need to manually specify
                    # kernel / initrd
                    kvm.extend(('-kernel', f'{mntdir}/casper/vmlinuz'))
                    kvm.extend(('-initrd', get_initrd(mntdir)))
                    kvm.extend(('-append', ' '.join(appends)))
                    run(kvm)
            else:
                run(kvm)


@contextlib.contextmanager
def tpm_emulator(directory=None):
    if directory is None:
        directory_context = tempfile.TemporaryDirectory()
    else:
        directory_context = contextlib.nullcontext(enter_result=directory)

    with directory_context as tempdir:
        socket = os.path.join(tempdir, 'swtpm-sock')
        logfile = os.path.join(tempdir, 'log')
        tpmstate = tempdir

        ps = subprocess.Popen(['swtpm', 'socket',
                               '--tpmstate', f'dir={tpmstate}',
                               '--ctrl', f'type=unixio,path={socket}',
                               '--tpm2',
                               '--log',  f'file={logfile},level=20'],
                              )
        try:
            yield TPMEmulator(socket=pathlib.Path(socket),
                              logfile=pathlib.Path(logfile),
                              tpmstate=pathlib.Path(tpmstate))
        finally:
            ps.communicate()


def boot(ctx):
    target = ctx.target
    if ctx.args.img:
        target = ctx.args.img

    with kvm_prepare_common(ctx) as kvm, appends:
        kvm.extend(drive(target))
        run(kvm)


def help():
    parser.print_usage()
    sys.exit(1)


def main() -> None:
    """ Entry point. """
    try:
        ctx = parse_args()
    except TypeError:
        help()

    if ctx.args.base and ctx.args.build:
        raise Exception('cannot use base iso and build')

    os.makedirs(ctx.workdir, exist_ok=True)

    if ctx.args.build:
        build(ctx)
    if ctx.args.install:
        install(ctx)
    if ctx.args.boot:
        boot(ctx)
    if True not in (ctx.args.build, ctx.args.install, ctx.args.boot):
        parser.print_help()


if __name__ == "__main__":
    main()
