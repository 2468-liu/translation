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
