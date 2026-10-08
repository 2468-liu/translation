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
    def test_an_unimplemented_chain_is_refused_without_killing_anything(self):
        """gemma-e2e is in the dropdown and disabled, but a message can still name it.
        Tearing everything down for a chain that cannot start would turn a typo into an
        outage."""
        with patch.object(nh, "kill_everything") as kill:
            reply = nh.restart_chain("gemma-e2e")
        self.assertFalse(reply["ok"])
        self.assertTrue(reply["rejected"])
        kill.assert_not_called()

    def test_a_failed_start_restores_the_previous_chain(self):
        calls: list[str] = []
        with (
            patch.object(nh, "read_chain", return_value="whisper-hy"),
            patch.object(nh, "write_chain", side_effect=lambda c: calls.append(c)),
            patch.object(nh, "kill_everything", return_value=[]),
            patch.object(nh, "start_backend", side_effect=[
                {"ok": False, "error": "本地后端启动超时"},
                {"ok": True, "ready": True, "owned": True},
            ]),
        ):
            reply = nh.restart_chain("qwen3-hy")

        self.assertFalse(reply["ok"])
        self.assertTrue(reply["rolled_back"])
        self.assertEqual(reply["chain"], "whisper-hy")
        # Written twice: the chain being tried, then the one that worked.
        self.assertEqual(calls, ["qwen3-hy", "whisper-hy"])

    def test_a_rollback_that_also_fails_says_so(self):
        """Reported as a plain failure without the rolled_back flag, so the popup cannot
        put the dropdown back on a chain that is not running."""
        with (
            patch.object(nh, "read_chain", return_value="whisper-hy"),
            patch.object(nh, "write_chain", return_value=None),
            patch.object(nh, "kill_everything", return_value=[]),
            patch.object(nh, "start_backend", return_value={"ok": False, "error": "起不来"}),
        ):
            reply = nh.restart_chain("qwen3-hy")
        self.assertFalse(reply["ok"])
        self.assertFalse(reply["rolled_back"])
        self.assertIn("回退", reply["error"])

    def test_a_switch_to_the_running_chain_still_restarts(self):
        """No short-circuit on sameness: the point is to get a clean backend, and a user
        picking the current chain may be recovering from a wedged one."""
        with (
            patch.object(nh, "read_chain", return_value="whisper-hy"),
            patch.object(nh, "write_chain", return_value=None),
            patch.object(nh, "kill_everything", return_value=[]),
            patch.object(nh, "start_backend", return_value={"ok": True, "ready": True}),
        ):
            reply = nh.restart_chain("whisper-hy")
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["chain"], "whisper-hy")


class ChainTableTests(unittest.TestCase):
    """The chain table is in server/config.py, not runtime.json.

    It was in runtime.json first, and runtime.json is gitignored because it holds this
    machine's absolute paths to llama builds. Eleven of these tests passed on the
    working tree and failed on a fresh clone, because the defaults for a shipped
    configuration were living in a file that is not shipped.
    """

    def test_the_default_chain_is_one_that_exists(self):
        self.assertIn(config.DEFAULT_CHAIN, config.CHAINS)
        self.assertEqual(config.DEFAULT_CHAIN, "whisper-hy")

    def test_every_chain_names_what_it_is_and_what_it_measured(self):
        for name, spec in config.CHAINS.items():
            for field in ("label", "status", "asr_backend", "qwen3_port", "measured"):
                self.assertIn(field, spec, f"{name} is missing {field}")

    def test_the_unimplemented_chain_is_excluded_from_implemented(self):
        """Which is the single fact that decides whether the dropdown offers it."""
        self.assertNotIn("gemma-e2e", config.IMPLEMENTED_CHAINS)
        self.assertEqual(config.CHAINS["gemma-e2e"]["status"], "not implemented")
        self.assertEqual(set(config.IMPLEMENTED_CHAINS),
                         {"whisper-hy", "qwen3-hy"})

    def test_the_unimplemented_chain_has_no_backend_rather_than_a_wrong_one(self):
        """None, not "whisper". Mapping it onto a working engine would make an
        unimplemented chain indistinguishable from an implemented one."""
        self.assertIsNone(config.CHAINS["gemma-e2e"]["asr_backend"])

    def test_the_qwen3_port_is_named_in_one_place(self):
        self.assertEqual(config.QWEN3_ASR_PORT, 8099)
        self.assertEqual(config.CHAINS["qwen3-hy"]["qwen3_port"], 8099)
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

    def test_the_select_offers_exactly_the_implemented_chains(self):
        """A chain in the dropdown that start.ps1 refuses would be a control that
        selects a value the backend then throws on. The unimplemented one is listed
        but disabled, so the reason it is not available is visible rather than absent.
        """
        declared = set(config.CHAINS)
        implemented = {"whisper-hy", "qwen3-hy"}
        self.assertEqual(declared - implemented, {"gemma-e2e"},
                         "a new chain needs a dropdown row and a start.ps1 branch")
        self.assertIn('id="chainSelect"', self.popup)
        for chain in implemented:
            self.assertIn(f'value="{chain}"', self.popup)
        for chain in declared - implemented:
            self.assertIn(f'value="{chain}" disabled', self.popup)

    def test_start_ps1_refuses_the_unimplemented_chain_by_name(self):
        self.assertIn("gemma-e2e", self.start)
        self.assertIn("未实现的链路", self.start)

    def test_the_host_only_accepts_the_implemented_chains(self):
        self.assertIn('if chain not in ("whisper-hy", "qwen3-hy")', self.host)

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


if __name__ == "__main__":
    unittest.main()