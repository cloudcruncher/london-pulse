#!/usr/bin/env bash
# Serve the site locally with the Mapbox token from .env injected into a throwaway copy (never into site/).
set -eu
cd "$(dirname "$0")/.."
rm -rf work/serve && mkdir -p work && cp -R site work/serve
key=""
if [ -f .env ]; then
  key=$(grep -E '^(export )?(MAPBOX_API_KEY|MAPBOX_TOKEN|MAPBOX_ACCESS_TOKEN)=' .env | head -1 | cut -d= -f2- | tr -d "\"' " || true)
fi
if [ -n "$key" ]; then
  printf 'window.LP_CONFIG = { mapboxToken: "%s" };\n' "$key" > work/serve/config.js
else
  echo "no Mapbox key found in .env: using the basic map"
fi
echo "http://localhost:8000/"
cd work/serve && exec python3 -m http.server 8000
