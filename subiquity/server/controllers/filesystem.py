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
import functools
import glob
import json
import logging
import os
import platform
import select
from typing import Dict, List, Optional

from curtin.storage_config import ptable_uuid_to_flag_entry

import pyudev

from subiquitycore.async_helpers import (
    run_in_thread,
    schedule_task,
    SingleInstanceTask,
    TaskAlreadyRunningError,
    )
from subiquitycore.context import with_context
from subiquitycore.utils import (
    run_command,
    )
from subiquitycore.lsb_release import lsb_release

from subiquity.common.apidef import API
from subiquity.common.errorreport import ErrorReportKind
from subiquity.common.filesystem.actions import (
    DeviceAction,
    )
from subiquity.common.filesystem import (
    boot,
    gaps,
    labels,
    sizes,
)
from subiquity.common.filesystem.manipulator import (
    FilesystemManipulator,
)
from subiquity.common.types import (
    AddPartitionV2,
    Bootloader,
    Disk,
    GuidedChoice,
    GuidedChoiceV2,
    GuidedStorageResponse,
    GuidedStorageResponseV2,
    GuidedStorageTarget,
    GuidedStorageTargetReformat,
    GuidedStorageTargetResize,
    GuidedStorageTargetUseGap,
    ModifyPartitionV2,
    ProbeStatus,
    ReformatDisk,
    StorageEncryptionSupport,
    StorageResponse,
    StorageResponseV2,
    )
from subiquity.models.filesystem import (
    ActionRenderMode,
    align_up,
    align_down,
    _Device,
    Disk as ModelDisk,
    LVM_CHUNK_SIZE,
    Raid,
    )
from subiquity.server.controller import (
    SubiquityController,
    )
from subiquity.server import snapdapi
from subiquity.server.mounter import Mounter
from subiquity.server.types import InstallerChannels


log = logging.getLogger("subiquity.server.controllers.filesystem")
block_discover_log = logging.getLogger('block-discover')


system_defective_encryption_text = _("""
The model being installed requires TPM-backed encryption but this
system does not support it.
""")

system_multiple_volumes_text = _("""
The model being installed defines multiple volumes, which is not currently
supported.
""")

system_non_gpt_text = _("""
The model being installed defines a volume with a partition table type other
than GPT, which is not currently supported.
""")


class FilesystemController(SubiquityController, FilesystemManipulator):

    endpoint = API.storage

    autoinstall_key = "storage"
    autoinstall_schema = {'type': 'object'}  # ...
    model_name = "filesystem"

    _configured = False

    def __init__(self, app):
        self.ai_data = {}
        super().__init__(app)
        self.model.target = app.base_model.target
        if self.opts.dry_run and self.opts.bootloader:
            name = self.opts.bootloader.upper()
            self.model.bootloader = getattr(Bootloader, name)
        self.model.storage_version = self.opts.storage_version
        self._monitor = None
        self._errors = {}
        self._probe_once_task = SingleInstanceTask(
            self._probe_once, propagate_errors=False)
        self._probe_task = SingleInstanceTask(
            self._probe, propagate_errors=False, cancel_restart=False)
        self._get_system_task = SingleInstanceTask(self._get_system)
        self.supports_resilient_boot = False
        self.app.hub.subscribe(
            (InstallerChannels.CONFIGURED, 'source'),
            self._get_system_task.start_sync)
        self._system: Optional[snapdapi.SystemDetails] = None
        self._core_boot_classic_error: str = ''
        self._system_mounter: Optional[Mounter] = None
        self._role_to_device: Dict[snapdapi.Role: _Device] = {}

    def load_autoinstall_data(self, data):
        log.debug("load_autoinstall_data %s", data)
        if data is None:
            if not self.interactive():
                data = {
                    'layout': {
                        'name': 'lvm',
                        },
                    }
            else:
                data = {}
        log.debug("self.ai_data = %s", data)
        self.ai_data = data

    async def configured(self):
        self._configured = True
        await super().configured()
        self.stop_listening_udev()

    async def _mount_system(self):
        source_path = self.app.controllers.Source.source_path
        cur_systems_dir = '/var/lib/snapd/seed/systems'
        source_systems_dir = os.path.join(source_path, cur_systems_dir[1:])
        self._system_mounter = Mounter(self.app)
        await self._system_mounter.bind_mount_tree(
            source_systems_dir, cur_systems_dir)

    async def _unmount_system(self):
        if self._system_mounter is not None:
            await self._system_mounter.cleanup()
            self._system_mounter = None

    async def _get_system(self):
        await self._unmount_system()
        await self._mount_system()
        label = self.app.base_model.source.current.snapd_system_label
        if label is None:
            self._system = None
            return
        system = await self.app.snapdapi.v2.systems[label].GET()
        log.debug("got system %s", system)
        if len(system.volumes) == 0:
            # This means the system does not define a gadget or kernel
            # so isn't a core boot classic system.
            await self._unmount_system()
            return
        self._system = system
        if len(system.volumes) > 1:
            self._core_boot_classic_error = system_multiple_volumes_text
        [volume] = system.volumes.values()
        if volume.schema != 'gpt':
            self._core_boot_classic_error = system_non_gpt_text
        if system.storage_encryption.support == \
           StorageEncryptionSupport.DEFECTIVE:
            self._core_boot_classic_error = system_defective_encryption_text

    @with_context()
    async def apply_autoinstall_config(self, context=None):
        await self._start_task
        await self._probe_task.wait()
        await self._get_system_task.wait()
        if False in self._errors:
            raise self._errors[False][0]
        if True in self._errors:
            raise self._errors[True][0]
        self.convert_autoinstall_config(context=context)
        if not self.model.is_root_mounted():
            raise Exception("autoinstall config did not mount root")
        if self.model.needs_bootloader_partition():
            raise Exception(
                "autoinstall config did not create needed bootloader "
                "partition")

    def update_devices(self, device_map):
        for action in self.model._actions:
            path = device_map.get(action.id)
            if path is not None:
                log.debug("recording path %r for device %s", path, action.id)
                action.path = path

    def guided_direct(self, gap):
        spec = dict(fstype="ext4", mount="/")
        self.create_partition(device=gap.device, gap=gap, spec=spec)

    def guided_lvm(self, gap, lvm_options=None):
        device = gap.device
        part_align = device.alignment_data().part_align
        bootfs_size = align_up(sizes.get_bootfs_size(gap.size), part_align)
        gap_boot, gap_rest = gap.split(bootfs_size)
        spec = dict(fstype="ext4", mount='/boot')
        self.create_partition(device, gap_boot, spec)
        part = self.create_partition(device, gap_rest, dict(fstype=None))

        vg_name = 'ubuntu-vg'
        i = 0
        while self.model._one(type='lvm_volgroup', name=vg_name) is not None:
            i += 1
            vg_name = 'ubuntu-vg-{}'.format(i)
        spec = dict(name=vg_name, devices=set([part]))
        if lvm_options and lvm_options['encrypt']:
            spec['password'] = lvm_options['luks_options']['password']
        vg = self.create_volgroup(spec)
        # There's no point using LVM and unconditionally filling the
        # VG with a single LV, but we should use more of a smaller
        # disk to avoid the user running into out of space errors
        # earlier than they probably expect to.
        if vg.size < 10 * (1 << 30):
            # Use all of a small (<10G) disk.
            lv_size = vg.size
        elif vg.size < 20 * (1 << 30):
            # Use 10G of a smallish (<20G) disk.
            lv_size = 10 * (1 << 30)
        elif vg.size < 200 * (1 << 30):
            # Use half of a larger (<200G) disk.
            lv_size = vg.size // 2
        else:
            # Use at most 100G of a large disk.
            lv_size = 100 * (1 << 30)
        lv_size = align_down(lv_size, LVM_CHUNK_SIZE)
        self.create_logical_volume(
            vg=vg, spec=dict(
                size=lv_size,
                name="ubuntu-lv",
                fstype="ext4",
                mount="/",
                ))

    @functools.singledispatchmethod
    def start_guided(self, target: GuidedStorageTarget,
                     disk: ModelDisk) -> gaps.Gap:
        """Setup changes to the disk to prepare the gap that we will be
        doing a guided install into."""
        raise NotImplementedError(target)

    @start_guided.register
    def start_guided_reformat(self, target: GuidedStorageTargetReformat,
                              disk: ModelDisk) -> gaps.Gap:
        """Perform the reformat, and return the resulting gap."""
        self.reformat(disk, wipe='superblock-recursive')
        return gaps.largest_gap(disk)

    @start_guided.register
    def start_guided_use_gap(self, target: GuidedStorageTargetUseGap,
                             disk: ModelDisk) -> gaps.Gap:
        """Lookup the matching model gap."""
        return gaps.at_offset(disk, target.gap.offset)

    @start_guided.register
    def start_guided_resize(self, target: GuidedStorageTargetResize,
                            disk: ModelDisk) -> gaps.Gap:
        """Perform the resize of the target partition,
        and return the resulting gap."""
        partition = self.get_partition(disk, target.partition_number)
        part_align = disk.alignment_data().part_align
        new_size = align_up(target.new_size, part_align)
        if new_size > partition.size:
            raise Exception(f'Aligned requested size {new_size} too large')
        partition.size = new_size
        partition.resize = True
        # Calculating where that gap will be can be tricky due to alignment
        # needs and the possibility that we may be splitting a logical
        # partition, which needs an extra 1MiB spacer.
        gap = gaps.after(disk, partition.offset)
        if gap is None:
            pgs = gaps.parts_and_gaps(disk)
            raise Exception(f'gap not found after resize, pgs={pgs}')
        return gap

    def build_lvm_options(self, password):
        if password is None:
            return None
        else:
            return {
                'encrypt': True,
                'luks_options': {
                    'password': password,
                    },
                }

    def guided(self, choice: GuidedChoiceV2):
        self.model.guided_configuration = choice

        disk = self.model._one(id=choice.target.disk_id)
        gap = self.start_guided(choice.target, disk)
        if DeviceAction.TOGGLE_BOOT in DeviceAction.supported(disk):
            self.add_boot_disk(disk)
        # find what's left of the gap after adding boot
        gap = gap.within()
        if gap is None:
            raise Exception('failed to locate gap after adding boot')

        if choice.use_lvm:
            lvm_options = self.build_lvm_options(choice.password)
            self.guided_lvm(gap, lvm_options=lvm_options)
        else:
            self.guided_direct(gap)

    async def _probe_response(self, wait, resp_cls):
        if self._probe_task.task is None or not self._probe_task.task.done():
            if wait:
                await self._start_task
                await self._probe_task.wait()
            else:
                return resp_cls(status=ProbeStatus.PROBING)
        if True in self._errors:
            return resp_cls(
                status=ProbeStatus.FAILED,
                error_report=self._errors[True][1].ref())
        if self._get_system_task.task is None or \
           not self._get_system_task.task.done():
            if wait:
                await self._get_system_task.wait()
            else:
                return resp_cls(status=ProbeStatus.PROBING)
        return None

    def full_probe_error(self):
        if False in self._errors:
            return self._errors[False][1].ref()
        else:
            return None

    def _done_response(self):
        return StorageResponse(
            status=ProbeStatus.DONE,
            bootloader=self.model.bootloader,
            error_report=self.full_probe_error(),
            orig_config=self.model._orig_config,
            config=self.model._render_actions(mode=ActionRenderMode.ALL),
            blockdev=self.model._probe_data['blockdev'],
            dasd=self.model._probe_data.get('dasd', {}),
            storage_version=self.model.storage_version)

    async def GET(self, wait: bool = False, use_cached_result: bool = False) \
            -> StorageResponse:
        if not use_cached_result:
            probe_resp = await self._probe_response(wait, StorageResponse)
            if probe_resp is not None:
                return probe_resp
        return self._done_response()

    async def POST(self, config: list):
        log.debug(config)
        self.model._actions = self.model._actions_from_config(
            config, self.model._probe_data['blockdev'], is_probe_data=False)
        await self.configured()

    def get_guided_disks(self, check_boot=True, with_reformatting=False):
        disks = []
        for raid in self.model._all(type='raid'):
            if check_boot and not boot.can_be_boot_device(
                    raid, with_reformatting=with_reformatting):
                continue
            disks.append(raid)
        for disk in self.model._all(type='disk'):
            if check_boot and not boot.can_be_boot_device(
                    disk, with_reformatting=with_reformatting):
                continue
            cd = disk.constructed_device()
            if isinstance(cd, Raid):
                can_be_boot = False
                for v in cd._subvolumes:
                    if check_boot and boot.can_be_boot_device(
                            v, with_reformatting=with_reformatting):
                        can_be_boot = True
                if can_be_boot:
                    continue
            disks.append(disk)
        return disks

    async def guided_GET(self, wait: bool = False) -> GuidedStorageResponse:
        probe_resp = await self._probe_response(wait, GuidedStorageResponse)
        if probe_resp is not None:
            return probe_resp
        # This calculation is pretty much a hack and we should
        # actually think about it at some point (like: maybe the
        # source catalog should directly specify the minimum suitable
        # size?)
        min_size = 2*self.app.base_model.source.current.size + (1 << 30)
        disks = self.get_guided_disks(with_reformatting=True)
        se = None
        if self._system is not None:
            se = self._system.storage_encryption
            offsets_and_sizes = list(self._offsets_and_sizes_for_system())
            _structure, last_offset, last_size = offsets_and_sizes[-1]
            min_size = last_offset + last_size
        return GuidedStorageResponse(
            status=ProbeStatus.DONE,
            error_report=self.full_probe_error(),
            disks=[labels.for_client(d, min_size=min_size) for d in disks],
            core_boot_classic_error=self._core_boot_classic_error,
            storage_encryption=se)

    def _offsets_and_sizes_for_system(self):
        offset = self.model._partition_alignment_data['gpt'].min_start_offset
        [volume] = self._system.volumes.values()
        for structure in volume.structure:
            if structure.role == snapdapi.Role.MBR:
                continue
            if ',' not in structure.type:
                continue
            if structure.offset is not None:
                offset = structure.offset
            yield (structure, offset, structure.size)
            offset = offset + structure.size

    def _add_structure(self, *, disk: Disk, offset: int, size: int,
                       is_last: bool, structure: snapdapi.VolumeStructure):
        if structure.role == snapdapi.Role.SYSTEM_DATA and is_last:
            size = gaps.largest_gap(disk).size
        flag = ptable_uuid_to_flag_entry(structure.gpt_part_uuid())[0]
        part = self.model.add_partition(
            disk, offset=offset, size=size, flag=flag,
            partition_name=structure.name)
        if structure.filesystem:
            part.wipe = 'superblock'
            fs = self.model.add_filesystem(
                part, structure.filesystem, label=structure.label)
            if structure.role == snapdapi.Role.SYSTEM_DATA:
                self.model.add_mount(fs, '/')
            elif structure.role == snapdapi.Role.SYSTEM_BOOT:
                self.model.add_mount(fs, '/boot')
            elif flag == 'boot':
                self.model.add_mount(fs, '/boot/efi')
        self._role_to_device[structure.role] = part

    def apply_system(self, disk_id):
        disk = self.model._one(id=disk_id)
        self.reformat(disk)

        [volume] = self._system.volumes.values()

        for structure, offset, size in self._offsets_and_sizes_for_system():
            self._add_structure(
                disk=disk, offset=offset, size=size, structure=structure,
                is_last=structure == volume.structure[-1])
        disk._partitions.sort(key=lambda p: p.number)

    def _on_volumes(self) -> Dict[str, snapdapi.OnVolume]:
        # Return a value suitable for use as the 'on-volumes' part of a
        # SystemActionRequest.
        #
        # This must be run after curtin partitioning, which will result in a
        # call to update_devices which will have set .path on all block
        # devices.
        [(key, volume)] = self._system.volumes.items()
        on_volume = snapdapi.OnVolume.from_volume(volume)
        for on_volume_structure in on_volume.structure:
            role = on_volume_structure.role
            if role in self._role_to_device:
                on_volume_structure.device = self._role_to_device[role].path
        return {key: on_volume}

    @with_context(description="making system bootable")
    async def finish_install(self, context):
        label = self.app.base_model.source.current.snapd_system_label
        await snapdapi.post_and_wait(
            self.app.snapdapi,
            self.app.snapdapi.v2.systems[label].POST,
            snapdapi.SystemActionRequest(
                action=snapdapi.SystemAction.INSTALL,
                step=snapdapi.SystemActionStep.FINISH,
                on_volumes=self._on_volumes()))

    async def guided_POST(self, data: GuidedChoice) -> StorageResponse:
        log.debug(data)
        if self._system is not None:
            self.apply_system(data.disk_id)
            await self.configured()
        else:
            self.guided(GuidedChoiceV2.from_guided_choice(data))
        return self._done_response()

    async def reset_POST(self, context, request) -> StorageResponse:
        log.info("Resetting Filesystem model")
        self.model.reset()
        return await self.GET(context)

    async def has_rst_GET(self) -> bool:
        search = '/sys/module/ahci/drivers/pci:ahci/*/remapped_nvme'
        for remapped_nvme in glob.glob(search):
            with open(remapped_nvme, 'r') as f:
                if int(f.read()) > 0:
                    return True
        return False

    async def has_bitlocker_GET(self) -> List[Disk]:
        '''list of Disks that contain a partition that is BitLockered'''
        bitlockered_disks = []
        for disk in self.model.all_disks():
            for part in disk.partitions():
                fs = part.fs()
                if not fs:
                    continue
                fstype = fs.fstype
                if fstype == "BitLocker":
                    bitlockered_disks.append(disk)
                    break
        return [labels.for_client(disk) for disk in bitlockered_disks]

    def get_partition(self, disk, number):
        for p in disk.partitions():
            if p.number == number:
                return p
        raise ValueError(f'Partition {number} on {disk.id} not found')

    def calculate_suggested_install_min(self):
        source_min = self.app.base_model.source.current.size
        align = max((pa.part_align
                     for pa in self.model._partition_alignment_data.values()))
        return sizes.calculate_suggested_install_min(source_min, align)

    async def get_v2_storage_response(self, model, wait):
        probe_resp = await self._probe_response(wait, StorageResponseV2)
        if probe_resp is not None:
            return probe_resp
        disks = model._all(type='disk')
        minsize = self.calculate_suggested_install_min()
        return StorageResponseV2(
                status=ProbeStatus.DONE,
                disks=[labels.for_client(d) for d in disks],
                need_root=not model.is_root_mounted(),
                need_boot=model.needs_bootloader_partition(),
                install_minimum_size=minsize,
                )

    async def v2_GET(self, wait: bool = False) -> StorageResponseV2:
        return await self.get_v2_storage_response(self.model, wait)

    async def v2_POST(self) -> StorageResponseV2:
        await self.configured()
        return await self.v2_GET()

    async def v2_orig_config_GET(self) -> StorageResponseV2:
        model = self.model.get_orig_model()
        return await self.get_v2_storage_response(model, False)

    async def v2_reset_POST(self) -> StorageResponseV2:
        log.info("Resetting Filesystem model")
        self.model.reset()
        return await self.v2_GET()

    async def v2_guided_GET(self, wait: bool = False) \
            -> GuidedStorageResponseV2:
        """Acquire a list of possible guided storage configuration scenarios.
        Results are sorted by the size of the space potentially available to
        the install."""
        probe_resp = await self._probe_response(wait, GuidedStorageResponseV2)
        if probe_resp is not None:
            return probe_resp

        scenarios = []
        install_min = self.calculate_suggested_install_min()

        for disk in self.get_guided_disks(with_reformatting=True):
            if disk.size >= install_min:
                reformat = GuidedStorageTargetReformat(disk_id=disk.id)
                scenarios.append((disk.size, reformat))

        for disk in self.get_guided_disks(with_reformatting=False):
            if len(disk.partitions()) < 1:
                # On an empty disk, don't bother to offer it with UseGap, as
                # it's basically the same as the Reformat case.
                continue
            gap = gaps.largest_gap(disk)
            if gap is not None and gap.size >= install_min:
                api_gap = labels.for_client(gap)
                use_gap = GuidedStorageTargetUseGap(
                        disk_id=disk.id,
                        gap=api_gap)
                scenarios.append((gap.size, use_gap))

        for disk in self.get_guided_disks(check_boot=False):
            part_align = disk.alignment_data().part_align
            for partition in disk.partitions():
                vals = sizes.calculate_guided_resize(
                        partition.estimated_min_size, partition.size,
                        install_min, part_align=part_align)
                if vals is None:
                    continue
                if not boot.can_be_boot_device(
                        disk, resize_partition=partition,
                        with_reformatting=False):
                    continue
                resize = GuidedStorageTargetResize.from_recommendations(
                        partition, vals)
                scenarios.append((vals.install_max, resize))

        scenarios.sort(reverse=True, key=lambda x: x[0])
        return GuidedStorageResponseV2(
                status=ProbeStatus.DONE,
                configured=self.model.guided_configuration,
                possible=[s[1] for s in scenarios])

    async def v2_guided_POST(self, data: GuidedChoiceV2) \
            -> GuidedStorageResponseV2:
        log.debug(data)
        self.guided(data)
        return await self.v2_guided_GET()

    async def v2_reformat_disk_POST(self, data: ReformatDisk) \
            -> StorageResponseV2:
        self.reformat(self.model._one(id=data.disk_id), data.ptable)
        return await self.v2_GET()

    async def v2_add_boot_partition_POST(self, disk_id: str) \
            -> StorageResponseV2:
        disk = self.model._one(id=disk_id)
        if boot.is_boot_device(disk):
            raise ValueError('device already has bootloader partition')
        if DeviceAction.TOGGLE_BOOT not in DeviceAction.supported(disk):
            raise ValueError("disk does not support boot partiton")
        self.add_boot_disk(disk)
        return await self.v2_GET()

    async def v2_add_partition_POST(self, data: AddPartitionV2) \
            -> StorageResponseV2:
        log.debug(data)
        if data.partition.format is None:
            raise ValueError('add_partition must supply format')
        if data.partition.boot is not None:
            raise ValueError('add_partition does not support changing boot')
        disk = self.model._one(id=data.disk_id)
        requested_size = data.partition.size or 0
        if requested_size > data.gap.size:
            raise ValueError('new partition too large')
        if requested_size < 1:
            requested_size = data.gap.size
        spec = {
            'size': requested_size,
            'fstype': data.partition.format,
            'mount': data.partition.mount,
        }

        gap = gaps.at_offset(disk, data.gap.offset).split(requested_size)[0]
        self.create_partition(disk, gap, spec, wipe='superblock')
        return await self.v2_GET()

    async def v2_delete_partition_POST(self, data: ModifyPartitionV2) \
            -> StorageResponseV2:
        log.debug(data)
        disk = self.model._one(id=data.disk_id)
        partition = self.get_partition(disk, data.partition.number)
        self.delete_partition(partition)
        return await self.v2_GET()

    async def v2_edit_partition_POST(self, data: ModifyPartitionV2) \
            -> StorageResponseV2:
        log.debug(data)
        disk = self.model._one(id=data.disk_id)
        partition = self.get_partition(disk, data.partition.number)
        if data.partition.size not in (None, partition.size) \
                and self.app.opts.storage_version < 2:
            raise ValueError('edit_partition does not support changing size')
        if data.partition.boot is not None \
                and data.partition.boot != partition.boot:
            raise ValueError('edit_partition does not support changing boot')
        spec = {'mount': data.partition.mount or partition.mount}
        if data.partition.format is not None:
            if data.partition.format != partition.original_fstype():
                if data.partition.wipe is None:
                    raise ValueError(
                        'changing partition format requires a wipe value')
            spec['fstype'] = data.partition.format
        if data.partition.size is not None:
            spec['size'] = data.partition.size
        spec['wipe'] = data.partition.wipe
        self.partition_disk_handler(disk, spec, partition=partition)
        return await self.v2_GET()

    @with_context(name='probe_once', description='restricted={restricted}')
    async def _probe_once(self, *, context, restricted):
        if restricted:
            probe_types = {'blockdev'}
            fname = 'probe-data-restricted.json'
            key = "ProbeDataRestricted"
        else:
            probe_types = {'defaults', 'filesystem_sizing'}
            if self.app.opts.use_os_prober:
                probe_types |= {'os'}
            fname = 'probe-data.json'
            key = "ProbeData"
        storage = await run_in_thread(
            self.app.prober.get_storage, probe_types)
        # It is possible for the user to submit filesystem config
        # while a probert probe is running. We don't want to overwrite
        # the users config with a blank one if this happens! (See
        # https://bugs.launchpad.net/bugs/1954848).
        if self._configured:
            return
        fpath = os.path.join(self.app.block_log_dir, fname)
        with open(fpath, 'w') as fp:
            json.dump(storage, fp, indent=4)
        self.app.note_file_for_apport(key, fpath)
        self.model.load_probe_data(storage)

    @with_context()
    async def _probe(self, *, context=None):
        self._errors = {}
        for (restricted, kind) in [
                (False, ErrorReportKind.BLOCK_PROBE_FAIL),
                (True,  ErrorReportKind.DISK_PROBE_FAIL),
                ]:
            try:
                await self._probe_once_task.start(
                    context=context, restricted=restricted)
                # We wait on the task directly here, not
                # self._probe_once_task.wait as if _probe_once_task
                # gets cancelled, we should be cancelled too.
                if platform.machine() == 'riscv64':
                    # block probing is taking much longer on RISC-V - but why?
                    timeout = 60.0
                else:
                    timeout = 15.0
                await asyncio.wait_for(self._probe_once_task.task, timeout)
            except asyncio.CancelledError:
                # asyncio.CancelledError is a subclass of Exception in
                # Python 3.6 (sadface)
                raise
            except Exception as exc:
                block_discover_log.exception(
                    "block probing failed restricted=%s", restricted)
                report = self.app.make_apport_report(kind, "block probing")
                if report is not None:
                    self._errors[restricted] = (exc, report)
                continue
            break

    def run_autoinstall_guided(self, layout):
        name = layout['name']
        mode = layout.get('mode', 'reformat_disk')
        self.validate_layout_mode(mode)

        if mode == 'reformat_disk':
            match = layout.get("match", {'size': 'largest'})
            disk = self.model.disk_for_match(self.model.all_disks(), match)
            target = GuidedStorageTargetReformat(disk_id=disk.id)
        elif mode == 'use_gap':
            bootable = [d for d in self.model.all_disks()
                        if boot.can_be_boot_device(d, with_reformatting=False)]
            gap = gaps.largest_gap(bootable)
            if not gap:
                raise Exception("autoinstall cannot configure storage "
                                "- no gap found large enough for install")
            target = GuidedStorageTargetUseGap(disk_id=gap.device.id, gap=gap)

        log.info(f'autoinstall: running guided {name} install in mode {mode} '
                 f'using {target}')
        use_lvm = name == 'lvm'
        self.guided(GuidedChoiceV2(target=target, use_lvm=use_lvm))

    def validate_layout_mode(self, mode):
        if mode not in ('reformat_disk', 'use_gap'):
            raise ValueError(f'Unknown layout mode {mode}')

    @with_context()
    def convert_autoinstall_config(self, context=None):
        log.debug("self.ai_data = %s", self.ai_data)
        if 'layout' in self.ai_data:
            if 'config' in self.ai_data:
                log.warning("The 'storage' section should not contain both "
                            "'layout' and 'config', using 'layout'")
            self.run_autoinstall_guided(self.ai_data['layout'])
        elif 'config' in self.ai_data:
            self.model.apply_autoinstall_config(self.ai_data['config'])
            self.model.grub = self.ai_data.get('grub')
            self.model.swap = self.ai_data.get('swap')

    def start(self):
        if self.model.bootloader == Bootloader.PREP:
            self.supports_resilient_boot = False
        else:
            release = lsb_release(dry_run=self.app.opts.dry_run)['release']
            self.supports_resilient_boot = release >= '20.04'
        self._start_task = schedule_task(self._start())

    async def _start(self):
        context = pyudev.Context()
        self._monitor = pyudev.Monitor.from_netlink(context)
        self._monitor.filter_by(subsystem='block')
        self._monitor.enable_receiving()
        self.start_listening_udev()
        await self._probe_task.start()

    def start_listening_udev(self):
        loop = asyncio.get_running_loop()
        loop.add_reader(self._monitor.fileno(), self._udev_event)

    def stop_listening_udev(self):
        loop = asyncio.get_running_loop()
        loop.remove_reader(self._monitor.fileno())

    def _udev_event(self):
        cp = run_command(['udevadm', 'settle', '-t', '0'])
        if cp.returncode != 0:
            log.debug("waiting 0.1 to let udev event queue settle")
            self.stop_listening_udev()
            loop = asyncio.get_running_loop()
            loop.call_later(0.1, self.start_listening_udev)
            return
        # Drain the udev events in the queue -- if we stopped listening to
        # allow udev to settle, it's good bet there is more than one event to
        # process and we don't want to kick off a full block probe for each
        # one.  It's a touch unfortunate that pyudev doesn't have a
        # non-blocking read so we resort to select().
        while select.select([self._monitor.fileno()], [], [], 0)[0]:
            action, dev = self._monitor.receive_device()
            log.debug("_udev_event %s %s", action, dev)
        try:
            self._probe_task.start_sync()
        except TaskAlreadyRunningError:
            log.debug('Skipping run of Probert - probe run already active')
        else:
            log.debug('Triggered Probert run on udev event')

    def make_autoinstall(self):
        rendered = self.model.render()
        r = {
            'config': rendered['storage']['config']
            }
        if 'swap' in rendered:
            r['swap'] = rendered['swap']
        return r
