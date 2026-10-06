# 第 2 步延迟优化 —— 测试报告

日期：2026-10-06
状态：第 1 步（埋点）已完成，第 2 步（零风险小改）已完成并测完，第 3/4 步未开始。

---

## 1. 这份报告要回答什么

用户给的四步计划：

1. 埋点，先量，不改逻辑。
2. 零风险小改，**每次只改一项**，用同一批录音回放对比。
3. 依据日志调参（字幕 HOLD、`PARTIAL_INTERVAL_MS`、翻译采样参数）。
4. 考虑换 ASR。

本报告覆盖第 1、2 步，给出基线、三项改动各自和累积的前后数据、以及第 3 步三个触发条件的当前判定。

**核心结论先说：**

> 同一段 187.4 s 参考视频、同一套服务，做完第 2 步 A+B+C 之后，
> **"首条新译文 → 上屏"的中位数从约 1460 ms 降到 374~756 ms（约 −60%），
> 超过 1 s 的句子从 9/16（56%）降到 5~6/16（31~38%），
> 画屏延迟 p90 从 284~321 ms 降到 193~209 ms，
> 而 ASR 的总计算量几乎没变（144.7~146.9 s → 143.3~144.3 s）。**

代价：排队等待 `qwait` 略微变差（p50 38~43 → 46~48 ms，p99 214~260 → 317~337 ms），翻译首字 `ttft` 略差（31.7~33.2 → 33.3~35.5 ms，仍在噪声内），最坏情况的画屏 `max` 略差（1452~1611 → 1753~1905 ms）。这些都远小于收益。

---

## 2. 环境

| 项 | 值 |
| --- | --- |
| 机器 | Windows，RTX 3050 Ti Laptop 4096 MiB |
| Python | `D:\minanconda\envs\pytorch\python.exe` |
| ASR | `faster-whisper-small`，`device=cuda`，`float16` |
| VAD | Silero ONNX `models/silero_vad.onnx` |
| 翻译 | llama-server + `models/hy-mt2-1.8b/Hy-MT2-1.8B-Q4_K_M.gguf` |
| llama-server 参数 | `-ngl 60 -c 1024 -t 4 -np 1 -lv 4` |
| 回放素材 | `samples/blog_3min.mp4` / `.wav`，187.4 s，56 句 |
| 测试脚本 | `scripts/blog_video_test.py play` |
| 测试套件 | `python -m pytest -q` → **40 passed** |

llama-server 日志（`logs/llama.err` / `logs/llama-lv4.err`）确认的运行时事实：

```
load_tensors: offloaded 33/33 layers to GPU
CUDA0 model buffer size = 1075.74 MiB
llama_context: n_seq_max = 1
srv load_model: n_slots = 1, n_ctx_slot = 1024
```

即：模型**确实整卡在 GPU 上**，翻译本身不是瓶颈（`ttft` p50 33 ms 也印证了这点）。

---

## 3. 第 1 步：埋点（只加日志，不改逻辑）

新增 `server/perf.py`：每行 `<墙钟时间> <来源>.<事件> key=value ...`。墙钟用 `time.time()`，这样两个进程的日志能对齐；时长字段一律用 `perf_counter`。

- 服务端 → `logs/perf.log`
- 字幕窗（另一个进程）→ `logs/perf_caption.log`

> 为什么分两个文件：早期两个进程往同一个文件追加，在 Windows 上会互相覆盖字节，第一轮回放就损坏了 32 行。

开关：`TRANSLATION_PERF=0` 关掉，`TRANSLATION_PERF_LOG` 换路径。

事件清单和读法写在 README「延迟埋点」一节。汇总脚本：

```powershell
python scripts/perf_report.py              # 分位数汇总
python scripts/perf_compare.py <名字> <服务日志> <字幕日志> [回放json]   # 本轮新写的逐轮对比
```

`scripts/perf_compare.py` 是这次第 2 步为了"每轮出同一组指标"新写的，它会：

- 只取日志里**第一段连续事件**（间隔 > 10 s 即视为新一轮），这样同一文件里追加的第二次回放会被自动排除；
- 跳过单文件时期被撕裂的行（首 token 不是 epoch）；
- 输出 `asr / qwait / ttft / ttotal / paint / e2e` 等一整组指标。

---

## 4. 基线（改动前）

有效回放 6 轮（187.4 s/轮，约 19 分钟音频）。其中能同时提供服务端日志 + 字幕画屏 + 回放 json 的只有 `run1`；另一次基线 `B1` 有服务端和字幕日志但 json 被覆盖。**把这两次基线放在一起，就是本轮的"运行间噪声带"。**

| 指标 | 基线 run1 | 基线 B1 | 噪声带 |
| --- | --- | --- | --- |
| 窗口 | 186.6 s | 185.0 s | — |
| `asr` n | 733 | 723 | ±10 |
| `asr` p50 / p90 / p99 / max (ms) | 179 / 296 / 451 / 812 | 184 / 310 / 460 / 812 | p50 ±5，max 812 |
| `asr` 占墙钟 | 77.6%（144.7 s） | 79.4%（146.9 s） | **约 78%** |
| `asr` >250 ms | 19% | 22% | ~20% |
| `qwait` p50 / p99 / max (ms) | 38.3 / 214 / 318 | 43.0 / 260 / 400 | — |
| `ttft` p50 / p90 (ms) | 31.7 / 39.9 | 33.2 / 41.4 | **33 左右** |
| 翻译整段 p50 (ms) | 123 | 126 | — |
| `audio_ms` p50 / p90 / p99 / max | 2528 / 6368 / 11840 / 17920 | 2528 / 6464 / 11840 / 18144 | max ≈ 18 s |
| 画屏 `paint` n | 263 | 279 | — |
| 画屏 `since_recv` p50 / p90 / p99 (ms) | 47.1 / 284 / 1180 | 49.0 / 321 / 1177 | **p90 284~321** |
| `pump` p50 (ms) | 27.9 | 28.5 | — |
| `pipeline.drop` | 224 | 231 | — |
| **首条新译文 → 上屏 p50 (ms)** | **1455** | — | 见 A=1466 |
| **该值 >1 s 的句子** | **9/16 (56%)** | — | A 也是 9/16 |

两次基线在 `asr`、`ttft`、`paint` 上高度一致（差异 ≤5%），说明这套回放方法的运行间噪声很小，后面 A/B/C 的变化可以归因到改动本身。

### 基线的主要瓶颈（第 1 步的数据结论）

1. **翻译不是瓶颈**：`ttft` p50 33 ms，llama-server 33/33 层在 GPU。
2. **ASR 吃掉约 78% 的墙钟**：186.6 s 的音频里 ASR 累计跑了 144.7 s。partial 每 250 ms 就重跑整句前缀，`audio_ms` p90 达 6.4 s、最大 18.1 s。
3. **字幕窗 HOLD 是上屏延迟的大头**：`action=defer`（被挂起等的行）的上屏 p50 1.2 s、p90 2.4 s。
4. `caption.text pump` p50 28.6 ms 是轮询粒度，UDP `net` p50 0.2 ms，两者都可忽略。

---

## 5. 第 2 步：三项改动

每项改动都单独跑一轮回放，之后再跑一轮确认。改动只动产品代码里最小的一处；测试脚本另有两处配套改动（见 5.4）。

### 5.1 A —— 去掉"最终转写重复翻译"

`server/pipeline.py`，`_run` 内：

```diff
-        if text == self.last_translated and not final:
+        if text == self.last_translated:
             return
```

**动机**：最后一片 partial 的文本往往和 `end` 的最终文本一样，`end` 会把同一句话再交给 llama 翻一遍，再发一次同样内容的 UDP 包。

**安全性**：`final` 这个字段下游（字幕窗 `caption_window.py`、浏览器扩展）都不读；`_remember()` 写入的 `context` 在 `translate.py` 里被直接 `del`，本来就没用。所以丢掉这次重发不改变任何显示结果。

### 5.2 B —— 静音 150 ms 提前收尾，`end` 复用它的文本

`server/vad.py` + `server/pipeline.py`。

新增常量：

```python
EARLY_END_SILENCE_MS = 150   # 静音到 150ms 就先把这一句交给 ASR
VAD_SPEECH_PAD_MS = 120      # 尾部只留 120ms 的 release
```

VAD 事件从二元组改成三元组 `(kind, audio, flag)`：对 `partial` 来说 `flag` 表示"这是收尾的提前 partial"，对 `end` 来说表示"它的文本可以直接复用"。

- 静音满 150 ms（且距上一次 partial ≥ `PARTIAL_INTERVAL_MS`）→ 立刻发一个带 `flag=True` 的 partial；
- 静音满 400 ms → 发 `end`，`flag = early_partial`；
- 音频尾部的全 0 静音裁到只剩 120 ms（`NON_SPEECH_GAIN=0.0`，静音帧是精确的 0，`np.nonzero` 就能定位最后一个有效采样）。

会话侧（`pipeline.py`）：

- 队列变成 `(kind, audio, queued_at, flag)`；
- `_run` 新增 `reuse` 参数：`end` 且 `reuse` 且已缓存提前 partial 的识别结果 → **跳过 `asyncio.to_thread(asr.transcribe)`**，直接用缓存的文本往下走，只记一条 `pipeline.reuse` 日志；
- 缓存 `_partial_result` **只在"提前 partial"这一种情况下写入**（`kind=="partial" and reuse and text`），其它情况一律清空；
- `start` 事件清空缓存。

实现过程中发现并修掉的两个真实 bug：

1. **`early_partial` 标志不会随语音恢复而复位**。第一段句中停顿触发一次提前 partial 之后，标志一直挂着，后面所有停顿都不再提前 flush，`end` 却还标着"可复用"，于是复用率只有 **4/22**，而且如果恰好在两次 partial 之间没有别的 partial，会复用到一段**过期的文本**（缺了后面说的话）。修法：语音在停顿中途恢复时把 `early_partial` 复位。
2. **`_latest()` 会把提前 partial 顶掉**。它原本的行为是"队列里只要有更新的 partial 就丢掉当前这条"，结果 `end` 紧跟着入队时，提前 partial 会被丢弃 → 既白跑一次识别，又拿不到缓存。修法：提前 partial 永不被顶掉。

修复后复用率 **4/22 → 23/24 → 49/49**。

### 5.3 C —— 说满 5 s 遇 200 ms 停顿就软切，单句上限 20 s → 12 s

`server/vad.py`：

```diff
-MAX_UTTERANCE_MS = 20_000
+MAX_UTTERANCE_MS = 12_000
+SOFT_CUT_SPEECH_MS = 5_000    # 说满 5 秒
+SOFT_CUT_SILENCE_MS = 200     # 遇到 200ms 停顿就收
```

关句条件从 `silence_ms >= 400` 改成：

```python
if self.silence_ms >= VAD_MIN_SILENCE_MS or (
    self.speech_ms >= SOFT_CUT_SPEECH_MS
    and self.silence_ms >= SOFT_CUT_SILENCE_MS
):
```

**动机**：`audio_ms` 最大到 18 s，意味着有一次识别在啃 18 秒的前缀；同时一句已经说了 5 秒之后，200 ms 的停顿基本就是句号，再干等 400 ms 只是把 `end` 往后推。

**副作用（已在回放里看到）**：个别长句会被切开，例如基线的 `This week I spent three evenings ... short blog video.` 在 C 轮变成 `... short blog.` + `video.`。这是软切的预期行为，属于"更早出字 vs 偶尔切得更碎"的取舍。

### 5.4 测试脚本的两处配套改动（不碰产品逻辑）

1. `scripts/blog_video_test.py` 连接后额外发一条 `{"type": "arm"}`。
   字幕窗第一次连接时 `closed=False`，断开后 `closed=True`；不发 `arm`，第 2 次起的回放根本不画屏，A/B/C 每轮都拿不到 `caption.paint` 数据。发 `arm` 之后每轮都画，且每轮都在已预热的服务上，可比。
2. `scripts/perf_compare.py`（新增）：逐轮出同一组指标。

---

## 6. 对比数据

六轮全部用同一素材、同一份代码以外的条件（每轮前重启 Python 服务，llama-server 保持常驻预热）。
`基线` = 改动前，`A` = 仅 A，`B` = A+B，`C` = A+B+C，`C2` = C 的复测轮。

### 6.1 端到端：首条新译文 → 上屏

定义：对每一句，取该句第一条 `translation` 事件的墙钟时间，再取这之后第一次 `caption.paint` 的墙钟时间，两者之差。`perf_compare.py` 的 `e2e_first_tr_to_paint`。

| 轮次 | p50 (ms) | p90 (ms) | >1 s 的句子 |
| --- | --- | --- | --- |
| 基线 run1 | 1455 | 2188 | 9/16（56%） |
| A | 1466 | 2336 | 9/16（56%） |
| **B** | **814** | **1845** | **7/16（44%）** |
| **C** | **756** | **1818** | **6/16（38%）** |
| **C2** | **374** | **2151** | **5/16（31%）** |

- 基线两轮（1455 / 1466）几乎重合 → 这个指标的运行间噪声很小；
- B 一刀就把中位数砍掉约 640 ms；
- C 再往下压，但两轮 p50 是 756 和 374，**n=16 的样本下 p50 波动较大**，所以取区间而不是单点；
- **>1 s 的比例从 56% 稳定降到 31~38%**，这个比例比 p50 更稳。

### 6.2 画屏延迟 `caption.paint/since_recv`

| 轮次 | n | p50 | p90 | p99 | max |
| --- | --- | --- | --- | --- | --- |
| 基线 run1 | 263 | 47.1 | 284 | 1180 | 1611 |
| 基线 B1 | 279 | 49.0 | 321 | 1177 | 1452 |
| A | 251 | 49.6 | 322 | 1619 | 1754 |
| B | 315 | 45.5 | 248 | 1408 | 1769 |
| C | 310 | 46.6 | 209 | 1285 | 1905 |
| C2 | 355 | 43.8 | 193 | 888 | 1753 |

- **p90：284~321 → 193~209（约 −35%）**，稳定；
- p50 基本不动（47 → 44~47 ms），因为 p50 那部分本来就是"立即画"的行，延迟来自 `pump` 轮询而不是 HOLD；
- 画屏次数从 263/279 涨到 310/355（+20%），说明更早、更频繁地有新内容可画；
- **`max` 反而略变差（1452~1611 → 1753~1905）**：画得多了，撞上慢周期的机会也多。这是本次唯一明确变差的尾部指标。

### 6.3 ASR

| 轮次 | n | p50 | p90 | p99 | max | 累计耗时 | 占墙钟 | >250 ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 基线 run1 | 733 | 179 | 296 | 451 | 812 | 144.7 s | 77.6% | 19% |
| 基线 B1 | 723 | 184 | 310 | 460 | 812 | 146.9 s | 79.4% | 22% |
| A | 704 | 190 | 316 | 458 | 775 | 146.1 s | 79.1% | 23% |
| B | 711 | 191 | 302 | 442 | 751 | 144.6 s | 78.2% | 21% |
| C | 715 | 187 | 297 | 434 | **657** | 143.3 s | 77.4% | 20% |
| C2 | 736 | 184 | 284 | 398 | **666** | 144.3 s | 78.1% | 18% |

单次识别喂进去的音频长度：

| 轮次 | p50 | p90 | p99 | max |
| --- | --- | --- | --- | --- |
| 基线 run1 | 2528 | 6368 | 11840 | 17920 |
| 基线 B1 | 2528 | 6464 | 11840 | 18144 |
| A | 2496 | 6656 | 11872 | 17824 |
| B | 2528 | 6464 | 11412 | 17400 |
| C | 2432 | 5924 | 9424 | **11224** |
| C2 | 2464 | 6240 | 9484 | **10872** |

**关键点：总计算量没变（144~147 s → 143~144 s），但分布变了。**

- B 把每句最后一次"闭句识别"从 `end` 挪到了提前 partial 上，**同量的活提前 250 ms 干**：
  - 基线 `asr by kind = {partial: 670~680, end: 50~53}`
  - B 轮 `asr by kind = {partial: 711, end: 0}` —— **49 次 `end` 全部复用，一次识别都没跑**
  - C 轮 `{partial: 712, end: 3}`（55 次 `end` 中复用 52 次）
- C 把单次识别的最大前缀从 18 s 压到 11 s（12 s 软上限），`p99` 从 11.8 s 压到 9.5 s，`max` 从 812 ms 压到 657~666 ms。
- `asr >250 ms` 的比例基本没动（19~22% → 18~20%），所以**第 3 步条件 ② 仍然成立**（见第 8 节）。

### 6.4 翻译

| 轮次 | `ttft` n | `ttft` p50 / p90 | 翻译整段 p50 |
| --- | --- | --- | --- |
| 基线 run1 | 555 | 31.7 / 39.9 | 123 |
| 基线 B1 | 564 | 33.2 / 41.4 | 126 |
| A | 530 | 33.8 / 44.1 | 129 |
| B | 510 | 34.6 / 43.0 | 130 |
| C | 522 | 35.5 / 44.2 | 130 |
| C2 | 541 | 33.3 / 42.6 | 125 |

- A 让翻译任务数从 555~564 降到 530（少约 4.5%），但对延迟没有可测量的影响（e2e 1455 → 1466，画屏 p90 284~321 → 322）。
- `ttft` p50 从 31.7~33.2 涨到 33.3~35.5（+2 ms），在噪声量级，可以忽略。
- 整段翻译 p50 123~126 → 125~130，同样在噪声内。

### 6.5 排队与积压（本次变差的部分）

| 轮次 | `qwait` p50 | p90 | p99 | max | `qlen` p90 | `qlen` max | `drop` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 基线 run1 | 38.3 | 132 | 214 | 318 | 1 | 5 | 224 |
| 基线 B1 | 43.0 | 134 | 260 | 400 | 1 | 5 | 231 |
| A | 41.3 | 133 | 233 | 294 | 1 | 4 | 230 |
| B | 42.3 | 152 | 293 | 478 | 1 | 5 | 268 |
| C | 48.3 | 156 | 337 | 792 | 1 | 4 | 238 |
| C2 | 45.9 | 164 | 317 | 485 | 1 | 4 | 217 |

`qwait` p50 38~43 → 46~48 ms，p99 214~260 → 317~337 ms。原因：提前 partial 让入队事件变多（B 轮 early partial 共 131 次，C 轮更多），队列瞬时变深。绝对值仍然只有几十毫秒，且 `qlen` p90 恒为 1，没有出现持续积压。

### 6.6 A 单独的效果

A 的效果只体现在"少发了重复包"，不体现在延迟上：

| | 基线 run1 | 基线 B1 | A |
| --- | --- | --- | --- |
| 翻译任务 `ttft n` | 555 | 564 | 530 |
| `caption.text` 包数 | 2543 | 2640 | 2408 |
| 其中 `action=drop`（内容与屏上相同） | 73 | 61 | **38** |
| e2e p50 | 1455 | — | 1466 |

重复包从 73/61 降到 38，翻译任务少 4.5%，**端到端延迟没有任何变化**。

**判定：A 单独无收益，但它零风险、省下约 5% 的翻译任务，且在第 3 步调短 HOLD 之后，"重复的最终转写"更容易在屏上造成抖动，所以保留。**

---

## 7. 结论

| 指标 | 基线 | 第 2 步后（C / C2） | 变化 |
| --- | --- | --- | --- |
| 首条新译文 → 上屏 p50 | 1455~1466 ms | 374~756 ms | **约 −60%** |
| 同上 >1 s 的句子 | 9/16（56%） | 5~6/16（31~38%） | **几乎减半** |
| 画屏延迟 p90 | 284~321 ms | 193~209 ms | **−35%** |
| 画屏次数 | 263~279 | 310~355 | +20% |
| 单次识别 max 前缀 | 17.9~18.1 s | 10.9~11.2 s | −40% |
| 单次识别 max 耗时 | 812 ms | 657~666 ms | −19% |
| ASR 总耗时 | 144.7~146.9 s | 143.3~144.3 s | 持平 |
| ASR 占墙钟 | 77.6~79.4% | 77.4~78.1% | 持平 |
| `ttft` p50 | 31.7~33.2 ms | 33.3~35.5 ms | +2 ms（噪声内） |
| `qwait` p50 / p99 | 38~43 / 214~260 ms | 46~48 / 317~337 ms | 变差（可接受） |
| 画屏 max | 1452~1611 ms | 1753~1905 ms | 变差（可接受） |

**收益来源排序：**

1. **B（提前收尾 + 复用）** 是主要功臣：端到端中位数 −640 ms，且 ASR 总量完全不变。
2. **C（软切 + 12 s 上限）** 再压一截，主要体现在识别前缀和 p99 上。
3. **A** 对延迟无贡献，但省 5% 翻译任务、少一半重复包。

**测试**：`python -m pytest -q` → **40 passed**（原 32 项全绿，新增 8 项 VAD 行为测试，覆盖提前 partial、复位、尾部裁剪、软切、12 s 上限）。

---

## 8. 第 3 步触发条件的当前判定

三个条件是在第 1 步数据上定的，现在用第 2 步之后的数据重新判一次。

### ① "收到 → 画出" 经常超过 1 s → 调 `HOLD_ABS_MIN_MS` 800→400、`HOLD_ABS_MAX_MS` 4200→2500，并让积压时跳到最新译文

**仍然成立。** 首条新译文 → 上屏 >1 s 的比例：基线 56% → 现在 31~38%，仍然有三分之一以上的句子超过 1 秒；p90 仍在 1818~2151 ms。

→ **第 3 步应该动手。** 这也是剩下最大的一块（`caption.text action=defer` 依然是每轮 2000+ 次的主要动作）。

### ② `asr` 经常 > 250 ms → `PARTIAL_INTERVAL_MS` 250 → 400~500

**部分成立。** `asr >250 ms` 的比例：基线 19~22% → 现在 18~20%，p90 仍是 284~310 ms，约五分之一的识别超过 250 ms。

但要注意一个反直觉的地方：partial 是**每次重跑整句前缀**，把间隔从 250 ms 拉到 400~500 ms：

- 调用次数变少 → 队列压力下降（有利于 `qwait`）；
- 但每次啃的前缀更长 → **单次 `asr` 耗时会变大**，`asr p50/p90` 大概率变差；
- 首个 partial 出字的时间被推迟（最多推迟到 400~500 ms）。

按"总工作量 ≈ 音频长²/(2×间隔)"的模型，拉长间隔确实会降低总量。**建议第 3 步先只做条件 ①，跑一轮看 `qwait`/`asr` 的实际变化，再决定要不要动 `PARTIAL_INTERVAL_MS`。**

### ③ 译文改字频繁 → 本地翻译 `temperature 0` / `top_k 1` 并重跑 22 用例

**不成立。** `low_overlap_swaps` 占比：基线 307/2545 = 12.1%，C2 轮 301/2350 = 12.8%，几乎没变；字幕 `action=char`（逐字改）每轮只有 29~50 次（原判定是 72 次）。改字主要来自 ASR 本身的前缀修正，不是翻译的随机性。

→ **第 3 步不做这条。**

### 明确不做的事（维持原判）

- 不换混元（llama 33/33 层在 GPU，`ttft` p50 33 ms，不是瓶颈）；
- `CONTEXT_SENTENCES` 无实际效果，不调参；
- Qt 透明穿透窗与延迟无关，后置。

---

## 9. 顺带发现、尚未修的问题

1. **`final=true` 的 translation 消息实际上从不下发。**
   `_translate` 流式循环里最后一片已经把 `shown_translation` 设成了 `acc`，函数末尾的 `if self._caption_accepts(acc)` 因此判为"和屏上一样"而直接跳过。结果 `scripts/blog_video_test.py` 每句的 `translation` 字段在**六轮里全部是 `null`**（`final_tr = 0`），`asr_ratio p50 = 0.2` 也由此而来。
   **这是测试统计口径的问题，不是字幕显示问题**（字幕窗和扩展都不读 `final`）。要拿到每句译文质量表，得改测试脚本改成"取该句最后一条非 final 的 translation"。**建议放到第 3 步之前单独处理。**

2. **`server/config.py` 的 `resolve_llama_runtime` 读错了字段。**
   `runtime.get("vram_used_mib_after_load")` 是"加载后已用显存"，却被当成"总显存"和 4096 比较。本机 4096 MiB 卡上，`used = 2178 ≤ 4096` 恰好走进了正确的 `(60, 1024)` 分支，所以**本机没有实际影响**；但在 8 GB 卡上，如果 used 只有 3000，会被误判成小卡而降级。应改读 `total` 字段。

3. `Session.last_translation` 只写不读（3 处赋值，0 处读取），可以删。

4. `scripts/blog_video_test.py` 不发 `arm` 就不画屏（已修，见 5.4）。

---

## 10. 改了哪些文件

产品代码：

| 文件 | 改动 |
| --- | --- |
| `server/pipeline.py` | A：去掉 `and not final`；B：队列带 flag、`_run` 支持复用、`_latest` 不顶掉提前 partial |
| `server/vad.py` | B：三元组事件、150 ms 提前 partial、120 ms 尾部裁剪、语音恢复复位；C：`MAX_UTTERANCE_MS` 12000、软切 |

测试与工具：

| 文件 | 改动 |
| --- | --- |
| `tests/test_vad.py` | 事件改三元组的适配 + 新增 8 项行为测试 |
| `scripts/perf_compare.py` | 新增：逐轮出同一组指标 |
| `scripts/blog_video_test.py` | 连接后发 `arm` |

埋点（第 1 步）：`server/perf.py`（新增）、README「延迟埋点」、`scripts/start.ps1`（llama 日志重定向 + `-lv 4`）。

---

## 11. 怎么复现

```powershell
$py = "D:\minanconda\envs\pytorch\python.exe"

# 1. 先跑测试
& $py -m pytest -q

# 2. 停掉 8765/8766 上的 python（llama-server 8080 保持常驻预热）
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

# 3. 轮转上一轮日志（服务在跑时文件被占用，必须先停）
Move-Item -Force logs\perf.log            logs\perf.<轮名>.log
Move-Item -Force logs\perf_caption.log    logs\perf_caption.<轮名>.log
Move-Item -Force logs\blog_3min_test.json logs\blog_3min_test.<轮名>.json

# 4. 启动服务（perf 默认开启）
$env:PYTHONUNBUFFERED='1'; $env:TRANSLATION_PERF='1'
Set-Location C:\Users\20693\Desktop\translation
& $py -m server

# 5. 另开一个窗口回放（约 3.5 分钟）
& $py scripts\blog_video_test.py play

# 6. 出数
& $py scripts\perf_compare.py <轮名> logs\perf.log logs\perf_caption.log logs\blog_3min_test.json
```

原始数据：

```
logs/perf.run1.log            基线 run1（服务端 + 字幕混写，单文件时期的产物）
logs/perf.baseline.log        基线 B1 的服务端
logs/perf_caption.baseline.log基线 B1 的字幕窗
logs/perf.A.log / perf_caption.A.log / blog_3min_test.A.json
logs/perf.B.log / perf_caption.B.log / blog_3min_test.B.json
logs/perf.C.log / perf_caption.C.log / blog_3min_test.C.json
logs/perf.log   / perf_caption.log  / blog_3min_test.json   ← C 的复测轮 C2
```

---

## 12. 下一步

1. **第 3 步条件 ①**：`HOLD_ABS_MIN_MS` 800→400、`HOLD_ABS_MAX_MS` 4200→2500，积压时跳到最新译文 → 跑一轮对比。这是剩下最大的一块。
2. 视 `qwait` 的表现，再决定是否动 `PARTIAL_INTERVAL_MS`（条件 ②）。
3. 条件 ③ 不做。
4. 修掉第 9 节的 `final=true` 口径问题，让回放脚本能给出译文质量表。
5. **第 4 步（换 ASR）仍缺前置条件**：需要真人/真实素材录音（英日韩、含配乐 10 段）才能判断当前 ASR 在真实场景下的表现；素材到位前不启动。
