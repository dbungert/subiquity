# Copyright 2019 Canonical, Ltd.
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
import unittest

import attr

from subiquity.common.filesystem.actions import (
    DeviceAction,
    )
from subiquity.common.filesystem import gaps, sizes
from subiquity.common.filesystem.manipulator import FilesystemManipulator
from subiquity.models.tests.test_filesystem import (
    make_disk,
    make_model,
    make_partition,
    make_filesystem,
    )
from subiquity.models.filesystem import (
    Bootloader,
    MiB,
    Partition,
    )


def make_manipulator(bootloader=None):
    manipulator = FilesystemManipulator()
    manipulator.model = make_model(bootloader)
    manipulator.supports_resilient_boot = True
    return manipulator


def make_manipulator_and_disk(bootloader=None):
    manipulator = make_manipulator(bootloader)
    return manipulator, make_disk(manipulator.model)


@attr.s(auto_attribs=True)
class MoveResize:
    part: Partition
    offset: int
    size: int


def Move(part, offset):
    return MoveResize(part, offset, 0)


def Resize(part, size):
    return MoveResize(part, 0, size)


def Unchanged(part):
    return MoveResize(part, 0, 0)


@attr.s(auto_attribs=True)
class Create:
    offset: int
    size: int


class TestFilesystemManipulator(unittest.TestCase):

    def test_delete_encrypted_vg(self):
        manipulator, disk = make_manipulator_and_disk()
        spec = {
            'password': 'passw0rd',
            'devices': {disk},
            'name': 'vg0',
            }
        vg = manipulator.create_volgroup(spec)
        manipulator.delete_volgroup(vg)
        dm_crypts = [
            a for a in manipulator.model._actions if a.type == 'dm_crypt']
        self.assertEqual(dm_crypts, [])

    def test_can_only_add_boot_once(self):
        # This is really testing model code but it's much easier to test with a
        # manipulator around.
        for bl in Bootloader:
            manipulator, disk = make_manipulator_and_disk(bl)
            if DeviceAction.TOGGLE_BOOT not in DeviceAction.supported(disk):
                continue
            manipulator.add_boot_disk(disk)
            self.assertFalse(
                DeviceAction.TOGGLE_BOOT.can(disk)[0],
                "add_boot_disk(disk) did not make _can_TOGGLE_BOOT false "
                "with bootloader {}".format(bl))

    def assertIsMountedAtBootEFI(self, device):
        efi_mnts = device._m._all(type="mount", path="/boot/efi")
        self.assertEqual(len(efi_mnts), 1)
        self.assertEqual(efi_mnts[0].device.volume, device)

    def assertNotMounted(self, device):
        if device.fs():
            self.assertIs(device.fs().mount(), None)

    def add_existing_boot_partition(self, manipulator, disk):
        if manipulator.model.bootloader == Bootloader.BIOS:
            part = manipulator.model.add_partition(
                disk, size=1 << 20, offset=0, flag="bios_grub")
        elif manipulator.model.bootloader == Bootloader.UEFI:
            part = manipulator.model.add_partition(
                disk, size=512 << 20, offset=0, flag="boot")
        elif manipulator.model.bootloader == Bootloader.PREP:
            part = manipulator.model.add_partition(
                disk, size=8 << 20, offset=0, flag="prep")
        part.preserve = True
        return part

    def assertIsBootDisk(self, manipulator, disk):
        if manipulator.model.bootloader == Bootloader.BIOS:
            self.assertTrue(disk.grub_device)
            self.assertEqual(disk.partitions()[0].flag, "bios_grub")
        elif manipulator.model.bootloader == Bootloader.UEFI:
            for part in disk.partitions():
                if part.flag == "boot" and part.grub_device:
                    return
            self.fail("{} is not a boot disk".format(disk))
        elif manipulator.model.bootloader == Bootloader.PREP:
            for part in disk.partitions():
                if part.flag == "prep" and part.grub_device:
                    self.assertEqual(part.wipe, 'zero')
                    return
            self.fail("{} is not a boot disk".format(disk))

    def assertIsNotBootDisk(self, manipulator, disk):
        if manipulator.model.bootloader == Bootloader.BIOS:
            self.assertFalse(disk.grub_device)
        elif manipulator.model.bootloader == Bootloader.UEFI:
            for part in disk.partitions():
                if part.flag == "boot" and part.grub_device:
                    self.fail("{} is a boot disk".format(disk))
        elif manipulator.model.bootloader == Bootloader.PREP:
            for part in disk.partitions():
                if part.flag == "prep" and part.grub_device:
                    self.fail("{} is a boot disk".format(disk))

    def test_boot_disk_resilient(self):
        for bl in Bootloader:
            if bl == Bootloader.NONE:
                continue
            manipulator = make_manipulator(bl)
            manipulator.supports_resilient_boot = True

            disk1 = make_disk(manipulator.model, preserve=False)
            disk2 = make_disk(manipulator.model, preserve=False)
            gap = gaps.largest_gap(disk2)
            disk2p1 = manipulator.model.add_partition(
                disk2, size=gap.size, offset=gap.offset)

            manipulator.add_boot_disk(disk1)
            self.assertIsBootDisk(manipulator, disk1)
            if bl == Bootloader.UEFI:
                self.assertIsMountedAtBootEFI(disk1.partitions()[0])

            size_before = disk2p1.size
            manipulator.add_boot_disk(disk2)
            self.assertIsBootDisk(manipulator, disk1)
            self.assertIsBootDisk(manipulator, disk2)
            if bl == Bootloader.UEFI:
                self.assertIsMountedAtBootEFI(disk1.partitions()[0])
                self.assertNotMounted(disk2.partitions()[0])
            self.assertEqual(len(disk2.partitions()), 2)
            self.assertEqual(disk2.partitions()[1], disk2p1)
            self.assertEqual(
                disk2.partitions()[0].size + disk2p1.size, size_before)

            manipulator.remove_boot_disk(disk1)
            self.assertIsNotBootDisk(manipulator, disk1)
            self.assertIsBootDisk(manipulator, disk2)
            if bl == Bootloader.UEFI:
                self.assertIsMountedAtBootEFI(disk2.partitions()[0])
            self.assertEqual(len(disk1.partitions()), 0)

            manipulator.remove_boot_disk(disk2)
            self.assertIsNotBootDisk(manipulator, disk2)
            self.assertEqual(len(disk2.partitions()), 1)
            self.assertEqual(disk2p1.size, size_before)

    def test_boot_disk_no_resilient(self):
        for bl in Bootloader:
            if bl == Bootloader.NONE:
                continue
            manipulator = make_manipulator(bl)
            manipulator.supports_resilient_boot = False

            disk1 = make_disk(manipulator.model, preserve=False)
            disk2 = make_disk(manipulator.model, preserve=False)
            gap = gaps.largest_gap(disk2)
            disk2p1 = manipulator.model.add_partition(
                disk2, size=gap.size, offset=gap.offset)

            manipulator.add_boot_disk(disk1)
            self.assertIsBootDisk(manipulator, disk1)
            if bl == Bootloader.UEFI:
                self.assertIsMountedAtBootEFI(disk1.partitions()[0])

            size_before = disk2p1.size
            manipulator.add_boot_disk(disk2)
            self.assertIsNotBootDisk(manipulator, disk1)
            self.assertIsBootDisk(manipulator, disk2)
            if bl == Bootloader.UEFI:
                self.assertIsMountedAtBootEFI(disk2.partitions()[0])
            self.assertEqual(len(disk2.partitions()), 2)
            self.assertEqual(disk2.partitions()[1], disk2p1)
            self.assertEqual(
                disk2.partitions()[0].size + disk2p1.size, size_before)

    def test_boot_disk_existing(self):
        for bl in Bootloader:
            if bl == Bootloader.NONE:
                continue
            manipulator = make_manipulator(bl)

            disk1 = make_disk(manipulator.model, preserve=True)
            part = self.add_existing_boot_partition(manipulator, disk1)

            wipe_before = part.wipe
            manipulator.add_boot_disk(disk1)
            self.assertIsBootDisk(manipulator, disk1)
            if bl == Bootloader.UEFI:
                self.assertIsMountedAtBootEFI(part)

            manipulator.remove_boot_disk(disk1)
            self.assertIsNotBootDisk(manipulator, disk1)
            self.assertEqual(len(disk1.partitions()), 1)
            self.assertEqual(part.wipe, wipe_before)
            if bl == Bootloader.UEFI:
                self.assertNotMounted(part)

    def test_mounting_partition_makes_boot_disk(self):
        manipulator = make_manipulator(Bootloader.UEFI)
        disk1 = make_disk(manipulator.model, preserve=True)
        disk1p1 = manipulator.model.add_partition(
            disk1, size=512 << 20, offset=0, flag="boot")
        disk1p1.preserve = True
        disk1p2 = manipulator.model.add_partition(
            disk1, size=8192 << 20, offset=513 << 20)
        disk1p2.preserve = True
        manipulator.partition_disk_handler(
            disk1, {'fstype': 'ext4', 'mount': '/'}, partition=disk1p2)
        efi_mnt = manipulator.model._mount_for_path("/boot/efi")
        self.assertEqual(efi_mnt.device.volume, disk1p1)

    def test_add_boot_has_valid_offset(self):
        for bl in Bootloader:
            if bl == Bootloader.NONE:
                continue
            manipulator = make_manipulator(bl)

            disk1 = make_disk(manipulator.model, preserve=True)
            manipulator.add_boot_disk(disk1)
            part = gaps.parts_and_gaps(disk1)[0]
            self.assertEqual(1024 * 1024, part.offset)

    @contextlib.contextmanager
    def assertPartitionOperations(self, disk, *ops):

        existing_parts = set(disk.partitions())
        part_details = {}

        for op in ops:
            if isinstance(op, MoveResize):
                part_details[op.part] = (
                    op.part.offset + op.offset, op.part.size + op.size)

        try:
            yield
        finally:
            new_parts = set(disk.partitions())
            created_parts = new_parts - existing_parts
            try:
                for op in ops:
                    if isinstance(op, MoveResize):
                        new_parts.remove(op.part)
                        self.assertEqual(
                            part_details[op.part],
                            (op.part.offset, op.part.size))
                    else:
                        for part in created_parts:
                            lhs = (part.offset, part.size)
                            rhs = (op.offset, op.size)
                            if lhs == rhs:
                                created_parts.remove(part)
                                new_parts.remove(part)
                                break
                        else:
                            self.fail("did not find new partition")
            except AssertionError as exc:
                self.fail("Failure checking {}:\n{}".format(op, exc))
            if new_parts:
                self.fail("no assertion about {}".format(new_parts))

    def test_add_boot_BIOS_empty(self):
        manipulator = make_manipulator(Bootloader.BIOS)
        disk = make_disk(manipulator.model, preserve=True)
        with self.assertPartitionOperations(
                disk,
                Create(
                    offset=disk.alignment_data().min_start_offset,
                    size=sizes.BIOS_GRUB_SIZE_BYTES),
                ):
            manipulator.add_boot_disk(disk)

        self.assertIsBootDisk(manipulator, disk)

    def test_add_boot_BIOS_full(self):
        manipulator = make_manipulator(Bootloader.BIOS)
        disk = make_disk(manipulator.model, preserve=True)
        part = make_partition(
            manipulator.model, disk, size=gaps.largest_gap_size(disk))

        with self.assertPartitionOperations(
                disk,
                Create(
                    offset=disk.alignment_data().min_start_offset,
                    size=sizes.BIOS_GRUB_SIZE_BYTES),
                MoveResize(
                    part=part,
                    offset=sizes.BIOS_GRUB_SIZE_BYTES,
                    size=-sizes.BIOS_GRUB_SIZE_BYTES),
                ):
            manipulator.add_boot_disk(disk)

        self.assertIsBootDisk(manipulator, disk)

    def test_add_boot_BIOS_half_full(self):
        manipulator = make_manipulator(Bootloader.BIOS)
        disk = make_disk(manipulator.model, preserve=True)
        part = make_partition(
            manipulator.model, disk, size=gaps.largest_gap_size(disk)//2)
        with self.assertPartitionOperations(
                disk,
                Create(
                    offset=disk.alignment_data().min_start_offset,
                    size=sizes.BIOS_GRUB_SIZE_BYTES),
                Move(
                    part=part, offset=sizes.BIOS_GRUB_SIZE_BYTES),
                ):
            manipulator.add_boot_disk(disk)
        self.assertIsBootDisk(manipulator, disk)

    def test_add_boot_BIOS_full_resizes_larger(self):
        manipulator = make_manipulator(Bootloader.BIOS)
        # 2002MiB so that the space available for partitioning (2000MiB)
        # divided by 4 is an whole number of megabytes.
        disk = make_disk(manipulator.model, preserve=True, size=2002*MiB)
        part_smaller = make_partition(
            manipulator.model, disk, size=gaps.largest_gap_size(disk)//4)
        part_larger = make_partition(
            manipulator.model, disk, size=gaps.largest_gap_size(disk))
        with self.assertPartitionOperations(
                disk,
                Create(
                    offset=disk.alignment_data().min_start_offset,
                    size=sizes.BIOS_GRUB_SIZE_BYTES),
                Move(
                    part=part_smaller, offset=sizes.BIOS_GRUB_SIZE_BYTES),
                MoveResize(
                    part=part_larger,
                    offset=sizes.BIOS_GRUB_SIZE_BYTES,
                    size=-sizes.BIOS_GRUB_SIZE_BYTES),
                ):
            manipulator.add_boot_disk(disk)
        self.assertIsBootDisk(manipulator, disk)

    def DONT_test_add_boot_BIOS_preserved(self):  # needs v2 partitioning
        manipulator = make_manipulator(Bootloader.BIOS)
        disk = make_disk(manipulator.model, preserve=True)
        half_size = gaps.largest_gap_size(disk)//2
        part = make_partition(
            manipulator.model, disk, size=half_size, offset=half_size)
        with self.assertPartitionOperations(
                disk,
                Create(
                    offset=disk.alignment_data().min_start_offset,
                    size=sizes.BIOS_GRUB_SIZE_BYTES),
                Unchanged(part=part),
                ):
            manipulator.add_boot_disk(disk)

    def _test_add_boot_empty(self, manipulator, disk, size):
        with self.assertPartitionOperations(
                disk,
                Create(
                    offset=disk.alignment_data().min_start_offset,
                    size=size),
                ):
            manipulator.add_boot_disk(disk)
        self.assertIsBootDisk(manipulator, disk)

    def test_add_boot_UEFI_empty(self):
        manipulator = make_manipulator(Bootloader.UEFI)
        disk = make_disk(manipulator.model, preserve=True)
        self._test_add_boot_empty(
            manipulator, disk, sizes.get_efi_size(disk))

    def test_add_boot_PREP_empty(self):
        manipulator = make_manipulator(Bootloader.PREP)
        disk = make_disk(manipulator.model, preserve=True)
        self._test_add_boot_empty(
            manipulator, disk, sizes.PREP_GRUB_SIZE_BYTES)

    def _test_add_boot_full(self, manipulator, disk, size):
        part = make_partition(
            manipulator.model, disk, size=gaps.largest_gap_size(disk))
        with self.assertPartitionOperations(
                disk,
                Create(
                    offset=disk.alignment_data().min_start_offset,
                    size=size),
                MoveResize(
                    part=part,
                    offset=size,
                    size=-size),
                ):
            manipulator.add_boot_disk(disk)
        self.assertIsBootDisk(manipulator, disk)

    def test_add_boot_UEFI_full(self):
        manipulator = make_manipulator(Bootloader.UEFI)
        disk = make_disk(manipulator.model, preserve=True)
        self._test_add_boot_full(
            manipulator, disk, sizes.get_efi_size(disk))

    def test_add_boot_PREP_full(self):
        manipulator = make_manipulator(Bootloader.PREP)
        disk = make_disk(manipulator.model, preserve=True)
        self._test_add_boot_full(
            manipulator, disk, sizes.PREP_GRUB_SIZE_BYTES)

    def _test_add_boot_half_full(self, manipulator, disk, size):
        part = make_partition(
            manipulator.model, disk, size=gaps.largest_gap_size(disk)//2)
        with self.assertPartitionOperations(
                disk,
                Unchanged(part=part),
                Create(
                    offset=part.offset + part.size,
                    size=size),
                ):
            manipulator.add_boot_disk(disk)
        self.assertIsBootDisk(manipulator, disk)

    def test_add_boot_UEFI_half_full(self):
        manipulator = make_manipulator(Bootloader.UEFI)
        disk = make_disk(manipulator.model, preserve=True)
        self._test_add_boot_half_full(
            manipulator, disk, sizes.get_efi_size(disk))

    def test_add_boot_PREP_half_full(self):
        manipulator = make_manipulator(Bootloader.PREP)
        disk = make_disk(manipulator.model, preserve=True)
        self._test_add_boot_half_full(
            manipulator, disk, sizes.PREP_GRUB_SIZE_BYTES)

    def _test_add_boot_full_resizes_larger(self, manipulator, disk, size):
        part_smaller = make_partition(
            manipulator.model, disk, size=gaps.largest_gap_size(disk)//4)
        part_larger = make_partition(
            manipulator.model, disk, size=gaps.largest_gap_size(disk))
        with self.assertPartitionOperations(
                disk,
                Unchanged(part_smaller),
                Create(
                    offset=part_smaller.offset + part_smaller.size,
                    size=size),
                MoveResize(
                    part=part_larger,
                    offset=size,
                    size=-size),
                ):
            manipulator.add_boot_disk(disk)
        self.assertIsBootDisk(manipulator, disk)

    def test_add_boot_UEFI_full_resizes_larger(self):
        manipulator = make_manipulator(Bootloader.UEFI)
        # 2002MiB so that the space available for partitioning (2000MiB)
        # divided by 4 is an whole number of megabytes.
        disk = make_disk(manipulator.model, preserve=True, size=2002*MiB)
        self._test_add_boot_full_resizes_larger(
            manipulator, disk, sizes.get_efi_size(disk))

    def test_add_boot_PREP_full_resizes_larger(self):
        manipulator = make_manipulator(Bootloader.PREP)
        # 2002MiB so that the space available for partitioning (2000MiB)
        # divided by 4 is an whole number of megabytes.
        disk = make_disk(manipulator.model, preserve=True, size=2002*MiB)
        self._test_add_boot_full_resizes_larger(
            manipulator, disk, sizes.PREP_GRUB_SIZE_BYTES)


class TestReformat(unittest.TestCase):
    def setUp(self):
        self.manipulator = make_manipulator()

    def test_reformat_default(self):
        disk = make_disk(self.manipulator.model, ptable=None)
        self.manipulator.reformat(disk)
        self.assertEqual(None, disk.ptable)

    def test_reformat_keep_current(self):
        disk = make_disk(self.manipulator.model, ptable='msdos')
        self.manipulator.reformat(disk)
        self.assertEqual('msdos', disk.ptable)

    def test_reformat_to_gpt(self):
        disk = make_disk(self.manipulator.model, ptable=None)
        self.manipulator.reformat(disk, 'gpt')
        self.assertEqual('gpt', disk.ptable)

    def test_reformat_to_msdos(self):
        disk = make_disk(self.manipulator.model, ptable=None)
        self.manipulator.reformat(disk, 'msdos')
        self.assertEqual('msdos', disk.ptable)


class TestCanResize(unittest.TestCase):
    def setUp(self):
        self.manipulator = make_manipulator()
        self.manipulator.model._probe_data = {}

    def test_resize_unpreserved(self):
        disk = make_disk(self.manipulator.model, ptable=None)
        part = make_partition(self.manipulator.model, disk, preserve=False)
        self.assertTrue(self.manipulator.can_resize_partition(part))

    def test_resize_ext4(self):
        disk = make_disk(self.manipulator.model, ptable=None)
        part = make_partition(self.manipulator.model, disk, preserve=True)
        make_filesystem(self.manipulator.model, partition=part, fstype='ext4')
        self.assertTrue(self.manipulator.can_resize_partition(part))

    def test_resize_invalid(self):
        disk = make_disk(self.manipulator.model, ptable=None)
        part = make_partition(self.manipulator.model, disk, preserve=True)
        make_filesystem(self.manipulator.model, partition=part, fstype='asdf')
        self.assertFalse(self.manipulator.can_resize_partition(part))
