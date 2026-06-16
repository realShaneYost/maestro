"""``maestro update`` — reinstall the Maestro tool and reconcile editable deps.

The command does two things, in order:

1. **Upgrade the installed tool.** For a ``uv tool`` install it cleans the
   package's build cache and reinstalls with ``--reinstall --no-cache`` so a
   same-version code change cannot be served from a stale cached build — the
   no-op-upgrade footgun the issue calls out. It then reads the installed
   version back from ``uv tool list`` so a silent no-op is visible. A source
   checkout is already live (nothing to reinstall); a pipx or unrecognized
   install is reported with manual guidance rather than driven blindly.
2. **Reconcile editable dependencies.** Every artifact the manifest enumerates
   (worker skill, ``maestro.yml``, CI workflow) is compared against the template
   Maestro ships. Drift is *reported*, never reconciled automatically — Maestro
   never overwrites a user's edits. For the config it additionally surfaces any
   new top-level key the shipped example gained, with its default, so a breaking
   schema change is actionable rather than a wall of diff.

Following the repo's core/CLI split, this module returns data
(:class:`UpdateReport`); :mod:`maestro.cli` renders it. Every subprocess call
goes through an injectable runner so the suite needs no uv install or network.

Reconciliation reads each template from the installed package via
:func:`maestro.manifest.read_template`, which resolves the bundled templates
under :data:`maestro.manifest.PACKAGE_FILES` via ``importlib.resources``. That
works for a wheel install, an editable install, or a source checkout alike. A
template that still cannot be read is reported as such — a safe, non-overwriting
outcome — rather than crashing.
"""

from __future__ import annotations

import enum
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.resources.abc import Traversable
from pathlib import Path

import yaml

from maestro.config import Config
from maestro.install import InstallInfo, InstallSource, detect_install
from maestro.manifest import MANIFEST, PACKAGE_FILES, WORKER_SKILL, Dependency, read_template

# (args) -> (returncode, stdout, stderr). Mirrors the doctor's gh runner shape so
# the suite can drive `maestro update` with canned uv output.
UvRunner = Callable[[list[str]], tuple[int, str, str]]

# uv invocations that force a genuine reinstall. `cache clean` evicts any stale
# cached build of maestro; the upgrade then rebuilds from the recorded source
# with the cache bypassed, so even a same-version code change takes effect.
UV_CACHE_CLEAN: tuple[str, ...] = ("cache", "clean", "maestro")
UV_REINSTALL: tuple[str, ...] = ("tool", "upgrade", "maestro", "--reinstall", "--no-cache")

# Matches the maestro line of `uv tool list`, e.g. "maestro v0.2.0".
_TOOL_LIST_VERSION = re.compile(r"^maestro v(\S+)", re.MULTILINE)


def _run_uv(args: list[str]) -> tuple[int, str, str]:
    """Run ``uv`` with ``args`` and return ``(returncode, stdout, stderr)``."""
    try:
        result = subprocess.run(["uv", *args], capture_output=True, text=True, check=False)
    except OSError as err:
        return 1, "", str(err)
    return result.returncode, result.stdout, result.stderr


@dataclass(frozen=True)
class CommandOutcome:
    """The result of one subprocess invocation made during an upgrade.

    Attributes:
        args: The uv argument vector that was run (without the ``uv`` prefix).
        returncode: The process exit code.
        stdout: Captured standard output.
        stderr: Captured standard error.
    """

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """Whether the command exited zero."""
        return self.returncode == 0


@dataclass(frozen=True)
class UpgradeResult:
    """The outcome of the tool-upgrade phase.

    Attributes:
        attempted: Whether Maestro actually ran the upgrade. False for a source
            checkout (already live) and for pipx/unknown installs (reported with
            manual guidance instead of driven blindly).
        ok: Whether the upgrade succeeded — always True when ``attempted`` is
            False, since there was nothing that could fail.
        source: The classified install source the decision was based on.
        version_before: The version Maestro was running when ``update`` started.
        version_after: The version read back from ``uv tool list`` after a
            reinstall, or ``None`` when it was not attempted or could not be
            determined.
        detail: Human-readable summary of what happened and any next step.
        commands: Each uv command that ran, in order, for transparency.
    """

    attempted: bool
    ok: bool
    source: InstallSource
    version_before: str
    version_after: str | None
    detail: str
    commands: tuple[CommandOutcome, ...]


class ReconcileStatus(enum.Enum):
    """How one editable dependency compares against its shipped template."""

    IN_SYNC = "in sync"
    DRIFTED = "drifted"
    MISSING = "missing"
    NO_TEMPLATE = "no template"
    TEMPLATE_UNAVAILABLE = "template unavailable"
    INFORMATIONAL = "informational"


@dataclass(frozen=True)
class ConfigKey:
    """A configuration key the shipped example carries that the user's lacks.

    Attributes:
        name: The top-level YAML key.
        default: The default value the shipped example gives it, formatted for
            display.
    """

    name: str
    default: str


@dataclass(frozen=True)
class DependencyReconciliation:
    """The reconciliation outcome for one manifest dependency.

    Attributes:
        name: The dependency's manifest name.
        location: Its repo-relative location.
        status: How the repo's copy compares to the shipped template.
        detail: Human-readable explanation and any recommended action.
        new_config_keys: For a drifted config, top-level keys the shipped
            example gained that the user's file lacks; empty otherwise.
    """

    name: str
    location: str
    status: ReconcileStatus
    detail: str
    new_config_keys: tuple[ConfigKey, ...] = ()


@dataclass(frozen=True)
class UpdateReport:
    """Everything ``maestro update`` did, for the CLI to render.

    Attributes:
        install: How the running Maestro was installed.
        upgrade: The tool-upgrade outcome.
        reconciliations: One entry per manifest dependency, in manifest order.
    """

    install: InstallInfo
    upgrade: UpgradeResult
    reconciliations: tuple[DependencyReconciliation, ...] = field(default_factory=tuple)


def _installed_version(uv_runner: UvRunner) -> str | None:
    """Read the installed maestro version back from ``uv tool list``.

    Args:
        uv_runner: Callable that runs uv; injectable for tests.

    Returns:
        The version string, or ``None`` if uv failed or named no maestro entry.
    """
    code, stdout, _stderr = uv_runner(["tool", "list"])
    if code != 0:
        return None
    match = _TOOL_LIST_VERSION.search(stdout)
    return match.group(1) if match else None


def _upgrade_tool(install: InstallInfo, uv_runner: UvRunner) -> UpgradeResult:
    """Upgrade the installed Maestro tool when it is a uv tool install.

    A source checkout is already live, so nothing is reinstalled. A pipx or
    unrecognized install is reported with manual guidance rather than driven.
    For a uv tool install the build cache is cleaned and the package reinstalled
    with the cache bypassed, then the installed version is read back so a no-op
    upgrade cannot pass silently.

    Args:
        install: How the running Maestro was installed.
        uv_runner: Callable that runs uv; injectable for tests.

    Returns:
        An :class:`UpgradeResult` describing what happened.
    """
    before = install.version
    if install.source is InstallSource.SOURCE:
        return UpgradeResult(
            attempted=False,
            ok=True,
            source=install.source,
            version_before=before,
            version_after=before,
            detail=(
                "Running from a source checkout — `git pull` to update; your editable "
                "install is already live, so there is nothing to reinstall."
            ),
            commands=(),
        )
    if install.source is InstallSource.PIPX:
        return UpgradeResult(
            attempted=False,
            ok=True,
            source=install.source,
            version_before=before,
            version_after=None,
            detail=(
                "Installed via pipx — upgrade it yourself with `pipx upgrade maestro` "
                "(or `pipx install --force <source>`). Auto-upgrade only drives uv tool "
                "installs."
            ),
            commands=(),
        )
    if install.source is InstallSource.UNKNOWN:
        return UpgradeResult(
            attempted=False,
            ok=True,
            source=install.source,
            version_before=before,
            version_after=None,
            detail=(
                "Install source unrecognized — cannot upgrade automatically. Reinstall "
                "Maestro by hand, then re-run `maestro update` to reconcile dependencies."
            ),
            commands=(),
        )

    commands: list[CommandOutcome] = []
    for args in (UV_CACHE_CLEAN, UV_REINSTALL):
        code, stdout, stderr = uv_runner(list(args))
        commands.append(CommandOutcome(args, code, stdout, stderr))
    reinstall = commands[-1]
    if not reinstall.ok:
        detail = (
            f"upgrade failed: {reinstall.stderr.strip() or f'uv exited {reinstall.returncode}'}"
        )
        return UpgradeResult(
            attempted=True,
            ok=False,
            source=install.source,
            version_before=before,
            version_after=None,
            detail=detail,
            commands=tuple(commands),
        )

    after = _installed_version(uv_runner)
    if after is None:
        detail = (
            "Reinstalled, but could not confirm the version via `uv tool list` — "
            "run `maestro doctor` in a new shell to verify the upgrade took effect."
        )
    elif after != before:
        detail = f"Upgraded maestro {before} -> {after}."
    else:
        detail = (
            f"Reinstalled maestro {after} (version unchanged); --reinstall --no-cache "
            "forces fresh code even at the same version. Open a new shell and run "
            "`maestro doctor` to confirm."
        )
    return UpgradeResult(
        attempted=True,
        ok=True,
        source=install.source,
        version_before=before,
        version_after=after,
        detail=detail,
        commands=tuple(commands),
    )


def _format_default(value: object) -> str:
    """Format a YAML default value for display in a report line."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _new_config_keys(user_text: str, template_text: str) -> tuple[ConfigKey, ...]:
    """Return top-level keys the shipped example has that the user's config lacks.

    Args:
        user_text: The user's ``maestro.yml`` contents.
        template_text: The shipped ``maestro.example.yml`` contents.

    Returns:
        One :class:`ConfigKey` per new top-level key, in the example's order.
        Empty when either document is not a YAML mapping or fails to parse.
    """
    try:
        user = yaml.safe_load(user_text)
        template = yaml.safe_load(template_text)
    except yaml.YAMLError:
        return ()
    if not isinstance(user, dict) or not isinstance(template, dict):
        return ()
    return tuple(
        ConfigKey(str(key), _format_default(value))
        for key, value in template.items()
        if key not in user
    )


def _reconcile_dependency(
    dependency: Dependency, repo_path: Path, package_root: Traversable
) -> DependencyReconciliation:
    """Compare one dependency's repo copy against its shipped template.

    Args:
        dependency: The manifest entry to reconcile.
        repo_path: Root of the driven repository.
        package_root: Root the template path is resolved against.

    Returns:
        A :class:`DependencyReconciliation` describing the comparison. Drift is
        reported, never reconciled — Maestro never overwrites a user's copy.
    """
    if dependency.template is None:
        return DependencyReconciliation(
            dependency.name,
            dependency.location,
            ReconcileStatus.NO_TEMPLATE,
            "no packaged template ships for this dependency; nothing to compare",
        )
    try:
        template_text = read_template(dependency, package_root)
    except OSError:
        path = dependency.template_path(package_root)
        return DependencyReconciliation(
            dependency.name,
            dependency.location,
            ReconcileStatus.TEMPLATE_UNAVAILABLE,
            f"packaged template not found at {path}; cannot compare",
        )

    destination = dependency.destination(repo_path)
    if not destination.is_file():
        return DependencyReconciliation(
            dependency.name,
            dependency.location,
            ReconcileStatus.MISSING,
            f"not found at {dependency.location}; copy the packaged template in",
        )
    try:
        repo_text = destination.read_text(encoding="utf-8")
    except OSError as err:
        return DependencyReconciliation(
            dependency.name,
            dependency.location,
            ReconcileStatus.TEMPLATE_UNAVAILABLE,
            f"present but could not be read: {err}",
        )

    if dependency.name == "config":
        return _reconcile_config(dependency, repo_text, template_text)
    if dependency.name == WORKER_SKILL:
        return _reconcile_worker_skill(dependency, repo_text, template_text)
    return _reconcile_content(dependency, repo_text, template_text)


def _reconcile_config(
    dependency: Dependency, repo_text: str, template_text: str
) -> DependencyReconciliation:
    """Reconcile ``maestro.yml`` by key schema, never by raw content.

    The shipped example carries placeholder values (``your-org/your-project``,
    ``project_number: 1``) that a filled-in config is *supposed* to differ from,
    so a raw content diff is a permanent false positive. The only actionable
    signal is key-schema drift: a top-level key the example gained that the
    user's file lacks. Values are the user's to set and are never reported.

    Args:
        dependency: The config manifest entry.
        repo_text: The user's ``maestro.yml`` contents.
        template_text: The shipped ``maestro.example.yml`` contents.

    Returns:
        DRIFTED carrying the new keys when the schema drifted; IN_SYNC otherwise,
        regardless of how the values differ.
    """
    new_keys = _new_config_keys(repo_text, template_text)
    if new_keys:
        return DependencyReconciliation(
            dependency.name,
            dependency.location,
            ReconcileStatus.DRIFTED,
            "config schema drift — the shipped example added top-level key(s) your "
            "maestro.yml lacks; add them by hand. Differing values are expected and "
            "are not reported.",
            new_keys,
        )
    return DependencyReconciliation(
        dependency.name,
        dependency.location,
        ReconcileStatus.IN_SYNC,
        "all top-level keys present; values are yours to set",
    )


def _reconcile_worker_skill(
    dependency: Dependency, repo_text: str, template_text: str
) -> DependencyReconciliation:
    """Reconcile the worker skill, tolerating the contract-vs-implementation delta.

    ``.claude/skills/cc-work/SKILL.md`` is a Claude-specific *implementation* of
    the model-agnostic *contract* shipped at ``templates/worker-skill.md``; the
    two differ by design, so a raw content diff is a permanent false positive.
    Telling a genuine upstream change apart from that intentional adaptation needs
    the base version the skill was provisioned from (#19) and a 3-way compare
    (#21). Until that base tracking exists, a content difference is downgraded to
    a quiet informational note rather than a permanent "differs".

    Args:
        dependency: The worker-skill manifest entry.
        repo_text: The repo's ``SKILL.md`` contents.
        template_text: The shipped ``worker-skill.md`` contract contents.

    Returns:
        IN_SYNC on an exact match; INFORMATIONAL otherwise.
    """
    if repo_text == template_text:
        return DependencyReconciliation(
            dependency.name,
            dependency.location,
            ReconcileStatus.IN_SYNC,
            "matches the packaged contract template",
        )
    return DependencyReconciliation(
        dependency.name,
        dependency.location,
        ReconcileStatus.INFORMATIONAL,
        "differs from the packaged contract template — expected, since the cc-work "
        "skill is a Claude-specific implementation of a model-agnostic contract. "
        "Flagging only genuine upstream changes needs base-version tracking (#19) "
        "and a 3-way compare (#21); until then this is informational, not drift.",
    )


def _reconcile_content(
    dependency: Dependency, repo_text: str, template_text: str
) -> DependencyReconciliation:
    """Reconcile a templated dependency by exact content match.

    The conservative default for any future templated artifact whose repo copy is
    meant to track the shipped template verbatim.

    Args:
        dependency: The manifest entry being reconciled.
        repo_text: The repo copy's contents.
        template_text: The shipped template's contents.

    Returns:
        IN_SYNC on an exact match; DRIFTED otherwise. Drift is reported, never
        reconciled — Maestro never overwrites a user's copy.
    """
    if repo_text == template_text:
        return DependencyReconciliation(
            dependency.name,
            dependency.location,
            ReconcileStatus.IN_SYNC,
            "matches the packaged template",
        )
    return DependencyReconciliation(
        dependency.name,
        dependency.location,
        ReconcileStatus.DRIFTED,
        "differs from the packaged template — review and reconcile by hand; "
        "Maestro will not overwrite your copy",
    )


def reconcile(
    repo_path: Path, package_root: Traversable = PACKAGE_FILES
) -> tuple[DependencyReconciliation, ...]:
    """Reconcile every manifest dependency against its shipped template.

    Args:
        repo_path: Root of the driven repository.
        package_root: Root the template paths are resolved against; defaults to
            :data:`maestro.manifest.PACKAGE_FILES`. Injectable for tests.

    Returns:
        One :class:`DependencyReconciliation` per manifest entry, in order.
    """
    return tuple(_reconcile_dependency(dep, repo_path, package_root) for dep in MANIFEST)


@dataclass(frozen=True)
class ReconcileSummary:
    """A roll-up over every dependency's reconciliation, for the CLI to render.

    The four buckets are mutually exclusive and together partition the
    reconciliation results. They exist so the CLI never rolls "compared nothing"
    up into a green "in sync": a clean bill of health requires that something was
    actually compared and matched.

    Attributes:
        verified: Dependencies compared against their template and found to match.
        needs_attention: Dependencies that drifted from, or are missing relative
            to, their template — actionable, but the comparison itself succeeded.
        unverifiable: Dependencies whose comparison could not be made because the
            packaged template (or the repo copy) could not be read. These are the
            false-positive risk: nothing about them is known to be in sync.
        not_applicable: Dependencies that ship no template by design, so there was
            nothing to compare. Benign — neither a problem nor evidence of sync.
        informational: Dependencies that differ from their shipped template by
            design (the worker skill is an implementation of a contract), reported
            as a quiet note rather than drift. Not actionable, so they never block
            an "in sync" roll-up — but a comparison was made, so a run that
            surfaced one is not a no-op.
    """

    verified: tuple[DependencyReconciliation, ...]
    needs_attention: tuple[DependencyReconciliation, ...]
    unverifiable: tuple[DependencyReconciliation, ...]
    not_applicable: tuple[DependencyReconciliation, ...]
    informational: tuple[DependencyReconciliation, ...] = ()

    @property
    def all_in_sync(self) -> bool:
        """Whether every comparable dependency was compared and matched.

        True only when at least one dependency was actually compared and matched
        and none drifted, went missing, or could not be verified. A reconciliation
        that compared nothing is never "in sync".
        """
        return bool(self.verified) and not self.needs_attention and not self.unverifiable

    @property
    def nothing_compared(self) -> bool:
        """Whether no dependency could be compared at all.

        True when nothing matched, drifted, went missing, was unverifiable, or
        surfaced an informational note — every dependency simply had no template to
        compare against. Such a run is a no-op, distinct from a verified-clean one
        and never reported as success.
        """
        return not (
            self.verified or self.needs_attention or self.unverifiable or self.informational
        )


# Statuses that mean "compared, and the comparison found something to act on."
_ATTENTION_STATUSES = frozenset({ReconcileStatus.DRIFTED, ReconcileStatus.MISSING})


def summarize_reconciliation(
    reconciliations: tuple[DependencyReconciliation, ...],
) -> ReconcileSummary:
    """Bucket reconciliation results into the roll-up the CLI renders.

    Args:
        reconciliations: One :class:`DependencyReconciliation` per manifest entry.

    Returns:
        A :class:`ReconcileSummary` partitioning the results so that "in sync" can
        be claimed only when something was actually compared and matched.
    """
    verified: list[DependencyReconciliation] = []
    needs_attention: list[DependencyReconciliation] = []
    unverifiable: list[DependencyReconciliation] = []
    not_applicable: list[DependencyReconciliation] = []
    informational: list[DependencyReconciliation] = []
    for item in reconciliations:
        if item.status is ReconcileStatus.IN_SYNC:
            verified.append(item)
        elif item.status in _ATTENTION_STATUSES:
            needs_attention.append(item)
        elif item.status is ReconcileStatus.TEMPLATE_UNAVAILABLE:
            unverifiable.append(item)
        elif item.status is ReconcileStatus.INFORMATIONAL:
            informational.append(item)
        else:  # NO_TEMPLATE — ships nothing to compare against, by design.
            not_applicable.append(item)
    return ReconcileSummary(
        verified=tuple(verified),
        needs_attention=tuple(needs_attention),
        unverifiable=tuple(unverifiable),
        not_applicable=tuple(not_applicable),
        informational=tuple(informational),
    )


def run_update(
    config: Config,
    *,
    uv_runner: UvRunner = _run_uv,
    install_info: InstallInfo | None = None,
    package_root: Traversable = PACKAGE_FILES,
) -> UpdateReport:
    """Upgrade the installed tool, then reconcile the repo's editable deps.

    Args:
        config: Loaded Maestro configuration; supplies the driven repo path.
        uv_runner: Callable that runs uv; injectable for tests.
        install_info: How Maestro was installed; detected when omitted.
        package_root: Root the template paths are resolved against. Injectable
            for tests.

    Returns:
        An :class:`UpdateReport` carrying the install info, upgrade outcome, and
        per-dependency reconciliation results.
    """
    install = install_info if install_info is not None else detect_install()
    upgrade = _upgrade_tool(install, uv_runner)
    reconciliations = reconcile(config.repo_path, package_root)
    return UpdateReport(install=install, upgrade=upgrade, reconciliations=reconciliations)
