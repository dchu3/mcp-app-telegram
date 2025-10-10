#!/bin/bash
set -e

echo "Updating system packages..."
apt update -y
apt install -y python3 python3-venv python3-full python3-pip

echo "Preparing Python virtual environments..."
cd "$(dirname "$0")"
if [ -d "venv" ]; then
  echo "Runtime virtual environment already detected; skipping creation."
else
  python3 -m venv venv
  echo "Runtime virtual environment created at ./venv."
fi

if [ -d "venv-dev" ]; then
  echo "Developer virtual environment already detected; skipping creation."
else
  python3 -m venv venv-dev
  echo "Developer virtual environment created at ./venv-dev."
fi

echo "Installing runtime dependencies into ./venv..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

echo "Installing development dependencies into ./venv-dev..."
source venv-dev/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -r requirements-dev.txt
deactivate

echo "Developer setup complete."
echo "Activate the runtime environment with:"
echo "  source venv/bin/activate"
echo "Run the test suite with:"
echo "  ./venv-dev/bin/pytest"
