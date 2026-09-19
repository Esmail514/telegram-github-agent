"""
Tests for the agent context builder.
"""
import tempfile
from pathlib import Path

from app.runner.context_builder import AgentContextBuilder
from app.runner.jobs import generate_job_id, make_branch_name, make_commit_message, make_pr_title

# ---- Context builder tests ----

def test_build_tree_basic():
    builder = AgentContextBuilder()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "src").mkdir()
        (root / "src" / "main.py").write_text("print('hello')")
        (root / "README.md").write_text("# Test")

        tree = builder._build_tree(root)
        assert "src" in tree
        assert "main.py" in tree or "README" in tree


def test_read_key_files():
    builder = AgentContextBuilder()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "README.md").write_text("# My Project\nSome docs here.")
        (root / "package.json").write_text('{"name": "test", "version": "1.0"}')

        files = builder._read_key_files(root)
        assert "README.md" in files
        assert "My Project" in files["README.md"]
        assert "package.json" in files


def test_read_key_files_truncates_large():
    builder = AgentContextBuilder()
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "README.md").write_text("X" * 5000)

        files = builder._read_key_files(root)
        assert len(files["README.md"]) <= 3000


# ---- Jobs helper tests ----

def test_generate_job_id():
    job_id = generate_job_id("owner/my-repo", 42)
    assert job_id.startswith("job_my_repo_42_")
    assert len(job_id) > 10


def test_make_branch_name():
    assert make_branch_name(42) == "ai/issue-42"
    assert make_branch_name(1) == "ai/issue-1"


def test_make_commit_message_feat():
    msg = make_commit_message("Add PDF compression", 42)
    assert msg.startswith("feat:")
    assert "Closes #42" in msg


def test_make_commit_message_fix():
    msg = make_commit_message("Fix login bug", 7)
    assert msg.startswith("fix:")
    assert "Closes #7" in msg


def test_make_pr_title():
    title = make_pr_title("Add PDF compression")
    assert title.startswith("feat:")

    title_fix = make_pr_title("Fix crash on startup")
    assert title_fix.startswith("fix:")
