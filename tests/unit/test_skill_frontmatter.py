"""WP7: SKILL.md frontmatter must parse (live-log bug).

The production log showed deepagents rejecting the computer-use skill:
'Invalid YAML ... mapping values are not allowed here' — the description
line contains a colon after a plain (unquoted) scalar, which YAML reads
as a nested mapping. Skills must parse or the agent loses its operating
procedure; this test pins the regression on the REAL skill files.
"""

from __future__ import annotations

from pathlib import Path

from deepagents.middleware.skills import _parse_skill_metadata

SKILLS_ROOT = Path("src/assistant/skills")


def _skill_files() -> list[Path]:
    return sorted(SKILLS_ROOT.glob("*/SKILL.md"))


def test_every_skill_frontmatter_parses_with_real_loader() -> None:
    files = _skill_files()
    assert files, "skill fixtures must exist"
    for path in files:
        content = path.read_text()
        metadata = _parse_skill_metadata(
            content, f"skills/{path.parent.name}/SKILL.md", path.parent.name
        )
        assert metadata is not None, (
            f"{path}: deepagents rejected the frontmatter (colon in a plain"
            " description breaks yaml.safe_load); the agent loses this skill"
        )
        assert metadata["name"] == path.parent.name
        assert str(metadata["description"]).strip()


def test_colon_in_description_is_rejected_by_yaml_unless_quoted() -> None:
    """Documents the mechanism: a colon+space inside plain scalars fails."""
    unquoted = (
        "---\n"
        "name: demo\n"
        "description: Use tools: observe, act\n"
        "---\n\n# body\n"
    )
    assert _parse_skill_metadata(unquoted, "x/SKILL.md", "x") is None
    quoted = (
        "---\n"
        'name: demo\n'
        'description: "Use tools: observe, act"\n'
        "---\n\n# body\n"
    )
    parsed = _parse_skill_metadata(quoted, "x/SKILL.md", "x")
    assert parsed is not None
    assert parsed["description"] == "Use tools: observe, act"
