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

echo "Installing development dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt

echo "Developer setup complete."
echo "Activate the environment with:"
echo "  source venv/bin/activate"
echo "Run the test suite with:"
echo "  pytest"
