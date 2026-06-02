#!/bin/bash
cd "$(dirname "$0")"
open http://127.0.0.1:5066
python3 server.py
