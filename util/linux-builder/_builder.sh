#!/bin/bash

set -e

export LD_LIBRARY_PATH=/vial-gui/util/python36/prefix/lib/

cd /vial-gui
python3 -m venv docker_venv
. docker_venv/bin/activate
pip install -r requirements.txt
pyinstaller vial.spec
deactivate
/pkg2appimage-*/pkg2appimage misc/Vial.yml
mv out/Vial-*.AppImage /output/Vial-x86_64.AppImage
