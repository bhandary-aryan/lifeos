#!/usr/bin/env bash
# Copy frontend libraries from node_modules into static/vendor and strip source-map comments.
# Production static storage (WhiteNoise manifest) refuses to collect a file whose sourceMappingURL points at a .map we did not ship.
set -euo pipefail
cd "$(dirname "$0")"
cp node_modules/htmx.org/dist/htmx.min.js static/vendor/
cp node_modules/alpinejs/dist/cdn.min.js static/vendor/alpine.min.js
cp node_modules/chart.js/dist/chart.umd.js static/vendor/chart.umd.js
cp node_modules/lucide/dist/umd/lucide.min.js static/vendor/lucide.min.js
sed -i -E '/^\/\/[#@] sourceMappingURL=.*$/d; s#\/\/[#@] sourceMappingURL=[^[:space:]]+\s*$##' static/vendor/*.js
echo "vendor files updated"
