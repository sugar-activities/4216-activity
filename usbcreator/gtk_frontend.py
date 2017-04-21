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

import subprocess, sys
import os

from usbcreator.backend import Backend
import usbcreator.backend
import gettext
import locale
import pygtk
import gobject
import gtk
import gnomevfs
import logging

MIN_PERSIST = 128 # The minimal size, in megabytes, that a persistence file can be.
LOCALEDIR = 'locale'

#class GtkFrontend(Frontend):
class GtkFrontend:
    @classmethod
    def startup_failure(cls, message):
        dialog = gtk.MessageDialog(None, 0, gtk.MESSAGE_ERROR,
            gtk.BUTTONS_CLOSE, message)
        dialog.run()
        dialog.destroy()
        
    @classmethod
    def DBusMainLoop(cls):
        from dbus.mainloop.glib import DBusGMainLoop
        DBusGMainLoop(set_as_default=True)

    def __init__(self, img=None, persistent=True):

        locale.setlocale(locale.LC_ALL, '')
        gettext.bindtextdomain('usbcreator', LOCALEDIR)
        gettext.textdomain('usbcreator')

        import __builtin__
        __builtin__._ = gettext.gettext

        self.all_widgets = set()

        self.builder = gtk.Builder()
        self.builder.set_translation_domain('usbcreator')
        self.builder.add_from_file('gui/usbcreator-sugar.ui')

        for widget in self.builder.get_objects():
            # Taken from ubiquity:
            # We generally want labels to be selectable so that people can
            # easily report problems in them
            # (https://launchpad.net/bugs/41618), but GTK+ likes to put
            # selectable labels in the focus chain, and I can't seem to turn
            # this off in glade and have it stick. Accordingly, make sure
            # labels are unfocusable here.
            if isinstance(widget, gtk.Label):
                widget.set_property('can-focus', False)
            if issubclass(type(widget), gtk.Widget):
                self.all_widgets.add(widget)
                setattr(self, gtk.Widget.get_name(widget), widget)

        self.install_window.set_transient_for(self.window)
        self.warning_dialog.set_transient_for(self.window)
        self.finished_dialog.set_transient_for(self.window)
        self.failed_dialog.set_transient_for(self.window)

        gtk.window_set_default_icon_from_file('desktop/usb-creator-gtk.png')
        self.builder.connect_signals (self, None)
        self.cancelbutton.connect('clicked', lambda x: self.warning_dialog.hide())
        self.exitbutton.connect('clicked', lambda x: self.abort())
        self.progress_cancel_button.connect('clicked', lambda x: self.warning_dialog.show())
        def format_value(scale, value):
            return format_mb_size(value)
        self.persist_value.set_adjustment(
            gtk.Adjustment(0, 0, 100, 1, 10, 0))
        self.persist_value.connect('format-value', format_value)

        self.backend = None
        self.setup_sources_treeview()
        self.setup_targets_treeview()
        m = self.dest_treeview.get_model()
        self.persist_vbox.set_sensitive(False)
        self.backend = Backend(self)
        selection = self.source_treeview.get_selection()
        selection.connect('changed', lambda x: self.backend.refresh_targets())
        selection = self.dest_treeview.get_selection()
        selection.connect('changed', self.dest_selection_changed)

        
        # used to hold the backgrounded subprocess.Popen to monitor
        self.__process = None

        if img is not None:
            self.backend.add_file_source(img)
        
        if not persistent:
            self.persist_disabled.set_active(True)

    def add_timeout(self, interval, func, *args):
        '''Add a new timer for function 'func' with optional arguments. Wraps a
        similar gobject call timeout_add.'''

        timer = gobject.timeout_add(interval, func, *args)

        return timer

    def background_process(self, cmd, stdout=False, stderr=False, env=None):
        '''Add a new background process and begin running it.'''

        if stdout and stderr:
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                      universal_newlines=True, env=env)
        else:
            if stdout:
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                          stderr=sys.stderr,
                          universal_newlines=True, env=env)
            elif stderr:
                process = subprocess.Popen(cmd, stdout=sys.stdout,
                          stderr=subprocess.PIPE,
                          universal_newlines=True, env=env)

        self.__process = process

    def get_process_pid(self):
        '''Return the pid of the current backgrounded process'''

        pid = None
        if self.__process:
            pid = self.__process.pid
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
        '''Read a line of data from process data channel'''

        line = ''
        if source:
            line = str(source.readline())

        return line

    def add_io_watch(self, source, type, func, *args):
        '''Add a new watcher for function 'func' with optional arguments. Wraps a
        similar gobject call io_add_watch.'''

        watcher = None

        # our watcher is a connected signal for readyRead
        if type == 'stderr':
            watcher = gobject.io_add_watch(source.stderr,
                         gobject.IO_IN | gobject.IO_HUP,
                         func, *args)
        if type == 'stdout':
            watcher = gobject.io_add_watch(source.stdout,
                         gobject.IO_IN | gobject.IO_HUP,
                         func, *args)

        return watcher

    def add_child_watch(self, pid, func, *args):
        '''Add a new child process watcher for exitng PID. Wrap gobject call to
        child_watch_add'''

        gobject.child_watch_add(pid, func, *args)

    def delete_timeout(self, timer):
        '''Remove the specified timer. Wraps gobject source_remove call.'''

        return gobject.source_remove(timer)

    def delete_io_watch(self, source):
        '''Remove the specified watcher. Wraps gobject source_remove call.'''

        return gobject.source_remove(source)

    def set_targets(self, targets_list):
        '''Populates the treeview model with the UDIs of the possible disks or
        partitions to use, as generated in backend.refresh_targets.'''

        # Save the selection in the event that the disk or partition that the
        # user selected is in the new list of disks and partitions generated by
        # the backend.  In which case, select it again.

        sel = self.dest_treeview.get_selection()
        model, iterator = sel.get_selected()
        if iterator:
            saved_selection = model[iterator][0]
        else:
            saved_selection = None
        
        model.clear()

        for target in targets_list:
            i = model.append([target])
            if target == saved_selection:
                sel.select_iter(i)

        sel = self.dest_treeview.get_selection()
        if not iterator:
            sel.select_path(0)

    def add_source(self, source):
        logging.debug('add_source: %s' % str(source))
        model = self.source_treeview.get_model()
        model.append([source])

        sel = self.source_treeview.get_selection()
        m, i = sel.get_selected()
        if not i:
            sel.select_path(0)
    
    def remove_source(self, source):
        model = self.source_treeview.get_model()
        iterator = m.get_iter_first()
        to_delete = None
        while iterator is not None:
            if model.get_value(iterator, 0) == source:
                to_delete = iterator
            iterator = m.iter_next(iterator)
        if to_delete is not None:
            model.remove(to_delete)
        
        # TODO evand 2009-04-28: Confirm that this works by ejecting a CD.
        sel = self.source_treeview.get_selection()
        m, i = sel.get_selected()
        if not i:
            sel.select_path(0)

    def get_source(self):
        '''Returns the UDI of the selected source image.'''
        sel = self.source_treeview.get_selection()
        m, i = sel.get_selected()
        if i:
            return m[i][0]
        else:
            logging.debug('No source selected.')
            return ''

    def get_target(self):
        '''Returns the UDI of the selected target disk or partition.'''
        sel = self.dest_treeview.get_selection()
        m, i = sel.get_selected()
        if i:
            return m[i][0]
        else:
            logging.debug('No target selected.')
            return ''
    
    def get_persistence(self):
        if self.persist_enabled.get_active() and \
            self.persist_enabled.state != gtk.STATE_INSENSITIVE:
            val = self.persist_value.get_value()
            return int(val)
        else:
            return 0

    def get_gnome_drive(self, udi):
        monitor = gnomevfs.VolumeMonitor()
        for drive in monitor.get_connected_drives():
            if drive.get_hal_udi() == udi:
                return drive

    def setup_sources_treeview(self):
        def column_data_func(layout, cell, model, iterator, column):
            if not self.backend:
                return
            udi = model[iterator][0]
            dev = self.backend.cds[udi]
            if column == 0:
                drive = self.get_gnome_drive(udi)
                if drive:
                    cell.set_property('text', drive.get_display_name())
                else:
                    cell.set_property('text', dev['device'])
            elif column == 1:
                cell.set_property('text', dev['label'])
            elif column == 2:
                cell.set_property('text', format_size(dev['capacity']))

        def pixbuf_data_func(column, cell, model, iterator):
            if not self.backend:
                return
            udi = model[iterator][0]
            drive = self.get_gnome_drive(udi)
            type = self.backend.cds[udi]['type']
            if drive:
                cell.set_property('icon-name', drive.get_icon())
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

        # Drag and drop support.
        # FIXME evand 2009-04-28: Anything can be dropped on the source
        # treeview.  Ideally, the user should only be able to drop ISO and IMG
        # files.

        def motion_cb(wid, context, x, y, time):
            context.drag_status(gtk.gdk.ACTION_COPY, time)
            return True
        
        def drop_cb(w, context, x, y, time):
            target_list = w.drag_dest_get_target_list()
            target = w.drag_dest_find_target(context, target_list)
            selection = w.drag_get_data(context, target)
            context.finish(True, True)
            return True

        def data_received_cb(w, context, x, y, selection, target_type, timestamp):
            # FIXME evand 2009-04-28: Use the GNOME VFS?  Test with a sshfs
            # nautilus window.
            file = selection.data.strip('\r\n\x00')
            if file.startswith('file://'):
                file = file[7:]
            elif file.startswith('file:'):
                file = file[5:]
            self.backend.add_file_source(file)
            
        self.source_treeview.drag_dest_set(gtk.gdk.ACTION_DEFAULT,
            [('text/uri-list', 0, 600)], gtk.gdk.ACTION_COPY)
        self.source_treeview.connect('drag_motion', motion_cb)
        self.source_treeview.connect('drag_drop', drop_cb)
        self.source_treeview.connect('drag-data-received', data_received_cb)

    def update_target(self, udi):
        m = self.dest_treeview.get_model()
        iterator = m.get_iter_first()
        while iterator is not None:
            if m.get_value(iterator, 0) == udi:
                m.row_changed(m.get_path(iterator), iterator)
                break
            iterator = m.iter_next(iterator)

    def dest_selection_changed(self, selection):
        '''The selected partition has changed and the bounds on the persistence
        slider need to be changed, or the slider needs to be disabled, to
        reflect the amount of free space on the partition.'''

        # XXX evand 2009-05-06: I'm tempted to move this into the backend,
        # should it get any more complicated, but right now most of the work
        # here is for the frontend.

        # TODO evand 2009-05-06: When we factor in the difference in size after
        # Ubuntu CD directories are removed (casper, pool, etc), we should add
        # it as a property of the partition, as we'll need to factor it in
        # here.
        self.persist_vbox.set_sensitive(False)
        self.persist_enabled_vbox.set_sensitive(False)
        
        if not self.backend:
            return
        model, iterator = selection.get_selected()
        if not iterator:
            return
        target_udi = model[iterator][0]
        source_udi = self.get_source()
        if not source_udi:
            return
        source = self.backend.cds[source_udi]
        if target_udi in self.backend.partitions:
            # We're dealing with a partition, therefore we need to calculate
            # how much, if any, extra space can be used for the persistence
            # file.
            target = self.backend.partitions[target_udi]
            persist_max = (target['free'] - source['capacity']) / 1024 / 1024
            if persist_max > MIN_PERSIST:
                self.persist_vbox.set_sensitive(True)
                self.persist_enabled_vbox.set_sensitive(True)
                self.persist_value.set_range(MIN_PERSIST, persist_max)

    def setup_targets_treeview(self):
        def column_data_func(layout, cell, model, iterator, column):
            if not self.backend:
                return
            udi = model[iterator][0]
            if udi in self.backend.devices:
                dev = self.backend.devices[udi]
            else:
                dev = self.backend.partitions[udi]
            drive = self.get_gnome_drive(udi)
            if column == 0:
                if drive:
                    cell.set_property('text', drive.get_display_name() )
                else:
                    cell.set_property('text', dev['device'])
            elif column == 1:
                cell.set_property('text', dev['label'])
            elif column == 2:
                cell.set_property('text', format_size(dev['capacity']))
            elif column == 3:
                cell.set_property('text', format_size(dev['free']))

        def pixbuf_data_func(column, cell, model, iterator):
            if not self.backend:
                return
            udi = model[iterator][0]
            if udi in self.backend.devices:
                status = self.backend.devices[udi]['status']
            else:
                status = self.backend.partitions[udi]['status']

            if status == Backend.NEED_SPACE:
                cell.set_property('stock-id', gtk.STOCK_DIALOG_WARNING)
            elif status == Backend.CANNOT_USE:
                # TODO evand 2009-05-05: Implement disabled rows as a
                # replacement?
                cell.set_property('stock-id', gtk.STOCK_DIALOG_ERROR)
            else:
                drive = self.get_gnome_drive(udi)
                if drive:
                    cell.set_property('icon-name', drive.get_icon())
                else:
                    cell.set_property('stock-id', None)

        list_store = gtk.ListStore(str)
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

    def add_file_source_dialog(self, *args):
        filename = ''
        chooser = gtk.FileChooserDialog(title=None,action=gtk.FILE_CHOOSER_ACTION_OPEN,
            buttons=(gtk.STOCK_CANCEL,gtk.RESPONSE_CANCEL,gtk.STOCK_OPEN,gtk.RESPONSE_OK))
        for p, n in (('*.iso', _('ISO Files')), ('*.img', _('IMG Files'))):
            filter = gtk.FileFilter()
            filter.add_pattern(p)
            filter.set_name(n)
            chooser.add_filter(filter)
        # FIXME evand 2009-04-28: I think there's a bug open about this block
        # of code.  Looks fairly wonky.
        if 'SUDO_USER' in os.environ:
            folder = os.path.expanduser('~' + os.environ['SUDO_USER'])
        else:
            folder = os.path.expanduser('~')
        chooser.set_current_folder(folder)
        response = chooser.run()
        if response == gtk.RESPONSE_OK:
            filename = chooser.get_filename()
            self.backend.add_file_source(filename)
        chooser.destroy()

    def install(self, widget):
        # FIXME evand 2009-05-01: If we hide the window and the backend returns
        # because an error occurred that can bring us back to the main
        # interface, the user wont see it.
        starting_up = _('Starting up')
        self.progress_title.set_markup('<big><b>' + starting_up + '</b></big>')
        self.progress_info.set_text('')
        self.install_window.show()
        self.backend.install()

    def progress(self, percent_done, remaining_time, speed, desc):
        if remaining_time is None or speed is None:
            text = _('%d%% complete') % percent_done
        else:
            minutes = int(remaining_time / 60)
            seconds = int(remaining_time % 60)
            text = _('%d%% complete (%dm%ss remaining)') % \
                     (percent_done, minutes, seconds)
        self.progress_info.set_text(text)
        self.progress_bar.set_fraction(percent_done / 100.0)
        self.progress_title.set_markup('<big><b>' + desc + '</b></big>')
    
    def abort(self, *args):
        self.backend.abort()
        sys.exit(0)

    def quit(self, *args):
        self.backend.quit()
        sys.exit(0)

    def failed(self, title=None):
        self.backend.cleanup()
        self.warning_dialog.hide()
        self.install_window.hide()
        if title:
            self.failed_dialog_label.set_text(title)
            logging.critical('Install failed: ' + title)
        else:
            logging.critical('Install failed')
        self.failed_dialog.run()
        gtk.main_quit()
    
    def finished(self, *args):
        self.warning_dialog.hide()
        self.install_window.hide()
        self.finished_dialog.run()
        sys.exit(0)

    def notify(self, message):
        dialog = gtk.MessageDialog(None, 0, gtk.MESSAGE_WARNING,
            gtk.BUTTONS_CLOSE, message)
        dialog.run()
        dialog.destroy()

    def format_dest_clicked(self, *args):
        # FIXME evand 2009-04-30: This needs a big warning dialog.
        model, iterator = self.dest_treeview.get_selection().get_selected()
        if not iterator:
            return
        udi = model[iterator][0]
        self.backend.format_device(udi)

    def open_dest_folder(self, *args):
        model, iterator = self.dest_treeview.get_selection().get_selected()
        if not iterator:
            logging.error('Open button pressed but there was no selection.')
            return
        disk = model[iterator][0]
        self.backend.open_mountpoint(udi)
    

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

# vim: set ai et sts=4 tabstop=4 sw=4:
