#!/usr/bin/python

# Copyright (C) 2008 Canonical Ltd.

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

import getopt
import os
import stat
import sys
import shutil
import subprocess
import gettext
import locale

LOCALEDIR = "/usr/share/locale"
locale.setlocale(locale.LC_ALL, '')
gettext.bindtextdomain('usbcreator', LOCALEDIR)
gettext.textdomain('usbcreator')

import __builtin__
__builtin__._ = gettext.gettext


def popen(cmd):
    print >>sys.stderr, str(cmd)
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE,
        stderr=sys.stderr, stdin=subprocess.PIPE)
    process.communicate()
    return process

def main(source, target, persist):
    # Some of the code in this function was copied from Ubiquity's
    # scripts/install.py

    sys.stdout.write(_('Copying files\n'))
    sys.stdout.flush()
    if not os.path.exists(source) or not os.path.exists(target):
        print >>sys.stderr, 'Source or target does not exist.'
        sys.exit(1)
    for dirpath, dirnames, filenames in os.walk(source):
        sp = dirpath[len(source) + 1:]
        for name in dirnames + filenames:
            relpath = os.path.join(sp, name)
            sourcepath = os.path.join(source, relpath)
            targetpath = os.path.join(target, relpath)
            st = os.lstat(sourcepath)
            mode = stat.S_IMODE(st.st_mode)
            if stat.S_ISLNK(st.st_mode):
                if os.path.lexists(targetpath):
                    os.unlink(targetpath)
                linkto = os.readlink(sourcepath)
                #os.symlink(linkto, targetpath)
                # FIXME: Handle this somehow?
                sys.stderr.write('Tried to symlink %s -> %s\n' % \
                    (linkto, targetpath))
                pass
            elif stat.S_ISDIR(st.st_mode):
                if not os.path.isdir(targetpath):
                    os.mkdir(targetpath, mode)
            elif stat.S_ISCHR(st.st_mode):
                os.mknod(targetpath, stat.S_IFCHR | mode, st.st_rdev)
            elif stat.S_ISBLK(st.st_mode):
                os.mknod(targetpath, stat.S_IFBLK | mode, st.st_rdev)
            elif stat.S_ISFIFO(st.st_mode):
                os.mknod(targetpath, stat.S_IFIFO | mode)
            elif stat.S_ISSOCK(st.st_mode):
                os.mknod(targetpath, stat.S_IFSOCK | mode)
            elif stat.S_ISREG(st.st_mode):
                if os.path.exists(targetpath):
                    os.unlink(targetpath)
                fail = False
                try:
                    sourcefh = open(sourcepath, 'rb')
                    targetfh = open(targetpath, 'wb')
                    # TODO: md5 check.
                    try:
                        shutil.copyfileobj(sourcefh, targetfh)
                    except Exception, e:
                        fail = True
                        print >>sys.stderr, str(e) + '\n'
                finally:
                    sourcefh.close()
                    targetfh.close()
                    if fail:
                        sys.exit(1)
    
    # Modify contents to be suitable for a USB drive.
    popen(['rm', '-rf', '%s/syslinux' % target])
    popen(['mv', '%s/isolinux' % target, '%s/syslinux' % target])
    popen(['mv', '%s/syslinux/isolinux.cfg' % target,
            '%s/syslinux/syslinux.cfg' % target])
    for filename in ['syslinux/syslinux.cfg', 'syslinux/text.cfg']:
        f = None
        try:
            f = open(os.path.join(target, filename), 'r')
            label = ''
            to_write = []
            for line in f.readlines():
                line = line.strip('\n').split(' ')
                for l in line:
                    if l:
                        command = l
                        break
                if command.lower() == 'append':
                    pos = line.index(command) + 2
                    if label not in ('check', 'memtest', 'hd'):
                        if persist != '0':
                            line.insert(pos, 'persistent')
                        line.insert(pos, 'cdrom-detect/try-usb=true')
                    if label not in ('memtest', 'hd'):
                        line.insert(pos, 'noprompt')
                elif command.lower() == 'label':
                    label = line[1].strip()
                to_write.append(' '.join(line) + '\n')
            f.close()
            f = open(os.path.join(target, filename), 'w')
            f.writelines(to_write)
        except Exception, e:
            print >>sys.stderr, str(e) + '\n'
            print >>sys.stderr, 'Unable to add persistence to the ' \
                'configuration (%s)\n' % filename
        finally:
            if f:
                f.close()
    
    # /syslinux.cfg is present to work around a bug.
    # TODO: find bug number.  Wasn't this fixed in Intrepid?
    popen(['cp', '%s/syslinux/syslinux.cfg' % target,
        '%s/syslinux.cfg' % target])
    
    if persist != '0':
        sys.stdout.write(_('Creating persistence file\n'))
        sys.stdout.flush()
        popen(['dd', 'if=/dev/zero', 'bs=1M', 'of=%s/casper-rw' % target, 'count=%s' % persist])
        sys.stdout.write(_('Making persistence filesystem\n'))
        sys.stdout.flush()
        popen(['mkfs.ext3', '-F', '%s/casper-rw' % target])
    sys.stdout.write('Syncing media to disk. Please Wait\n')
    sys.stdout.flush()
    popen(['sync'])

if __name__ == '__main__':
    source = ''
    target = ''
    persist = 0
    try:
        opts, args = getopt.getopt(sys.argv[1:], 's:t:p:')
    except getopt.GetoptError:
        sys.exit(1)
    for opt, arg in opts:
        if opt == '-s':
            source = arg
        elif opt == '-t':
            target = arg
        elif opt == '-p':
            persist = arg
    if source and target:
        main(source, target, persist)
        sys.exit(0)
    else:
        print >> sys.stderr, \
            'Source or target device not specified.  Cannot continue.\n'
        sys.exit(1)

