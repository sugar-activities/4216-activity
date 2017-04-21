all:

update-po:
	find -regex "./\(usbcreator\|scripts\|gui\).*\.\(py\|glade\|ui\)" > po/POTFILES.in
	echo ./gui/usbcreator-gtk.ui >> po/POTFILES.in
	echo ./desktop/usb-creator-gtk.desktop.in >> po/POTFILES.in
	echo ./desktop/usb-creator-kde.desktop.in >> po/POTFILES.in
	python setup.py build_i18n --merge-po --po-dir po
