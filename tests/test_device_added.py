import unittest
import time
import dbus
from dbus.mainloop.glib import DBusGMainLoop
DBusGMainLoop(set_as_default=True)
import gobject
import sys
sys.path.insert(0, '..')
from usbcreator.backend import Backend

TIMEOUT = 15 # In seconds

class TestFrontend():
    def set_sources(self, sources_dict):
        self.sources = sources_dict

    def set_targets(self, targets_dict):
        self.targets = targets_dict

    def get_source(self):
        pass

class TestBackend(unittest.TestCase):
    def setUp(self):
        # Make sure we imported the right module.
        import usbcreator.backend
        assert usbcreator.backend.__file__.startswith('..')

        self.bus = dbus.SystemBus()
        hal_obj = self.bus.get_object('org.freedesktop.Hal',
                                      '/org/freedesktop/Hal/Manager')
        self.hal = dbus.Interface(hal_obj, 'org.freedesktop.Hal.Manager')
        
        loop = gobject.MainLoop()
        self.ctx = loop.get_context()
        

class TestDeviceAdded(TestBackend, TestFrontend):
    def waitForUDI(self, udi, in_object):
        seconds = 0
        while not in_object(udi) and seconds < TIMEOUT * 2:
            while self.ctx.pending():
                self.ctx.iteration()
            seconds += 1
            time.sleep(0.5)
        assert in_object(udi)

    def testCDAdded(self):
        """Check that a CD, inserted after running, gets added to the backend and frontend"""
        self.frontend = TestFrontend()
        self.backend = Backend(self.frontend)
        udi = self.addFakeCD()
        try:
            in_frontend = lambda x : self.frontend.sources.has_key(x)
            in_backend = lambda x : self.backend.cds.has_key(x)
            self.waitForUDI(udi, in_backend)
            self.waitForUDI(udi, in_frontend)
        finally:
            self.hal.Remove(udi)

    def testUSBDeviceAdded(self):
        """Check that a USB device, inserted after running, gets added to the backend and frontend"""
        self.frontend = TestFrontend()
        # Create a fake raw disk image file selection.
        self.frontend.get_source = lambda : 'fakeudi'
        self.backend = Backend(self.frontend)
        self.backend.cds = { 'fakeudi' : { 'type' : self.backend.IMG } }
        udi = self.addFakeUSBDevice()
        try:
            in_frontend = lambda x : self.frontend.targets.has_key(x)
            in_backend = lambda x : self.backend.devices.has_key(x)
            self.waitForUDI(udi, in_backend)
            self.waitForUDI(udi, in_frontend)
        finally:
            self.hal.Remove(udi)
    
    def testUSBPartitionAdded(self):
        """Check that a USB disk partition, inserted after running, gets added to the backend and frontend"""
        self.frontend = TestFrontend()
        # Create a fake raw disk image file selection.
        self.frontend.get_source = lambda : 'a'
        self.backend = Backend(self.frontend)
        self.backend.cds = { 'a' : { 'type' : self.backend.ISO } }
        parent = self.addFakeUSBDevice()
        udi = self.addFakeUSBPartition(parent)
        try:
            in_frontend = lambda x : self.frontend.targets.has_key(x)
            in_backend = lambda x : self.backend.partitions.has_key(x)
            self.waitForUDI(udi, in_backend)
            self.waitForUDI(udi, in_frontend)
        finally:
            self.hal.Remove(udi)
            self.hal.Remove(parent)

    def addFakeCD(self):
        # TODO evand 2009-04-23: Use SetMultipleProperties().
        udi = self.hal.NewDevice()
        dev_obj = self.bus.get_object('org.freedesktop.Hal', udi)
        dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')
        dev.SetPropertyString('volume.label', 'Test Label')
        dev.SetPropertyString('info.category', 'volume')
        dev.SetPropertyString('info.parent', 'null')
        dev.SetPropertyBoolean('volume.is_disc', True)
        dev.SetPropertyBoolean('volume.disc.is_blank', False)
        dev.SetPropertyString('volume.mount_point', '/mnt')
        dev.SetPropertyInteger('volume.size', 0)
        dev.SetPropertyString('block.device', '/dev/null')
        dev.SetPropertyString('volume.fstype', 'iso9660')
        self.hal.CommitToGdl(udi, udi)
        return udi

    def addFakeUSBDevice(self):
        udi = self.hal.NewDevice()
        dev_obj = self.bus.get_object('org.freedesktop.Hal', udi)
        dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')
        dev.SetPropertyString('info.category', 'storage')
        dev.SetPropertyString('storage.bus', 'usb')
        dev.SetPropertyString('storage.drive_type', 'disk')
        dev.SetPropertyString('storage.model', 'Storage Media')
        dev.SetPropertyBoolean('storage.removable', True)
        dev.SetPropertyInteger('storage.removable.media_size', 0)
        dev.SetPropertyString('block.device', '/dev/null')
        self.hal.CommitToGdl(udi, udi)
        return udi

    def addFakeUSBPartition(self, parent):
        udi = self.hal.NewDevice()
        dev_obj = self.bus.get_object('org.freedesktop.Hal', udi)
        dev = dbus.Interface(dev_obj, 'org.freedesktop.Hal.Device')
        dev.SetPropertyString('info.parent', parent)
        dev.SetPropertyString('info.category', 'volume')
        dev.SetPropertyString('volume.label', 'Fake Partition')
        dev.SetPropertyBoolean('volume.is_disc', False)
        dev.SetPropertyInteger('volume.size', 0)
        dev.SetPropertyString('volume.fstype', 'vfat')
        dev.SetPropertyString('volume.mount_point', '/mnt')
        dev.SetPropertyString('block.device', '/dev/null')
        self.hal.CommitToGdl(udi, udi)
        return udi

if __name__ == '__main__':
    unittest.main()

# 0: udi = '/org/freedesktop/Hal/devices/volume_label_Ubuntu_8_10_i386'
#   linux.hotplug_type = 3  (0x3)  (int)
#   info.capabilities = { 'volume.disc', 'volume', 'block' } (string list)
#   info.interfaces = { 'org.freedesktop.Hal.Device.Volume' } (string list)
#   block.storage_device = '/org/freedesktop/Hal/devices/storage_model_DVD_R___UJ_85J'  (string)
#   volume.fstype = 'iso9660'  (string)
#   volume.fsusage = 'filesystem'  (string)
#   volume.fsversion = ''  (string)
#   volume.uuid = ''  (string)
#   volume.label = 'Ubuntu 8.10 i386'  (string)
#   info.product = 'Ubuntu 8.10 i386'  (string)
#   volume.is_mounted = false  (bool)
#   volume.is_mounted_read_only = false  (bool)
#   volume.linux.is_device_mapper = false  (bool)
#   volume.is_disc = true  (bool)
#   volume.is_partition = false  (bool)
#   volume.mount_point = ''  (string)
#   volume.block_size = 2048  (0x800)  (int)
#   volume.num_blocks = 1431784  (0x15d8e8)  (uint64)
#   volume.size = 733073408  (0x2bb1d000)  (uint64)
#   volume.ignore = false  (bool)
#   info.udi = '/org/freedesktop/Hal/devices/volume_label_Ubuntu_8_10_i386'  (string)
#   org.freedesktop.Hal.Device.Volume.method_names = { 'Mount', 'Unmount', 'Eject' } (string list)
#   org.freedesktop.Hal.Device.Volume.method_signatures = { 'ssas', 'as', 'as' } (string list)
#   org.freedesktop.Hal.Device.Volume.method_argnames = { 'mount_point fstype extra_options', 'extra_options', 'extra_options' } (string list)
#   org.freedesktop.Hal.Device.Volume.method_execpaths = { 'hal-storage-mount', 'hal-storage-unmount', 'hal-storage-eject' } (string list)
#   volume.mount.valid_options = { 'ro', 'sync', 'dirsync', 'noatime', 'nodiratime', 'noexec', 'quiet', 'remount', 'exec', 'utf8', 'uid=', 'mode=', 'iocharset=' } (string list)
#   volume.unmount.valid_options = { 'lazy' } (string list)
#   volume.disc.type = 'cd_rom'  (string)
#   volume.disc.has_audio = false  (bool)
#   volume.disc.has_data = true  (bool)
#   volume.disc.is_blank = false  (bool)
#   volume.disc.is_appendable = false  (bool)
#   volume.disc.is_rewritable = false  (bool)
#   linux.sysfs_path = '/sys/devices/pci0000:00/0000:00:1f.1/host0/target0:0:0/0:0:0:0/block/sr0/fakevolume'  (string)
#   info.parent = '/org/freedesktop/Hal/devices/storage_model_DVD_R___UJ_85J'  (string)
#   volume.disc.is_videodvd = false  (bool)
#   volume.disc.is_blurayvideo = false  (bool)
#   block.device = '/dev/sr0'  (string)
#   block.major = 11  (0xb)  (int)
#   block.minor = 0  (0x0)  (int)
#   block.is_volume = true  (bool)
#   volume.disc.is_vcd = false  (bool)
#   info.category = 'volume'  (string)
#   volume.disc.is_svcd = false  (bool)
#   volume.disc.capacity = 733073408  (0x2bb1d000)  (uint64)

# 0: udi = '/org/freedesktop/Hal/devices/volume_uuid_2B5C_5A44'
#   info.capabilities = { 'volume', 'block', 'access_control' } (string list)
#   access_control.file = '/dev/sdb1'  (string)
#   access_control.type = 'removable-block'  (string)
#   info.callouts.add = { 'hal-acl-tool --add-device' } (string list)
#   info.callouts.remove = { 'hal-acl-tool --remove-device' } (string list)
#   info.interfaces = { 'org.freedesktop.Hal.Device.Volume' } (string list)
#   block.storage_device = '/org/freedesktop/Hal/devices/storage_serial_Sony_Storage_Media_5A08040716852_0_0'  (string)
#   volume.fstype = 'vfat'  (string)
#   volume.fsusage = 'filesystem'  (string)
#   info.udi = '/org/freedesktop/Hal/devices/volume_uuid_2B5C_5A44'  (string)
#   volume.uuid = '2B5C-5A44'  (string)
#   volume.label = ''  (string)
#   volume.fsversion = 'FAT32'  (string)
#   volume.mount_point = ''  (string)
#   info.product = 'Volume (vfat)'  (string)
#   block.device = '/dev/sdb1'  (string)
#   block.major = 8  (0x8)  (int)
#   block.minor = 17  (0x11)  (int)
#   volume.partition.number = 1  (0x1)  (int)
#   block.is_volume = true  (bool)
#   volume.num_blocks = 1957887  (0x1ddfff)  (uint64)
#   volume.size = 1002438144  (0x3bbffe00)  (uint64)
#   volume.partition.start = 512  (0x200)  (uint64)
#   volume.partition.media_size = 1002438656  (0x3bc00000)  (uint64)
#   volume.is_mounted_read_only = false  (bool)
#   volume.linux.is_device_mapper = false  (bool)
#   volume.is_disc = false  (bool)
#   volume.is_partition = true  (bool)
#   volume.is_mounted = false  (bool)
#   volume.block_size = 512  (0x200)  (int)
#   volume.ignore = false  (bool)
#   org.freedesktop.Hal.Device.Volume.method_names = { 'Mount', 'Unmount', 'Eject' } (string list)
#   org.freedesktop.Hal.Device.Volume.method_signatures = { 'ssas', 'as', 'as' } (string list)
#   org.freedesktop.Hal.Device.Volume.method_argnames = { 'mount_point fstype extra_options', 'extra_options', 'extra_options' } (string list)
#   org.freedesktop.Hal.Device.Volume.method_execpaths = { 'hal-storage-mount', 'hal-storage-unmount', 'hal-storage-eject' } (string list)
#   volume.mount.valid_options = { 'ro', 'sync', 'dirsync', 'noatime', 'nodiratime', 'noexec', 'quiet', 'remount', 'exec', 'utf8', 'shortname=', 'codepage=', 'iocharset=', 'umask=', 'dmask=', 'fmask=', 'uid=', 'flush' } (string list)
#   linux.sysfs_path = '/sys/devices/pci0000:00/0000:00:1d.7/usb2/2-1/2-1:1.0/host5/target5:0:0/5:0:0:0/block/sdb/sdb1'  (string)
#   info.parent = '/org/freedesktop/Hal/devices/storage_serial_Sony_Storage_Media_5A08040716852_0_0'  (string)
#   volume.unmount.valid_options = { 'lazy' } (string list)
#   info.category = 'volume'  (string)
#   linux.hotplug_type = 3  (0x3)  (int)
# 
# 1: udi = '/org/freedesktop/Hal/devices/storage_serial_Sony_Storage_Media_5A08040716852_0_0'
#   info.capabilities = { 'storage', 'block', 'access_control' } (string list)
#   access_control.file = '/dev/sdb'  (string)
#   access_control.type = 'removable-block'  (string)
#   info.callouts.add = { 'hal-acl-tool --add-device' } (string list)
#   info.callouts.remove = { 'hal-acl-tool --remove-device' } (string list)
#   info.interfaces = { 'org.freedesktop.Hal.Device.Storage.Removable' } (string list)
#   storage.removable.media_size = 1002438656  (0x3bc00000)  (uint64)
#   storage.partitioning_scheme = 'mbr'  (string)
#   block.storage_device = '/org/freedesktop/Hal/devices/storage_serial_Sony_Storage_Media_5A08040716852_0_0'  (string)
#   info.product = 'Storage Media'  (string)
#   info.udi = '/org/freedesktop/Hal/devices/storage_serial_Sony_Storage_Media_5A08040716852_0_0'  (string)
#   block.device = '/dev/sdb'  (string)
#   block.major = 8  (0x8)  (int)
#   block.minor = 16  (0x10)  (int)
#   block.is_volume = false  (bool)
#   storage.bus = 'usb'  (string)
#   storage.no_partitions_hint = false  (bool)
#   storage.media_check_enabled = true  (bool)
#   storage.automount_enabled_hint = true  (bool)
#   storage.drive_type = 'disk'  (string)
#   storage.model = 'Storage Media'  (string)
#   storage.vendor = 'Sony'  (string)
#   storage.serial = 'Sony_Storage_Media_5A08040716852-0:0'  (string)
#   storage.firmware_version = '0100'  (string)
#   storage.lun = 0  (0x0)  (int)
#   storage.originating_device = '/org/freedesktop/Hal/devices/usb_device_54c_243_5A08040716852_if0'  (string)
#   storage.removable.media_available = true  (bool)
#   storage.removable = true  (bool)
#   storage.size = 0  (0x0)  (uint64)
#   storage.hotpluggable = true  (bool)
#   storage.requires_eject = false  (bool)
#   linux.sysfs_path = '/sys/devices/pci0000:00/0000:00:1d.7/usb2/2-1/2-1:1.0/host5/target5:0:0/5:0:0:0/block/sdb'  (string)
#   info.parent = '/org/freedesktop/Hal/devices/usb_device_54c_243_5A08040716852_if0_scsi_host_0_scsi_device_lun0'  (string)
#   storage.removable.support_async_notification = false  (bool)
#   info.category = 'storage'  (string)
#   info.addons = { 'hald-addon-storage' } (string list)
#   info.vendor = 'Sony'  (string)
#   linux.hotplug_type = 3  (0x3)  (int)
