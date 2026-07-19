"""Static release-governance checks for the automated snapshot publisher."""

from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "update-news.yml"


def workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def workflow_document() -> dict:
    # BaseLoader avoids YAML 1.1 treating the GitHub Actions `on` key as true.
    return yaml.load(workflow_text(), Loader=yaml.BaseLoader)


def publish_script() -> str:
    workflow = workflow_document()
    steps = workflow["jobs"]["update"]["steps"]
    for step in steps:
        if step.get("name") == "Publish reviewed snapshot PR":
            return step["run"]
    raise AssertionError("snapshot publication step is missing")


def test_workflow_is_valid_yaml_and_has_a_single_update_job():
    workflow = workflow_document()
    assert set(workflow["jobs"]) == {"update"}
    assert workflow["jobs"]["update"]["timeout-minutes"] == "15"


def test_publication_requires_default_branch_and_pins_run_start_sha():
    text = workflow_text()
    assert "github.ref_name != github.event.repository.default_branch" in text
    assert "ref: ${{ github.sha }}" in text
    assert "fetch-depth: 0" in text
    assert "DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}" in text
    assert '--base "$DEFAULT_BRANCH"' in text


def test_publication_has_only_the_permissions_needed_for_pr_validation():
    text = workflow_text()
    assert "contents: write" in text
    assert "pull-requests: write" in text
    assert "actions: write" in text
    assert "checks: write" not in text


def test_pr_creation_fails_closed_and_removes_the_unreviewed_branch():
    script = publish_script()
    assert "if ! PR_URL=$(gh pr create" in script
    assert "GitHub Actions could not create its PR" in script
    assert 'git push origin --delete "$BRANCH" || true' in script
    assert "actions/permissions/workflow" not in workflow_text()


def test_publication_never_pushes_the_default_branch_directly():
    script = publish_script()
    push_lines = [line.strip() for line in script.splitlines() if re.search(r"\bgit push\b", line)]
    assert push_lines == [
        'git push --set-upstream origin "$BRANCH"',
        'git push origin --delete "$BRANCH" || true',
    ]
    assert "git push origin master" not in script
    assert "git push origin \"$DEFAULT_BRANCH\"" not in script


def test_dispatched_test_is_selected_and_verified_by_snapshot_sha():
    script = publish_script()
    assert 'SNAPSHOT_SHA=$(git rev-parse HEAD)' in script
    assert '--commit "$SNAPSHOT_SHA"' in script
    assert 'TEST_SHA=$(gh run view "$RUN_ID" --json headSha' in script
    assert 'if [ "$TEST_SHA" != "$SNAPSHOT_SHA" ]' in script
    assert 'gh run watch "$RUN_ID" --exit-status' in script


def test_merge_rechecks_pr_head_and_matches_the_tested_commit():
    script = publish_script()
    assert 'PR_HEAD_SHA=$(gh pr view "$PR_URL" --json headRefOid' in script
    assert 'if [ "$PR_HEAD_SHA" != "$SNAPSHOT_SHA" ]' in script
    assert '--match-head-commit "$SNAPSHOT_SHA"' in script
    assert "--squash" in script
    assert "--delete-branch" in script
