#!/bin/bash
set -e

echo "Updating system packages..."
apt update -y
apt install -y python3 python3-venv python3-full python3-pip

echo "Preparing Python virtual environment..."
cd "$(dirname "$0")"
if [ -d "venv" ]; then
  echo "Virtual environment already detected; skipping creation."
else
  python3 -m venv venv
  echo "Virtual environment created."
fi

echo "Installing required Python packages..."
. "venv/bin/activate"
pip install --upgrade pip
pip install -r requirements.txt

echo "Setup complete."
