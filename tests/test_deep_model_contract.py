"""Deep Model Contract and Resilience Testing Suite.

Inspired by DeepL / OpenAI Client contract specifications:
1. Language Contract Criterion: Zero KeyError and safe fallbacks for edge-case/malformed language codes across NV ASR and Hy-MT2.
2. Circuit Breaker Criterion: Fault tolerance during backend translation disconnections and seamless resumption on recovery.
"""

from __future__ import annotations

import httpx
import pytest
from unittest.mock import AsyncMock, patch

from server.asr import (
    to_nemo_bcp47,
    parse_nemotron_transcript,
    parse_qwen3_asr,
    format_qwen3_prompt,
)
from server.pipeline import Session
from server.translate import build_messages, resolve_lang_en_name, stream_translation


class TestLanguageContractCriterion:
    """Language Contract Criterion: Exhaustive input validation across protocol layer."""

    @pytest.mark.parametrize(
        "raw_input, expected_bcp47",
        [
            ("en", "en-US"),
            ("zh", "zh-CN"),
            ("ja", "ja-JP"),
            ("ko", "ko-KR"),
            ("en-US", "en-US"),
            ("zh-CN", "zh-CN"),
            ("auto", None),
            ("", None),
            (None, None),
            ("unknown_lang", None),
            ("!!@#$%", None),
            ("zh-TW", "zh-CN"),
            ("es-MX", "es-ES"),
        ],
    )
    def test_nemo_bcp47_mapping_never_raises_keyerror(self, raw_input, expected_bcp47):
        """to_nemo_bcp47 must handle any string/None without throwing KeyError."""
        result = to_nemo_bcp47(raw_input)
        assert result == expected_bcp47

    @pytest.mark.parametrize(
        "raw_lang, expected_en_name",
        [
            ("en", "English"),
            ("zh", "Chinese"),
            ("ja", "Japanese"),
            ("ko", "Korean"),
            ("en-US", "English"),
            ("zh-CN", "Chinese"),
            ("", "English"),
            (None, "English"),
            ("invalid_code_123", "English"),
            ("###$$$", "English"),
        ],
    )
    def test_hy_mt2_resolve_lang_en_name_never_raises_keyerror(self, raw_lang, expected_en_name):
        """resolve_lang_en_name must always fallback safely without KeyError."""
        assert resolve_lang_en_name(raw_lang, default="English") == expected_en_name

    @pytest.mark.parametrize(
        "lang_input, expected_prompt",
        [
            ("auto", "Transcribe the audio."),
            ("", "Transcribe the audio."),
            (None, "Transcribe the audio."),
            ("zh", "Transcribe the Chinese audio."),
            ("zh-CN", "Transcribe the Chinese audio."),
            ("zh-TW", "Transcribe the Chinese audio."),
            ("en", "Transcribe the English audio."),
            ("en-US", "Transcribe the English audio."),
            ("ja", "Transcribe the Japanese audio."),
            ("ko", "Transcribe the Korean audio."),
            ("es", "Transcribe the Spanish audio."),
            ("fr", "Transcribe the French audio."),
            ("de", "Transcribe the German audio."),
        ],
    )
    def test_format_qwen3_prompt_standards(self, lang_input, expected_prompt):
        """format_qwen3_prompt must generate exact natural language instructions."""
        assert format_qwen3_prompt(lang_input) == expected_prompt

    @pytest.mark.parametrize(
        "raw_output, expected_text, expected_lang",
        [
            ("language zh<asr_text>你好世界", "你好世界", "zh"),
            ("language en<asr_text>Hello world", "Hello world", "en"),
            ("language Japanese<asr_text>こんにちは", "こんにちは", "ja"),
            ("language Korean<asr_text>안녕하세요", "안녕하세요", "ko"),
            ("<|zh|>你好", "你好", "zh"),
            ("<|en|>Hello", "Hello", "en"),
            ("<zh>你好", "你好", "zh"),
            ("language: zh\n你好", "你好", "zh"),
            ("language: English: Good morning", "Good morning", "en"),
            ("单纯的文本没有任何标记", "单纯的文本没有任何标记", None),
            ("", "", None),
        ],
    )
    def test_parse_qwen3_asr_formats(self, raw_output, expected_text, expected_lang):
        """parse_qwen3_asr must strip tags and accurately detect language."""
        text, detected, _ = parse_qwen3_asr(raw_output)
        assert text == expected_text
        assert detected == expected_lang


class TestCircuitBreakerCriterion:
    """Circuit Breaker Criterion: Translation backend failures must degrade gracefully and resume seamlessly."""

    @pytest.mark.asyncio
    async def test_backend_connection_refused_emits_warning_without_crashing(self):
        """When llama-server is unreachable, pipeline sends warn status and worker stays alive."""
        sent_messages = []

        async def capture_send(msg):
            sent_messages.append(msg)

        class DummyAsr:
            is_transcribing = False

        session = Session(DummyAsr())
        # Point to unreachable port
        session.client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(status_code=503)
        ))

        # Run translation directly
        await session._translate(
            text="Hello world",
            final=True,
            language="en",
            generation=session.media_generation,
            job=session.job_id,
            send=capture_send,
        )

        # Must report warning status without propagating unhandled exceptions
        status_warnings = [m for m in sent_messages if m.get("type") == "status" and m.get("level") == "warn"]
        assert len(status_warnings) >= 1
        assert "未启动" in status_warnings[0]["message"] or "失败" in status_warnings[0]["message"]

    @pytest.mark.asyncio
    async def test_backend_recovers_after_temporary_failure(self):
        """Pipeline can resume normal translations after backend recovers."""
        sent_messages = []

        async def capture_send(msg):
            sent_messages.append(msg)

        class DummyAsr:
            is_transcribing = False

        session = Session(DummyAsr())

        # 1. First request fails with 500
        fail_client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(status_code=500)
        ))
        session.client = fail_client
        await session._translate("First text", True, "en", session.media_generation, session.job_id, capture_send)

        # 2. Server recovers: mock valid SSE streaming response
        def sse_handler(request):
            body = (
                b"data: {\"choices\": [{\"delta\": {\"content\": \"\xe4\xbd\xa0\xe5\xa5\xbd\"}}]}\n\n"
                b"data: [DONE]\n\n"
            )
            return httpx.Response(200, content=body)

        success_client = httpx.AsyncClient(transport=httpx.MockTransport(sse_handler))
        session.client = success_client
        session.warned_translate = False  # Reset warning flag for new requests

        session.job_id += 1
        await session._translate("Hello again", True, "en", session.media_generation, session.job_id, capture_send)

        translations = [m for m in sent_messages if m.get("type") == "translation"]
        assert len(translations) >= 1
        assert "你好" in translations[0]["text"]
