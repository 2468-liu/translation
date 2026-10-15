"""How much does injected context change the translation, and what does it cost?

The question is the user's: "上下文注入要测试影响有多大". It cannot be answered
from the live pipeline, because there the context count is one of a dozen things
that differ between two runs. So this probe holds everything else still:

  one server        the same llama-server, unchanged, for every arm
  one sentence set  every arm sees the same lines in the same order
  temperature 0     the difference between arms is the prompt, not the sampling
  one variable      the number of preceding (source, translation) pairs in the
                    prompt -- 0 is the control arm, and with 0 the request is
                    byte-identical to what the whisper path sent before this
                    change (translate.build_messages deleted its context)

Three effects are reported separately rather than averaged into one verdict:

  quality    the translations themselves, side by side, per sentence. This is
             the primary result and it is read by a human.
  latency    prompt_ms (prefill) and ms/token (decode), per arm.
  cache      a repeated prompt is answered from llama-server's prompt cache --
             measured elsewhere on this project at 58ms against 314ms cold -- so
             each arm's FIRST sentence is reported on its own and the median
             excludes it. Without that split the cache looks like a context cost.

Deliberately not done here: running both arms against servers in parallel. Two
llama-servers of this size do not both fit on a 4GB card, and the second one's
spill to shared memory would be attributed to the context.

Usage:
    conda activate pytorch
    python scripts/probe_mt_context.py --samples 0 2 6
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import config  # noqa: E402
from server.translate import build_messages, collapse_translation_repeats, _drop_think  # noqa: E402

GEMMA_URL = config.GEMMA_TRANSLATOR_URL
HYMT_URL = config.LLAMA_URL


def backend_url(backend: str) -> str:
    return GEMMA_URL if backend == "gemma4" else HYMT_URL


def load_sentences(timing: Path, samples) -> list[dict]:
    """Source lines out of a timeline.

    The field names are a trap worth writing down: ``text`` is the recognised
    sentence and ``src`` is the *language* the recogniser decided on, not the
    source text. Two of the four timelines also have no ``lang`` key at all
    (they were rebuilt with the language living in ``src``). Reading ``src`` as
    the sentence yields an empty set, which is how this probe first reported
    "no source lines" on a file with 16 of them.
    """
    data = json.loads(timing.read_text(encoding="utf-8"))
    rows = data["segments"] if isinstance(data, dict) else data
    out = []
    for row in rows:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        lang = row.get("lang") or row.get("src") or "en"
        out.append({"i": row.get("i", len(out)), "lang": lang, "src": text,
                    "start": row.get("start"), "end": row.get("end")})
    return out[:samples] if samples else out


def one(client: httpx.Client, url: str, messages: list[dict], backend: str) -> dict:
    payload = {
        "model": "local",
        "messages": messages,
        "temperature": 0,  # determinism between arms, which is the point
        "max_tokens": config.TRANSLATE_MAX_TOKENS,
        "stream": False,
        "cache_prompt": True,
    }
    started = time.perf_counter()
    response = client.post(url, json=payload, timeout=180.0)
    wall_ms = (time.perf_counter() - started) * 1000.0
    response.raise_for_status()
    data = response.json()
    choice = (data.get("choices") or [{}])[0]
    timings = data.get("timings") or {}
    raw = (choice.get("message") or {}).get("content") or ""
    visible, _ = _drop_think(raw, False)
    return {
        "text": collapse_translation_repeats(visible).strip(),
        "raw": raw,
        "wall_ms": wall_ms,
        "prompt_ms": timings.get("prompt_ms"),
        "prompt_n": timings.get("prompt_n"),
        "cache_n": timings.get("cache_n"),
        "predicted_n": timings.get("predicted_n"),
        "predicted_ms": timings.get("predicted_ms"),
        "ms_per_token": (timings["predicted_ms"] / timings["predicted_n"])
        if timings.get("predicted_n")
        else None,
        "draft_n": timings.get("draft_n"),
        "draft_n_accepted": timings.get("draft_n_accepted"),
        "finish": choice.get("finish_reason"),
    }


def run_arm(client: httpx.Client, url: str, rows: list[dict], backend: str, sentences: int) -> list[dict]:
    """One context arm over the whole sentence set.

    Context is grown from this arm's own output, which is what the live pipeline
    does -- it remembers the final translation it sent to the caption, not an
    idealised reference. Feeding a reference instead would measure a system
    nobody runs.
    """
    history: list[tuple[str, str]] = []
    out: list[dict] = []
    for row in rows:
        src = (row.get("src") or "").strip()
        messages = build_messages(src, row.get("lang"), history, backend, sentences)
        got = one(client, url, messages, backend)
        got["id"] = row.get("i", len(out))
        got["lang"] = row.get("lang")
        got["src"] = src
        got["context_sentences"] = sentences
        got["context_pairs"] = len(history[-sentences:]) if sentences > 0 else 0
        out.append(got)
        if got["text"]:
            history.append((src, got["text"]))
    return out


def summarize(rows: list[dict]) -> dict:
    def med(key: str, skip_first: bool = True) -> float | None:
        vals = [r[key] for r in rows[skip_first:] if isinstance(r.get(key), (int, float))]
        return round(statistics.median(vals), 1) if vals else None

    drafted = sum(r["draft_n"] or 0 for r in rows)
    accepted = sum(r["draft_n_accepted"] or 0 for r in rows)
    return {
        "n": len(rows),
        "first_prompt_ms": rows[0]["prompt_ms"] if rows else None,
        "first_cache_n": rows[0]["cache_n"] if rows else None,
        "median_prompt_ms": med("prompt_ms"),
        "median_ms_per_token": med("ms_per_token"),
        "median_wall_ms": med("wall_ms"),
        "median_predicted_n": med("predicted_n"),
        "draft_tokens": drafted,
        "draft_accepted": accepted,
        "draft_accept_rate": round(accepted / drafted, 3) if drafted else None,
        "cache_hits": sum(1 for r in rows if (r.get("cache_n") or 0) > 0),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timing", default="samples/blog_3min_timing.json",
                    help="timeline whose per-segment src text is the input set")
    ap.add_argument("--backend", default=config.MT_BACKEND, choices=["hy-mt2", "gemma4"])
    ap.add_argument("--samples", default="0,2,6",
                    help="comma-separated context widths; 0 is the control arm")
    ap.add_argument("--limit", type=int, default=0, help="use only the first N sentences")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    arms = [int(x) for x in str(args.samples).split(",") if x.strip() != ""]
    timing = ROOT / args.timing
    if not timing.exists():
        raise SystemExit(f"timeline not found: {timing}")
    rows = load_sentences(timing, args.limit)
    if not rows:
        raise SystemExit(f"no source lines in {timing}")
    url = backend_url(args.backend)

    print(f"backend        {args.backend}  {url}")
    print(f"timeline       {timing.relative_to(ROOT)}  ({len(rows)} sentences)")
    print(f"arms           context = {', '.join(str(a) for a in arms)} sentences")
    print()

    # Reachability first, and loudly. A probe that reports timings from a server
    # that was never there is the failure this project keeps re-learning.
    with httpx.Client() as client:
        try:
            probe = client.get(url.replace("/chat/completions", "/models"), timeout=10.0)
            probe.raise_for_status()
        except Exception as exc:
            raise SystemExit(
                f"no llama-server at {url} ({exc.__class__.__name__}: {exc}).\n"
                f"Start one: see scripts/start.ps1 (gemma4 branch), or run\n"
                f"  powershell -File scripts/start.ps1 -DryRun   to see what it would pick."
            )

        results: dict[str, list[dict]] = {}
        for sentences in arms:
            print(f"-- context = {sentences}")
            arm = run_arm(client, url, rows, args.backend, sentences)
            results[str(sentences)] = arm
            s = summarize(arm)
            print(f"   first prompt   {s['first_prompt_ms']} ms (cache_n={s['first_cache_n']})")
            print(f"   prefill median {s['median_prompt_ms']} ms   decode {s['median_ms_per_token']} ms/token"
                  f"   wall median {s['median_wall_ms']} ms")
            print(f"   draft          {s['draft_accepted']}/{s['draft_tokens']} accepted"
                  f" ({s['draft_accept_rate']})   cache hits {s['cache_hits']}/{s['n']}")
            print()

    # Side by side, per sentence. This is the part a human reads.
    print("=" * 100)
    print("逐句对照（原文 → 各 arm 的译文）")
    print("=" * 100)
    for index, row in enumerate(rows):
        print(f"\n[{index}] ({row.get('lang')}) {row.get('src')}")
        for sentences in arms:
            got = results[str(sentences)][index]
            print(f"    ctx={sentences}: {got['text']}")

    if args.out:
        payload = {
            "backend": args.backend,
            "url": url,
            "timeline": str(timing.relative_to(ROOT)),
            "arms": arms,
            "summary": {k: summarize(v) for k, v in results.items()},
            "rows": {k: [{kk: vv for kk, vv in r.items() if kk != "raw"} for r in v]
                     for k, v in results.items()},
        }
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
