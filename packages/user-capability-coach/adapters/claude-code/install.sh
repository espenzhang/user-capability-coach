#!/usr/bin/env bash
# Install User Capability Coach for Claude Code
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

SKILLS_SRC="$PACKAGE_DIR/skills"
SKILLS_DEST="$HOME/.claude/skills"
DATA_DIR="$HOME/.claude/user-capability-coach"
CLAUDE_MD="$HOME/.claude/CLAUDE.md"
SNIPPET="$SCRIPT_DIR/claude_md_snippet.md"
MARKER_START="<!-- user-capability-coach:start -->"
MARKER_END="<!-- user-capability-coach:end -->"

echo "Installing User Capability Coach for Claude Code..."

# 1. Create skill directories
mkdir -p "$SKILLS_DEST"

for skill in prompt-coach growth-coach; do
    src="$SKILLS_SRC/$skill"
    dst="$SKILLS_DEST/$skill"
    if [ -d "$dst" ]; then
        echo "  Updating skill: $skill"
        rm -rf "$dst"
    else
        echo "  Installing skill: $skill"
    fi
    cp -r "$src" "$dst"
done

# 2. Create data directory, copy CLI tools, and initialize DB
mkdir -p "$DATA_DIR"
rm -rf "$DATA_DIR/tools"
cp -r "$PACKAGE_DIR/tools" "$DATA_DIR/tools"
python3 -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '$DATA_DIR')
from tools import memory, settings
memory.init_db(profile='$DATA_DIR')
# Write default config only if not already present (preserve existing user settings)
cfg_path = Path('$DATA_DIR') / 'config.json'
if not cfg_path.exists():
    settings.save(dict(settings.DEFAULT_CONFIG), profile='$DATA_DIR')
" 2>/dev/null || true

# 3. Set up coach wrapper script
COACH_BIN="$DATA_DIR/coach"
cat > "$COACH_BIN" <<WRAPPER
#!/usr/bin/env bash
exec python3 "$DATA_DIR/tools/cli.py" --profile "$DATA_DIR" "\$@"
WRAPPER
chmod +x "$COACH_BIN"

# Also create a symlink in ~/.local/bin if it exists
if [ -d "$HOME/.local/bin" ]; then
    ln -sf "$COACH_BIN" "$HOME/.local/bin/coach" 2>/dev/null || true
fi

# 4. Append snippet to CLAUDE.md (idempotent)
if [ ! -f "$CLAUDE_MD" ]; then
    touch "$CLAUDE_MD"
fi

if grep -q "$MARKER_START" "$CLAUDE_MD" 2>/dev/null; then
    # Refresh the snippet in place so re-running install picks up any
    # changes (new modes, updated instructions). Uses Python for portable
    # multi-line replacement between the two markers.
    CLAUDE_MD="$CLAUDE_MD" SNIPPET="$SNIPPET" python3 - <<'PYEOF'
import os, re, pathlib
claude_md = pathlib.Path(os.environ["CLAUDE_MD"])
new_snippet = pathlib.Path(os.environ["SNIPPET"]).read_text().rstrip() + "\n"
content = claude_md.read_text()
pattern = re.compile(
    r"<!-- user-capability-coach:start -->.*?<!-- user-capability-coach:end -->",
    re.DOTALL,
)
# Strip trailing newline from new_snippet's markers-end for clean replacement
new_block = new_snippet.strip()
new_content = pattern.sub(new_block, content)
claude_md.write_text(new_content)
print("  Refreshed CLAUDE.md snippet (existing markers updated in place)")
PYEOF
else
    echo "" >> "$CLAUDE_MD"
    cat "$SNIPPET" >> "$CLAUDE_MD"
    echo "  Appended coaching snippet to CLAUDE.md"
fi

echo ""
echo "✅ Installation complete!"
echo "   Skills: $SKILLS_DEST/prompt-coach, $SKILLS_DEST/growth-coach"
echo "   Data:   $DATA_DIR"
echo "   CLI:    $COACH_BIN"
echo ""
echo "Coaching is OFF by default. To enable, run:"
echo "   coach enable"
echo "   or say '/coach on' in Claude Code"
