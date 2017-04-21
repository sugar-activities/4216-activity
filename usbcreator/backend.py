# Copyright (C) 2008-2009 Canonical Ltd.

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3,
# as published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import signal
import subprocess, sys
import stat
import shutil
import dbus
import time
import tempfile

from remtimest import RemainingTimeEstimator
import logging
import logging.handlers

UPDATE_FREE_INTERVAL = 2000 # in milliseconds.

class USBCreatorProcessException(Exception):
    pass

def popen(cmd):
    logging.debug(str(cmd))
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE,
        stderr=sys.stderr, stdin=subprocess.PIPE)
    out, err = process.communicate()
    if process.returncode is None:
        process.wait()
    elif process.returncode != 0:
        raise USBCreatorProcessException(err)
    return out

def free_space(dev):
    try:
        stat = os.statvfs(dev)
    except:
        # This could get called in the event loop as we're shutting down, after
        # we've unmounted filesystems.
        return 0
    return stat.f_bsize * stat.f_bavail

def setup_logging():
    l = os.environ.get('USBCREATOR_LOG_LEVEL', None)
    if l:
        l = l.lower()
    if l == 'debug':
        level = logging.DEBUG
    elif l == 'critical':
        level = logging.CRITICAL
    else:
        level = logging.INFO

    logging.basicConfig(level=level,
                        format='usb-creator %(asctime)s (%(levelname)s) %(filename)s:%(lineno)d: %(message)s',
                        datefmt='%H:%M:%S')
    handler = logging.handlers.SysLogHandler('/dev/log')
    logging.getLogger().addHandler(handler)

class Backend:
    IMG, ISO, CD = range(3)
    CAN_USE, CANNOT_USE, NEED_SPACE = range(3)
    def __init__(self, frontend):
        self.devices = {}
        self.partitions = {}
        self.cds = {}
        self.timeouts = {}
        #self.copy_timeout = 0
        #self.original_size = 0
        self.mountpoints = []
        self.progress_description = ''
        self.frontend = frontend
        setup_logging()
        # Let the Frontend determine and setup the DBusMainLoop (Qt/Gtk/etc)
        self.frontend.DBusMainLoop()
        self.bus = dbus.SystemBus()
        hal_obj = self.bus.get_object('org.freedesktop.Hal',
            '/org/freedesktop/Hal/Manager')
        self.hal = dbus.Interface(hal_obj, 'org.freedesktop.Hal.Manager')
        self.estimator = RemainingTimeEstimator()
        
        self.bus.add_signal_receiver(self.device_added,
            'DeviceAdded',
            'org.freedesktop.Hal.Manager',
            'org.freedesktop.Hal',
            '/org/freedesktop/Hal/Manager')
        self.bus.add_signal_receiver(self.device_removed,
            'DeviceRemoved',
            'org.freedesktop.Hal.Manager',
            'org.freedesktop.Hal',
            '/org/freedesktop/Hal/Manager')
        self.detect_devices()

    # Device detection functions

    def device_added(self, udi):
        logging.debug('Possibly adding: %s' % str(udi))
        dev_obj = self.bus.get_object("org.freedesktop.Hal", udi)
        dev = dbus.Interface(dev_obj, "org.freedesktop.Hal.Device")

        # TODO evand 2009-04-21: Confirm these are the only, and correct,
        # values.
        bus_types = ('usb', 'mmc')
        drive_types = ('compact_flash', 'memory_stick', 'smart_media', 'sd_mmc')

        storage = False
        volume = False
        fs = False
        disc = False
        bus = False
        type = False

        try:
            # TODO evand 2009-04-22: Use GetAllProperties() instead?  See
            # lshal.py
            udi = dev.GetProperty('info.udi')
            # I hate HAL.  info.subsystem would make the most sense here, but
            # despite the specification saying that it is a mandatory property,
            # it is not always present.
            if dev.PropertyExists('info.category'):
                # FIXME evand 2009-04-26: QueryCapability to be consistent with
                # detect_devices?
                storage = dev.GetProperty('info.category') == 'storage'
                volume = dev.GetProperty('info.category') == 'volume'
            if volume:
                fs = dev.GetProperty('volume.fstype') == 'vfat'
                # FIXME evand 2009-04-26: Need to check for .disk/info.
                disc = dev.GetProperty('volume.is_disc') and not \
                       dev.GetProperty('volume.disc.is_blank')
                label = dev.GetProperty('volume.label')
                capacity = dev.GetProperty('volume.size')
                mountpoint = dev.GetProperty('volume.mount_point')
                parent = dev.GetProperty('info.parent')
            if storage:
                bus = dev.GetProperty('storage.bus') in bus_types
                type = dev.GetProperty('storage.drive_type') in drive_types
                if bus or type:
                    label = dev.GetProperty('storage.model')
                    if dev.PropertyExists('storage.removable'):
                        capacity = dev.GetProperty('storage.removable.media_size')
                    else:
                        capacity = dev.GetProperty('storage.size')
            if storage or volume:
                # Keybuk can't tell the difference between the gvfs volume
                # names.
                device = dev.GetProperty('block.device')
                    
        except dbus.DBusException, e:
            if e.get_dbus_name() == 'org.freedesktop.Hal.NoSuchProperty':
                # FIXME evand 2009-04-21: Add warning support to the frontend.
                # Warnings are like errors, only the program can continue after
                # warnings.
                # self.frontend.warning(str(e))
                logging.critical('No such property: %s' % str(e))
                return
        
        logging.debug('    volume: %s' % volume)
        logging.debug('    disc: %s' % disc)
        logging.debug('    fs: %s' % fs)
        logging.debug('    storage: %s' % storage)
        logging.debug('    bus: %s' % bus)
        logging.debug('    type: %s' % type)

        # Only add properties that are needed by the frontend.  The rest can be
        # queried using the UDI as a key.
        if volume:
            # CD (source)
            if disc:
                # TODO evand 2009-04-21: Rename to self.sources.
                self.cds[udi] = {
                    'label' : label,
                    'type' : Backend.CD,
                    'capacity' : capacity,
                    'mountpoint' : mountpoint,
                    # TODO evand 2009-04-21: Needed?
                    'device' : device,
                }
                self.frontend.add_source(udi)
                self.bus.add_signal_receiver(self.property_modified,
                                             'PropertyModified',
                                             'org.freedesktop.Hal.Device',
                                             'org.freedesktop.Hal', udi,
                                             path_keyword='udi')
            # Partition (target)
            if fs:
                self.partitions[udi] = {
                    'label' : label,
                    'capacity' : capacity,
                    'free' : 0,
                    'device' : device,
                    'status' : Backend.CAN_USE,
                }
                logging.debug('    %s' % str(self.partitions[udi]))
                if parent in self.devices:
                    self.devices[parent]['partitions'].append(udi)
                    self.refresh_targets()
                else:
                    self.devices[parent] = {
                        'partitions' : [udi],
                    }
                    # Don't refresh the frontend, as we know we're going to be
                    # adding a device entry soon.
                self.bus.add_signal_receiver(self.property_modified,
                                             'PropertyModified',
                                             'org.freedesktop.Hal.Device',
                                             'org.freedesktop.Hal', udi,
                                             path_keyword='udi')
                # TODO evand 2009-05-05: Move to refresh_targets()?  We don't
                # want to mount partitions if we never care about their free
                # space.
                self.timeouts[udi] = self.frontend.add_timeout(UPDATE_FREE_INTERVAL,
                                                         self.update_free, udi)
        elif storage:
            if bus or type:
                # Drive (target)
                if udi in self.devices:
                    partitions = self.devices[udi]['partitions']
                else:
                    partitions = []
                self.devices[udi] = {
                    'label' : label,
                    'capacity' : capacity,
                    'device' : device,
                    'partitions' : partitions,
                    'free' : 0,
                    'status' : Backend.CAN_USE,
                }
                self.bus.add_signal_receiver(self.property_modified,
                                             'PropertyModified',
                                             'org.freedesktop.Hal.Device',
                                             'org.freedesktop.Hal', udi,
                                             path_keyword='udi')
                self.refresh_targets()
        
        logging.debug('devices: %s' % str(self.devices))

    def refresh_targets(self):
        source = self.frontend.get_source()
        ret = []
        if source:
            type = self.cds[source]['type']
            if type == Backend.CD or type == Backend.ISO:
                # We want to show partitions and devices with no usable
                # partitions.
                for device in self.devices:
                    if self.devices[device]['partitions']:
                        for partition in self.devices[device]['partitions']:
                            self.update_free(partition)
                            ret.append(partition)
                    else:
                        ret.append(device)
            elif type == Backend.IMG:
                # We only want to show the devices.
                ret = self.devices.keys()
        else:
            # FIXME evand 2009-04-25: I'm not entirely confident this is the
            # right approach, but we should give the user some indication that
            # the USB disk they've plugged in has been recognized.
            ret = self.partitions.keys()
            for r in ret:
                self.update_free(r)
        if ret:
            for r in ret:
                self.update_state(r)
            self.frontend.set_targets(ret)

    def device_removed(self, udi):
        logging.debug('Removing %s' % str(udi))
        self.bus.remove_signal_receiver(self.property_modified, path=udi)
        if udi in self.cds:
            self.cds.pop(udi)
            self.frontend.remove_source(udi)
        # FIXME evand 2009-04-26: This is getting really ugly really quickly.
        # Fold partitions back into devices?
        elif udi in self.devices:
            self.devices.pop(udi)
            self.refresh_targets()
        elif udi in self.partitions:
            self.partitions.pop(udi)
            for u in self.devices:
                if udi in self.devices[u]['partitions']:
                    self.devices[u]['partitions'].remove(udi)
                    break
            self.refresh_targets()
        else:
            logging.critical('Was notified of changes to a device we do not' \
                             ' care about: %s' % str(udi))
    
    def property_modified(self, num_changes, change_list, udi):
        dev_obj = self.bus.get_object('org.freedesktop.Hal', udi)
        dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')
        # FIXME evand 2009-04-29:
        logging.debug('device_udi: %s' % udi)
        logging.debug('num_changes: %d' % num_changes)
        print change_list
        for c in change_list:
            logging.debug('change: %s %s' % (str(c[0]), str(c[1])))
        # TODO evand 2009-04-26: What happens if a device doesn't have a
        # property value we want now, but does so later?  Will that ever
        # happen?
        try:
            dev_obj = self.bus.get_object("org.freedesktop.Hal", udi)
            dev = dbus.Interface(dev_obj, "org.freedesktop.Hal.Device")
            if udi in self.cds:
                self.cds[udi]['mountpoint'] = dev.GetProperty('volume.mount_point')
                logging.debug('changed: %s' % str(self.cds[udi]))
        except dbus.DBusException, e:
            logging.debug('Unable to read the new mount point from %s' % udi)
    
    def detect_devices(self):
        volumes = self.hal.FindDeviceByCapability('volume')
        for volume in volumes:
            self.device_added(volume)
        disks = self.hal.FindDeviceByCapability('storage')
        for disk in disks:
            self.device_added(disk)

    # Manual device addition.
    
    # HAL doesn't support loop mounted filesystems, despite udev apparently
    # supporting them (according to Keybuk), and indeed when manually mounted,
    # the device does not appear in hal-device.  So we have to just manually
    # add the device.

    def add_file_source(self, filename):
        try:
            res = popen(['file', '-b', filename])
        except USBCreatorProcessException, e:
            logging.error(e)
            return
        if res.startswith('x86 boot sector'):
            self.add_image_source(filename)
        elif res.startswith('ISO 9660'):
            self.add_iso_source(filename)
        else:
            logging.error('Unknown file type: %s' % str(res))

    def add_image_source(self, filename):
        size = os.stat(filename).st_size
        # TODO: Rename to self.imgs
        self.cds[filename] = { 'label' : '',
                               'type' : Backend.IMG,
                               'mountpoint' : '',
                               'device' : os.path.basename(filename),
                               'capacity' : size }
        self.frontend.add_source(filename)
        
    def add_iso_source(self, filename):
        # TODO evand 2009-04-28: Replace with generic mount function that
        # watches all mountpoints and unmounts them in the end.
        mount_dir = tempfile.mkdtemp()
        try:
            # Really need to use HAL for all mounting and unmounting.  Be sure
            # to handle org.freedesktop.Hal.Device.Volume.NotMountedByHal .
            # Oh, not possible is it, given ISO?  At least do it for all
            # partitions? It might not be us mounting it elsewhere. Mounted by
            # hal property.  Also catch Volume.NotMounted and Volume.Busy?
            res = popen(['mount', '-t', 'iso9660', '-o', 'loop,ro', filename, mount_dir])
        except USBCreatorProcessException, e:
            logging.debug('unable to mount %s to %s:\n%s' % (filename, mount_dir, str(e)))
            self.frontend.notify(_('Unable to mount the image %s.\n\n'
                'Please see ~/.usb-creator.log for more details') % filename)
            return

        fp = None
        label = ''
        try:
            fp = open('%s/.disk/info' % mount_dir)
            label = fp.readline().strip('\0')
            label = ' '.join(label.split(' ')[:2])
        except Exception, e:
            logging.debug('Not an Ubuntu CD: %s' % str(e))
        finally:
            fp and fp.close()
            try:
                popen(['umount', mount_dir])
                popen(['rmdir', mount_dir])
            except USBCreatorProcessException, e:
                logging.error('Unable to unmount %s: %s' % (mount_dir, str(e)))

        if label:
            size = os.stat(filename).st_size
            self.cds[filename] = { 'label' : label,
                                   'type' : Backend.ISO,
                                   'mountpoint' : '',
                                   'device' : os.path.basename(filename),
                                   'capacity' : size }
            self.frontend.add_source(filename)

    # Utility functions

    def update_state(self, udi):
        '''Determines whether a disk/partition has sufficient free space for
        the selected source, or if the source is larger than the disk/partition
        itself and therefore the disk is unusable with that image.'''
        source = self.frontend.get_source()
        if not source:
            return

        source = self.cds[source]
        source_capacity = source['capacity']

        # XXX evand 2009-05-05: We assume that a formatted disk will have a
        # partition that has the same size as the disk's capacity, which is
        # never the case, but we have no way of accurately guessing the size of
        # the resulting filesystem.

        # TODO evand 2009-05-06: Need to factor in the removal of Ubuntu CD
        # directories (casper, pool, etc).
        if udi in self.devices:
            device = self.devices[udi]
            if source_capacity > device['capacity']:
                # The image is larger than this disk, we cannot use it.
                device['status'] = Backend.CANNOT_USE
            else:
                # We can use the disk.
                device['status'] = Backend.CAN_USE
        else:
            if udi in self.partitions:
                partition = self.partitions[udi]
                if source_capacity > partition['capacity']:
                    partition['status'] = Backend.CANNOT_USE
                elif source_capacity > partition['free']:
                    partition['status'] = Backend.NEED_SPACE
                else:
                    partition['status'] = Backend.CAN_USE

    def update_free(self, udi):
        '''Peroidically called via a timeout to update the amount of
        free space on a partition target.  If the amount of space has changed,
        it calls update_state, which will determine if there is sufficient free
        space to hold the selected source image, and then notifies the frontend
        that the partition has changed and it needs to update its UI to reflect
        that.'''
        # FIXME evand 2009-05-05: Don't poll, use an inotify watch instead.
        dev_obj = self.bus.get_object('org.freedesktop.Hal', udi)
        dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')
        if not dev.GetProperty('volume.is_mounted'):
            d = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device.Volume')
            try:
                d.Mount(self.partitions[udi]['label'], 'vfat', [])
                self.mountpoints.append(udi)
            except dbus.DBusException, e:
                logging.error(str(e))
                logging.error('Cannot mount %s, not updating free space.' % udi)
                return False
        mp = dev.GetProperty('volume.mount_point')

        if mp:
            free = free_space(mp)
            if self.partitions[udi]['free'] != free:
                logging.debug('%s now has %d B free.' % (udi, free))
                self.partitions[udi]['free'] = free
                self.update_state(udi)
                self.frontend.update_target(udi)
        return True

    def unmount(self, partitions_list):
        '''Unmount a list of Device.Volumes, specified by UDI.'''
        for partition in partitions_list:
            if partition in self.cds:
                mp = self.cds[partition]['mountpoint']
                mp and popen(['umount', mp])
            dev_obj = self.bus.get_object('org.freedesktop.Hal', partition)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')
            if dev.GetProperty('volume.is_mounted'):
                d = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device.Volume')
                # TODO evand 2009-04-28: Catch unmount exceptions.
                try:
                    d.Unmount([])
                except dbus.DBusException, e:
                    n = e.get_dbus_name()
                    if n == 'org.freedesktop.Hal.Device.Volume.NotMountedByHal':
                        mountpoint = dev.GetProperty('volume.mount_point')
                        popen(['umount', mountpoint])
                    elif n == 'org.freedesktop.Hal.Device.Volume.Busy':
                        # TODO evand 2009-05-01: Need to call into the frontend
                        # for an error dialog here.
                        raise

    def format_device(self, udi):
        '''Format the disk device.  If the UDI specified is a Device.Volume,
           find its parent and format it instead.'''

        device = None
        if udi in self.devices:
            device = udi
        else:
            for dev in self.devices:
                if udi in self.devices[dev]['partitions']:
                    device = dev
                    break
        if not device:
            logging.critical('Could not find device to format: %s' % udi)
            return
        dev_obj = self.bus.get_object('org.freedesktop.Hal', udi)
        dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')
        # TODO evand 2009-04-28: Catch lock exceptions.
        dev.Lock('Formatting device')
        partitions = self.devices[device]['partitions']
        try:
            self.unmount(partitions)
        except dbus.DBusException, e:
            logging.error('Unable to unmount partitions before formatting.')
            return

        device = self.devices[device]['device']
        try:
            # TODO: This could really use a progress dialog.
            popen(['parted', '-s', device, 'mklabel', 'msdos'])
            popen(['parted', '-s', device, 'mkpartfs', 'primary',
                   'fat32', '0', '--', '-0'])
        except USBCreatorProcessException, e:
            message = _('Unable to format the device')
            logging.debug('%s %s: %s' % (message, device, str(e)))
            self.frontend.notify(message)
            return
        # Probably unnecessary.
        try:
            dev.Unlock()
        except dbus.DBusException:
            return

    # Install routines

    def install(self):
        # TODO evand 2009-05-03: Perhaps raise USBCreatorInstallException on
        # recoverable errors?  Or FatalInstallException and InstallException?
        source = self.frontend.get_source()
        target = self.frontend.get_target()
        persist = self.frontend.get_persistence()
        if not (source and target):
            # We do not fail here because the user can go back and select a
            # proper source and target, should they somehow get here.
            logging.error('Pressed install without a source or target.')
            return

        try:
            dev_obj = self.bus.get_object('org.freedesktop.Hal', target)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')
            dev.Lock('Writing %s to the disk' % source)
            if self.cds[source]['type'] == Backend.IMG:
                self.unmount(self.devices[target]['partitions'])
                self.write_image(source, target)
            else:
                # TODO evand 2009-05-06: Call self.unmount()
                self.install_bootloader(target)
                self.copy_files(source, target, persist)
        except (dbus.DBusException, Exception), e:
            import traceback
            print traceback.print_exc()
            message = _('An error occured during the installation, and it ' \
                        'was unable to complete.')
            # FIXME evand 2009-05-03: Unmount both source and target here.
            self.cleanup()
            self.frontend.failed(message)
        finally:
            try:
                dev.Unlock()
            except dbus.DBusException:
                pass

    def dd_status(self, pid):
        try:
            if pid is not None:
                os.kill(pid, signal.SIGUSR1)
        except OSError:
            return False
        return True

    def dd_progress(self, source, condition, source_size):
        char = self.frontend.read_line(source)
        splitted = char.split()
        # XXX evand 2009-05-05: Ick.
        if len(splitted) > 1 and splitted[1] == 'bytes':
            try:
                now = float(splitted[0])
                per = (now / source_size) * 100
                remaining, speed = self.estimator.estimate(now, source_size)
                self.frontend.progress(per, remaining, speed, _('Copying files'))
            except ValueError:
                self.frontend.progress(0, 0, 0, _('Copying files'))
        return True

    def write_image(self, source, target):
        print 'Writing %s to %s' % (source, target)
        target = self.devices[target]
        cmd = ['dd', 'bs=1M', 'if=%s' % source,
               'of=%s' % target['device'], 'conv=fsync']
        logging.debug(cmd)
        # TODO evand 2009-05-06: Either use popen() or write another function
        # that wraps errors in exceptions.
        self.frontend.background_process(cmd, stderr=True, env={'LC_ALL':'C'})
        source_size = float(self.cds[source]['capacity'])
        pipe = self.frontend.get_process_pipe()
        pid = self.frontend.get_process_pid()
        self.watch = self.frontend.add_io_watch(pipe, 'stderr',
                     self.dd_progress, source_size)
        self.copy_timeout = self.frontend.add_timeout(5000, self.dd_status, pid)
        # Wait for the process to complete
        self.frontend.add_child_watch(pid, self.on_end)

    def install_bootloader(self, target):
        # TODO evand 2009-04-29: Perhaps detect bootloader code instead and
        # offer to overwrite.
        try:
            dev_obj = self.bus.get_object('org.freedesktop.Hal', target)
            dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')
            partition_num = str(dev.GetProperty('volume.partition.number'))
            device = ''
            rootdevice = ''
            assert target in self.partitions
            device = self.partitions[target]['device']
            for dev in self.devices:
                if target in self.devices[dev]['partitions']:
                    rootdevice = self.devices[dev]['device']
                    break
            assert device and rootdevice
        except (AssertionError, dbus.DBusException), e:
            logging.error('Could not determine the device: %s' % str(e))
            # We don't exit because we haven't actually done anything to the device.
            return
        
        self.frontend.progress(0, None, None, _('Installing the bootloader'))

        try:
            # Install the bootloader to the MBR.
            popen(['dd', 'if=/usr/lib/syslinux/mbr.bin',
                'of=%s' % rootdevice, 'bs=446', 'count=1', 'conv=sync'])
            args = ['syslinux']
            if 'USBCREATOR_SAFE' in os.environ:
                args.append('-s')
            args.append(device)
            popen(args)
            logging.debug('Marking partition %s as active.' % partition_num)
            popen(['parted', '-s', rootdevice, 'set', partition_num, 'boot', 'on'])
        except USBCreatorProcessException, e:
            logging.error('Failed to install the bootloader: %s' % str(e))
            # FIXME evand 2009-04-29: exit properly.
            sys.exit(1)

    def copy_files(self, source, target, persist):
        source_obj = self.cds[source]
        target_obj = self.partitions[target]

        # TODO evand 2009-05-05: Need to push this into its own function.
        if not source_obj['mountpoint'] and source_obj['type'] == Backend.ISO:
            mount_point = tempfile.mkdtemp()
            # mount(options [] = None, source, mount_point)?
            #  mounts, adds to mountpoint list
            #  what happens if it fails?  Let the exception bubble up to install()
            popen(['mount', '-o', 'loop,ro', source, mount_point])
            source_obj['mountpoint'] = mount_point
            self.mountpoints.append(source)
        elif not source_obj['mountpoint'] and source_obj['type'] == Backend.CD:
            # This should use HAL to mount.
            mount_point = tempfile.mkdtemp()
            popen(['mount', '-o', 'ro', source_obj['device'], mount_point])
            self.mountpoints.append(source)
            source_obj['mountpoint'] = mount_point

        dev_obj = self.bus.get_object('org.freedesktop.Hal', target)
        dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')
        if not dev.GetProperty('volume.is_mounted'):
            dev_obj = self.bus.get_object('org.freedesktop.Hal', target)
            d = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device.Volume')
            # TODO evand 2009-05-01: Should we use sync as a mount option here?
            # Ask Keybuk.
            d.Mount(target_obj['label'], 'vfat', [])
            self.mountpoints.append(target)
            mp = dev.GetProperty('volume.mount_point')
            if not mp:
                logging.critical('Unable to find mount point for target media.')
                # TODO evand 2009-05-05: FAIL
        target_mp = dev.GetProperty('volume.mount_point')
        # FIXME 2009-05-1: What if a CD is not mounted?
        source_mp = source_obj['mountpoint']

        # FIXME evand 2009-04-29: Move into scripts/install.py?  Can't, need to
        # do it before calculating the original size.
        # Remove files we're going to copy.
        try:
            for obj in os.listdir(source_mp):
                obj = os.path.join(target_mp, obj)
                if os.path.exists(obj):
                    popen(['rm', '-rf', obj])
            casper_rw = os.path.join(target_mp, 'casper-rw')
            popen(['rm', '-rf', casper_rw])
        except USBCreatorProcessException, e:
            # Chances are these files will not exist and rm will return
            # nonzero.
            pass
        
        # Update the progress by calculating the free space on the partition.
        original_size = free_space(target_mp)
        print 'persist', persist
        print 'capacity', source_obj['capacity']
        source_size = float(persist * 1024 * 1024) + float(source_obj['capacity'])
        self.copy_timeout = self.frontend.add_timeout(2000, self.copy_progress,
                                                original_size,
                                                target_mp,
                                                source_size)
        
        cmd = ['/usr/share/usb-creator/install.py', '-s', '%s/.' % source_mp,
               '-t', '%s' % target_mp, '-p', '%d' % persist]
        self.frontend.background_process(cmd, stdout=True)
        pipe = self.frontend.get_process_pipe()
        pid = self.frontend.get_process_pid()
        self.watch = self.frontend.add_io_watch(pipe, 'stdout',
                     self.data_available)
        # Wait for the process to complete
        self.frontend.add_child_watch(pid, self.on_end)
        

    def abort(self):
        '''Called when the user has canceled the installation.'''
        logging.debug('Forcing shutdown of the install process.')
        import errno
        pipe = self.frontend.get_process_pipe()
        pid = self.frontend.get_process_pid()
        try:
            if pipe is not None and pid is not None:
                os.kill(pid, signal.SIGTERM)
        except OSError, e:
            if e.errno == errno.ESRCH:
                # Already gone.
                pass
            else:
                logging.error('Unable to kill %d: %s' % pid, str(e))
        self.cleanup()

    def copy_progress(self, original_size, target_mountpoint, source_size):
        now = free_space(target_mountpoint)
        done = original_size - now
        per = (done / float(source_size)) * 100
        remaining, speed = self.estimator.estimate(done, source_size)
        self.frontend.progress(per, remaining, speed, self.progress_description)
        if per < 100:
            return True
        else:
            return False

    def data_available(self, source, condition):
        text = self.frontend.read_line(source)
        if len(text) > 0:
            self.progress_description = text.strip('\n')
            return True
        else:
            return False
    
    # Exit routines

    def cleanup(self):
        t = {}
        for udi, timeout in self.timeouts.iteritems():
            if not self.frontend.delete_timeout(timeout):
                t[udi] = timeout
        self.timeouts = t
        print 'unmounting', self.mountpoints
        self.unmount(self.mountpoints)

    def quit(self):
        '''Called when the user has quit the application.'''
        self.cleanup()

    def on_end(self, pid, error_code):
        # FIXME evand 2009-04-29:
        # unmount source and dest
        self.frontend.delete_io_watch(self.watch)
        if self.copy_timeout:
            self.frontend.delete_timeout(self.copy_timeout)
        logging.debug('Install command exited with code: %d' % error_code)
        if error_code != 0:
            self.frontend.failed()
        self.frontend.finished()
