#!/usr/bin/env bash
# Start Highlighter on Mac or Linux:  ./run.sh
set -e
cd "$(dirname "$0")"
if ! command -v ffmpeg >/dev/null; then
  echo "ffmpeg is not installed. On a Mac run:  brew install ffmpeg"
  exit 1
fi
if [ ! -d .venv ]; then
  echo "First run: setting things up, this takes a minute..."
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
exec .venv/bin/python app.py
