#!/bin/sh
# After queue_a: finish the knight sheet split, the photoreal painters, then the report.
cd "$(dirname "$0")"
PY=../../backend/.venv/Scripts/python.exe
L=../out/recipes/queue_b.log
until grep -q QUEUE-DONE ../out/recipes/queue_a.log; do sleep 20; done
$PY turn_ab.py run knight sheet1.0 >> $L 2>&1
$PY photoreal.py concepts >> $L 2>&1
$PY photoreal.py report >> $L 2>&1
$PY turn_ab.py report >> $L 2>&1
echo QUEUE-B-DONE >> $L
