#!/usr/bin/env bash
# Uninstall User Capability Coach from Codex
set -euo pipefail

PROJECT_ROOT="${COACH_PROJECT_ROOT:-$PWD}"
SKILLS_DEST="$PROJECT_ROOT/.agents/skills"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/user-capability-coach"
AGENTS_MD="$PROJECT_ROOT/AGENTS.md"
MARKER_START="<!-- user-capability-coach:start -->"

echo "Uninstalling User Capability Coach from $PROJECT_ROOT..."

# 1. Remove skill directories
for skill in prompt-coach growth-coach user-capability-coach; do
    dst="$SKILLS_DEST/$skill"
    if [ -d "$dst" ]; then
        rm -rf "$dst"
        echo "  Removed: $dst"
    fi
done

# 2. Remove snippet from AGENTS.md
# Pass AGENTS_MD path via env so Python always finds the correct file,
# independent of whether COACH_PROJECT_ROOT was set by the caller.
if [ -f "$AGENTS_MD" ] && grep -q "$MARKER_START" "$AGENTS_MD" 2>/dev/null; then
    AGENTS_MD="$AGENTS_MD" python3 - <<'PYEOF'
import os, re, pathlib
agents_md = pathlib.Path(os.environ["AGENTS_MD"])
content = agents_md.read_text()
pattern = r'\n*<!-- user-capability-coach:start -->.*?<!-- user-capability-coach:end -->\n?'
new_content = re.sub(pattern, '', content, flags=re.DOTALL)
agents_md.write_text(new_content)
print(f"  Removed coaching snippet from {agents_md}")
PYEOF
fi

# 3. Ask about data
echo ""
read -r -p "Delete coaching data at $DATA_DIR? [y/N] " confirm
if [[ "$confirm" =~ ^[Yy]$ ]]; then
    rm -rf "$DATA_DIR"
    echo "  Deleted coaching data"
else
    echo "  Data preserved at $DATA_DIR"
fi

echo "✅ Uninstall complete."
