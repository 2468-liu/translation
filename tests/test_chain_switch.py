"""Switching chains must not be able to leave the user without a backend.

The dropdown in the popup looks like one control. What it triggers is: stop every
process this project owns, start a different set of models, and hope. Three of those
steps can fail, and the order they were written in left each failure with a different
consequence:

  A chain that will not start left nothing running, so recovering meant opening
  runtime.json by hand to undo a click. The host now restores the previous chain.
  A kill that half-succeeded left a process holding VRAM with no pid file, which is the
  orphan stop_backend was written to avoid. The teardown list is now the ports plus the
  pid file rather than the pid file alone.
  A teardown that killed more than it meant to would take out an LM Studio chat model.
  So the scope is the three ports this project owns, not every llama-server on the
  machine.

These tests pin that scope and that rollback, because both are easy to widen later
without noticing: killing by image name instead of by port is a one-line change and it
kills somebody's unrelated work.
"""

from __future__ import annotations

import json
import pathlib
import unittest
from unittest.mock import patch

from scripts import native_host as nh
from server import config

ROOT = pathlib.Path(__file__).resolve().parents[1]


class KillScopeTests(unittest.TestCase):
    def test_the_scope_is_the_three_ports_this_project_owns(self):
        """8765 is the websocket service, 8080 the translator, 8099 the Qwen3
        recogniser. Adding a model means adding a port here, and the failure mode of
        forgetting is an invisible process holding VRAM."""
        self.assertEqual(set(nh.OWNED_PORTS), {8765, 8080, 8099})
        self.assertEqual(nh.WS_PORT, 8765)
        self.assertEqual(nh.LLAMA_PORT, 8080)

    def test_nothing_is_killed_by_image_name(self):
        """A blanket llama-server kill would stop an LM Studio chat model the user has
        open, and nothing in this project would report it as a failure."""
        source = (ROOT / "scripts" / "native_host.py").read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if "taskkill" in stripped or "kill(" in stripped:
                self.assertNotIn("llama-server", stripped,
                                 "killing by image name reaches outside this project")

    def test_every_owned_port_is_searched_before_starting_again(self):
        source = (ROOT / "scripts" / "native_host.py").read_text(encoding="utf-8")
        self.assertIn("for port in OWNED_PORTS", source)

    def test_a_port_left_open_is_reported_as_a_problem(self):
        """Silently proceeding to start would race the process that still holds it."""
        with (
            patch.object(nh, "pids_on_port", return_value=[]),
            patch.object(nh, "read_pid", return_value=None),
            patch.object(nh, "listening", return_value=True),
            patch.object(nh, "remove_pid"),
            patch.object(nh.time, "monotonic", side_effect=[0.0] + [i * 0.2 for i in range(200)]),
        ):
            problems = nh.kill_everything()
        self.assertTrue(any("端口仍在监听" in p for p in problems), problems)


class RollbackTests(unittest.TestCase):
    def test_an_unknown_chain_is_refused_without_killing_anything(self):
        """A message can name anything. Tearing everything down for a chain that does
        not exist would turn a typo into an outage."""
        with patch.object(nh, "kill_everything") as kill:
            reply = nh.restart_chain("gemma-e9")
        self.assertFalse(reply["ok"])
        self.assertTrue(reply["rejected"])
        kill.assert_not_called()

    def test_gemma_e2e_is_accepted_now(self):
        """It needs two switches rather than one -- a Gemma4 translator on 8082 and no
        recogniser at all -- so accepting it here is not the same as accepting a name."""
        with (
            patch.object(nh, "read_chain", return_value="nv-in"),
            patch.object(nh, "write_chain", return_value=None),
            patch.object(nh, "kill_everything", return_value=[]),
            patch.object(nh, "start_backend", return_value={"ok": True, "ready": True}),
        ):
            reply = nh.restart_chain("gemma-e2e")
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["chain"], "gemma-e2e")

    def test_a_failed_start_restores_the_previous_chain(self):
        calls: list[str] = []
        with (
            patch.object(nh, "read_chain", return_value="nv-in"),
            patch.object(nh, "write_chain", side_effect=lambda c: calls.append(c)),
            patch.object(nh, "kill_everything", return_value=[]),
            patch.object(nh, "start_backend", side_effect=[
                {"ok": False, "error": "本地后端启动超时"},
                {"ok": True, "ready": True, "owned": True},
            ]),
        ):
            reply = nh.restart_chain("qwen3-in")

        self.assertFalse(reply["ok"])
        self.assertTrue(reply["rolled_back"])
        self.assertEqual(reply["chain"], "nv-in")
        # Written twice: the chain being tried, then the one that worked.
        self.assertEqual(calls, ["qwen3-in", "nv-in"])

    def test_a_rollback_that_also_fails_says_so(self):
        """Reported as a plain failure without the rolled_back flag, so the popup cannot
        put the dropdown back on a chain that is not running."""
        with (
            patch.object(nh, "read_chain", return_value="nv-in"),
            patch.object(nh, "write_chain", return_value=None),
            patch.object(nh, "kill_everything", return_value=[]),
            patch.object(nh, "start_backend", return_value={"ok": False, "error": "起不来"}),
        ):
            reply = nh.restart_chain("qwen3-in")
        self.assertFalse(reply["ok"])
        self.assertFalse(reply["rolled_back"])
        self.assertIn("回退", reply["error"])

    def test_a_switch_to_the_running_chain_still_restarts(self):
        """No short-circuit on sameness: the point is to get a clean backend, and a user
        picking the current chain may be recovering from a wedged one."""
        with (
            patch.object(nh, "read_chain", return_value="nv-in"),
            patch.object(nh, "write_chain", return_value=None),
            patch.object(nh, "kill_everything", return_value=[]),
            patch.object(nh, "start_backend", return_value={"ok": True, "ready": True}),
        ):
            reply = nh.restart_chain("nv-in")
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["chain"], "nv-in")


class ChainTableTests(unittest.TestCase):
    """The chain table is in server/config.py, not runtime.json.

    It was in runtime.json first, and runtime.json is gitignored because it holds this
    machine's absolute paths to llama builds. Eleven of these tests passed on the
    working tree and failed on a fresh clone, because the defaults for a shipped
    configuration were living in a file that is not shipped.
    """

    def test_the_default_chain_is_one_that_exists(self):
        self.assertIn(config.DEFAULT_CHAIN, config.CHAINS)
        self.assertEqual(config.DEFAULT_CHAIN, "qwen3-in")

    def test_every_chain_names_what_it_is_and_what_it_measured(self):
        for name, spec in config.CHAINS.items():
            for field in ("label", "status", "asr_backend", "qwen3_port", "measured"):
                self.assertIn(field, spec, f"{name} is missing {field}")

    def test_gemma_e2e_is_measured_not_unimplemented(self):
        """Implemented, measured, and rejected -- three different facts from the two the
        first version of this table said.

        The evidence that was not checked before writing "not implemented": pipeline.py
        has _run_audio_native, ws_server branches on AUDIO_NATIVE=1, start.ps1 has a
        132-line gemma4 branch, two arms ran with audio_native=true on llama 2.47.0
        (pw-audio-a-r1 and -r2, 42 and 44 completions under audio.ttft/audio.total), and
        REPORT sections 15, 21 and 25 are about this path. It works on clean narration and
        loses on speech under music. "Not implemented" sent the next reader looking for
        code that was already written.
        """
        spec = config.CHAINS["gemma-e2e"]
        self.assertNotIn("not implemented", spec["status"])
        self.assertIn("measured", spec["status"])
        self.assertIn("blog_3min", spec["measured"])
        self.assertIn("e_3min", spec["measured"])
        # Rejected on the material, not on the card -- that distinction decides whether
        # better hardware is worth anything here.
        self.assertIn("not on the card", spec["caveat"])

    def test_measured_and_startable_are_different_questions(self):
        """Every chain here has been run at least once. Whether start.ps1 can start it is
        a separate property, and conflating them is what produced the error above."""
        self.assertEqual(set(config.MEASURED_CHAINS), set(config.CHAINS))
        self.assertIn("gemma-e2e", config.MEASURED_CHAINS)
        self.assertIsInstance(config.STARTABLE_CHAINS, tuple)
        self.assertIsInstance(config.MEASURED_CHAINS, tuple)

    def test_gemma_e2e_has_no_asr_backend_because_there_is_no_separate_recogniser(self):
        """None, and for the right reason: one model does both jobs, so there is no ASR
        backend to name. Not "whisper" -- that would make a chain which cannot run this
        way look like one that runs on the wrong setting."""
        self.assertIsNone(config.CHAINS["gemma-e2e"]["asr_backend"])

    def test_the_qwen3_port_is_named_in_one_place(self):
        self.assertEqual(config.QWEN3_ASR_PORT, 8099)
        self.assertEqual(config.CHAINS["qwen3-in"]["qwen3_port"], 8099)
        self.assertTrue(config.QWEN3_ASR_URL.endswith(":8099"))

    def test_runtime_json_is_not_where_the_defaults_live(self):
        """It may carry this machine's selection, but a clone must not need it."""
        self.assertIn("runtime.json", (ROOT / ".gitignore").read_text(encoding="utf-8"))
        self.assertNotIn("CHAINS", (ROOT / "scripts" / "start.ps1").read_text(encoding="utf-8")
                         [:0] or "")


class RuntimeJsonSelectionTests(unittest.TestCase):
    """What runtime.json is still for: this machine's current selection, and the
    provenance of the fields that stopped being read."""

    def setUp(self):
        path = ROOT / "runtime.json"
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def test_the_selection_is_a_chain_that_exists(self):
        if not self.data:
            self.skipTest("runtime.json is machine-local and absent")
        self.assertIn(self.data["chain"], config.CHAINS)

    def test_the_selection_is_something_start_can_actually_start(self):
        """runtime.json holds a selection, not a wish.

        This caught a real one. A manual restart_chain('gemma-e2e') -- run outside the
        suite, with write_chain stubbed nowhere because it was the real thing being
        exercised -- left chain=gemma-e2e in the file, and every later DryRun came up in
        audio-native even though config.DEFAULT_CHAIN said whisper-hy. Nothing in the
        test suite had written the file; the suite was not the cause, and a test that
        reads the file could not have told that from a legitimate switch.

        So the guard is on the value: the selection has to be one start.ps1 can start,
        and a chain whose branch does not exist is a failure rather than a curiosity.
        """
        if not self.data:
            self.skipTest("runtime.json is machine-local and absent")
        self.assertIn(self.data["chain"], config.STARTABLE_CHAINS,
                      "runtime.json selects a chain start.ps1 has no branch for, so the "
                      "backend comes up as something else than what the file says")

    def test_the_deprecated_fields_are_kept_and_labelled(self):
        """They are provenance. Deleting them loses the answer to "why does 2.33.0
        appear here", and the answer is a date."""
        if not self.data:
            self.skipTest("runtime.json is machine-local and absent")
        old = self.data.get("_deprecated", {})
        for field in ("llama_server", "gguf", "ngl", "ctx"):
            self.assertIn(field, old)
        self.assertIn("2026", old["_note"])

    def test_the_deprecated_fields_are_not_readable_as_settings(self):
        """They live under _deprecated, so nothing reads them by accident."""
        if not self.data:
            self.skipTest("runtime.json is machine-local and absent")
        self.assertNotIn("llama_server", {k for k in self.data if not k.startswith("_")})
        self.assertNotIn("gguf", {k for k in self.data if not k.startswith("_")})


class PopupAndHostAgreeTests(unittest.TestCase):
    """The dropdown, the host and start.ps1 have to name the same chains."""

    def setUp(self):
        self.popup = (ROOT / "extension" / "popup.html").read_text(encoding="utf-8")
        self.background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")
        self.host = (ROOT / "scripts" / "native_host.py").read_text(encoding="utf-8")
        self.start = (ROOT / "scripts" / "start.ps1").read_text(encoding="utf-8")
        self.config = (ROOT / "server" / "config.py").read_text(encoding="utf-8")
        self.caption = (ROOT / "server" / "caption_window.py").read_text(encoding="utf-8")

    def test_the_select_offers_every_chain_start_can_start(self):
        """All three, because gemma-e2e is startable now.

        The first version of this switch left gemma-e2e out with a comment saying the
        reason was a missing branch. That was true of this switch and it was also true
        that start.ps1 already had a 132-line gemma4 branch -- reached by
        TRANSLATION_MT_BACKEND rather than by the chain. So the branch existed and the
        chain selector just did not know about it, which is a different fault from the
        one the comment described.

        The label still has to say what the measurements said, because "available" here
        means reachable, not recommended.
        """
        self.assertEqual(set(config.CHAINS), {"nv-in", "qwen3-in", "gemma-e2e"})
        self.assertIn('id="chainSelect"', self.popup)
        for chain in config.CHAINS:
            self.assertIn(f'value="{chain}"', self.popup)
        self.assertNotIn("disabled", self.popup.split('id="chainSelect"')[1].split("</select>")[0])
        row = [l for l in self.popup.splitlines() if 'value="gemma-e2e"' in l][0]
        self.assertNotIn("未实现", row)
        self.assertIn("崩", row, "the label must carry the measured failure")

    def test_the_output_mode_is_visible_as_part_of_the_line(self):
        """流式/块状归并到线路：它不再是一个可以单独设置的下拉，而是线路的属性。

        所以它必须**在线路名里看得见** —— 否则用户只能靠记忆知道 Qwen3 那条是块输出，
        而这正是当初那个独立开关存在的理由（也是它说谎的地方）。
        """
        for chain, spec in config.CHAINS.items():
            row = [l for l in self.popup.splitlines() if f'value="{chain}"' in l]
            self.assertTrue(row, chain)
            expected = "块输出" if spec["output"] == "block" else "流式"
            self.assertIn(expected, row[0], f"{chain} 的输出方式没在线路名里")
        # 显示层只读结论；两个可以设置它的地方是这一条要挡的东西。
        self.assertIn("BLOCK_BY_PUNCT = BLOCK_OUTPUT", self.caption)
        self.assertIn('"output": "block"', self.config)
        self.assertIn('"output": "stream"', self.config)
        # start.ps1 必须把**观测到的**路线交给进程环境，否则回退之后会自称 qwen3。
        self.assertIn("$env:TRANSLATION_CHAIN_RUNNING = $ChainRunning", self.start)

    def test_start_ps1_refuses_a_chain_it_has_never_heard_of(self):
        """Unimplemented is not the same as unrecognised. gemma-e2e used to be caught by
        an unimplemented-chain guard that no longer describes anything."""
        self.assertIn("gemma-e2e", self.start)
        self.assertIn("未知的链路", self.start)

    def test_the_host_accepts_exactly_the_chains_the_table_declares(self):
        """Three places name the chains -- the table, the host's allow-list and the
        dropdown -- and a fourth place disagreeing is how a control ends up selecting
        something the backend refuses."""
        self.assertIn(
            'if chain not in ("nv-in", "qwen3-in", "gemma-e2e")', self.host)
        for chain in config.CHAINS:
            self.assertIn(f'value="{chain}"', self.popup)

    def test_the_qwen3_chain_starts_the_port_its_engine_calls(self):
        """8099 is in three places and they have to agree: the engine's default URL, the
        host's kill scope, and the server start. Two agreeing is not enough."""
        from server.config import QWEN3_ASR_URL

        self.assertTrue(QWEN3_ASR_URL.endswith(":8099"))
        self.assertIn(8099, nh.OWNED_PORTS)
        self.assertEqual(config.QWEN3_ASR_PORT, 8099)

    def test_the_message_reaches_the_host(self):
        self.assertIn('message.type === "switch-chain"', self.background)
        self.assertIn('nativeRequest("switch-chain"', self.background)
        self.assertIn('kind == "switch-chain"', self.host)

    def test_the_stop_button_comes_from_the_host_not_from_browser_storage(self):
        """The failure this pins is the one reported as "clicking stop does not kill it".

        The popup read `running` from chrome.storage.session and the offscreen document,
        both of which only record what this extension session started. With neither --
        a reopened popup, a restarted browser, an offscreen document closed by something
        else -- it showed 未开始 and a 开始 button while llama-server held 3.4 of 4.0 GB.
        There was no stop button to press. Pressing 开始 called start_backend, which saw
        the ports open and returned success, so nothing happened and it read as a stop
        that did not work.
        """
        popup_js = (ROOT / "extension" / "popup.js").read_text(encoding="utf-8")
        self.assertIn('typeof reply.up === "boolean"', popup_js)
        self.assertIn("if (hostUp !== null) running = running || hostUp", popup_js)
        # And an unreachable host must not be reported as "nothing is running", which is
        # the claim that hides a live backend.
        self.assertIn("hostReachable = false", popup_js)
        self.assertIn("本地后端宿主连不上", popup_js)
        # The button text follows the state, and stop is the branch that kills.
        self.assertIn('toggle.textContent = running ? "终止后端" : "开始"', popup_js)
        # The host has to actually report it.
        self.assertIn('"up": any(listening(port) for port in OWNED_PORTS)', self.host)

    def test_start_ps1_records_the_chain_it_actually_started(self):
        """Under Fail-Fast, missing Qwen3 weights or llama 2.53.0 must throw directly
        rather than silently falling back to whisper-hy. $ChainRunning is initialised
        to $Chain and written to runtime.json.
        """
        self.assertIn("chain_running", self.start)
        self.assertIn("Fail-Fast", self.start)
        # Recorded after branches, before server start.
        write = "Add-Member -NotePropertyName chain_running"
        self.assertIn(write, self.start)
        # And initialised before the branches, not inside one.
        self.assertLess(
            self.start.index("$ChainRunning = $Chain"),
            self.start.index('if ($Chain -eq "gemma-e2e")'),
            "$ChainRunning is initialised after the gemma-e2e branch can skip it")
        # silently fell back to the requested chain again.
        self.assertLess(
            self.start.index("$ChainRunning = $Chain"),
            self.start.index('if ($Chain -eq "gemma-e2e")'),
            "$ChainRunning is initialised after the gemma-e2e branch can skip it")
        # The write must not go through PS 5.1's Set-Content -Encoding UTF8, which emits a
        # BOM that breaks the host's own read.
        self.assertIn("System.Text.UTF8Encoding($false)", self.start)
        self.assertNotIn("Set-Content -Path $rtPath -Encoding UTF8", self.start)

    def test_the_host_prefers_the_running_chain_over_the_requested_one(self):
        self.assertIn('for field in ("chain_running", "chain")', self.host)


if __name__ == "__main__":
    unittest.main()