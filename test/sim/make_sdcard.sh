#!/bin/sh
# Build the bench SD image the way a real card is formatted: FAT with 32 KB
# clusters. A 512 B-cluster image (the mkfs default for a 64 MB volume) makes
# SdFat walk 64x more FAT entries per backward seek and opens a big book 1.7x
# slower than silicon would. Volumes >= ~1100 MB do not mount in the SD model
# yet, so this stays at 160 MB / FAT16, which is the largest FAT16 32 KB layout.
#
#   ./make_sdcard.sh <content dir> [out.img]
#
# The content dir holds the EPUBs and an optional .crosspoint/ state tree.
# macOS only (hdiutil/newfs_msdos). The image is gitignored: never commit it.
set -eu
SRC=${1:?content dir}; OUT=${2:-sdcard-f16-32k.img}
rm -f "$OUT"; mkfile -n 160m "$OUT"
DEV=$(hdiutil attach -imagekey diskimage-class=CRawDiskImage -nomount "$OUT" | awk '{print $1}' | head -1)
newfs_msdos -F 16 -c 64 -v XTEINK "${DEV/disk/rdisk}" >/dev/null
hdiutil detach "$DEV" -quiet
VOL=$(hdiutil attach -imagekey diskimage-class=CRawDiskImage -readwrite -nobrowse "$OUT" | awk '/Volumes/{print $NF}')
( cd "$SRC" && COPYFILE_DISABLE=1 cp -R . "$VOL/" )
find "$VOL" -name '._*' -delete
ls -A "$VOL"
hdiutil detach "$VOL" -quiet
python3 -c "import struct,sys;b=open(sys.argv[1],'rb').read(512);bps,spc=struct.unpack_from('<HB',b,11);print('cluster bytes',bps*spc)" "$OUT"
