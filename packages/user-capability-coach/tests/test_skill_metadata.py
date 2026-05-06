from pathlib import Path


PACKAGE_DIR = Path(__file__).parent.parent


def _frontmatter_lines(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---", f"{path} must start with YAML frontmatter"
    end = lines[1:].index("---") + 1
    return lines[1:end]


def _frontmatter_value(lines: list[str], key: str) -> str:
    prefix = f"{key}:"
    matches = [line for line in lines if line.startswith(prefix)]
    assert len(matches) == 1
    return matches[0][len(prefix):].strip()


def test_skill_descriptions_are_loader_safe() -> None:
    skill_files = sorted((PACKAGE_DIR / "skills").glob("*/SKILL.md"))
    assert skill_files

    for skill_file in skill_files:
        frontmatter = _frontmatter_lines(skill_file)
        description = _frontmatter_value(frontmatter, "description")

        assert description not in {"|", ">"}, (
            f"{skill_file} must use a single-line description; block scalars "
            "can be rejected or misread by skill loaders"
        )
        assert description.startswith("Use when"), (
            f"{skill_file} description should describe triggering conditions"
        )
        assert len(description) <= 500, (
            f"{skill_file} description should stay well under loader limits"
        )
        assert len("\n".join(frontmatter)) <= 1024, (
            f"{skill_file} frontmatter should stay under the skill metadata limit"
        )
