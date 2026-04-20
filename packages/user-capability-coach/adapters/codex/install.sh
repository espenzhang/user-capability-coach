#!/usr/bin/env bash
# Install User Capability Coach for Codex / AGENTS.md environments
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCOPE="${COACH_CODEX_SCOPE:-project}"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/user-capability-coach"
SNIPPET="$SCRIPT_DIR/agents_md_snippet.md"
MARKER_START="<!-- user-capability-coach:start -->"

case "$SCOPE" in
    project)
        PROJECT_ROOT="${COACH_PROJECT_ROOT:-$PWD}"
        INSTALL_ROOT="$PROJECT_ROOT"
        SKILLS_DEST="$PROJECT_ROOT/.agents/skills"
        AGENTS_MD="$PROJECT_ROOT/AGENTS.md"
        COACH_CMD=".agents/skills/user-capability-coach/coach"
        echo "Installing User Capability Coach for Codex (project: $PROJECT_ROOT)..."
        ;;
    global)
        CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
        INSTALL_ROOT="$CODEX_HOME_DIR"
        SKILLS_DEST="$CODEX_HOME_DIR/skills"
        AGENTS_MD="$CODEX_HOME_DIR/AGENTS.md"
        COACH_CMD="$CODEX_HOME_DIR/skills/user-capability-coach/coach"
        echo "Installing User Capability Coach for Codex (global home: $CODEX_HOME_DIR)..."
        ;;
    *)
        echo "Unsupported COACH_CODEX_SCOPE: $SCOPE" >&2
        echo "Expected one of: project, global" >&2
        exit 1
        ;;
esac

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

# 3. Create Codex-specific wrapper so AGENTS.md never needs to guess which
# profile/path "coach" should target when Claude and Codex coexist.
COACH_WRAPPER="$SKILLS_DEST/user-capability-coach/coach"
cat > "$COACH_WRAPPER" <<WRAPPER
#!/usr/bin/env bash
exec python3 "$SKILLS_DEST/user-capability-coach/tools/cli.py" --profile "$DATA_DIR" "\$@"
WRAPPER
chmod +x "$COACH_WRAPPER"

# 4. Create data directory and init DB
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

# 5. Append snippet to AGENTS.md (idempotent)
mkdir -p "$INSTALL_ROOT"
if [ ! -f "$AGENTS_MD" ]; then
    touch "$AGENTS_MD"
fi

render_snippet() {
    AGENTS_MD="$AGENTS_MD" SNIPPET="$SNIPPET" COACH_CMD="$COACH_CMD" python3 - <<'PYEOF'
import os
import pathlib
import re

agents_md = pathlib.Path(os.environ["AGENTS_MD"])
template = pathlib.Path(os.environ["SNIPPET"]).read_text().rstrip()
new_snippet = template.replace("__COACH_CMD__", os.environ["COACH_CMD"])
content = agents_md.read_text()
pattern = re.compile(
    r"<!-- user-capability-coach:start -->.*?<!-- user-capability-coach:end -->",
    re.DOTALL,
)
if pattern.search(content):
    content = pattern.sub(new_snippet, content)
else:
    content = content.rstrip()
    if content:
        content += "\n\n"
    content += new_snippet + "\n"
agents_md.write_text(content)
PYEOF
}

if grep -q "$MARKER_START" "$AGENTS_MD" 2>/dev/null; then
    render_snippet
    echo "  Refreshed AGENTS.md snippet (existing markers updated in place)"
else
    render_snippet
    echo "  Appended coaching snippet to AGENTS.md"
fi

echo ""
echo "✅ Installation complete!"
echo "   Skills:  $SKILLS_DEST/prompt-coach, $SKILLS_DEST/growth-coach"
echo "   Data:    $DATA_DIR"
echo "   CLI:     $COACH_CMD"
echo ""
echo "Coaching is OFF by default."
