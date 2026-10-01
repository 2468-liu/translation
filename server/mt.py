"""NLLB-200 distilled 600M on CTranslate2. One model, many languages into Simplified Chinese."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import ctranslate2

from .config import MT_DIR, NLLB_DIR

TARGET = "zho_Hans"
LANG = {
    "en": "eng_Latn",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "es": "spa_Latn",
    "ru": "rus_Cyrl",
    "pt": "por_Latn",
    "it": "ita_Latn",
    "vi": "vie_Latn",
    "th": "tha_Thai",
    "id": "ind_Latn",
    "ar": "arb_Arab",
    "zh": "zho_Hans",
}
_SKIP = {"</s>", "<s>", "<unk>", "<pad>", TARGET}

_ENGINE: MtEngine | None = None


class MtEngine:
    def __init__(self) -> None:
        self.ready = False
        self.device = ""
        self.translator: ctranslate2.Translator | None = None
        self.sp = None

    def load(self) -> None:
        model_bin = MT_DIR / "model.bin"
        if not model_bin.exists():
            print(f"MT model missing: {model_bin}")
            return
        sp_path = MT_DIR / "sentencepiece.bpe.model"
        if not sp_path.exists():
            sp_path = NLLB_DIR / "sentencepiece.bpe.model"
        if not sp_path.exists():
            print(f"sentencepiece model missing: {sp_path}")
            return
        import sentencepiece as spm

        self.sp = spm.SentencePieceProcessor(model_file=str(sp_path))
        try:
            self.translator = ctranslate2.Translator(
                str(MT_DIR), device="cuda", compute_type="int8"
            )
            self.device = "cuda"
        except Exception as exc:
            print(f"MT cuda failed ({exc}); using cpu int8")
            self.translator = ctranslate2.Translator(
                str(MT_DIR),
                device="cpu",
                compute_type="int8",
                inter_threads=1,
                intra_threads=4,
            )
            self.device = "cpu"
        self.ready = True
        print(f"MT ready {MT_DIR.name} device={self.device} compute=int8")

    def stream(self, text: str, language: str | None) -> Iterator[str]:
        source_lang = LANG.get(language or "", "")
        cleaned = text.strip()
        if not cleaned or self.translator is None or self.sp is None:
            return
        if source_lang == TARGET or (language or "").startswith("zh"):
            yield cleaned
            return
        if not source_lang:
            source_lang = "eng_Latn"
        pieces = self.sp.encode(cleaned, out_type=str)
        source = [source_lang, *pieces, "</s>"]
        tokens: list[str] = []
        previous = ""
        for step in self.translator.generate_tokens(
            source,
            target_prefix=[TARGET],
            max_decoding_length=256,
            sampling_topk=1,
        ):
            token = step.token
            if token in _SKIP:
                if step.is_last:
                    break
                continue
            tokens.append(token)
            decoded = self.sp.decode(tokens).strip()
            if decoded and decoded != previous:
                yield decoded
                previous = decoded
            if step.is_last:
                break


def get_engine() -> MtEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = MtEngine()
        _ENGINE.load()
    return _ENGINE


async def stream_mt(text: str, language: str | None):
    engine = get_engine()
    if not engine.ready:
        return
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def run() -> None:
        try:
            for piece in engine.stream(text, language):
                loop.call_soon_threadsafe(queue.put_nowait, piece)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, f"\x00{exc}")
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    worker = asyncio.create_task(asyncio.to_thread(run))
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            if item.startswith("\x00"):
                raise RuntimeError(item[1:])
            yield "\r" + item
    finally:
        await worker
