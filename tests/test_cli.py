"""End-to-end tests through ``claus.cli.main`` with the fake claude."""

import os
import shutil

from tests.conftest import write


def test_listing_summarizes_text_only(tree, capsys):
    code, rows, err = tree.ls_json(capsys, "-A")
    assert code == 0, err
    assert rows["notes.txt"]["status"] == "done"
    assert rows["notes.txt"]["summary"] == "fake: shopping list"
    assert rows["script.py"]["status"] == "done"
    assert rows["data.json"]["status"] == "done"
    assert rows["image.bin"]["status"] == "excluded"
    assert rows["empty.txt"]["status"] == "excluded"
    assert rows[".env"]["status"] == "excluded"
    assert "!-name:.env" in rows[".env"]["rule"]
    assert rows["sub"]["status"] == "na" and rows["sub"]["type"] == "dir"
    assert len(tree.calls()) == 3
    call = tree.calls()[0]
    assert "--no-session-persistence" in call["argv"]
    assert call["cwd"].endswith("workdir")


def test_second_run_uses_cache(tree, capsys):
    tree.ls_json(capsys)
    n = len(tree.calls())
    code, rows, _ = tree.ls_json(capsys)
    assert code == 0
    assert len(tree.calls()) == n
    assert rows["notes.txt"]["status"] == "cached"


def test_move_and_copy_reuse_summary(tree, capsys):
    tree.ls_json(capsys)
    n = len(tree.calls())
    os.rename(tree.root / "notes.txt", tree.root / "renamed.txt")
    shutil.copy(tree.root / "script.py", tree.root / "copy.py")
    code, rows, _ = tree.ls_json(capsys)
    assert len(tree.calls()) == n
    assert rows["renamed.txt"]["status"] == "cached" and rows["renamed.txt"]["event"] == "moved"
    assert rows["copy.py"]["event"] == "copied"
    code, out, _ = tree.run(capsys, "info", "renamed.txt")
    assert "moved" in out and "notes.txt" in out


def test_duplicates_in_one_run_share_one_call(env, capsys):
    for i in range(3):
        write(env.root / f"dup{i}.txt", "identical\n")
    code, rows, _ = env.ls_json(capsys)
    assert len(env.calls()) == 1
    assert {r["status"] for r in rows.values()} == {"done"}


def test_confirmation_threshold_non_interactive(env, capsys):
    env.write_config("confirm_threshold = 2\n")
    for i in range(5):
        write(env.root / f"f{i}.txt", f"file {i}\n")
    code, rows, err = env.ls_json(capsys)
    assert len(env.calls()) == 2
    statuses = sorted(r["status"] for r in rows.values())
    assert statuses == ["done", "done", "skipped", "skipped", "skipped"]
    assert "3 more summaries" in err and "-y" in err
    code, rows, err = env.ls_json(capsys, "-y")
    assert len(env.calls()) == 5


def test_no_generate_and_dry_run(tree, capsys):
    code, rows, err = tree.ls_json(capsys, "--no-generate")
    assert tree.calls() == []
    assert rows["notes.txt"]["status"] == "skipped" and rows["notes.txt"]["summary"] is None
    code, out, err = tree.run(capsys, "--dry-run", "--plain")
    assert tree.calls() == []
    assert "would summarize" in out and "3 file(s) would be summarized" in err


def test_errors_and_exit_status(tree, capsys):
    tree.write_config("retries = 0\n", FAKE_CLAUDE_MODE="error")
    code, rows, _ = tree.ls_json(capsys)
    assert code == 1
    assert rows["notes.txt"]["status"] == "error" and "boom" in rows["notes.txt"]["error"]


def test_fatal_error_stops_generation(tree, capsys):
    tree.write_config("concurrency = 1\n", FAKE_CLAUDE_MODE="fatal")
    code, rows, err = tree.ls_json(capsys)
    assert code == 1
    assert len(tree.calls()) == 1
    assert "usage limit" in err
    assert {rows[n]["status"] for n in ("notes.txt", "script.py", "data.json")} == {"skipped"}


def test_missing_claude(tree, capsys):
    tree.config.write_text(f'[summary]\nclaude_path = "{tree.home}/nope"\n')
    code, rows, err = tree.ls_json(capsys)
    assert "not found" in err
    assert rows["notes.txt"]["status"] == "skipped"


def test_missing_path_argument(env, capsys):
    code, out, err = env.run(capsys, "--plain", "nope")
    assert code == 2 and "cannot access 'nope'" in err


def test_recursion_depth_and_skip_dirs(env, capsys):
    write(env.root / "a" / "b" / "c" / "deep.txt", "deep\n")
    write(env.root / "a" / "top.txt", "top\n")
    write(env.root / "node_modules" / "x.js", "x\n")
    code, rows, _ = env.ls_json(capsys, "-R", "--depth", "1", "--no-generate")
    assert "a/top.txt" in rows
    assert "a/b" in rows and "a/b/c" not in rows
    assert not any(p.startswith("node_modules/") for p in rows)
    code, rows, _ = env.ls_json(capsys, "-R", "--no-generate")
    assert "a/b/c/deep.txt" in rows


def test_directory_rules_and_cli_rules(env, capsys):
    write(env.root / "keep.txt", "keep\n")
    write(env.root / "vendor" / "lib.txt", "lib\n")
    write(env.root / "vendor" / ".claus.toml", '[filter]\nrules = ["-name:*.txt"]\n')
    code, rows, _ = env.ls_json(capsys, "-R", "--no-generate")
    assert rows["vendor/lib.txt"]["status"] == "excluded"
    assert rows["keep.txt"]["status"] == "skipped"
    code, rows, _ = env.ls_json(capsys, "-R", "--no-generate", "--include", "name:lib.txt")
    assert rows["vendor/lib.txt"]["status"] == "skipped"


def test_symlink_to_secret_is_excluded(env, capsys):
    secret = write(env.home / ".ssh" / "config", "Host x\n")
    os.symlink(secret, env.root / "innocent.txt")
    code, rows, _ = env.ls_json(capsys, "--no-generate")
    assert rows["innocent.txt"]["status"] == "excluded"
    assert "/.ssh/" in rows["innocent.txt"]["rule"]


def test_plain_and_stream_output(tree, capsys):
    code, out, _ = tree.run(capsys, "--plain")
    lines = out.splitlines()
    assert lines[0].startswith("total ")
    notes = next(line for line in lines if " notes.txt" in line)
    assert notes.startswith("-rw") and notes.endswith("fake: shopping list")
    code, out, _ = tree.run(capsys, "--stream", "-R")
    assert any(line.rstrip().endswith("sub/deep.md  fake: # Deep") for line in out.splitlines())


def test_removed_files_and_gc(tree, capsys):
    tree.ls_json(capsys)
    (tree.root / "notes.txt").unlink()
    tree.ls_json(capsys)  # reconcile marks it removed + orphaned
    code, out, _ = tree.run(capsys, "info", "notes.txt")
    assert "removed" in out
    code, out, _ = tree.run(capsys, "gc", "--retention-days", "0")
    assert code == 0 and "purged 1 contents" in out


def test_explain_and_config(tree, capsys):
    code, out, _ = tree.run(capsys, "explain", ".env", "notes.txt")
    assert "!-name:.env" in out and "excluded" in out and "INCLUDED" in out
    code, out, _ = tree.run(capsys, "config", "--paths")
    assert "global config:" in out and "cache:" in out
    code, out, _ = tree.run(capsys, "config")
    assert "[summary]" in out and 'model = "haiku"' in out
