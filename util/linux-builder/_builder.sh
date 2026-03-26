#!/bin/bash

set -e

cd /vial-gui
python3 -m venv docker_venv
. docker_venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --noconfirm vial.spec
deactivate

VERSION=$(python3 -c 'import json; print(json.load(open("src/build/settings/base.json"))["version"])')

rm -rf target/pkgroot target/Vial.deb
mkdir -p target/pkgroot/opt/Vial
mkdir -p target/pkgroot/usr/share/applications
mkdir -p target/pkgroot/usr/share/icons/hicolor/256x256/apps

cp -a dist/Vial/. target/pkgroot/opt/Vial/
cp src/main/icons/linux/256.png target/pkgroot/usr/share/icons/hicolor/256x256/apps/Vial.png
cat > target/pkgroot/usr/share/applications/Vial.desktop <<EOF
[Desktop Entry]
Type=Application
Name=Vial
Exec=/opt/Vial/Vial-bin
Icon=Vial
Categories=Utility;
Terminal=false
EOF

fpm -s dir -t deb -n Vial -v "${VERSION}" -C target/pkgroot .
/pkg2appimage-*/pkg2appimage misc/Vial.yml
mv out/Vial-*.AppImage /output/Vial-x86_64.AppImage
