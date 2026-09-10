"""``on.missing_tag`` / ``on.shallow`` and ``ScmVersion.tag_found`` (#1506).

A repository with full history but no tag used to yield ``0.1.dev1+g<hash>``
in complete silence, and ``fail_on_shallow`` could not catch it because it only
looked at shallowness.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from vcs_versioning import Configuration
from vcs_versioning._backends import _git as git
from vcs_versioning._config import (
    GitConfiguration,
    OnAction,
    OnConfiguration,
    ScmConfiguration,
    TagConfiguration,
)
from vcs_versioning._run_cmd import run
from vcs_versioning._scm_version import ScmVersion
from vcs_versioning.test_api import DebugMode, WorkDir

pytestmark = pytest.mark.issue(1506)


@pytest.fixture
def wd(wd: WorkDir, monkeypatch: pytest.MonkeyPatch, debug_mode: DebugMode) -> WorkDir:
    debug_mode.disable()
    wd.setup_git(monkeypatch)
    debug_mode.enable()
    return wd


@pytest.fixture
def tagless_wd(wd: WorkDir) -> WorkDir:
    """Full history, no tags -- the case #1506 is about."""
    wd.commit_testfile()
    return wd


def on(
    *,
    missing_tag: OnAction = OnAction.WARN,
    shallow: OnAction = OnAction.WARN,
    missing_submodules: OnAction = OnAction.IGNORE,
) -> OnConfiguration:
    return OnConfiguration(
        missing_tag=missing_tag,
        shallow=shallow,
        missing_submodules=missing_submodules,
    )


def parse(root: object, config: Configuration) -> ScmVersion | None:
    return git.parse(str(root), config)


# --------------------------------------------------------------------------
# on.missing_tag
# --------------------------------------------------------------------------


def test_tagless_warns_by_default(
    tagless_wd: WorkDir, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        version = parse(tagless_wd.cwd, Configuration())
    assert version is not None
    assert "no version tag found" in caplog.text
    assert 'on.missing_tag = "ignore"' in caplog.text


def test_tagless_warns_only_once(
    tagless_wd: WorkDir, caplog: pytest.LogCaptureFixture
) -> None:
    """A build constructs the configuration twice; the user sees one line."""
    with caplog.at_level(logging.WARNING):
        parse(tagless_wd.cwd, Configuration())
        parse(tagless_wd.cwd, Configuration())
    assert caplog.text.count("no version tag found") == 1


def test_tagless_ignore_is_silent(
    tagless_wd: WorkDir, caplog: pytest.LogCaptureFixture
) -> None:
    config = Configuration(on=on(missing_tag=OnAction.IGNORE))
    with caplog.at_level(logging.WARNING):
        assert parse(tagless_wd.cwd, config) is not None
    assert "no version tag found" not in caplog.text


def test_tagless_fail_raises(tagless_wd: WorkDir) -> None:
    config = Configuration(on=on(missing_tag=OnAction.FAIL))
    with pytest.raises(ValueError, match="no version tag found"):
        parse(tagless_wd.cwd, config)


def test_fallback_version_opts_out(
    tagless_wd: WorkDir, caplog: pytest.LogCaptureFixture
) -> None:
    """Setting fallback_version is an explicit "no tag is fine"."""
    config = Configuration(fallback_version="1.2.3", on=on(missing_tag=OnAction.FAIL))
    with caplog.at_level(logging.WARNING):
        assert parse(tagless_wd.cwd, config) is not None
    assert "no version tag found" not in caplog.text


def test_tagged_repo_is_silent(wd: WorkDir, caplog: pytest.LogCaptureFixture) -> None:
    wd.commit_testfile()
    wd.create_tag("v1.0.0")
    config = Configuration(on=on(missing_tag=OnAction.FAIL))
    with caplog.at_level(logging.WARNING):
        assert parse(wd.cwd, config) is not None
    assert "no version tag found" not in caplog.text


def test_unmatched_tags_get_a_different_hint(wd: WorkDir) -> None:
    """Tags exist but none match: almost always a misconfiguration."""
    wd.commit_testfile()
    wd.create_tag("v1.0.0")
    config = Configuration(
        tag=TagConfiguration(prefix="nomatch-"), on=on(missing_tag=OnAction.FAIL)
    )
    with pytest.raises(ValueError, match="none match 'nomatch-"):
        parse(wd.cwd, config)


def test_no_tags_at_all_hint(tagless_wd: WorkDir) -> None:
    config = Configuration(on=on(missing_tag=OnAction.FAIL))
    with pytest.raises(ValueError, match="git fetch --tags"):
        parse(tagless_wd.cwd, config)


# --------------------------------------------------------------------------
# on.shallow -- a refinement of on.missing_tag, not an independent condition
# --------------------------------------------------------------------------


@pytest.fixture
def shallow_tagless_wd(wd: WorkDir, tmp_path: Path) -> Path:
    """Shallow clone whose truncated history holds no tag."""
    for _ in range(3):
        wd.commit_testfile()
    target = tmp_path / "shallow_tagless"
    run(["git", "clone", f"file://{wd.cwd}", target, "--depth=1"], tmp_path, check=True)
    return target


@pytest.fixture
def shallow_but_tagged_wd(wd: WorkDir, tmp_path: Path) -> Path:
    """Shallow clone deep enough that a matching tag is still reachable.

    HEAD is deliberately *not* on the tag, so the distance-0 carve-out from
    #1241 does not apply -- only "did describe succeed" saves this repository.
    """
    # deep enough history that a --depth=5 clone is still truncated, with the
    # tag two commits back so it lands inside the truncated window
    for _ in range(10):
        wd.commit_testfile()
    wd.create_tag("v1.0.0")
    for _ in range(2):
        wd.commit_testfile()
    target = tmp_path / "shallow_tagged"
    run(["git", "clone", f"file://{wd.cwd}", target, "--depth=5"], tmp_path, check=True)
    run(
        ["git", "fetch", "--depth=5", "origin", "refs/tags/*:refs/tags/*"],
        target,
        check=True,
    )
    workdir = git.GitWorkdir(target, _config=Configuration(root=target))
    assert workdir.is_shallow(), "fixture must stay shallow"
    assert workdir.default_describe().returncode == 0, "tag must stay reachable"
    return target


def test_shallow_without_tag_is_reported(
    shallow_tagless_wd: Path, recwarn: pytest.WarningsRecorder
) -> None:
    parse(shallow_tagless_wd, Configuration())
    assert any("is shallow and may cause errors" in str(w.message) for w in recwarn)


def test_shallow_without_tag_fail(shallow_tagless_wd: Path) -> None:
    config = Configuration(on=on(shallow=OnAction.FAIL))
    with pytest.raises(ValueError, match="git fetch --unshallow"):
        parse(shallow_tagless_wd, config)


def test_shallow_with_reachable_tag_is_silent(
    shallow_but_tagged_wd: Path,
    recwarn: pytest.WarningsRecorder,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The false positive this change removes.

    Before #1506 a ``--depth`` clone holding a tag a few commits back still
    warned, because the guard only recognised a tag *exactly* on HEAD.
    """
    config = Configuration(on=on(shallow=OnAction.FAIL, missing_tag=OnAction.FAIL))
    with caplog.at_level(logging.WARNING):
        version = parse(shallow_but_tagged_wd, config)
    assert version is not None
    assert version.tag_found is True
    assert not [w for w in recwarn if "shallow" in str(w.message)]
    assert "no version tag found" not in caplog.text


def test_shallow_ignore_falls_through_to_missing_tag(
    shallow_tagless_wd: Path,
) -> None:
    config = Configuration(on=on(shallow=OnAction.IGNORE, missing_tag=OnAction.FAIL))
    with pytest.raises(ValueError, match="no version tag found"):
        parse(shallow_tagless_wd, config)


def test_shallow_and_missing_tag_can_both_be_set(shallow_tagless_wd: Path) -> None:
    """The combination a single ``pre_parse`` string could never express."""
    config = Configuration(on=on(shallow=OnAction.FAIL, missing_tag=OnAction.FAIL))
    with pytest.raises(ValueError, match="git fetch --unshallow"):
        parse(shallow_tagless_wd, config)


def test_shallow_fetch_retries_describe(
    shallow_tagless_wd: Path, recwarn: pytest.WarningsRecorder
) -> None:
    config = Configuration(on=on(shallow=OnAction.FETCH))
    parse(shallow_tagless_wd, config)
    assert any("git fetch was used to rectify" in str(w.message) for w in recwarn)
    assert not git.GitWorkdir(shallow_tagless_wd).is_shallow()


def test_shallow_fetch_does_not_fire_when_a_tag_is_reachable(
    shallow_but_tagged_wd: Path, recwarn: pytest.WarningsRecorder
) -> None:
    """No needless network round-trip for an already-usable shallow clone."""
    config = Configuration(on=on(shallow=OnAction.FETCH))
    parse(shallow_but_tagged_wd, config)
    assert not [w for w in recwarn if "rectify" in str(w.message)]
    assert git.GitWorkdir(shallow_but_tagged_wd).is_shallow()


def test_is_shallow_detects_worktrees(wd: WorkDir, tmp_path: Path) -> None:
    """``.git`` is a *file* in a worktree, so the old path check said False."""
    for _ in range(3):
        wd.commit_testfile()
    clone = tmp_path / "shallow_clone"
    run(["git", "clone", f"file://{wd.cwd}", clone, "--depth=1"], tmp_path, check=True)
    worktree = tmp_path / "wt"
    run(["git", "worktree", "add", "--detach", str(worktree)], clone, check=True)
    assert (worktree / ".git").is_file(), "fixture must produce a .git file"
    assert git.GitWorkdir(worktree).is_shallow()


# --------------------------------------------------------------------------
# ScmVersion.tag_found
# --------------------------------------------------------------------------


def test_tag_found_false_without_tag(tagless_wd: WorkDir) -> None:
    version = parse(tagless_wd.cwd, Configuration(on=on(missing_tag=OnAction.IGNORE)))
    assert version is not None
    assert version.tag_found is False


def test_tag_found_true_with_tag(wd: WorkDir) -> None:
    wd.commit_testfile()
    wd.create_tag("v1.0.0")
    version = parse(wd.cwd, Configuration())
    assert version is not None
    assert version.tag_found is True


def test_tag_found_true_for_a_genuine_0_0_tag(
    wd: WorkDir, caplog: pytest.LogCaptureFixture
) -> None:
    """The case the ``str(version.tag) == "0.0"`` workaround gets wrong.

    A project that really tagged ``0.0`` is indistinguishable from an invented
    tag by value alone, which is why this needs to be recorded rather than
    guessed.
    """
    wd.commit_testfile()
    wd.create_tag("0.0")
    wd.commit_testfile()
    with caplog.at_level(logging.WARNING):
        version = parse(wd.cwd, Configuration())
    assert version is not None
    assert str(version.tag) == "0.0"
    assert version.tag_found is True
    assert "no version tag found" not in caplog.text


def test_version_scheme_can_reject_an_invented_tag(tagless_wd: WorkDir) -> None:
    """The borgbackup use case, without sniffing the fallback tag."""

    def strict_scheme(version: ScmVersion) -> str:
        if not version.tag_found:
            raise RuntimeError("refusing to build without a release tag")
        return str(version.tag)

    version = parse(tagless_wd.cwd, Configuration(on=on(missing_tag=OnAction.IGNORE)))
    assert version is not None
    with pytest.raises(RuntimeError, match="refusing to build"):
        strict_scheme(version)


# --------------------------------------------------------------------------
# config surface
# --------------------------------------------------------------------------


def test_on_from_dotted_toml(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[tool.setuptools_scm]\non.missing_tag = "fail"\non.shallow = "fetch"\n',
        encoding="utf-8",
    )
    config = Configuration.from_file(pyproject)
    assert config.on.missing_tag is OnAction.FAIL
    assert config.on.shallow is OnAction.FETCH
    assert config.on.missing_submodules is OnAction.IGNORE


def test_unknown_condition_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown on.\\* condition"):
        Configuration.from_data(relative_to=".", data={"on": {"nope": "warn"}})


def test_invalid_action_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid action 'nope' for on.missing_tag"):
        Configuration.from_data(relative_to=".", data={"on": {"missing_tag": "nope"}})


def test_fetch_is_only_valid_for_shallow() -> None:
    with pytest.raises(
        ValueError, match="Invalid action 'fetch' for on.missing_submodules"
    ):
        Configuration.from_data(
            relative_to=".", data={"on": {"missing_submodules": "fetch"}}
        )


@pytest.mark.parametrize(
    ("pre_parse", "expected"),
    [
        ("warn_on_shallow", {"shallow": OnAction.WARN}),
        ("fail_on_shallow", {"shallow": OnAction.FAIL}),
        ("fetch_on_shallow", {"shallow": OnAction.FETCH}),
        (
            "fail_on_missing_submodules",
            {"shallow": OnAction.IGNORE, "missing_submodules": OnAction.FAIL},
        ),
    ],
)
def test_pre_parse_migrates_to_on(
    pre_parse: str, expected: dict[str, OnAction]
) -> None:
    """``fail_on_missing_submodules`` used to *replace* the shallow warning."""
    with pytest.warns(DeprecationWarning, match="scm.git.pre_parse"):
        config = Configuration.from_data(
            relative_to=".", data={"scm": {"git": {"pre_parse": pre_parse}}}
        )
    for name, action in expected.items():
        assert getattr(config.on, name) is action


def test_pre_parse_conflicts_with_on() -> None:
    with pytest.raises(ValueError, match="Cannot specify both"):
        Configuration.from_data(
            relative_to=".",
            data={
                "scm": {"git": {"pre_parse": "fail_on_shallow"}},
                "on": {"shallow": "warn"},
            },
        )


def test_pre_parse_default_is_unset() -> None:
    assert Configuration().scm.git.pre_parse is None
    assert Configuration().on == OnConfiguration()


def test_explicit_pre_parse_callable_still_wins(tagless_wd: WorkDir) -> None:
    """The documented escape hatch for third parties."""
    calls: list[object] = []

    def spy(wd: object) -> None:
        calls.append(wd)

    config = Configuration(
        scm=ScmConfiguration(git=GitConfiguration()),
        on=on(missing_tag=OnAction.IGNORE),
    )
    git.parse(str(tagless_wd.cwd), config, pre_parse=spy)
    assert len(calls) == 1
