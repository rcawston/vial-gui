#!/bin/bash

set -e

cd /vial-gui
python3 -m venv docker_venv
. docker_venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --noconfirm vial.spec
deactivate
/pkg2appimage-*/pkg2appimage misc/Vial.yml
mv out/Vial-*.AppImage /output/Vial-x86_64.AppImage
