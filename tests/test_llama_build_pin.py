"""Which llama.cpp build runs is one question with two answers, and it used to have two resolvers.

start.ps1 had its own Resolve-LlamaServer, defaulting to the newest installed build
behind -UsePinned. scripts/resolve_llama.ps1 defaulted to a hardcoded 2.47.0 behind
-AutoLatest. replay.ps1 called the second one.

That is why every number in REPORT.md is on 2.47.0 and the shipping default runs
2.53.0 without anyone having decided they should differ: the measuring path and the
running path answered the same question two different ways, and neither one enforced
anything. The next edit to either -- including installing a new build through LM Studio,
which changes nothing in the repository -- would have moved the measurements onto a
different binary with the meta block honestly recording the new version and the two runs
silently incomparable.

So the rule these tests pin is not "pin 2.47.0". It is narrower and is what makes the
pin hold: one resolver, and every measurement path names the build it needs rather than
inheriting whatever the machine happens to have.

    shipping   unspecified  -> newest installed, because 体感 is what is being optimised
                             and some models only load on a newer build
    measuring  MEASUREMENT_LLAMA_VERSION, which lives in config.py with the rest of the
               defaults, and replay.ps1 asks for it
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import unittest

from server import config

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class OneResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolve = _read("scripts/resolve_llama.ps1")
        self.start = _read("scripts/start.ps1")
        self.replay = _read("scripts/replay.ps1")

    def test_start_ps1_has_no_second_answer(self):
        """The duplicate is what let the two defaults diverge unnoticed.

        Asserted against the two shapes it took: a local Resolve-LlamaServer, and a
        directory scan for llama.cpp-* builds. Removing the function while leaving the
        scan behind would still be a second answer.
        """
        self.assertNotIn("function Resolve-LlamaServer", self.start)
        self.assertNotIn(
            'Filter "llama.cpp-$LlamaPlatform-*"', self.start,
            "start.ps1 still enumerates llama builds, so it is still deciding for itself")
        self.assertNotIn("$BackendsRoot", self.start)

    def test_start_ps1_calls_the_one_resolver(self):
        self.assertIn("resolve_llama.ps1", self.start)
        self.assertIn('@resolveArgs', self.start)

    def test_the_three_flags_are_gone(self):
        """-UsePinned, -AutoLatest and -Pin were three spellings of one value, and two of
        them disagreed about what the value was when you did not pass it. A switch cannot
        express "newest unless told otherwise" without also having to express "pinned
        unless told otherwise", which is what happened.

        Matched as param declarations, because both names survive in comments explaining
        the history, and a test that fails on the explanation is a test that gets deleted.
        """
        for script, name in (("scripts/start.ps1", self.start),
                             ("scripts/resolve_llama.ps1", self.resolve)):
            for flag in ("UsePinned", "AutoLatest", "Pin"):
                self.assertNotIn(
                    f"[switch] ${flag}", script,
                    f"{script} still declares -{flag}")
                self.assertNotIn(f"-{flag}", _strip_comments(script),
                                 f"{script} still branches on -{flag}")

    def test_unspecified_means_newest_installed(self):
        """Shipping behaviour, and the reason the resolver has two states rather than one.

        Pinned by default would be defensible for reproducibility and wrong here: the
        user optimises for 体感 and some models do not load at all on an older build. So
        the default must be the newest build on the machine, read from the filesystem
        rather than from a constant that stops being true when LM Studio updates.
        """
        # No version named anywhere on the path that picks one.
        self.assertIn('$chosen = $installed | Sort-Object key | Select-Object -Last 1', self.resolve)
        self.assertIn('Install', self.resolve)  # sanity: it does enumerate installs

    def test_the_sort_is_numeric(self):
        """2.52.0 has to beat 2.9.0. String comparison gets that backwards, and the
        failure is silent: the resolver picks a build, reports it honestly, and the
        measurement is simply on the wrong binary.
        """
        self.assertIn("key = [Version]$version", self.resolve)
        self.assertNotRegex(self.resolve, r"Sort-Object\s+version")

    def test_a_named_version_that_is_missing_raises(self):
        """The fallback this file used to have, in the file written to prevent exactly it.

        "pin missing, fell back to newest" is how an arm ends up on a different build
        while every record of it looks correct. A measurement is either on the build it
        was asked for or it did not happen.
        """
        self.assertIn("is not installed", self.resolve)
        self.assertNotIn("fell back to newest", _strip_comments(self.resolve))
        # And the error has to say what is available, or the reader cannot act on it.
        self.assertIn("$available", self.resolve)

    def test_the_precedence_is_argument_then_environment_then_file(self):
        body = self.resolve.split("$chosen = $null", 1)[1]
        order = [
            body.index("if ($Version)"),
            body.index("$env:TRANSLATION_LLAMA_VERSION"),
            body.index("Get-RuntimeValue"),
        ]
        self.assertEqual(order, sorted(order), "the precedence order changed")


class MeasurementBuildTests(unittest.TestCase):
    """A measurement has to be comparable to the last measurement, not to this machine."""

    def setUp(self) -> None:
        self.replay = _read("scripts/replay.ps1")

    def test_the_measurement_build_lives_in_config(self):
        self.assertEqual(config.MEASUREMENT_LLAMA_VERSION, "2.47.0")
        # Not hardcoded a second time in the measuring script. Two copies is the same
        # class of bug as two resolvers: one gets updated and the other does not.
        self.assertNotIn('"2.47.0"', _strip_comments(self.replay))
        self.assertIn("MEASUREMENT_LLAMA_VERSION", self.replay)

    def test_replay_asks_the_resolver_for_that_build(self):
        self.assertIn("$resolveArgs = @{ Version = $LlamaVersionWanted }", self.replay)

    def test_replay_checks_the_build_actually_serving(self):
        """The resolved build proves nothing, because replay leaves 8080 alone.

        $Llama is only used on the audio-native path, where replay starts the server
        itself. Every whisper arm is served by whatever the caller started, so recording
        the resolved version in the meta while another one serves is how "2.47.0" ends
        up written over a run that used something else -- and the meta block is read
        later by whoever decides whether two runs are comparable.
        """
        self.assertIn("Get-ProcessCommandLine -Name llama-server", self.replay)
        self.assertIn("served by llama.cpp", self.replay)
        self.assertRegex(self.replay, r"\$liveLlama\s+-ne\s+\$LlamaVersion")

    def test_an_unreadable_command_line_is_refused_not_assumed(self):
        """"Could not find out" and "it is the right one" are different facts.

        WMI is refused in a confined session, and a guard that treats an unreadable
        command line as agreement passes exactly when it cannot check.
        """
        self.assertIn("读不出 8080 上那个 llama-server 是哪一版", self.replay)
        block = self.replay.split("if (-not $liveLlama) {", 1)
        self.assertEqual(len(block), 2, "the unreadable-command-line branch is gone")
        self.assertIn("throw", block[1].split("}", 1)[0])

    def test_shipping_does_not_read_the_measurement_pin(self):
        """The other half: config.MEASUREMENT_LLAMA_VERSION must not reach the serving
        path. start.ps1 ships on newest, and the moment it honours the measurement build
        the two are the same setting with one name and the policy has collapsed.
        """
        self.assertNotIn("MEASUREMENT_LLAMA_VERSION", _strip_comments(_read("scripts/start.ps1")))

    def test_the_serving_build_regex_matches_a_real_path(self):
        """Taken out of replay.ps1 and run here, because it was wrong and no assertion
        about the source could have said so.

        The platform segment is win-x86_64-nvidia-cuda12-avx2 -- it contains an
        underscore. The class was written [A-Za-z0-9\\-], so it matched no real command
        line on this machine, the guard could not name the build it was looking at, and
        it refused every run. A guard that always refuses and a guard that is absent fail
        the same visible way -- no measurement -- and the difference only shows up when
        somebody runs a real replay, which is the slowest possible place to find a typo.
        """
        match = re.search(r"\[regex\]::Match\(\$liveCmd, '([^']+)'\)", self.replay)
        self.assertIsNotNone(match, "replay.ps1 no longer parses the build out of the live command line")
        pattern = re.compile(match.group(1).replace("\\-", "-"))

        cases = {
            r'"C:\Users\me\.lmstudio\extensions\backends\llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.47.0\llama-server.exe" -m x.gguf':
                "2.47.0",
            r'C:\Users\me\.lmstudio\extensions\backends\llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.53.0\llama-server.exe -m y.gguf':
                "2.53.0",
            # Shortest plausible platform segment, to keep the lazy quantifier honest.
            r'C:\b\llama.cpp-x-2.9.0\llama-server.exe': "2.9.0",
        }
        for command_line, expected in cases.items():
            hit = pattern.search(command_line)
            self.assertIsNotNone(hit, f"no match for {command_line}")
            self.assertEqual(hit.group(1), expected)

    def test_the_regex_rejects_a_two_digit_minor_it_should_not_split(self):
        """2.53.0 has to come back whole. A class that stops at the first digit-run would
        return "53" on a real path and compare against "2.53.0" -- which fails, so it is
        caught, but as a refusal rather than as the version it should have reported."""
        match = re.search(r"\[regex\]::Match\(\$liveCmd, '([^']+)'\)", self.replay)
        pattern = re.compile(match.group(1).replace("\\-", "-"))
        hit = pattern.search(r"C:\llama.cpp-win-x86_64-nvidia-cuda12-avx2-2.53.0\llama-server.exe")
        self.assertEqual(hit.group(1), "2.53.0")

    def test_the_default_value_is_floating(self):
        """Unset means newest, in config as well as in the resolver. If config defaulted
        to the measurement build, importing it would pin every consumer of it.
        """
        self.assertEqual(config.LLAMA_VERSION, "")


class ResolverBehaviourTests(unittest.TestCase):
    """The four states, executed. Skipped where no llama build is installed, because a
    test that fails on a fresh clone teaches people to skip the suite."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.backs = pathlib.Path.home() / ".lmstudio" / "extensions" / "backends"
        cls.installed = sorted(
            m.group(1)
            for d in (cls.backs.glob("llama.cpp-win-x86_64-nvidia-cuda12-avx2-*") if cls.backs.exists() else [])
            if (m := re.match(r"llama\.cpp-win-x86_64-nvidia-cuda12-avx2-(\d+\.\d+\.\d+)$", d.name))
            and (d / "llama-server.exe").exists()
        )
        if not cls.installed:
            raise unittest.SkipTest("no llama.cpp build installed for this platform")

    def _resolve(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        full_env = {**dict(__import__("os").environ), **(env or {})}
        return subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(ROOT / "scripts" / "resolve_llama.ps1"), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(ROOT), env=full_env)

    def test_unspecified_resolves_to_the_highest_installed_version(self):
        """With nothing naming a version, the newest installed one runs.

        "Nothing" has to be checked rather than assumed. runtime.json's llama_version is
        a machine-level pin, and the resolver honours it, so a pinned machine resolves to
        the pinned build even though a newer one is installed. That is the policy working,
        not a fault -- and it arrived during this work, when 2.55.0 was installed while
        every measurement stayed on 2.53.0.

        So a pinned machine skips rather than fails. Failing would report a fault that does
        not exist; passing silently would report a newest-installed behaviour that was
        never exercised.
        """
        pinned = ""
        runtime = ROOT / "runtime.json"
        if runtime.exists():
            try:
                pinned = str(json.loads(runtime.read_text(encoding="utf-8")).get(
                    "llama_version") or "")
            except (ValueError, OSError):
                pinned = ""
        if pinned:
            self.skipTest(
                f"runtime.json pins llama_version={pinned!r}, so 'unspecified' is not "
                f"reached on this machine. Remove the key to exercise this rule.")

        done = self._resolve()
        self.assertEqual(done.returncode, 0, done.stderr)
        highest = max(self.installed, key=lambda v: tuple(int(p) for p in v.split(".")))
        self.assertIn(f"-{highest} (newest installed)", done.stdout)

    def test_named_version_is_honoured(self):
        done = self._resolve("-Version", self.installed[0])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"-{self.installed[0]} (argument)", done.stdout)

    def test_a_version_that_is_not_installed_fails_loudly(self):
        done = self._resolve("-Version", "99.99.99")
        self.assertNotEqual(done.returncode, 0, "a missing build must not resolve to anything")
        self.assertIn("99.99.99", done.stdout + done.stderr)
        # And it must not have served the newest one under the name that was asked for.
        self.assertNotIn("newest installed", done.stdout)

    def test_the_environment_outranks_the_file(self):
        """runtime.json is the machine's standing choice, so a one-off environment
        variable has to win it -- otherwise pinning this machine for a day cannot be
        undone for a single run.
        """
        if len(self.installed) < 2:
            self.skipTest("needs two installed builds to tell the two apart")
        other = [v for v in self.installed if v != self.installed[-1]][0]
        done = self._resolve(env={"TRANSLATION_LLAMA_VERSION": other})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"-{other} (environment)", done.stdout)


def _strip_comments(script: str) -> str:
    """PowerShell comments, including the <# #> blocks at the top of the files.

    Needed because the history being explained in these files uses the same names as the
    flags being removed, and asserting on the explanation produces a failure that gets
    deleted rather than fixed.
    """
    script = re.sub(r"(?s)<#.*?#>", "", script)
    return "\n".join(re.sub(r"(?<!`)#.*$", "", line) for line in script.split("\n"))


if __name__ == "__main__":
    unittest.main()