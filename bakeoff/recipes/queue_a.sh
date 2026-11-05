#!/bin/sh
# Trimmed sequential queue for the 3060 (instance a) after card b was switched off.
cd "$(dirname "$0")"
PY=../../backend/.venv/Scripts/python.exe
L=../out/recipes/queue_a.log
export RECIPE_SEEDS=1101
$PY concepts.py pixred kleinpix >> $L 2>&1
$PY styles_check.py v3 retro64 lowpoly_flat >> $L 2>&1
$PY styles_check.py v4 clay >> $L 2>&1
$PY styles_check.py sheet >> $L 2>&1
$PY turn_ab.py source knight >> $L 2>&1
$PY turn_ab.py source chest >> $L 2>&1
$PY turn_ab.py run knight fal cam1.0 sheet1.0 >> $L 2>&1
$PY turn_ab.py run chest fal cam1.0 sheet1.0 >> $L 2>&1
$PY turn_ab.py report >> $L 2>&1
echo QUEUE-DONE >> $L
