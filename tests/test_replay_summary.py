"""How the replay harness rebuilds one sentence from its final translations.

The soft cut in ``vad.py`` can close a long sentence as two utterances, so the
first final message for a sentence may only cover its first half.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from blog_video_test import _sentence_translation  # noqa: E402


def _tr(at: float, text: str, final: bool = True) -> dict:
    return {"type": "translation", "final": final, "audio_sec": at, "text": text}


def _asr(at: float, text: str = "") -> dict:
    return {"type": "asr", "final": True, "audio_sec": at, "text": text}


def test_single_final_is_used_as_is():
    events = [_tr(10.5, "本周做了三条视频。")]
    assert _sentence_translation(events, [_asr(10.4)], 10.0) == "本周做了三条视频。"


def test_a_split_sentence_is_joined():
    """Soft cut closes the sentence twice; the two halves barely overlap."""
    events = [_tr(9.7, "本周做了三条"), _tr(10.5, "视频。")]
    marks = [_asr(9.6), _asr(10.4)]
    assert _sentence_translation(events, marks, 10.0) == "本周做了三条视频。"


def test_a_duplicate_final_is_not_appended_twice():
    events = [_tr(10.5, "本周做了三条视频。"), _tr(10.6, "本周做了三条视频。")]
    assert _sentence_translation(events, [_asr(10.4)], 10.0) == "本周做了三条视频。"


def test_a_shorter_revision_keeps_the_longer_text():
    events = [_tr(10.5, "本周做了三条视频。"), _tr(10.6, "本周做了三条")]
    assert _sentence_translation(events, [_asr(10.4)], 10.0) == "本周做了三条视频。"


def test_a_rewrite_replaces_rather_than_concatenates():
    events = [_tr(10.5, "本周做了三条视频。"), _tr(10.7, "本周做了三条视频短片。")]
    assert _sentence_translation(events, [_asr(10.4)], 10.0) == "本周做了三条视频短片。"


def test_the_next_sentence_is_not_pulled_in():
    """Its utterance ended outside the window, so its final is not ours."""
    events = [_tr(10.5, "本周做了三条视频。"), _tr(14.0, "湖面已经结冰了。")]
    marks = [_asr(10.4), _asr(13.9)]
    assert _sentence_translation(events, marks, 10.0) == "本周做了三条视频。"


def test_streaming_pieces_are_ignored():
    events = [_tr(9.1, "本", False), _tr(9.2, "本周", False), _tr(10.5, "本周做了三条视频。")]
    assert _sentence_translation(events, [_asr(10.4)], 10.0) == "本周做了三条视频。"


def test_a_missing_final_does_not_shift_the_others():
    """Matching by time, not by index: one utterance translated to nothing."""
    events = [_tr(9.7, "本周做了三条"), _tr(10.5, "视频。")]
    marks = [_asr(9.6), _asr(9.9), _asr(10.4)]
    assert _sentence_translation(events, marks, 10.0) == "本周做了三条视频。"


def test_no_asr_marks_gives_nothing():
    assert _sentence_translation([_tr(10.5, "本周做了三条视频。")], [], 10.0) is None