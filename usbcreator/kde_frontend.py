#!/usr/bin/python

# Copyright (C) 2009 Roderick B. Greening <roderick.greening@gmail.com>
# 
# Based in part on work by:
#  David Edmundson <kde@davidedmundson.co.uk>
#  Canonical Ltd. USB Creator Team
# 
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

import sys
import os

from PyQt4.QtCore import *
from PyQt4.QtGui import *
from PyQt4 import uic
from PyKDE4.kdeui import KIcon, KMessageBox
from PyKDE4.kdecore import KProcess, KStandardDirs, KUrl
from PyKDE4.kio import KFileDialog
from PyKDE4.solid import Solid

from usbcreator.translate import translate
uic.properties.Properties._string = translate
from usbcreator.backend import Backend
import gettext
import locale
import logging

MIN_PERSIST = 128 # The minimal size, in megabytes, that a persistence file can be.
LOCALEDIR = "/usr/share/locale"

class KdeFrontend(QObject):
    @classmethod
    def startup_failure(cls, message):
        KMessageBox.sorry(None, message, "", KMessageBox.Notify)

    @classmethod
    def DBusMainLoop(cls):
        from dbus.mainloop.qt import DBusQtMainLoop
        DBusQtMainLoop(set_as_default=True)

    def __init__(self, img=None, persistent=True):
        QObject.__init__(self)

        #our passed vars - keep them private
        self.__persistent = persistent
        self.__img = img

        locale.setlocale(locale.LC_ALL, '')
        gettext.bindtextdomain('usbcreator', LOCALEDIR)
        gettext.textdomain('usbcreator')

        import __builtin__
        __builtin__._ = gettext.gettext

        # Perform some initialization
        self.__initPrivateVars()
        self.__initUI()

        # FIXME: preload the source/taget treeview...
        #self.setup_sources_treeview()
        #self.setup_targets_treeview()

        #enable the backend
        self.__backend = Backend(self)

        #add any file sources passed
        if self.__img is not None:
            self.__backend.add_file_source(self.__img)

    def __initPrivateVars(self):
        """Initialize Private Variables"""

        # main window
        self.__mainWindow = QDialog()

        # ui file
        self.__mainWindow_ui = "usbcreator.ui"

        # init Backend to None - easier to debug...
        self.__backend = None

        # used to hold the backgrounded kprocess to monitor
        self.__process = None

    def __initUI(self):
        """Initialize the interface"""

        # Locate the ui for the main window and load it.
        if os.path.exists(self.__mainWindow_ui):
            appdir = QDir.currentPath()
        else:
            file =  KStandardDirs.locate("appdata", self.__mainWindow_ui)
            appdir = file.left(file.lastIndexOf("/"))
        uic.loadUi(appdir + "/" + self.__mainWindow_ui, self.__mainWindow)

        #set default persist size
        self.__mainWindow.ui_persist_label.setText(format_mb_size(128))

        #set persistent ui elements state
        if self.__persistent:
            self.__mainWindow.ui_persist_enabled.setChecked(True)
            self.__mainWindow.ui_persist_text.setEnabled(True)
            self.__mainWindow.ui_persist_slider.setEnabled(True)
            self.__mainWindow.ui_persist_label.setEnabled(True)
        else:
            self.__mainWindow.ui_persist_disabled.setChecked(True)
            self.__mainWindow.ui_persist_text.setDisabled(True)
            self.__mainWindow.ui_persist_slider.setDisabled(True)
            self.__mainWindow.ui_persist_label.setDisabled(True)

        #disable the start button and persist frame by default
        self.__mainWindow.ui_persist_frame.setEnabled(False)
        self.__mainWindow.ui_start_button.setEnabled(False)


        #add some buttons
        self.__mainWindow.ui_quit_button.setIcon(KIcon("application-exit"))
        self.__mainWindow.ui_start_button.setIcon(KIcon("dialog-ok-apply"))
        self.__mainWindow.ui_add_source.setIcon(KIcon("media-optical"))
        self.__mainWindow.ui_format_dest.setIcon(KIcon("drive-removable-media-usb-pendrive"))

        #set up signals
        self.connect(self.__mainWindow.ui_add_source,SIGNAL('clicked()'),
            self.add_file_source_dialog)
        self.connect(self.__mainWindow.ui_persist_slider,SIGNAL('valueChanged(int)'),
            lambda value: self.__mainWindow.ui_persist_label.setText(format_mb_size(value)))
        self.connect(self.__mainWindow.ui_quit_button,SIGNAL('clicked()'),
            self.quit)
        self.connect(self.__mainWindow.ui_start_button,SIGNAL('clicked()'),
            self.install)
        self.connect(self.__mainWindow.ui_dest_list,SIGNAL(
            'currentItemChanged(QTreeWidgetItem*,QTreeWidgetItem*)'),
            self.dest_selection_changed)
        self.connect(self.__mainWindow.ui_source_list,SIGNAL(
            'currentItemChanged(QTreeWidgetItem*,QTreeWidgetItem*)'),
            self.source_selection_changed)
        self.connect(self.__mainWindow.ui_format_dest,SIGNAL('clicked()'),
            self.format_dest_clicked)

        self.progress_bar = QProgressDialog("","Cancel",0,100,self.__mainWindow)
        #prevent progress bar from emitting reset on reaching max value (and auto closing)
        self.progress_bar.setAutoReset(False)
        #force immediate showing, rather than waiting...
        self.progress_bar.setMinimumDuration(0)
        #must disconnect the canceled() SIGNAL, otherwise the progress bar is actually destroyed
        self.disconnect(self.progress_bar,SIGNAL('canceled()'),self.progress_bar.cancel)
        #now we connect our own signal to display a warning dialog instead
        self.connect(self.progress_bar,SIGNAL('canceled()'),self.warning_dialog)

        #show the window
        self.__mainWindow.show()

    def __timeout_callback(self, func, *args):
        '''Private callback wrapper used by add_timeout'''

        timer = self.sender()
        active = func(*args)
        if not active:
            timer.stop()

    def __io_watch_callback(self, func, *args):
        '''Private callback wrapper used by add_io_watch'''

        process = self.sender()
        active = func(*args)
        #FIXME: we need to close? or something here to disable to watch?
        if not active:
            process.close()

    def add_timeout(self, interval, func, *args):
        '''Add a new timer for function 'func' with optional arguments. Mirrors a 
        similar gobject call timeout_add.'''

        # FIXME: now that we are part of a Qt object, we may be able to alter for builtin timers
        timer = QTimer()
        QObject.connect(timer,
            SIGNAL('timeout()'),
            lambda: self.__timeout_callback(func, *args))
        timer.start(interval)

        return timer

    def background_process(self, cmd, stdout=False, stderr=False, env=None):
        '''Add a new background process and begin running it.'''

        process = KProcess()
        #if env:
        #    process.setEnv()
        process.setNextOpenMode(QIODevice.ReadOnly|QIODevice.Text)
        if stdout and stderr:
            process.setOutputChannelMode(KProcess.MergedChannels)
        else:
            if stdout:
                process.setOutputChannelMode(KProcess.OnlyStdoutChannel)
            elif stderr:
                process.setOutputChannelMode(KProcess.OnlyStderrChannel)
        process.setProgram(cmd)
        process.start()

        self.__process = process

    def get_process_pid(self):
        '''Return the pid of the current backgrounded process'''

        pid = None
        if self.__process:
            pid = int(self.__process.pid())
            if not pid:
                pid = None
        return pid

    def get_process_pipe(self):
        '''Return the handle of the current backgrounded process'''

        if self.__process is None:
            return None
        else:
            return self.__process

    def read_line(self, source):
        '''Read a line of data from source data channel'''

        line = ''
        if source:
            line = str(source.readLine())

        return line

    def add_io_watch(self, source, type, func, *args):
        '''Add a new watcher for function 'func' with optional arguments. Mirrors a 
        similar gobject call io_add_watch.'''

        # our watcher is a connected signal for readyRead
        if type == 'stderr':
            self.connect(source,
                SIGNAL('readyReadStandardError()'),
                lambda: self.__io_watch_callback(func, source, None, *args))
        if type == 'stdout':
            self.connect(source,
                SIGNAL('readyReadStandardOutput()'),
                lambda: self.__io_watch_callback(func, source, None, *args))
        
        return source

    def add_child_watch(self, pid, func, *args):
        '''Add a new child process watcher for exitng PID. Mirrors gobject call to
        child_watch_add'''

        process = self.get_process_pipe()

        self.connect(process,
            SIGNAL('finished(int, QProcess::ExitStatus)'),
            lambda exit: func(pid, exit, *args))

    def delete_timeout(self, timer):
        '''Remove the specified timer'''

        if timer.isActive():
            return False
        timer.stop()
        return True

    def delete_io_watch(self, process):
        '''Remove the specified watcher'''

        # actually, since it's just a process, we close commumication...
        process.close()

        return True

    def set_targets(self, targets_list):
        '''Populates the treeview model with the UDIs of the possible disks or
        partitions to use, as generated in backend.refresh_targets.'''

        # Save the selection in the event that the disk or partition that the
        # user selected is in the new list of disks and partitions generated by
        # the backend.  In which case, select it again.

        # Save current selected item
        item = self.__mainWindow.ui_dest_list.currentItem()
        if item:
            saved_selection = str(item.data(0,Qt.UserRole).toString())
        else:
            saved_selection = None

        # clear the list
        self.__mainWindow.ui_dest_list.clear()

        for target in targets_list:
            target = str(target)
            new_item = QTreeWidgetItem(self.__mainWindow.ui_dest_list)
            new_item.setData(0,Qt.UserRole,QVariant(target))
            # FIXME:
            # the new_item lines should be auto triggered onChange to the TreeWidget
            # when new_item is appended. aka setup_targets_treeview + callback
            new_item.setText(0,target)
            new_item.setIcon(0,KIcon("drive-removable-media-usb-pendrive"))

            # FIXME:
            # This can't be done here as it's called as part of the Backend init and therefore
            # self.__backend does not yet exits... grrr.... how?
            #populate from device data
            if self.__backend is not None:
                if target in self.__backend.devices:
                    dev = self.__backend.devices[target]
                else:
                    dev = self.__backend.partitions[target]

                new_item.setText(0,dev['device'])
                new_item.setText(1,dev['label'])
                new_item.setText(2,format_size(dev['capacity']))
                new_item.setText(3,format_size(dev['free']))

            if target == saved_selection:
                self.__mainWindow.ui_dest_list.setCurrentItem(new_item,True)

        # FIXME: not sure this is correct....
        if saved_selection is None:
            item = self.__mainWindow.ui_dest_list.topLevelItem(0)
            if not item:
                return
            self.__mainWindow.ui_dest_list.setCurrentItem(item,True)

    def add_source(self, source):
        logging.debug('add_source: %s' % str(source))
        new_item = QTreeWidgetItem(self.__mainWindow.ui_source_list)
        new_item.setData(0,Qt.UserRole,QVariant(source))
        # FIXME:
        # the new_item lines should be auto triggered onChange to the TreeWidget
        # when new_item is appended. aka setup_targets_treeview + callback
        new_item.setText(0,source)
        new_item.setIcon(0,KIcon("media-optical"))

        item = self.__mainWindow.ui_source_list.currentItem()
        if not item:
            item = self.__mainWindow.ui_source_list.topLevelItem(0)
            if item:
                self.__mainWindow.ui_source_list.setCurrentItem(item,True)

        # how does this all get added? here or elsewhere...
        # populate from device data
        if self.__backend is not None:
            new_item.setText(0,self.__backend.cds[source]['device'])
            new_item.setText(1,self.__backend.cds[source]['label'])
            new_item.setText(2,format_size(self.__backend.cds[source]['capacity']))
 
    def remove_source(self, source):
        for i in range(0,self.__mainWindow.ui_source_list.topLevelItemCount()):
            item = self.__mainWindow.ui_source_list.topLevelItem(i)
            if item.data(0,Qt.UserRole).toString() == source:
                self.__mainWindow.ui_source_list.removeItemWidget(item,0)
                break

        if not self.__mainWindow.ui_source_list.currentItem():
            item = self.__mainWindow.ui_source_list.topLevelItem(0)
            if item:
                self.__mainWindow.ui_source_list.setCurrentItem(item,True)

    def get_source(self):
        '''Returns the UDI of the selected source image.'''
        item = self.__mainWindow.ui_source_list.currentItem()
        if item:
            # Must deal in str and not QString for backend
            source = str(item.data(0,Qt.UserRole).toString())
            return source
        else:
            logging.debug('No source selected.')
            return ''

    def get_target(self):
        '''Returns the UDI of the selected target disk or partition.'''
        item = self.__mainWindow.ui_dest_list.currentItem()
        if item:
            # Must deal in str and not QString for backend
            dest = str(item.data(0,Qt.UserRole).toString())
            return dest
        else:
            logging.debug('No target selected.')
            return ''

    def get_persistence(self):
        if (self.__mainWindow.ui_persist_enabled.isChecked() and
            self.__mainWindow.ui_persist_frame.isEnabled()):
            val = self.__mainWindow.ui_persist_slider.value()
            return int(val)
        else:
            return 0

    def get_solid_drive(self, udi):
        deviceList = Solid.Device.allDevices()
        drive = {}
        for device in deviceList:
            if device.isDeviceInterface(Solid.DeviceInterface.StorageVolume):
                volume = device.asDeviceInterface (Solid.DeviceInterface.StorageVolume)
                if str(device.udi()) == udi:
                    drive['icon'] = device.icon()
                    drive['display_name'] = volume.label()
        return drive

    def setup_sources_treeview(self):
        def column_data_func(item, column):
            if not self.__backend:
                return
            udi = str(item.data(0,Qt.UserRole).toString())
            dev = self.__backend.cds[udi]
            if column == 0:
                drive = self.get_solid_drive(udi)
                if drive:
                    cell.set_property('text', drive['display_name'])
                else:
                    cell.set_property('text', dev['device'])
            elif column == 1:
                cell.set_property('text', dev['label'])
            elif column == 2:
                cell.set_property('text', format_size(dev['capacity']))

        def pixbuf_data_func(column, cell, model, iterator):
            if not self.__backend:
                return
            udi = model[iterator][0]
            drive = self.get_gnome_drive(udi)
            type = self.__backend.cds[udi]['type']
            if drive:
                cell.set_property('icon-name', drive['icon'])
            elif type == Backend.ISO:
                cell.set_property('stock-id', gtk.STOCK_CDROM)
            elif type == Backend.IMG:
                cell.set_property('stock-id', gtk.STOCK_HARDDISK)
            else:
                cell.set_property('stock-id', None)

        list_store = gtk.ListStore(str)
        self.source_treeview.set_model(list_store)

        cell_name = gtk.CellRendererText()
        cell_pixbuf = gtk.CellRendererPixbuf()
        column_name = gtk.TreeViewColumn(_('CD-Drive/Image'))
        column_name.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
        column_name.pack_start(cell_pixbuf, expand=False)
        column_name.pack_start(cell_name, expand=True)
        self.source_treeview.append_column(column_name)
        column_name.set_cell_data_func(cell_name, column_data_func, 0)
        column_name.set_cell_data_func(cell_pixbuf, pixbuf_data_func)

        cell_version = gtk.CellRendererText()
        column_name = gtk.TreeViewColumn(_('OS Version'), cell_version)
        column_name.set_cell_data_func(cell_version, column_data_func, 1)
        column_name.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
        self.source_treeview.append_column(column_name)

        cell_size = gtk.CellRendererText()
        column_name = gtk.TreeViewColumn(_('Size'), cell_size)
        column_name.set_cell_data_func(cell_size, column_data_func, 2)
        column_name.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
        self.source_treeview.append_column(column_name)

        selection = self.source_treeview.get_selection()
        selection.connect('changed', lambda x: self.__backend.refresh_targets())

        # Drag and drop support.
        # FIXME evand 2009-04-28: Anything can be dropped on the source
        # treeview.  Ideally, the user should only be able to drop ISO and IMG
        # files.

        #def motion_cb(wid, context, x, y, time):
        #    context.drag_status(gtk.gdk.ACTION_COPY, time)
        #    return True

        #def drop_cb(w, context, x, y, time):
        #    target_list = w.drag_dest_get_target_list()
        #    target = w.drag_dest_find_target(context, target_list)
        #    selection = w.drag_get_data(context, target)
        #    context.finish(True, True)
        #    return True

        #def data_received_cb(w, context, x, y, selection, target_type, timestamp):
        #    # FIXME evand 2009-04-28: Use the GNOME VFS?  Test with a sshfs
        #    # nautilus window.
        #    file = selection.data.strip('\r\n\x00')
        #    if file.startswith('file://'):
        #        file = file[7:]
        #    elif file.startswith('file:'):
        #        file = file[5:]
        #    self.__backend.add_file_source(file)

        #self.source_treeview.drag_dest_set(gtk.gdk.ACTION_DEFAULT,
        #    [('text/uri-list', 0, 600)], gtk.gdk.ACTION_COPY)
        #self.source_treeview.connect('drag_motion', motion_cb)
        #self.source_treeview.connect('drag_drop', drop_cb)
        #self.source_treeview.connect('drag-data-received', data_received_cb)

    def update_target(self, udi):
        for i in range(0,self.__mainWindow.ui_dest_list.topLevelItemCount()):
            item = self.__mainWindow.ui_dest_list.topLevelItem(i)
            if str(item.data(0,Qt.UserRole).toString()) == udi:
                self.__mainWindow.ui_dest_list.emit(
                    SIGNAL('itemChanged(item,0)'))
                break

    def source_selection_changed(self, current_item, prev_item):
        '''The selected image has changed we need to refresh targets'''
        if self.__backend:
            self.__backend.refresh_targets()

    def dest_selection_changed(self, current_item, prev_item):
        '''The selected partition has changed and the bounds on the persistence
        slider need to be changed, or the slider needs to be disabled, to
        reflect the amount of free space on the partition.'''

        # Disable some elements
        self.__mainWindow.ui_persist_frame.setEnabled(False)
        self.__mainWindow.ui_start_button.setEnabled(False)

        if not self.__backend:
            return

        if current_item is None:
            return

        # Must deal in str and not QString for backend
        target_udi = str(current_item.data(0,Qt.UserRole).toString())
        source_udi = self.get_source()

        if not source_udi:
            return

        #enable start button
        self.__mainWindow.ui_start_button.setEnabled(True)

        source = self.__backend.cds[source_udi]
        if target_udi in self.__backend.partitions:
            # We're dealing with a partition, therefore we need to calculate
            # how much, if any, extra space can be used for the persistence
            # file.
            target = self.__backend.partitions[target_udi]
            persist_max = (target['free'] - source['capacity']) / 1024 / 1024
            if persist_max > MIN_PERSIST:
                self.__mainWindow.ui_persist_frame.setEnabled(True)
                self.__mainWindow.ui_persist_slider.setRange(MIN_PERSIST, persist_max)

    def setup_targets_treeview(self):
        def column_data_func(item, column):
            if not self.__backend:
                return
            udi = str(item.data(0,Qt.UserRole).toString())
            if udi in self.__backend.devices:
                dev = self.__backend.devices[udi]
            else:
                dev = self.__backend.partitions[udi]
            drive = self.get_solid_drive(udi)
            if column == 0:
                if drive:
                    cell.set_property('text', drive['display_name'])
                else:
                    cell.set_property('text', dev['device'])
            elif column == 1:
                cell.set_property('text', dev['label'])
            elif column == 2:
                cell.set_property('text', format_size(dev['capacity']))
            elif column == 3:
                cell.set_property('text', format_size(dev['free']))

        def pixbuf_data_func(column, cell, model, iterator):
            if not self.__backend:
                return
            udi = model[iterator][0]
            if udi in self.__backend.devices:
                status = self.__backend.devices[udi]['status']
            else:
                status = self.__backend.partitions[udi]['status']

            if status == Backend.NEED_SPACE:
                cell.set_property('stock-id', gtk.STOCK_DIALOG_WARNING)
            elif status == Backend.CANNOT_USE:
                # TODO evand 2009-05-05: Implement disabled rows as a
                # replacement?
                cell.set_property('stock-id', gtk.STOCK_DIALOG_ERROR)
            else:
                drive = self.get_gnome_drive(udi)
                if drive:
                    cell.set_property('icon-name', drive['icon'])
                else:
                    cell.set_property('stock-id', None)

        list_store = gtk.ListStore(str, int)
        self.dest_treeview.set_model(list_store)

        column_name = gtk.TreeViewColumn()
        column_name.set_title(_('Device'))
        cell_name = gtk.CellRendererText()
        cell_pixbuf = gtk.CellRendererPixbuf()
        column_name.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
        column_name.pack_start(cell_pixbuf, expand=False)
        column_name.pack_start(cell_name, expand=True)
        self.dest_treeview.append_column(column_name)
        column_name.set_cell_data_func(cell_name, column_data_func, 0)
        column_name.set_cell_data_func(cell_pixbuf, pixbuf_data_func)

        cell_name = gtk.CellRendererText()
        column_name = gtk.TreeViewColumn(_('Label'), cell_name)
        column_name.set_cell_data_func(cell_name, column_data_func, 1)
        column_name.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
        self.dest_treeview.append_column(column_name)

        cell_capacity = gtk.CellRendererText()
        column_name = gtk.TreeViewColumn(_('Capacity'), cell_capacity)
        column_name.set_cell_data_func(cell_capacity, column_data_func, 2)
        column_name.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
        self.dest_treeview.append_column(column_name)

        cell_free = gtk.CellRendererText()
        column_name = gtk.TreeViewColumn(_('Free Space'), cell_free)
        column_name.set_cell_data_func(cell_free, column_data_func, 3)
        column_name.set_sizing(gtk.TREE_VIEW_COLUMN_AUTOSIZE)
        self.dest_treeview.append_column(column_name)

        selection = self.dest_treeview.get_selection()
        selection.connect('changed', self.dest_selection_changed)

    def add_file_source_dialog(self):
        filename = ''
        filter = QString('*.iso|' + _('ISO Files') + '\n*.img|' + _('IMG Files'))
        # FIXME: should set the default path KUrl to users home dir...
        # This is all screwy as its run as root under kdesudo... Home = root and not user.. blarg!
        # Need to convert to plain string for backend to work
        filename = str(KFileDialog.getOpenFileName(KUrl(),filter))
        if not filename:
          return
        self.__backend.add_file_source(filename)

    def install(self):
        if (self.get_source() and self.get_target()):
            self.__mainWindow.hide()
            starting_up = _('Starting up')
            self.progress_bar.setLabelText(starting_up)
            self.progress_bar.show()
            self.__backend.install()
        else:
            message = _('You must select both source image and target device first.')
            self.notify(message)

    def progress(self, percent_done, remaining_time, speed, desc):
        self.progress_bar.setLabelText(desc)
        # Updating value cause dialog to re-appear from hidden (dunno why)
        if not self.progress_bar.isHidden():
            self.progress_bar.setValue(int(percent_done))

    def abort(self, *args):
        self.__backend.abort()
        sys.exit(0)

    def quit(self, *args):
        self.__backend.quit()
        sys.exit(0)

    def failed(self, title=None):
        self.__backend.cleanup()
        self.progress_bar.hide()
        if title:
            logging.critical('Install failed: ' + title)
        else:
            logging.critical('Install failed')
        KMessageBox.error(self.__mainWindow,title)
        sys.exit(1)

    def finished(self, *args):
        '''Install completed'''

        text = _('Installation is complete.  You may now reboot your computer with this USB thumb drive inserted to boot Ubuntu.')

        self.__backend.cleanup()
        self.progress_bar.hide()
        KMessageBox.information(self.__mainWindow,text)
        sys.exit(0)

    def notify(self,title):
        KMessageBox.sorry(self.__mainWindow,title)

    def warning_dialog(self):
        '''A warning dialog to show when progress dialog cancel is pressed'''

        caption = _('Quit the installation?')
        text = _('Do you really want to quit the installation now?')

        #hide the progress bar - install will still continue in bg
        self.progress_bar.hide()

        res = KMessageBox.warningYesNo(self.__mainWindow,text,caption)

        if res == KMessageBox.Yes:
            self.abort()

        #user chose not to quit, so re-show progress bar
        self.progress_bar.show()

    def format_dest_clicked(self):
        # FIXME evand 2009-04-30: This needs a big warning dialog.
        item = self.__mainWindow.ui_dest_list.currentItem()
        if not item:
            return
        udi = str(item.data(0,Qt.UserRole).toString())
        self.__backend.format_device(udi)

def format_size(size):
    """Format a partition size."""
    # Taken from ubiquity's ubiquity/misc.py
    if size < 1024:
        unit = 'B'
        factor = 1
    elif size < 1024 * 1024:
        unit = 'kB'
        factor = 1024
    elif size < 1024 * 1024 * 1024:
        unit = 'MB'
        factor = 1024 * 1024
    elif size < 1024 * 1024 * 1024 * 1024:
        unit = 'GB'
        factor = 1024 * 1024 * 1024
    else:
        unit = 'TB'
        factor = 1024 * 1024 * 1024 * 1024
    return '%.1f %s' % (float(size) / factor, unit)

def format_mb_size(size):
    if size < 1024:
        unit = 'MB'
        factor = 1
    elif size < 1024 * 1024:
        unit = 'GB'
        factor = 1024
    elif size < 1024 * 1024 * 1024:
        unit = 'TB'
        factor = 1024 * 1024
    return '%.1f %s' % (float(size) / factor, unit)
