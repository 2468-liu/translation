"""Local realtime subtitle service configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    """A threshold that a sweep can set without editing this file.

    Sweeps used to rewrite the constants here, which meant three things went
    wrong at once: the working tree went dirty so every run's stamp read
    "+dirty" and no two runs could be compared, eight vad tests that read these
    constants failed while a sweep was in flight, and a run could inherit the
    previous arm's value without anything recording it.
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"

# WebSocket (extension <-> Python)
WS_HOST = "127.0.0.1"
WS_PORT = 8765

# llama.cpp OpenAI-compatible server
LLAMA_HOST = "127.0.0.1"
LLAMA_PORT = 8080
LLAMA_URL = f"http://{LLAMA_HOST}:{LLAMA_PORT}/v1/chat/completions"
LLAMA_MODEL = "hy-mt2-1.8b"

# Audio-native model (gemma-audio branch): one model both hears and translates, so
# whisper and hy-mt2 are no longer loaded.
#
# The projector stays on the CPU because that is where it is faster, not because
# it is the only place it fits -- it would fit on the GPU with room over, and
# measured there it is 3.7x slower (first packet 3442ms against 920ms). The
# encoder's prefill competes with the main model for one card; on the CPU the two
# overlap. See scripts/start.ps1 for the full numbers and scripts/sweep_mmproj.ps1
# to reproduce them.
AUDIO_MODEL_URL = f"http://{LLAMA_HOST}:{LLAMA_PORT}/v1/chat/completions"
AUDIO_MODEL_NAME = "local"
# The model will answer in English or start reasoning unless told not to. A
# Chinese instruction was measured producing an empty answer after 585 tokens of
# thinking; this wording returned 17 tokens and the translation. Pinned, with a
# test, because getting it wrong is silent.
AUDIO_SYSTEM_PROMPT = (
    "Translate the following English speech into Simplified Chinese. "
    "Output only the Chinese translation."
)
# Gemma is a reasoning model. Left on, it spends the whole token budget thinking
# and returns nothing.
AUDIO_CHAT_TEMPLATE_KWARGS = {"enable_thinking": False}
AUDIO_MAX_TOKENS = 400
# A fixed token budget is not a length limit, it is a wire: a decoder that has
# stopped making progress keeps going until the budget runs out, which is how a
# 3-second fragment came to emit a 40-character clause six times over. Scaling
# the budget to the audio bounds the damage instead of the symptom.
#
# 6 characters per second of speech is roughly what a faithful Chinese rendering
# of English narration runs to; the margin covers a slow passage without letting
# a loop fill the fragment.
AUDIO_CHARS_PER_SECOND = 6.0
AUDIO_TOKEN_MARGIN = 48
# A little above 1. Decoding penalty punishes a token the model has just used,
# which is what a loop is made of. Far enough above 1 to discourage repetition
# and low enough not to distort wording or break proper nouns, which is what a
# higher value does.
AUDIO_REPEAT_PENALTY = 1.05
# Below this share of Chinese letters, the model transcribed the English instead
# of translating it.
#
# Measured on the two real failures and one real translation that quotes an
# English title: 0% Chinese for a pure transcription, 51% for a line that
# translates correctly around a quoted phrase, 100% for clean lines. Judging by
# the share of foreign letters instead inverts this -- the quoted title is 49%
# Latin and would have been thrown away as untranslated.
AUDIO_CHINESE_SHARE_MIN = 0.30

# Paths — filled after ModelScope download / conversion
#
# large-v3-turbo, not small. Chosen for the noisy-material case (film dialogue
# under music) where small misses speech. Measured on this machine, same
# backend, 205.8s of film audio:
#   small   +649 MiB loaded,  +731 MiB after a transcribe
#   turbo  +2107 MiB loaded, +2243 MiB after a transcribe
# With hy-mt2 resident (1290 MiB) the turbo peak lands at 3533 of 4096 MiB, so
# the fit depends on llama-server holding its -ngl 60 / -c 1024 allocation. If
# the ASR ever OOMs, drop llama's -ngl before dropping back to small.
ASR_MODEL_DIR = MODELS_DIR / "faster-whisper-large-v3-turbo"
ASR_FALLBACK_DIR = MODELS_DIR / "faster-whisper-small"
SILERO_ONNX = MODELS_DIR / "silero_vad.onnx"

# Audio
SAMPLE_RATE = 16000

# All local inference prefers GPU to reduce round-trip latency. CPU is a fallback
# only when CUDA is unavailable, not the normal path.
ASR_DEVICE = "cuda"
ASR_COMPUTE_TYPE = "float16"
ASR_CPU_THREADS = 4
ASR_BEAM_SIZE = 1
# Which recogniser runs. "whisper" is faster-whisper/CTranslate2 in this process;
# "qwen3" is a Qwen3-ASR gguf served by a second llama-server.
#
# The switch exists so a bad run costs one env var rather than a revert. The
# measurement behind it: turbo spends asr p50 821ms and 96% of wall clock re-decoding
# the same utterance prefix every 250ms (REPORT 38), and Qwen3-ASR does the same
# shape in 130-210ms. Shape unchanged, engine changed -- the KV cache does not help
# us, because llama.cpp retains one prompt and a request must be a prefix of it, and
# our partials grow past the retained one every time. The gain is speed only.
#
# The cost measured alongside it: hy-mt2's ttft goes from about 40ms to 150-245ms
# with the second model resident on the same card.
ASR_BACKEND = os.environ.get("TRANSLATION_ASR_BACKEND", "whisper").strip().lower()
QWEN3_ASR_MODEL = MODELS_DIR / "Qwen-ASR-0.6B" / "Qwen3-ASR-0.6B-Q8_0.gguf"
QWEN3_ASR_MMPROJ = MODELS_DIR / "Qwen-ASR-0.6B" / "mmproj-Qwen3-ASR-0.6B-Q8_0.gguf"
QWEN3_ASR_URL = os.environ.get("TRANSLATION_QWEN3_ASR_URL", "http://127.0.0.1:8099")

# Generation cap for Qwen3-ASR, in tokens.
#
# This is a bound on a failure, not a tuning knob. On samples/lecture_mix.wav 5 of 149
# calls never emitted an end token and generated until llama.cpp ran out of context:
# eval 3912 to 3951 tokens against a normal 7 to 46, 30 to 33 seconds per call, and
# 184 prompt tokens with 184 + 3912 = 4096 exactly, which is that server's -c. The
# text reached the screen and 74 seconds passed with nothing after "你真的，你真的".
#
# Measured normal output on that material peaks at 65 tokens for a 14s clip, and 33 to
# 39 on the ones the pipeline actually sends, so 128 leaves roughly three times the
# headroom the longest legitimate transcription needed. It is a round number above
# that margin rather than a fitted value, because the trigger is not understood and a
# tight cap would silently drop real speech if the trigger turns out to be common.
#
# Responses that hit the cap are dropped whole, not trimmed: see Qwen3AsrEngine.
QWEN3_ASR_MAX_TOKENS = _env_int("TRANSLATION_QWEN3_ASR_MAX_TOKENS", 128)
# How often recognition re-reads the utterance while it is still growing, which is how
# often the caption can be revised. 250 was the default for the whole project and it is
# what every earlier number was measured at.
#
# 750 now ships, because the two effects pull in opposite directions and on this
# material the revision count was the one that hurt. Measured on samples/blog_3min.wav
# at 250: 215 recognitions over 49 utterances, 4.39 per clause, and 112 caption
# rewrites. At 750 on samples/lecture_mix.wav: 136 recognitions, and the transcript
# lengths confirm the stream is no longer fragmented -- median 19 characters, 71% over
# 10, with only 4.4% under 5. The cost is the first line arriving later, which is why
# this is still adjustable in the extension rather than removed: 250 measured 382ms
# first-translation-to-paint against 750's 434ms on the same material, a 52ms
# difference for 3x fewer revisions.
#
# The engine matters here more than the number. turbo at 823ms per call cannot keep up
# with either value -- it overran a 250ms cadence on every one of 215 calls -- so 750 is
# what makes the current default usable rather than what makes it good. At 750 turbo
# still used 57.1% of wall clock on lecture_mix.
PARTIAL_INTERVAL_MS = _env_int("TRANSLATION_PARTIAL_INTERVAL_MS", 750)

# --------------------------------------------------------------- llama.cpp 版本
#
# One variable, two states: unset means "the newest build installed on this machine",
# and set means exactly that version. The decision to keep it a value rather than a
# command-line flag is that it had already become three flags in two files that
# disagreed -- start.ps1 defaulted to newest behind -UsePinned, resolve_llama.ps1
# defaulted to a hardcoded 2.47.0 behind -AutoLatest, and replay.ps1 called the second
# one. So measurements landed on 2.47.0 not because anything pinned them but because
# the measuring path and the running path resolved the same question differently, and
# the next edit to either would have made every earlier number incomparable.
#
# Resolved by scripts/resolve_llama.ps1, which is the only reader: an explicit -Version,
# then this variable, then runtime.json's llama_version, then newest installed. A named
# version that is not installed is an error, never a fallback -- see MEASUREMENT_LLAMA_VERSION.
LLAMA_VERSION = os.environ.get("TRANSLATION_LLAMA_VERSION", "").strip()

# The build every number in REPORT.md was measured on. replay.ps1 pins this rather than
# reading LLAMA_VERSION, because a measurement has to be comparable to the last
# measurement, not to whatever the machine currently has installed. Pinning from the
# machine's own setting would reintroduce exactly the drift the pin exists to stop, one
# level up: a new build gets installed, the machine's setting follows it, and the
# difference between two arms becomes the difference between two llama versions with
# nothing in the logs to say so.
#
# Shipping still floats (LLAMA_VERSION unset), so this constant is used by the
# measuring path only. See AGENT.md section 5, rule nine.
MEASUREMENT_LLAMA_VERSION = "2.47.0"

# The chains, and the default, live here rather than in runtime.json.
#
# runtime.json is machine-local and gitignored -- it holds absolute paths to this
# machine's llama builds and that is exactly why it is not versioned. Putting the chain
# table there made a fresh clone have no default at all, and eleven tests that read it
# failed on a clean checkout: the defaults for a shipped configuration cannot live in a
# file that is not shipped.
#
# A chain is the whole pipeline, not a recogniser. gemma-e2e is the audio-native path
# where one model both hears and translates, which removes the translator process
# entirely -- calling that "an ASR engine" would misdescribe it and would make a fourth
# option look like a fourth engine.
#
# `status` is what stops an unimplemented chain from being offered as if it worked.
DEFAULT_CHAIN = "whisper-hy"
CHAINS = {
    "whisper-hy": {
        "label": "whisper 识别 + 混元翻译",
        "status": "whisper measured; translator weights swapped to a fine-tune, unmeasured",
        "asr_backend": "whisper",
        "qwen3_port": 0,
        "measured": "e2e first-translation-to-paint p50 55ms on samples/lecture_mix.wav at "
                    "partial 750; asr p50 829ms, 100% of calls over a 250ms budget, 57.1% "
                    "of wall clock -- all of that on the ORIGINAL Hy-MT2 weights",
        "caveat": "whisper overruns both 250ms and 750ms, so 750 is what makes it usable "
                  "rather than what makes it good",
        "translator_weights": "Hy-MT2-1.8B-StreamRevise-v4-Q4_K_M.gguf (fine-tuned, "
                              "subtitle-domain; zero measurements of its own)",
    },
    "qwen3-hy": {
        "label": "Qwen3-ASR 识别 + 混元翻译",
        # "not adopted" was a decision made on an e2e_first_tr_to_paint reading that
        # turned out to be scored against the wrong sample's timeline -- the metric was
        # 434ms against A's 55ms, which is how "B loses 8x" got written down and repeated
        # a dozen times. Recomputed on lecture_mix's own timeline the four arms read
        # p50 47/47/55/52ms, so B was never slower. The status is INDETERMINATE now rather
        # than "not adopted", and it is not "adopted" either: one run per arm, n=17-25,
        # synthetic material, coverage still behind and the tail heavier. REPORT 47/48.
        "status": "implemented, measured, indeterminate -- not adopted and not rejected",
        "asr_backend": "qwen3",
        # The port Qwen3AsrEngine posts to. It is named here rather than in the engine
        # because three places have to agree -- the engine's default URL, the host's kill
        # scope and the server start -- and two agreeing is not enough.
        "qwen3_port": 8099,
        "measured": "on lecture_mix at partial 750, llama 2.53.0, same code for both arms. "
                    "Engine: asr p50 180ms against 822ms (4.6x), 15.8% of wall clock against "
                    "56.8%. audio_end_to_paint p50 548ms against 690ms, over 1000ms 7/24 "
                    "against 8/25. e2e_first_tr_to_paint p50 47ms against 52ms. Against it: "
                    "coverage 21/23 against 23/24, and e2e p99 2433ms against 2031ms -- the "
                    "tail, which the old reading hid by scoring against another clip's "
                    "timeline (it reported p99=max=996ms). dwell_gap and hold_wait identical "
                    "(1580 vs 1620, 986 vs 986)",
        "caveat": "128 tokens, whole response dropped on finish_reason=length, and on lecture_mix "
                  "that fires 5 times against whisper's 0 -- which is why B produces 23 finals "
                  "where A produces 25 from identical VAD segmentation, and why coverage "
                  "reads 91% against 96%. Raising the cap to 256 changed none of that: still "
                  "4 truncations, still 21/23, but the discarded text grew from 193 chars to "
                  "363. So the cap is not tight -- the recogniser degenerates on 2 of 25 "
                  "utterances and the cap only decides how long the garbage accumulates "
                  "before being discarded. What the head/tail of those dropped responses "
                  "shows, and what is NOT a long utterance: one cluster loops from the first "
                  "character while detecting Chinese on a ja/en clip, another starts as "
                  "coherent Japanese and then collapses into one repeated sentence. The next "
                  "question is why the language detection fails on those two, not the cap. "
                  "Separately: 5 of 149 calls repeated until the context filled, 30-33s "
                  "each, trigger still unknown. Single run per arm, synthetic material, and "
                  "audio_end_to_paint / e2e_first have run-to-run spreads wider than the "
                  "A/B differences, so those two are not quotable from one run each -- "
                  "REPORT 53. Unmeasured display-layer hypothesis, unrelated to the cap: "
                  "whisper's ASR is slow enough that translation idles and each emission is "
                  "short, so streaming output buys latency; Qwen3 leaves translation busy "
                  "and emits densely, so the same policy reads as flicker and should be "
                  "block-wise. The log records no per-paint delta, so that is not "
                  "measurable yet -- REPORT 50",
    },
    "gemma-e2e": {
        "label": "音频直出（一个模型同时听和译）",
        "status": "implemented, measured, rejected on music-backed material",
        "asr_backend": None,
        "qwen3_port": 0,
        "measured": "works on clean narration: blog_3min, 12.48s clip, four sentences "
                    "all correct. Degrades on music: er_3min mostly right, e_3min broken "
                    "-- \"I reckon we should call it a day.\" heard by whisper and "
                    "misheard by the Gemma4 audio encoder as 大鸟",
        "caveat": "rejected on this material, not on the card. REPORT 25.3 ruled out MTP, "
                  "layer split, projector placement, three prompts, five sample rates, "
                  "five clip durations and five gains by single-variable comparison; the "
                  "boundary is the model's audio understanding on speech under music "
                  "(crest 18.5dB blog / 13.4dB er / 12.9dB e_3min), not configuration",
    },
}
# Whether start.ps1 can start it, which is not the same question as whether it is any
# good. gemma-e2e can be started and was measured; it lost on material with music under
# the dialogue. It is excluded here only because start.ps1 has no branch for it, and the
# reason it is excluded is a missing branch rather than a missing implementation --
# the pipeline path exists (server/pipeline.py _run_audio_native) and start.ps1 has a
# 132-line gemma4 branch that this switch does not reach.
STARTABLE_CHAINS = tuple(n for n, s in CHAINS.items() if s["status"] != "not implemented")
# Tested at least once, which is every entry here. Kept separate from STARTABLE_CHAINS
# because a chain that was measured and rejected must not read as one that was never
# tried: that is the claim I made about gemma-e2e in the first version of this table, and
# the user had it in front of them.
MEASURED_CHAINS = tuple(CHAINS)
QWEN3_ASR_PORT = CHAINS["qwen3-hy"]["qwen3_port"]

# VAD
VAD_THRESHOLD = 0.5
# Leading pre-roll cap. 0 disables it, which is the production value until #20 is
# judged.
#
# The utterance used to begin exactly at the frame the detector first called speech,
# so every millisecond of detector latency was a millisecond of lost words -- on a
# 1s clause under a music bed that measured +0.19s at the median and +0.72s at worst
# (REPORT 38). A pre-roll extends the start backwards, clamped to the silence that
# actually preceded this utterance, so dense material gains nothing and sparse
# material recovers its opening words. The frames it contributes reach neither
# speech_ms nor silence_ms; see server/vad.py _take_preroll for why both exclusions
# are load-bearing.
VAD_PREROLL_MAX_MS = _env_int("TRANSLATION_VAD_PREROLL_MAX_MS", 0)
# The gate below is a DROP, not a delay: an utterance shorter than this is
# discarded at server/vad.py, never translated. It is injectable now because #6
# is a single-variable comparison of 250 against 1000, and editing this file
# between arms would make the two runs incomparable (the dirty-tree problem the
# sweeps already hit).
VAD_MIN_SPEECH_MS = _env_int("TRANSLATION_VAD_MIN_SPEECH_MS", 250)
# How much silence may pass before a held short utterance stops waiting for a
# neighbour and is closed on its own.
#
# The distinction matters on a movie with a loud score: music reads as speech,
# so a "short utterance" can be a bar of score rather than a word. Merging
# without a bound would hold the buffer open through the next movement and
# produce one enormous fragment, which is a worse failure than the one being
# fixed.
#
# DEFAULT 0, i.e. OFF, and that is deliberate: measuring this feature must not
# change what ships. With the window at zero the closing branch keeps the
# original unconditional drop, so a fresh process behaves exactly as it did
# before #6 -- verified by the probe, which reports the same 27 kept / 8 dropped
# on samples/e_3min.wav either way. Arm B sets TRANSLATION_VAD_MERGE_WINDOW_MS.
VAD_MERGE_WINDOW_MS = _env_int("TRANSLATION_VAD_MERGE_WINDOW_MS", 0)
VAD_MIN_SILENCE_MS = 400
VAD_SPEECH_PAD_MS = 120

# VAD, retuned for the audio-native model.
#
# Two reasons, both measured on samples/blog_3min.mp4 against
# translate_gemma4_sub-E2B-Q4_K_XL:
#
#   * Latency is paid on the whole fragment. First token arrives at roughly
#     300ms + 250ms per second of audio, so a 12s fragment costs ~1.25s before
#     any text appears. A long hold buys nothing.
#   * The model translates what it hears, so cutting mid-sentence costs quality
#     rather than just costing a re-run.
#
# So the closing silence is longer (700ms, was 400ms) to cut at sentence
# boundaries, but the ceiling is much lower (26s) than whisper's 30s window:
# there is no context stitching to fall back on.
GEMMA_MIN_SILENCE_MS = _env_int("TRANSLATION_GEMMA_MIN_SILENCE_MS", 1000)
GEMMA_MAX_UTTERANCE_MS = 26_000
GEMMA_VAD_SPEECH_PAD_MS = 200
# Past this much speech, close even if the speaker has not paused. Keeps a
# rambling speaker from building a fragment whose own latency is unacceptable.
GEMMA_SOFT_CUT_SPEECH_MS = 12_000
GEMMA_SOFT_CUT_SILENCE_MS = 700

# Translation
TRANSLATE_TEMPERATURE = 0.2
TRANSLATE_MAX_TOKENS = 256
CONTEXT_SENTENCES = 2

# ---------------------------------------------------------------- audio capture
# Where the audio comes from.
#
# "loopback" captures what the machine is playing, through WASAPI, in this
# process. "socket" takes PCM from the extension's tab capture.
#
# Exactly one of them feeds the splitter, and that exclusivity is the fix rather
# than a preference: both used to run at once, each pushing 100ms frames into the
# same Session, so the VAD saw the clip and the machine's output interleaved. On
# samples/e_3min.wav that turned 32 clean utterances into 48 fragments with a
# 96ms median whose every closing read why=silence, and the recogniser returned
# no text for most of them (REPORT 23).
#
# Loopback is the default because it has no dependency on Chrome, survives
# fullscreen and DRM, and cannot disagree with itself about the sample rate.
# TRANSLATION_AUDIO_SOURCE=socket restores the old behaviour, and that is what
# the replay uses: injecting PCM directly keeps a measurement locked to one
# variable instead of adding a speaker round trip.
AUDIO_SOURCE = os.environ.get("TRANSLATION_AUDIO_SOURCE", "loopback").strip().lower()
LOOPBACK_ENABLED = AUDIO_SOURCE != "socket"

# Measured on this machine, a 440Hz tone played at amplitude 0.5 comes back from
# the default speaker's loopback at a peak of about 0.024 -- 26 dB down -- and the
# ratio barely moved when the system volume was changed, so it is a property of
# the device path rather than of the slider.
#
# It is still not written down as a constant. Another machine, another driver or
# an enhancement layer like Nahimic can attenuate by something else, and the VAD
# threshold is absolute: too quiet an input reads as non-speech. So the loopback
# measures its own signal and derives the gain at runtime, and these are the
# bounds on that -- target to aim at, and the hard limits on what it may apply.
LOOPBACK_TARGET_PEAK = float(os.environ.get("TRANSLATION_LOOPBACK_TARGET_PEAK", "0.20"))
LOOPBACK_GAIN_MIN = float(os.environ.get("TRANSLATION_LOOPBACK_GAIN_MIN", "0.5"))
LOOPBACK_GAIN_MAX = float(os.environ.get("TRANSLATION_LOOPBACK_GAIN_MAX", "40.0"))

# Which text-to-text translator serves the whisper path.
#
# "hy-mt2" is the pinned default and the backend every latency number in
# REPORT.md was measured on. "gemma4" swaps in the same Gemma 4 E2B weights the
# audio-native branch uses, but as a text-only translator -- no --mmproj, so it
# never sees audio and whisper still does the recognising. That distinction is
# the whole point of the switch: the audio-native branch and this one share
# weights and nothing else.
MT_BACKEND = os.environ.get("TRANSLATION_MT_BACKEND", "hy-mt2").strip().lower()

# The Gemma translator runs as its own llama-server on its own port, because the
# audio-native branch answers the same /v1/chat/completions shape and the two
# cannot both own 8080.
#
# Only the port lives here. The model file, the GPU layer count, the context
# length and whether the MTP draft is enabled are read by scripts/start.ps1
# straight from TRANSLATION_GEMMA_* when it builds the llama-server command line,
# and it carries its own defaults there. They used to be duplicated in this file
# as GEMMA_TRANSLATOR_* constants, which meant three places to change a number
# and no way to tell from a log which one the running server had used -- the same
# failure as the stale runtime.json pin, in a different file.
GEMMA_TRANSLATOR_PORT = _env_int("TRANSLATION_GEMMA_PORT", 8082)
GEMMA_TRANSLATOR_URL = f"http://{LLAMA_HOST}:{GEMMA_TRANSLATOR_PORT}/v1/chat/completions"

# How much of the preceding conversation the translator is shown.
#
# Not copied from CONTEXT_SENTENCES, and that is deliberate: the question the
# user asked is what injection costs and buys, and it cannot be answered with a
# value that also changes when the other path's context changes. 0 is the
# control arm.
#
# The whisper path passed no context at all until this was wired up --
# translate.build_messages used to drop its context argument -- so "2" is a new
# behaviour rather than the old default.
MT_CONTEXT_SENTENCES = _env_int("TRANSLATION_MT_CONTEXT_SENTENCES", 2)

# The audio-native path sends no context, and this is why.
#
# Measured both ways on the two fragments that fail in every replay and translate
# correctly when sent alone: with the preceding lines in the prompt, the same
# audio comes back as a transcription of the English, or as a clause repeated
# until the fragment ends. Without it, four of four runs each produced a correct
# translation of both, and three full replays produced no bad fragment at all.
#
# It was already known to be expensive here -- 854ms of first token against 86ms
# without, plus a hallucinated word -- but the branch kept passing context while
# the rest of the report treated it as switched off.
#
# Injectable because the interaction with MTP is worth measuring rather than
# assuming: speculative decoding and a longer prompt change the same prefill
# cost, and a 2x2 on one backend settles it. Both axes are swept together for
# that reason and never separately.
AUDIO_CONTEXT_SENTENCES = _env_int("TRANSLATION_AUDIO_CONTEXT_SENTENCES", 0)
PARTIAL_STABLE_REPEAT = 1  # translate the first partial; later growth follows after it finishes

# llama.cpp GPU layers — probe script writes actual value into runtime.json
LLAMA_NGL_DEFAULT = 99

RUNTIME_JSON = ROOT / "runtime.json"

# Which llama.cpp build to run is not decided here. It used to be, as a hardcoded
# two-entry list naming 2.47.0 and 2.33.0, which went stale the moment a third
# build was installed and could not see a machine other than this one.
# scripts/resolve_llama.ps1 owns the decision now: it globs the LM Studio backends
# directory for this platform, honours -Pin and TRANSLATION_LLAMA_VERSION, and
# prints what it picked and what else is installed. A list of paths written down
# in Python is the thing that was wrong, not the lookup.


def resolve_llama_runtime(vram_mb: int | None = None) -> tuple[int, int]:
    """Use aggressive GPU offload on normal machines and partial offload only on 4GB GPUs.

    This project's live footprint is small enough for a 4GB card to run the Q4
    model without blowing up, but it still benefits from cutting a bit of the
    layer count when memory is tight. Machines with more than 4GB keep almost all
    layers inside VRAM to minimize latency.

    ``vram_mb`` is the card's *total* memory, not what is currently used.
    Comparing "used after load" against a fixed 4096 conflated the two: a used
    figure is a property of whichever model happened to be loaded, so it was
    both the wrong quantity and, in runtime.json, a stale one carried over from
    a deleted model. On an 8GB card whose probe recorded 3000 MiB used, this
    would read 3000 <= 4096 and quietly demote a card that did not need it.
    """
    if vram_mb is None:
        if RUNTIME_JSON.exists():
            try:
                runtime = json.loads(RUNTIME_JSON.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                runtime = {}
            # Total first; the used figure is only a fallback for old files.
            vram_mb = runtime.get("memory_total_mib")
            if vram_mb is None:
                vram_mb = runtime.get("vram_used_mib_after_load") or runtime.get("fit_target_mib")
        if vram_mb is None:
            vram_mb = 8192
    vram_mb = max(1, int(vram_mb))
    if vram_mb <= 4096:
        return 60, 1024
    return 99, 1536


