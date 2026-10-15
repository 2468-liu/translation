import unittest

import numpy as np

from server.pipeline import Session


class FakeAsr:
    def __init__(self, results):
        self.results = iter(results)
        self.languages = []

    def transcribe(self, _audio, language):
        self.languages.append(language)
        return next(self.results)


class PipelineLanguageTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_mode_redetects_after_previous_language(self):
        asr = FakeAsr(
            [
                ("hello", "en", 0.99),
                ("こんにちは", "ja", 0.99),
            ]
        )
        session = Session(asr)
        scheduled = []
        session._schedule_translate = lambda text, final, language, generation, send: scheduled.append(
            (text, final, language, generation)
        )
        async def send(_payload):
            return None
        try:
            await session._run("end", np.ones(16000, dtype=np.float32), send)
            await session._run("end", np.ones(16000, dtype=np.float32), send)
        finally:
            await session.close()

        self.assertEqual(asr.languages, [None, None])
        self.assertEqual([item[2] for item in scheduled], ["en", "ja"])
        self.assertEqual(session.language, "ja")
        self.assertFalse(session.language_locked)

    async def test_manual_language_stays_locked(self):
        asr = FakeAsr([("hello", "en", 0.99)])
        session = Session(asr)
        try:
            session.set_language("ja")
            async def send(_payload):
                return None

            await session._run("end", np.ones(16000, dtype=np.float32), send)
        finally:
            await session.close()

        self.assertEqual(asr.languages, ["ja"])
        self.assertEqual(session.language, "ja")
        self.assertTrue(session.language_locked)

    async def test_closing_event_reports_a_final_translation(self):
        """An unchanged closing text still has to be marked as final.

        Skipping the repeat translation is right, but skipping the message
        entirely leaves every recorder without a single final event, which is
        how per-sentence quality came back empty across six replays.
        """
        session = Session(FakeAsr([("hello", "en", 0.99), ("hello", "en", 0.99)]))
        sent: list[dict] = []
        scheduled: list[tuple] = []
        session._schedule_translate = (
            lambda text, final, language, generation, send: scheduled.append(
                (text, final, language)
            )
        )

        async def send(payload: dict) -> None:
            sent.append(payload)

        try:
            # The recogniser returns the same source text a second time, so the
            # repeat translation is skipped -- but the final must still go out.
            # last_translation_src says the cached text belongs to this source,
            # which is what makes reusing it safe.
            session.last_translated = "hello"
            session.last_translation = "你好"
            session.last_translation_src = "hello"
            await session._run("end", np.ones(16000, dtype=np.float32), send)
        finally:
            await session.close()

        finals = [p for p in sent if p.get("type") == "translation" and p["final"]]
        self.assertEqual([p["text"] for p in finals], ["你好"])

    async def test_new_media_clears_auto_language_and_caption_context(self):
        session = Session(FakeAsr([]))
        try:
            session.language = "en"
            session.context.append(("old", "旧字幕"))
            session.shown_translation = "旧翻译"
            session.reset_for_media()
        finally:
            await session.close()

        self.assertIsNone(session.language)
        self.assertFalse(session.language_locked)
        self.assertEqual(session.context, [])
        self.assertEqual(session.shown_translation, "")


if __name__ == "__main__":
    unittest.main()
