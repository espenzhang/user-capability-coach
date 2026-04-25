#!/usr/bin/env bash
# Uninstall User Capability Coach from Claude Code
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DEST="$HOME/.claude/skills"
DATA_DIR="$HOME/.claude/user-capability-coach"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
MARKER_START="<!-- user-capability-coach:start -->"
MARKER_END="<!-- user-capability-coach:end -->"

echo "Uninstalling User Capability Coach from Claude Code..."

# 1. Remove skill directories
for skill in prompt-coach growth-coach; do
    dst="$SKILLS_DEST/$skill"
    if [ -d "$dst" ]; then
        rm -rf "$dst"
        echo "  Removed skill: $skill"
    fi
done

# 2. Remove snippet from CLAUDE.md
if [ -f "$CLAUDE_MD" ] && grep -q "$MARKER_START" "$CLAUDE_MD" 2>/dev/null; then
    # Use Python for portable multi-line deletion
    python3 - <<'PYEOF'
import re, pathlib, sys
path = pathlib.Path.home() / ".claude" / "CLAUDE.md"
content = path.read_text()
pattern = r'\n*<!-- user-capability-coach:start -->.*?<!-- user-capability-coach:end -->\n?'
new_content = re.sub(pattern, '', content, flags=re.DOTALL)
path.write_text(new_content)
print("  Removed coaching snippet from CLAUDE.md")
PYEOF
fi

# 3. Remove installed executable/tooling, then ask about data directory
if [ -f "$DATA_DIR/coach" ]; then
    rm "$DATA_DIR/coach"
    echo "  Removed coach wrapper"
fi
if [ -d "$DATA_DIR/tools" ]; then
    rm -rf "$DATA_DIR/tools"
    echo "  Removed installed CLI tools"
fi

echo ""
read -r -p "Delete coaching data at $DATA_DIR? [y/N] " confirm
if [[ "$confirm" =~ ^[Yy]$ ]]; then
    rm -rf "$DATA_DIR"
    echo "  Deleted coaching data"
else
    echo "  Coaching data preserved at $DATA_DIR"
fi

# 4. Remove bin symlink
if [ -L "$HOME/.local/bin/coach" ]; then
    rm "$HOME/.local/bin/coach"
    echo "  Removed ~/.local/bin/coach symlink"
fi

echo ""
echo "✅ Uninstall complete."
