#!/usr/bin/env bash
# One-command setup for linux-guardian: creates a venv, installs deps, and
# scaffolds config.yaml from the example if you don't have one yet.
set -e
cd "$(dirname "$0")"

echo "Creating virtualenv (.venv)..."
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

if [ ! -f config.yaml ]; then
  cp config.example.yaml config.yaml
  echo "Created config.yaml from config.example.yaml — edit it for your server before running."
else
  echo "config.yaml already exists, leaving it as-is."
fi

echo ""
echo "Setup complete. Try it:"
echo "  .venv/bin/python guardian.py --list-checks"
echo "  .venv/bin/python guardian.py --config config.yaml --validate-config"
echo "  .venv/bin/python guardian.py --config config.yaml --dry-run"
echo ""
echo "To run on a schedule, add a cron entry:"
echo "  */15 * * * * cd $(pwd) && .venv/bin/python guardian.py --config config.yaml"
