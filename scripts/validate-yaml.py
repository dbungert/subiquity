#!/usr/bin/python3
import sys

import yaml


class StorageChecker:

    def __init__(self):
        self.actions = {}
        self.unmounted_swap_ids = set()
        self.path_to_mount = {}

    def _check_partition(self, action):
        assert 'device' in action
        assert 'size' in action
        size = str(action['size'])
        size_units = ['B', 'KB', 'K', 'MB', 'M', 'GB', 'G', 'TB', 'T', '%']
        valid_unit = any(unit in size for unit in size_units)
        assert size == '-1' or valid_unit or int(size) % 512 == 0
        assert 'number' in action
        assert action['device'] in self.actions
        assert 'ptable' in self.actions[action['device']]
        if action.get('flag') in ('boot', 'bios_grub', 'prep'):
            assert self.actions[action['device']]['type'] in ('disk', 'raid')

    def _check_format(self, action):
        assert 'volume' in action
        assert action['volume'] in self.actions
        if action['fstype'] == 'swap':
            self.unmounted_swap_ids.add(action['id'])

    def _check_mount(self, action):
        assert 'device' in action
        assert action['device'] in self.actions
        if not action.get('path') or action.get('path') == 'none':
            assert self.actions[action['device']]['fstype'] == "swap"
            self.unmounted_swap_ids.remove(action['device'])
        else:
            self.path_to_mount[action['path']] = action

    def _check_raid(self, action):
        assert 'raidlevel' in action
        for d in action['devices']:
            assert d in self.actions

    def _check_lvm_volgroup(self, action):
        assert 'name' in action
        for d in action['devices']:
            assert d in self.actions

    def _check_lvm_partition(self, action):
        assert 'name' in action
        assert 'size' in action
        assert isinstance(action['size'], str)
        assert int(action['size'][:-1]) % 512 == 0
        assert action['volgroup'] in self.actions

    def check(self, action):
        assert 'type' in action
        assert 'id' in action
        m = getattr(self, '_check_{type}'.format(**action), None)
        if m:
            m(action)
        self.actions[action['id']] = action

    def final_checks(self):
        # Check we mounted all the swap devices
        if len(self.unmounted_swap_ids) > 0:
            raise Exception(
                "some swap formats had no mounts: {}".format(
                    self.unmounted_swap_ids))

        # Check we mounted /
        assert '/' in self.path_to_mount


def validate_yaml(filename):
    with open(filename) as fp:
        config = yaml.safe_load(fp)

    storage_checker = StorageChecker()

    actions = []
    for path in sys.argv[1:]:
        config = yaml.safe_load(open(path))
        actions.extend(config['storage']['config'])

    for action in actions:
        try:
            storage_checker.check(action)
        except Exception:
            print('checking {} failed'.format(action))
            raise

    storage_checker.final_checks()


def main():
    validate_yaml(sys.argv[1])


if __name__ == '__main__':
    main()
