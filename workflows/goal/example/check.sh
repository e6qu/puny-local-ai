#!/usr/bin/env bash
# The goal is met when every test passes.
python3 -m unittest -v test_textstats 2>&1
