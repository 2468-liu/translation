"""A closing event must be reported with its own sentence's translation.

The closing event for an utterance usually carries the same source text as the
early partial that preceded it, so the repeat translation is skipped. The final
message still has to go out -- but the translation it reuses is only correct if
the job for *this* text has already finished streaming.
"""

import asyncio
import unittest

import numpy as np

from server import pipeline
from server.pipeline import Session


class SlowTranslate:
    """Stands in for the translation server: yields slowly, in pieces.

    Each source sentence gets a distinguishable translation, so a final carrying
    the wrong one is visible rather than merely plausible.
    """

    OUT = {"first": "第一句", "second": "第二句", "hello": "你好"}

    def __init__(self, delay: float = 0.30):
        self.delay = delay
        self.calls: list[str] = []

    async def __call__(self, text, language, context, client, api=None):
        self.calls.append(text)
        out = self.OUT.get(text, f"译[{text}]")
        await asyncio.sleep(self.delay)
        yield out[0]
        await asyncio.sleep(0.01)
        yield out[1:]


class FakeAsr:
    def __init__(self, results):
        self.results = iter(results)

    def transcribe(self, _audio, language):
        return next(self.results)


class PipelineFinalTextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._real = pipeline.stream_translation
        self._translate = SlowTranslate()
        pipeline.stream_translation = self._translate

    def tearDown(self):
        pipeline.stream_translation = self._real

    async def _drain(self, session):
        """Wait for any in-flight translation to finish."""
        for _ in range(60):
            if session.translate_task is None:
                return
            await asyncio.sleep(0.02)

    async def _run(self, session, kind, send, drain: bool = True):
        await session._run(kind, np.ones(16000, dtype=np.float32), send)
        if drain:
            await self._drain(session)

    async def test_final_is_not_the_previous_sentence_while_still_streaming(self):
        """The closing event arrives before this sentence's translation ends.

        Here "上一句" is translated first, then the next sentence's translation
        is started and deliberately left in flight. The closing event for that
        second sentence must not be answered with the first sentence's text.
        """
        session = Session(
            FakeAsr([("first", "en", 0.9), ("second", "en", 0.9), ("second", "en", 0.9)])
        )
        sent: list[dict] = []

        async def send(payload: dict) -> None:
            sent.append(payload)

        try:
            # First sentence, closed: its translation runs to completion, so
            # last_translation holds this sentence's text.
            await self._run(session, "end", send)
            first_finals = [
                p for p in sent if p.get("type") == "translation" and p["final"]
            ]
            self.assertTrue(first_finals, sent)
            self.assertEqual(first_finals[-1]["text"], "第一句")

            # Second sentence: ASR now returns a different text, so a new job
            # starts and streams slowly.
            await self._run(session, "partial", send)
            # ...and the closing event for it lands while that job is still going.
            await self._run(session, "end", send)
            await asyncio.sleep(0.05)

            finals = [
                p
                for p in sent
                if p.get("type") == "translation" and p["final"]
            ]
            # Two finals exist: the first sentence's, and one for the second. The
            # second must not be the first sentence's text.
            self.assertGreaterEqual(len(finals), 2, finals)
            for payload in finals[1:]:
                self.assertNotEqual(
                    payload["text"],
                    "第一句",
                    f"final carried the previous sentence's translation: {finals}",
                )
        finally:
            await session.close()

    async def test_final_reuses_its_own_completed_translation(self):
        """When the job has finished, reusing its text is correct and cheap."""
        session = Session(FakeAsr([("hello", "en", 0.9), ("hello", "en", 0.9)]))
        sent: list[dict] = []

        async def send(payload: dict) -> None:
            sent.append(payload)

        try:
            await self._run(session, "partial", send)
            await asyncio.sleep(0.4)
            before = len(sent)
            # Same text again, now closing: the translation is already on screen.
            await self._run(session, "end", send)
            finals = [
                p
                for p in sent[before:]
                if p.get("type") == "translation" and p["final"]
            ]
            self.assertEqual([p["text"] for p in finals], ["你好"])
        finally:
            await session.close()

    async def test_final_waits_for_the_in_flight_translation(self):
        """The closing event is answered when the job ends, not before.

        This is the case that actually occurs: the early partial starts the
        translation, and the closing event lands while it is still streaming.
        """
        session = Session(
            FakeAsr([("first", "en", 0.9), ("second", "en", 0.9), ("second", "en", 0.9)])
        )
        sent: list[dict] = []

        async def send(payload: dict) -> None:
            sent.append(payload)

        try:
            await self._run(session, "end", send)
            # Start the next sentence and leave it in flight.
            await self._run(session, "partial", send, drain=False)
            await asyncio.sleep(0.02)
            # Closing event for the same text while the job is still streaming.
            await self._run(session, "end", send)
            await self._drain(session)

            finals = [p["text"] for p in sent if p.get("type") == "translation" and p["final"]]
            self.assertIn("第一句", finals)
            # The second sentence's closing final must exist and be its own text.
            self.assertIn("第二句", finals, sent)
            # Nothing may carry the first sentence's text as the second's final.
            order = [p["text"] for p in sent if p.get("type") == "translation"]
            self.assertEqual(order[-1], "第二句", order)
        finally:
            await session.close()

    async def test_stale_final_is_never_sent(self):
        """A final whose text belongs to another source must not go out."""
        session = Session(FakeAsr([("alpha", "en", 0.9), ("beta", "en", 0.9)]))
        sent: list[dict] = []

        async def send(payload: dict) -> None:
            sent.append(payload)

        try:
            await self._run(session, "partial", send)
            await asyncio.sleep(0.4)
            session.shown_translation = ""
            session.last_translated = "alpha"
            # last_translation_src says this text is from an older job: reusing
            # it would label the wrong sentence as final.
            session.last_translation = "上一句的译文"
            session.last_translation_src = "some-earlier-sentence"

            # ASR returns "beta" this time; a fresh job runs for it.
            await self._run(session, "end", send)
            await asyncio.sleep(0.02)
            finals = [
                p
                for p in sent
                if p.get("type") == "translation" and p["final"]
            ]
            for payload in finals:
                self.assertNotEqual(payload["text"], "上一句的译文")
        finally:
            await session.close()


if __name__ == "__main__":
    unittest.main()