from distutils.core import setup
from DistUtilsExtra.command import *
import os

setup(name='usb-creator',
    version='0.2.0',
    description='Ubuntu USB desktop image creator',
    author='Evan Dandrea',
    author_email='evand@ubuntu.com',
    packages=['usbcreator'],
    scripts=['bin/usb-creator-gtk','bin/usb-creator-kde'],
    data_files=[('share/usb-creator', ['gui/usbcreator-gtk.ui', 'scripts/install.py']),
                ('share/pixmaps', ['desktop/usb-creator-gtk.png', 'desktop/usb-creator-kde.png']),
                ('share/kde4/apps/usb-creator-kde', ['gui/usbcreator.ui'])],
    cmdclass = { "build" : build_extra.build_extra,
        "build_i18n" :  build_i18n.build_i18n,
        "build_help" :  build_help.build_help,
        "build_icons" :  build_icons.build_icons,
        "clean": clean_i18n.clean_i18n, 
        }
    )

