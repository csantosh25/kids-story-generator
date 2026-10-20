"""Durable Instagram publication history -- the idempotency record that
guarantees a story (keyed by its Content Library `content_id`) is never
published twice.

WHERE IT LIVES: a single JSON file on a dedicated branch
(DEFAULT_BRANCH), NOT on main and NOT under output/ or data/:

  * the Daily Story workflow's retention (services/story_retention_
    service.py) only ever touches output/ and data/content_library.json
    on main, so expiring a story can never erase its publication record
    or make it eligible to publish again;
  * the Daily Story workflow pushes main; this store never pushes main,
    so it can never cause that push to be rejected;
  * a GitHub Actions runner is ephemeral -- the branch is not.

CONCURRENCY: every write is a compare-and-swap. A new commit is built
on top of the exact commit that was read and pushed WITHOUT force, so
git itself rejects the push (non-fast-forward) if any other run wrote
in between. update() then re-reads and re-applies the mutation, which
re-checks its own preconditions against the fresh state -- so two runs
can never both "claim" the same story.

The commits are built with git plumbing (hash-object / mktree /
commit-tree), so the main checkout's working tree and index are never
touched.
"""
import copy
import json
import os
import subprocess


DEFAULT_BRANCH = "instagram-publication-state"
DEFAULT_PATH = "instagram_publications.json"
SCHEMA_VERSION = 1

_README = """# Instagram publication state

Machine-written by publish_instagram.py (see docs/INSTAGRAM_PUBLISHING.md).
This branch is the durable duplicate-protection record for Instagram
carousel publishing. Do not delete it: without it, published stories
could become eligible to publish again.
"""


class StateStoreError(RuntimeError):
    pass


class StateConflictError(StateStoreError):
    """Someone else wrote the state after we read it."""


class SkipUpdate(Exception):
    """Raised by an update() mutator to abandon the write cleanly."""


def empty_state():
    return {"schema_version": SCHEMA_VERSION, "live_enabled_on": None, "stories": {}}


def _normalise(state):

    state = state or empty_state()
    state.setdefault("schema_version", SCHEMA_VERSION)
    state.setdefault("live_enabled_on", None)
    state.setdefault("stories", {})
    return state


class _BaseStore:

    def load(self):
        raise NotImplementedError

    def save(self, state, base_version, message):
        raise NotImplementedError

    def update(self, mutator, message, retries=5):
        """load -> mutator(state) -> save, retried on conflicts. The
        mutator must be safe to re-run on fresh state and re-check its
        own preconditions; it may raise SkipUpdate to write nothing.
        Returns (state, written)."""

        for _ in range(retries):

            state, version = self.load()

            try:
                mutator(state)
            except SkipUpdate:
                return state, False

            try:
                self.save(state, version, message)
            except StateConflictError:
                continue

            return state, True

        raise StateConflictError(
            f"Gave up writing publication state after {retries} conflicting attempts."
        )


class InMemoryPublicationStore(_BaseStore):
    """Same compare-and-swap contract, in memory. Used by tests and by
    dry runs (which must never write the real store)."""

    def __init__(self, state=None):
        self._state = _normalise(copy.deepcopy(state))
        self._version = 0
        self.writes = 0

    def load(self):
        return copy.deepcopy(self._state), self._version

    def save(self, state, base_version, message):

        if base_version != self._version:
            raise StateConflictError("in-memory state changed since it was read")

        self._state = copy.deepcopy(state)
        self._version += 1
        self.writes += 1


class GitBranchPublicationStore(_BaseStore):

    def __init__(self, repo_dir=".", remote="origin", branch=DEFAULT_BRANCH,
                 path=DEFAULT_PATH):

        self.repo_dir = str(repo_dir)
        self.remote = remote
        self.branch = branch
        self.path = path

    def _git(self, *args, input_text=None, check=True):

        env = dict(os.environ)
        env.setdefault("GIT_AUTHOR_NAME", "github-actions[bot]")
        env.setdefault("GIT_AUTHOR_EMAIL", "41898282+github-actions[bot]@users.noreply.github.com")
        env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
        env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
        env["GIT_TERMINAL_PROMPT"] = "0"

        # Bytes, not text mode: text mode would turn "\n" into "\r\n" on
        # Windows and corrupt mktree input / stored JSON.
        raw = subprocess.run(
            ["git", *args],
            cwd=self.repo_dir,
            input=input_text.encode("utf-8") if input_text is not None else None,
            capture_output=True,
            env=env,
        )

        result = subprocess.CompletedProcess(
            raw.args,
            raw.returncode,
            raw.stdout.decode("utf-8", errors="replace"),
            raw.stderr.decode("utf-8", errors="replace"),
        )

        if check and result.returncode != 0:
            raise StateStoreError(
                f"git {args[0]} failed: {result.stderr.strip()[:500]}"
            )

        return result

    def _remote_head(self):

        result = self._git("ls-remote", "--heads", self.remote, f"refs/heads/{self.branch}")
        line = result.stdout.strip()
        return line.split()[0] if line else None

    def load(self):

        head = self._remote_head()

        if head is None:
            return empty_state(), None

        self._git(
            "fetch", "--no-tags", "--depth=1", self.remote,
            f"+refs/heads/{self.branch}:refs/remotes/{self.remote}/{self.branch}",
        )

        shown = self._git("show", f"{head}:{self.path}", check=False)

        if shown.returncode != 0:
            raise StateStoreError(
                f"{self.branch} exists but has no {self.path} -- refusing to "
                f"treat publication history as empty."
            )

        try:
            state = json.loads(shown.stdout)
        except ValueError as error:
            raise StateStoreError(f"{self.path} is not valid JSON: {error}") from None

        return _normalise(state), head

    def save(self, state, base_version, message):

        content = json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n"

        blob = self._git("hash-object", "-w", "--stdin", input_text=content).stdout.strip()
        readme = self._git("hash-object", "-w", "--stdin", input_text=_README).stdout.strip()

        tree = self._git(
            "mktree",
            input_text=f"100644 blob {readme}\tREADME.md\n100644 blob {blob}\t{self.path}\n",
        ).stdout.strip()

        args = ["commit-tree", tree, "-m", f"{message} [skip ci]"]

        if base_version:
            args += ["-p", base_version]

        commit = self._git(*args).stdout.strip()

        pushed = self._git(
            "push", "--porcelain", self.remote, f"{commit}:refs/heads/{self.branch}",
            check=False,
        )

        if pushed.returncode != 0:

            output = (pushed.stdout + pushed.stderr).lower()

            if any(word in output for word in ("rejected", "non-fast-forward", "fetch first", "stale")):
                raise StateConflictError("publication state branch moved; re-reading")

            raise StateStoreError(f"git push failed: {pushed.stderr.strip()[:500]}")

        return commit
