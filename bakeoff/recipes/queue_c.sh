#!/bin/sh
# Photoreal turnaround: zbase_new concepts -> fal (front, left, back, right) and the tarn59 sheet at 1.0.
cd "$(dirname "$0")"
PY=../../backend/.venv/Scripts/python.exe
L=../out/recipes/queue_c.log
until grep -q QUEUE-B-DONE ../out/recipes/queue_b.log 2>/dev/null; do sleep 20; done
for s in lantern wall wolf human; do
  d=../out/recipes/turn/pr_$s; mkdir -p $d
  cp ../out/recipes/photoreal/${s}_zbase_new_img.png $d/source_img.png
  cp ../out/recipes/photoreal/${s}_zbase_new_cut.png $d/source_cut.png
  $PY -c "import sys; sys.path.insert(0,'.'); import rlib; rlib.imaging.white_composite('$d/source_cut.png', '$d/source_white.png')"
  TURN_SKIP=quarter $PY turn_ab.py run pr_$s fal sheet1.0 >> $L 2>&1
done
$PY turn_ab.py report >> $L 2>&1
echo QUEUE-C-DONE >> $L
