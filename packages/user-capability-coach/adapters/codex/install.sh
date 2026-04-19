#!/usr/bin/env bash
# Install User Capability Coach for Codex / AGENTS.md environments
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Target project root (default: current directory)
PROJECT_ROOT="${COACH_PROJECT_ROOT:-$PWD}"
SKILLS_DEST="$PROJECT_ROOT/.agents/skills"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/user-capability-coach"
AGENTS_MD="$PROJECT_ROOT/AGENTS.md"
SNIPPET="$SCRIPT_DIR/agents_md_snippet.md"
MARKER_START="<!-- user-capability-coach:start -->"

echo "Installing User Capability Coach for Codex (project: $PROJECT_ROOT)..."

# 1. Create skill directories
mkdir -p "$SKILLS_DEST"

for skill in prompt-coach growth-coach; do
    src="$PACKAGE_DIR/skills/$skill"
    dst="$SKILLS_DEST/$skill"
    if [ -d "$dst" ]; then
        echo "  Updating skill: $skill"
        rm -rf "$dst"
    else
        echo "  Installing skill: $skill"
    fi
    cp -r "$src" "$dst"
done

# 2. Copy CLI tools
mkdir -p "$SKILLS_DEST/user-capability-coach"
cp -r "$PACKAGE_DIR/tools" "$SKILLS_DEST/user-capability-coach/"

# 3. Create data directory and init DB
mkdir -p "$DATA_DIR"
python3 - <<PYEOF
import sys, json
from pathlib import Path
sys.path.insert(0, '$PACKAGE_DIR')
from tools import memory, settings
memory.init_db(profile='$DATA_DIR')
# Write default config (mode=off) only if not already present (preserve user settings)
cfg_path = Path('$DATA_DIR') / 'config.json'
if not cfg_path.exists():
    settings.save(dict(settings.DEFAULT_CONFIG), profile='$DATA_DIR')
PYEOF

# 4. Append snippet to AGENTS.md (idempotent)
if [ ! -f "$AGENTS_MD" ]; then
    touch "$AGENTS_MD"
fi

if grep -q "$MARKER_START" "$AGENTS_MD" 2>/dev/null; then
    echo "  AGENTS.md snippet already present (skipping)"
else
    echo "" >> "$AGENTS_MD"
    cat "$SNIPPET" >> "$AGENTS_MD"
    echo "  Appended coaching snippet to AGENTS.md"
fi

echo ""
echo "✅ Installation complete!"
echo "   Skills:  $SKILLS_DEST/prompt-coach, $SKILLS_DEST/growth-coach"
echo "   Data:    $DATA_DIR"
echo "   CLI:     python3 $SKILLS_DEST/user-capability-coach/tools/cli.py"
echo ""
echo "Coaching is OFF by default."
