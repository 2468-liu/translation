# STATE

一页。**每次会话读全文。** 细节在 `REPORT.md`（用 `python scripts/rag.py query "..."` 取片段，不要通读）。
规则在 `AGENT.md`。推进顺序在 `PLAN.md`。

## 目标（体感，不是数字）

1. 字幕不要慢声音一句半句
2. 长时间看视频风扇不要狂转
3. 字幕不要乱跳、不要看不清

## 约束

- 默认链路 = **CT2 small (cuda/float16) + hy-mt2**，4GB 卡
- **只调流水线 / 窗口 / VAD / 显示层**；除非被卡住，不换模型
- 换翻译模型（Hy-MT2 → Gemma）是**独立于音频直出的决策**，不随直出一起否决

## 当前默认配置（生效值，非源码常量）

```
ASR_DEVICE          cuda
ASR_COMPUTE_TYPE    float16
ASR_MODEL           faster-whisper-small
PARTIAL_INTERVAL_MS 750   ← 生效值。**这一行原来写的是 250，而这是一份「生效值」表，
                            最不该错的字段。**750 的理由不是"更快"：823ms 的 ASR 在
                            250 节奏下 215 次全部 overrun，750 下占墙钟 57.1%。
                            750 让 turbo *能用*，不是让它*好*。popup 滑杆 250~1000。
AUDIO_NATIVE        0（音频直出关闭）
HOLD_ABS_MIN_MS     800   （caption_window.py:69。参考表最底 1000 × 最快节奏 0.62
                            = 620 是这张表能产出的最低值 ⇒ 地板 ≤620 是空操作。
                            试过 600，无效，已回退。见 REPORT 42）
默认链路            whisper-hy（config.DEFAULT_CHAIN）
                     可选 whisper-hy / qwen3-hy / gemma-e2e（切换 = 全拆 + 重启）
投影层              CPU (--mmproj-device none)
翻译后端            启动取**本机已装最新**：win-x86_64-nvidia-cuda12-avx2-2.53.0（本条 2026-10-07 晚临时改）
                    已装 2.33.0 / 2.47.0 / 2.51.0 / 2.52.0 / 2.53.0
                    **版本政策（2026-10-08 定，AGENT.md §5 第九条）：默认走最新，
                    测量走绑定。**变量在 server/config.py：
                      LLAMA_VERSION             不指定 = 已装最新（出货行为）
                      MEASUREMENT_LLAMA_VERSION 2.47.0，replay.ps1 自己钉
                    唯一解析器 scripts\resolve_llama.ps1（-Version / 环境变量 /
                    runtime.json 的 llama_version → 否则最新），没装的那版**报错不退回**。
                    ⚠ REPORT 里全部延迟数是 2.47.0 上的，出货默认跑 2.53.0 ⇒
                      **默认组合从未在它出货的 build 上被测过**，引用旧数要带 build 号。
                      我此前说过「hy-mt2 仍在 2.47.0」，是错的，见 REPORT 44。
                      start.ps1:563 的注释也描述了一个代码没实现的意图，已改。
                      原来的两份解析器（start.ps1 默认最新 / resolve_llama.ps1 默认
                      2.47.0）方向相反，replay 调了后者 —— 测量落在 2.47.0 是碰巧，
                      不是被强制。已合成一份，见 REPORT 45。
翻译模型            MT_BACKEND=hy-mt2（默认，8080）｜gemma4（8082，文本翻译，不带 --mmproj）
                    gemma4 = 36 层上 32 层 + MTP 草稿 5/5 层，VRAM 2337 MiB，50 tok/s
                    ngl 99 + 投影放 CPU：75.4 tok/s（更快，见 REPORT 25.3 脚注）
音频采集            AUDIO_SOURCE=loopback（默认，WASAPI 系统回环，自测增益）
                    =socket 时回退到扩展/回放的 PCM 注入
                    **回放自己设这个**：`replay.ps1` 缺省 socket 并打印出来
                    （431686e 之前没有任何脚本设过它，见「已证伪」）
音频直出            Gemma4 音频路径：**对白被配乐压住的素材不可用**（REPORT 25）
                    适用素材 = 干净旁白（blog 类）；MTP 在音频路径净负，应关
上下文注入          MT_CONTEXT_SENTENCES 2（0 是对照臂）
                    **本轮之前从未生效**：translate.build_messages 把 context 删掉了（见 REPORT 21.3）
VAD                 THRESHOLD 0.5 / MIN_SPEECH 250 / MIN_SILENCE 400 / PAD 120
切分                MAX_UTTERANCE 12000, SOFT_CUT 5000ms 语音 + 200ms 停顿
显示层              SETTLE 450 / HOLD_MIN 800 / HOLD_MAX 4200 / READABLE 0.7
```

跑的是哪一版：启动日志含 `git_rev`，**llama 版本与 Python 环境在 `logs\start.log`**。
后者比前者要紧：`start.ps1` 的选择只写进这个文件，而 native host 把 stdout 丢进 DEVNULL——
"起了哪一版"曾经只存在于那个被丢掉的控制台里。

---


## MTP 这条线到此为止（2026-10-08）

用户指出 `mtp_draft` 这个名字有误导性：它只读命令行里有没有**外挂草稿文件**
（`--model-draft`），不覆盖**内置 MTP 层**。**这一点正确**，而且字段旁已经有注释
说明它答的是哪一问，另有 `spec_args` 字段管"推测解码到底开没开"。

**但更要紧的是权重本身。**用 llama.cpp 官方 `gguf` 包读 `Hy-MT2-1.8B-Q4_K_M.gguf`：

```
general.architecture = hunyuan-dense        hunyuan-dense.block_count = 32
元数据 36 键，张量 354 个
带 mtp / draft / nextn / medusa / eagle / speculat / predict 的张量：0
张量名前缀 34 种 = blk.0..blk.31（各 11 个）+ token_embd + output_norm
354 = 32 × 11 + 2
```

**这是一个纯 dense 模型，没有 MTP 头。**所以 hy-mt2 的 MTP 不是"未测"，
是**这份额重开不了**。运行时证据同一结论：

```
logs/llama.err
  spec common_specu: no implementations specified for speculative decoding
  prompt_save: - saving prompt with length 21, total state size = 1.313 MiB (draft: 0.000 MiB)
```

**⇒ 这条不成立，不排队。** 曾经设想的收益（"开更快则 REPORT 一大片数要重估，
关更快则是一条能写进已证伪的结论"）两个方向都不会发生。

**另一条已排除的担心**：推测解码**按构造就是无损的**（验证通过才接受，
输出与不开推测逐 token 相同），所以它**不可能**让同一句原文产出两个不同译文。
REPORT 33 的"来源 B = 0"与 MTP 开没开无关，**不需要为此复核**。

**⚠️ 记一个坑：二进制 grep 权重文件里的 "mtp" 是无效的。**
扫出来的 25 个命中全是量化权重里恰好拼出的随机字节（各 1 次）。
对照组（能扫出 `blk.0.attn_k.weight`）证明方法本身有效，
所以这是"信号被噪声淹没"——**必须真解析 gguf**。

## 已证伪（假设清单，不是错误清单）

**这些看着合理，最容易被重新提出。**每条都写了它错在哪、以及为什么它当时看着成立。

| 假设 | 证伪方式 | 为什么它当时看着成立 |
|---|---|---|
| **`total time` = 单次成本** | 它含 `load time`（本机 656–809ms/进程） | whisper 自己就打印这个字段，名字没骗人 |
| **`total − load` = 边际成本** | 常驻实测与该减法差 11 倍（同 ctx 660：CLI 1001ms vs 常驻 90ms） | 减法在数学上成立，问题在于两个程序的 `-ac` 行为不同 |
| **缩窗（`-ac`）买速度** | ctx 77→660（8.6×）encode 只 111.8→122.7ms | 名字叫 audio_ctx，直觉上就该缩计算量 |
| **`vad.end` 日志行数 = VAD 产生的事件数** | `perf.emit` 在 `MIN_SPEECH_MS` 门控**之前**，日志含被丢弃项 | 日志行数和事件数看起来是同一件事 |
| **日志里的 `speech_ms` 短 = 碎段** | 53 行日志里 50 行 <500ms，但那是门控前的记录 | 按 audio_ms 统计时结论相反 |
| **`language=auto` 导致空输出** | 三方对照：`small auto` 与 `small en` 输出**逐字相同** | auto 慢了 2 倍，看起来就像坏了 |
| **小窗口 ASR 慢 = 检测开销** | 见下条：慢在编码，不在检测 | 两者都慢，混在一起归因 |
| **回放锁 `en` 等于在测产品** | 产品默认 auto；回放曾写死 `en` | 素材大多是英语，写死 en 看着无害 |
| **旧的延迟基线能直接判断 auto 下的表现** | auto 下 asr p50 281ms，已超 250ms 节奏 | 两组数都叫"ASR 耗时"，量纲一样 |
| **量化省 GPU 功耗** | CUDA 74.3 / 85.6 / 74.3ms，差异在噪声内 | 量化必然更快是常识 |
| **RMS 阈值能判断停顿** | 底噪是响处 20.5% 的素材报"0 次 ≥1s 停顿"，管线埋点实测 35 次 | RMS 确实测能量，只是阈值定错了位置 |
| **GPU-only 读数能判断"挪 CPU 是否省电"** | GPU 降 10W / CPU 升 5W 与 CPU 升 12W 在本机读数完全一样 | 差分法看起来是标准做法 |
| **clip 级 `language=None` 能验多语种素材** | 222 被整段按英语解码，产出 `How do you speak Japanese?` 这类幻觉 | 参数就叫 `language=None`，看起来就是"自动" |
| **臂名/字段名说明了配置** | 12 格 "MTP 开" 全部是关的；关闭态的接受率字段根本不存在 | 名字写在结果表里，看着就是证据 |
| **`hold_ms()` 可以被 SETTLE_MS 调小** | 它恒 ≥800ms，任何低于 800 的值不可能生效 | 两个都是"等待相关"的常量 |
| **`clocks_event_reasons` 可当风扇代理** | 它是代理的代理，且本机 `fan.speed` 返回 `[N/A]` | 它确实记录了 GPU 进出活跃态 |
| **`VAD_MIN_SPEECH_MS` 是"最短语音"（延迟成段）** | 它同时是**丢弃**门槛：`_drop()` 无条件执行，不足长的音频被扔掉。LiveTranslate 的同名参数是"并入下一段" | 两个项目参数同名，语义相反 |
| **"抬门槛 = 合并短段"** | 抬到 1.0s 会额外丢 8 段 / 4.9s **真语音**（`e_3min_timing.json` 里那些段带 p=0.99 的真实文本）。合并要单独实现 | 两者都让"段变长"，看着是同一件事 |
| **单次运行的 VAD 分布可直接引用** | 探针同一文件报出 32/35/38/39 段：`load_vad()` 返回全局实例，Silero 跨帧携带 LSTM 状态且段间不清，第 N 次测量接着上次的末状态。加 `engine.reset()` 后三次都是 32 | 同一份代码同一份音频，看着必然确定 |
| **`conda activate` 在 PowerShell 脚本里能生效** | `cmd /c "call conda.bat activate pytorch"` 里的 cmd 是子进程，`CONDA_PREFIX` 不回传。于是回退到 **base** 环境（无 `websockets`），后端 `ModuleNotFoundError`，而扩展侧只看到 90 秒超时。上一轮"实测通过"是因为 shell 里恰好已有 conda 变量，把空值路径盖住了 | 命令确实执行了、也没报错，看着就是激活了 |
| **`Get-NetTCPConnection` 可以判断端口在听** | 它基于 CIM/WMI，受限会话里**拒绝访问**；加了 `-ErrorAction SilentlyContinue` 后返回空值，被读成"没有监听"。实测 8080 正在服务而它为 0 条 | 它名字里就是"TCP 连接"，看着就是端口检查 |
| **`Get-CimInstance` 失败会显式报错** | 在受限会话里它是终止错误（`$ErrorActionPreference="Stop"`），`replay.ps1` 因此死在第 37 行一行未跑；而在被 `SilentlyContinue` 包住的地方（草稿加载断言），它**静默变成"没有草稿"** | 同一条命令在两种 context 下表现不同 |
| **`Get-NetTCPConnection` 的问题已经修完** | 只修了 `replay.ps1`。`smoke.ps1` 两处、`sweep_*.ps1` 九处仍在用它，本轮实测 `smoke.ps1` 因此在 8080 有服务时读成"没服务" | 结论已写进这张表，看起来就像已经处理过了 |
| **`Get-Process` 的属性都能读** | `Sort-Object StartTime` 抛 `Access is denied`（受限会话无此权限），`replay.ps1` 因此在放音之前就中止。`Get-Process` 本身能用，**它的 `StartTime` 属性不能** | `Get-Process` 不走 WMI，所以"`Get-CimInstance` 被拒"推不出"`Get-Process` 也被拒" |
| **模型目录里"第一个 gguf"就是要跑的那个** | `models\hy-mt2-1.8b` 有两个 gguf，`Select-Object -First 1` 选中 `1.25bit-v2`，llama-server 加载失败退出。`start.ps1` 用 `*Q4_K_M*` 过滤所以没事 | 目录里长期只有一个文件时，"取第一个"和"取对的那个"结果一样 |
| **`USE_NLLB = False` 是一条可调的配置** | 它写死为 False，而它背后的 `server/mt.py` 与两个模型目录都不在。整条链**不可能运行**，只可能被误认成活路径 | 常量名叫开关，看着就像还有"开"的那一侧 |
| **回放的音频源是 shell 里的环境变量** | **没有任何脚本设 `TRANSLATION_AUDIO_SOURCE`**。`PLAN.md` 把它写成操作者要敲的一行，于是回放口径（socket 注入）取决于运行时有没有人记得敲。实测漏敲时：运行干净、无报错、`vad.end=0`、零译文 | 环境变量本来就是给 shell 用的；写进文档就等于已经生效 |
| **`_self_check()` 一直在跑** | 没人调用它（`__main__` 也没有），而且**它会失败**：两条期望早于 `speech_hold_factor`。即显示层状态机零覆盖 | 函数名叫 `_self_check`，读起来像启动时会执行 |

---

## 结论台账（每条带前提）

**前提变了，下面每条结论自动作废。**括号里是它成立所依赖的条件。

```
ASR 上 GPU 成本 +10W
  前提：投影层 CPU / partial 250ms / CT2 small cuda float16 / hy-mt2 空闲 / 后端 2.47.0
  ⚠️ 其中任何一条变了，这条数就不能引用

ttft p50 30~36ms
  前提：语种锁定 en。auto 下每次调用要带检测，成本不同

上下文注入的质量收益（REPORT 21.4）
  前提：hy-mt2 / 2.53.0 / ngl 60 ctx 1024 / e_3min 的 21 句成句原文 /
        temperature 0 / 逐句喂入且上下文取自本臂自己的译文
  ⚠️ 原文是**成句**时测的。真实管线喂进去的是 250ms 起就切出来的碎片，
     那种输入下上下文能救多少**没测过**，不能把这里的收益外推过去

hy-mt2 prefill 代价：ctx0 15.7ms / ctx2 31.1 / ctx6 46.4（中位，冷前缀）
  前提：同上。decode 105→114ms 基本不动
  ⚠️ 探针在同一批句子上跑时前缀被缓存，中位数会低估；冷前缀那两个数才是真的

gemma4 文本翻译：prefill 103~160ms + decode 220~250ms，50 tok/s，VRAM 2337 MiB
  前提：E2B Q4_K_XL / 36 层上 32 层 / ctx 2048 / MTP 草稿 n-max 3 / 2.53.0
  ⚠️ 解码速率是 50 tok/s（20ms/token）；曾经记到的 46ms/token 是**冷启动首请求**，
     权重还在分页，不能当稳态

asr 占墙钟 16.6% / asr>250ms 15%（er_3min 那格）
  前提：语种锁定 en

首字上屏延迟 p50 374~756ms
  前提：语种锁定 en

partial 节奏 250/500/750/1000ms 的省电曲线
  前提：语种锁定 en。auto 下 250ms 本身就跑不满（asr p50 281ms）

回放语种
  2026-10-07 起默认 auto，与产品一致（扩展默认"自动检测"）。
  代价：同一素材 asr p50 140~158ms → 281ms，asr>250ms 从 8~19% → 58%
  这意味着**上面几条锁定 en 的基线不可直接与 auto 的结果对比**。
  需要对照时：TRANSLATION_REPLAY_LANGUAGE=en

回放的音频源 = socket PCM 注入
  ⚠️ **431686e 之前这条前提没有任何脚本保证**，只写在 PLAN.md 里让人手动敲。
      漏掉时回放不会报错，只会跑出一个零事件的结果（vad.end=0）。
      现在由 replay.ps1 缺省设置并打印在 code stamp 旁边。
  🚩 **⇒ REPORT 24 / 25 / 26 / 27 的数字全部标为「待复核」**（见下方专节）。

whisper.cpp CPU 不可用（单次 400~1260ms）
  前提：ctx 220 / 6 线程 / Q8_0 / 1.4s 片段
  ⚠️ 前提变了要重测

audio_ctx 悬崖在 150（<150 时 77→5215ms）
  前提：whisper.cpp / CUDA / 1.4s 片段 / 贪心无温度回退
  ⚠️ 只对 whisper.cpp 成立，CT2 未测

Q5_1 在 CUDA 上同速但省 284MiB（74.3ms vs fp16 74.3ms）
  前提：CUDA / ctx 220 / 1.4s 片段。CPU 上 Q5_1 反而比 Q8 慢

分片级语种检测：large 干净 / small 不可信
  前提：large-v3-turbo。默认链路用 small，所以这条只用来定位问题

先上屏后覆盖不可行（错语种译文一致+相关仅 25%）
  前提：clip222_90s / hy-mt2 / 边界后首个 partial

C 的软切生效（5000ms 语音 + 200ms 停顿）
  前提：whisper 路线。音频直出用 GEMMA_SOFT_CUT（12000 + 700）

#6 合并臂：两套独立机制 + 一个被证伪的假设（REPORT 29.9）
  前提：gate 1000 / merge window 2000 / 语言 auto / partial 250 / ctx=2
        hy-mt2 / 2.47.0 / socket 注入 / e_3min 各跑满 184.3s 各 23 条译文
  ① 少中间态 → 队列压力降 → 首字更早    partial 263→86，e2e 首译 188→52ms
  ② 片段合并 → 识别上下文更完整 → 识别更准
     A「Sesh」/「into what it」错  →  B「Sash」/「until when it」对
  ③ 原假设「更长片段→更好翻译」**已证伪**：片段变多变短
  内容不丢：交付音频 90.0s→99.2s，ASR 词数 264→268（离线逐段判）
  代价：partial 每段 11.1→4.0，prefix 覆盖率 78%→61%
  ⚠️ ① 是延迟机制、② 是准确度机制，**分开记**——将来可分别被否证
  ⚠️ ② 闭环了 REPORT 21.6：碎片输入**先让识别差**，收益在 ASR 阶段就发生
  ⚠️ 只有 e_3min 在线跑过。blog/er/meeting 只有离线结论

新旧的唯一差异是 MAX_UTTERANCE 20000→12000
  前提：对比 pre-merge-2026-10-07 (6f84d9d) 与 e763151。改动 gemma-audio 之外的东西则不成立

折叠去重改 82,522 条中的 140 条（0.17%）
  前提：旧时间轴的 82,522 条存储文本

字幕侧折叠把 '哈哈哈哈哈哈' 削成 '哈'
  前提：CHAR_RUN_MAX=3。这个上限对不同长度行为不一致（4 个削到 1，10 个削到 3）
```

---

## 线 B 结案：显示层没有可做的问题（2026-10-08，REPORT 31/32/33）

```
grow  追加粒度中位 1 字、p90 2 字    → 已逐字，没有粒度问题
swap  占 18~35% 的 paint，但 89~99% 换到的是**从未显示过**的状态
                                   → 是推进或修正，不是抖动/回吐
char  占 1~6%，其中 **51~69% 是缩字** ← 这就是"3-4 个字块撤回"
         幅度 1~8 字、集中在 3~5 字
         每分钟 2.0（e_3min）/ 6.8（blog）/ 7.2（meeting）
队列代码 从未存在于 git 里（所有分支 + tag 零命中）→ 若要做只能重写
窗口     高度随行数变（1行→2行时整体上移约 35~40px），
         但 e_3min 一整段只发生 **4 次**（222 次 paint 的 2%）→ 不是闪烁
间隔     17~27% >1s、5~9% >2s、p90 1.8s、e_3min 最长 11.24s
```

**"字块撤回"的来源是 ASR 改口（来源 A），不是上下文注入。**
「同一句原文被重译」在 8 次运行 / 4 条素材上 **0 次**——
`PARTIAL_STABLE_REPEAT=1` 的结构决定了每个 utterance 只译一次、之后只增长。
**所以 ctx=0 vs ctx=2 的 A/B 不必做**（REPORT 33.1）。

**归属链条**：gate ↑ → 每段 partial 少 → 识别器改口机会少 → `char` 缩字少 → 字幕不抖。
**⇒ #19（gate 500）是这个体感项的候选解之一**；加上 ④ 节奏那条，共两条理由。

**B2（定速逐字输出）打偏了**：它要平顺 `grow`，而 `grow` 已是 1~2 字/次；
可见的跳跃是 `swap` 与 `char`，而定速输出**不动这两个**——它们由内容决定。

**⚠️ 全部是代理量。**如果用户看视频仍然觉得闪，**那是这些数不够，不是代码没问题**
（AGENT.md 体感优先）。而且**用户看的是哪条素材未知**：
若看的是 `e_3min`（撤回 2.0 次/分），体感可能主要来自 ④（停住 1.8 秒）而不是撤回。

---

## #6 的决定（2026-10-08）

**采纳合并臂（gate 1000 / merge window 2000）。逐句核对：没有整句漏掉。**

```
整句核对（词集覆盖率 <34% 判无对应）
  A 有而 B 完全缺失   0 条
  B 有而 A 完全缺失   1 条 = A 两句的合并 + 一句可疑内容
  去重词量            A 172 → B 189
  B 顺手把 A 的两句碎片并成完整句，且更准
     A  I'm sorry, Sesh. / I'm just playing a bit of a suck.
     B  I'm sorry, Sash. I'm just playing a bit of a suck.
     A  climbing up into what it wasn't   →  B  climbing up until when it wasn't
```

**代价是明确的，不是零**：prefix 覆盖率 78%→61%（数的是 partial 级命中，
而 B 的 partial 少 67%，两者机制上绑定）；B 另有 2 句重复上屏。

**默认值还没翻。** 闸 3 已跑完（REPORT 30）：
```
blog_3min      过    最终句 16/16 持平，e2e 710→509ms，prefix 78%→94%
e_3min         过    最终句 12/21 持平，e2e 188→52ms，prefix 78%→61%（代价）
meeting_6min   不过  最终句 90/140→83.5/140（臂内散布 1~2，臂间 6.5 → 真的）
                      e2e 判 VOID（臂内散布 4~8 倍，n=16 分辨不出 150ms 门槛）
```

**为什么不过**：`min_speech_ms` 同时是 **partial 的门槛**。抬到 1000ms 后，
本来就短的段落在闭合前发不出任何 partial，于是**整句不进翻译**——
不是译文变差，是句子没出现。`meeting_6min` 的 `asr_ratio p50 0.3`
正是"识别器本来就吐不出多少文字"的情形，门槛抬到 1000 对它雪上加霜。

**⚠️ 预登记的风险打错了指标**：我给 meeting 单列的 M5 是 prefix 覆盖率，
理由是"partial 砍 67% 后覆盖率可能跌到不可用"。**覆盖率反而涨了 3 点。**
真正掉的是最终句数。**prefix 覆盖率对"句子根本没出现"不敏感。**
判据要能看见你担心的那个失败形态——我把上一份素材的失败项当成了本份的风险指标。

**⇒ 下一步是 gate 取中间值 500 / merge window 1000**（队列 #19），
不是再随机试一个数：1000 太钝（伤 asr_ratio 低的素材）、
250 太碎（e_3min 上 prefix 掉 17 点），500 是两条失败各自机制给出的区间。

### 「风扇会变安静」是推断，不是实测

ASR 调用少 67% ⇒ GPU 占用时间少 ⇒ 平均功耗应当下降。**这一条没有测过**，
且按已定的边界：ASR 上 GPU 的全部成本是 **+10W**，而插电形态下 +10W 只是
风扇曲线上的一个点。所以**即使这条推断成立，也不改变已关闭的功耗测量线**。

不要把它写成结论。它是「如果哪天改回电池形态，第一件该先验的事」。

**为什么不能从"调用少 67%"直接推**：+10W 是"负载运行时 vs 空转"的差，
而调用变少改变的是**占空比**。功耗还取决于频率、内存时钟、以及是否有其他负载
并发——REPORT 17.4 那次错就栽在窗口上。所以这条要验，不能推。

### 本机可读与不可读的温度

```
GPU 温度   ✅ nvidia-smi --query-gpu=temperature.gpu   精度 0.1°C，无需安装
           已接进 replay.ps1 的 run.<name>.meta（连 power.draw /
           throttle_reasons / utilization 一起，采样在回放之后 = 静止态）
CPU 温度   ❌ **本机读不到**
           MSAcpi_ThermalZoneTemperature 返回空/类不存在
           （与之前"0 个 ACPI 热区"是同一个缺失）
           Windows 没有通用的用户态 CPU 温度 API；
           WMI 的 Win32_TemperatureProbe 在 Win10/11 基本无效
           只有第三方（Open Hardware Monitor / HWiNFO64）能读，需管理员权限
           且笔记本 EC 兼容性因品牌而异
```

**记读不到，不找代理量代替。**与 `fan.speed [N/A]` 放在同一类里。
CPU 温度**不作为阻塞项**——插电形态下它本来也只是参考量。
`nvidia-smi` 的温度 + 功耗 + 是否降频三个量合起来已经能回答
"#6 让 GPU 更忙还是更闲"。

---

## 🚩 待复核：REPORT 24~27（431686e 之后）

**这些数字不是错的，是前提没被保证过。**标出来是因为它们**看起来**是可引用的——
表格齐整、口径写明、和前面几节对得上——而这正是最容易被直接拿去用的情况。

```
受影响   REPORT 24 / 25 / 26 / 27 的全部回放实测数字
原因     那几节的回放没有由脚本保证 TRANSLATION_AUDIO_SOURCE=socket
         前提只以"操作者要敲的一行"写在 PLAN.md 里（28.2 第 4 条）
两种可能  (a) 当时那个 shell 里确实设了 → 数字有效，只是前提当时靠人记
         (b) 没设 → 那些运行是"干净但零事件"，而它们带着非零的数字，
                 所以 (b) 大概没发生——但**这是推断，不是核对**
为什么不能直接判废
         一个 vad.end=0 的运行不会产生 asr p50 286~316ms。
         数字非零 ⇒ 那次运行确实有语音进来 ⇒ 大概率是 socket 注入生效了。
         仍需重跑确认，因为"非零"能排除的失败形态有限。
重跑成本   四条素材 × 一臂 ≈ 20 分钟（blog 187s / e 184s / er 206s / meeting 361s）
怎么算复核完
         重跑后与 REPORT 27 的表逐格对照。差异 ≤10% 视为口径一致并解除标记；
         差异更大则说明当时的环境与现在不同，改口径而不是改数字。
```

**解除标记前，引用这四节的数字必须带上这行前提。**

### 🚩 REPORT 22（#6 的离线依据）同样待重算

**原因不是前提缺失，是数算在了错误的 population 上。**
`probe_vad_gate.py` 只读 `perf` 行，而合并臂里挂起一个片段会写一行 `vad.end`
却没有 end 事件。REPORT 22 报的"合并后 37 段 / 保留语音 79.3s"里，
**37 段有 13 段不存在**，真段数是 24（REPORT 29.2，离线与在线两条路都验过）。

因此 22.3 / 22.6 的方向仍然可信（**挂起比直接丢弃多保住语音**，这条由 ASR
逐段判过：B 交付音频 99.2s vs A 90.0s），但**具体数值不可引用**。

```
#6b  修埋点（队列 #17）→ 重算 REPORT 22 的表
```

### 部分解除（2026-10-08）：只对 blog_3min 与 e_3min 成立

逐条对 REPORT 27 的最终句：

```
blog_3min      REPORT 27 = 16/16     本轮 A = 16/16              ✅ 复现
e_3min         REPORT 27 = 12/21     本轮 A = 12/21              ✅ 复现
meeting_6min   REPORT 27 = 83/140    本轮 A = 91/140 与 89/140   ❌ 不复现（差 6~8 句）
```

**meeting_6min 那条维持未解除**，而且现在多了一个新解释：那次运行与本轮之间
存在某个**未识别的差异**，不只是"前提靠人记"。

⚠️ **别把两件事混起来**：`83/140` 恰好等于本轮 B 臂的 `83/140`，
容易读成"REPORT 27 其实跑的是合并臂"。**没有证据**——那次的 partial 是 250、
门槛是 250，与 B 臂的 meta 不符。更可能是巧合，或这个指标本身容易落在 83 附近。
**记下来，不解释。**

**部分解除（2026-10-08）**：#6 的 A 臂在 `e_3min` 上**完全复现了 REPORT 27 的
最终句 12/21**。这说明那次运行的前提确实成立（socket 注入当时是生效的），
上面的推断得到一次实测支持。**仍未解除**：另外三条素材没有重跑，
且下面的 `vad.end` 缺陷会让任何按日志读段数的对照失真。

---

## 队列

**2026-10-09（08:00–09:00）结案的：先修工具，再读数据。**

| # | 做什么 | 判断标准 / 结论 |
|---|---|---|
| **49-a** | manifest 的 `wav` 生产端（`blog_video_test.py:469` 记 `WAV_PATH` 常量，而跑的是 `_sample_wav()`） | **已修。** 现在同时记 `wav` 与 `timing`，两者不一致就拒绝出数——**从 `wav` 推导抓不住说谎的 `wav`**，谎称 blog_3min 会解析到一个真实存在的文件 |
| **49-b** | `perf_compare` 只处理裸 list，而 `build_timing.py` 产出 dict | **已修。** 喂 dict 会抛 TypeError ⇒ **这条路径从来没被真正执行过**，唯一被读过的文件正好是唯一能被读懂的那个形状 |
| **51** | coverage 差 5 个点的归因（用户给的顺序，先数 `asr by kind`/`enqueue end`） | **路径 B 证伪**（VAD 切分逐位相同，31/31、25/25、11/11）；**路径 A 也不是**（14 条 drop 全是 `kind=partial`，丢的是中间帧）。真链：截断 A 0 / B 5 → `asr_finals` 25 / 23 → coverage 96% / 91% |
| **53-a** | 同一配置连跑两次测散布 | **可复现**：coverage 21/23、asr by kind 137/11、window 207.1s。**不可复现**：`audio_end_to_paint` p50 548↔705、>1000ms 29%↔38%；e2e、dwell 同类 |
| **53-b** | 抬上限前先把判据做成能读的 | `asr.truncated` 现在带**有界的 `head`/`tail`**。**数字式的重复度分数试过并撤掉**：给「引述自己」的长转写打 0.75、给重复循环打 0.89，分不开 |

**⚠ 53-a 撤掉了 48-c 的部分结论**：`audio_end_to_paint` 的 A/B 差值
（690 对 548）**落在同配置连跑的散布之内，作废**。`2.47 的 600 对 2.53 的 548` 同理。

**所以 A 与 B 唯一站得住的差异只剩 coverage 一项**（96% 对 91%），机制已定位
（128-token 上限，5 次截断 ⇒ 2 个 utterance ⇒ 少 2 条终稿，1:1）。
**B 的判决仍是 `INDETERMINATE`——不是因为说不清，是因为要判的那一项需要重复测量。**

**仍然不要做的：** 不建阅读地板（48-b 失败条件已触发，地板是空操作）、不调 `hold`
（结构上够不着）、不动 `partial_interval_ms`（#19 已证）、不动 `_is_char_edit` 的 0.72、
**不引用单次运行的 `audio_end_to_paint` / `e2e_first_to_paint` 差值**、
**不再调 128 上限**（REPORT 54/55：治不了，病在识别侧；且「B 退化更严重」在文本层面是反的——内部重复 B 2.5% 对 A 6.0%）。
⚠ **出货 build 已浮到 2.55.0（今天 08:52 装的），它在任何东西上都没被测过**，而所有数在 2.53.0 上。`runtime.json` 的 `llama_version=2.53.0` 是机器级 pin，保数据可比；要不要在 2.55.0 上测一次**待用户定**。

### 队列（2026-10-09 收工时的真实状态）

| # | 做什么 | 状态 / 判断标准 |
|---|---|---|
| ~~1~~ | ~~`TRANSLATION_QWEN3_ASR_MAX_TOKENS` 128 → 256~~ | **已做，判据四步走完（REPORT 54）。** 守卫放行（206.9s）→ truncated 4 条（不是 0）→ coverage 21/23（第三次逐位相同）→ `head`/`tail` 确认重复填满。**上限不是太紧**：抬上去没改任何一项，只把丢弃的垃圾从 193 变成 363 字符 |
| **1'** | ~~为什么这 2 个 utterance 让语言判定出错~~ | **已做，REPORT 55，而且前提是错的**：语言一直在日志里（`pipeline.py:281` 的 `lang=`），从没卡在工具上。
| **1'a** | **B 把日语系统性地写成汉字并标成中文** | **未做，这是 B 侧的真缺陷。** zh 占 21.7%（A 0.7%），34 条 zh 文本**全部无假名**。素材 ja 53%。与 1' 的两个 utterance 退化是**两件事，修法不同** |
| **1'b** | 「B 跳字」与上面这条的连接 | **未验证，而且方向是反的**：hold/char 计数 B 5~6 对 A 7。缺 `caption.paint` 的文本——**同一件事第三次卡住**（见 REPORT 50/53-b/55） |
| ~~2~~ | ~~`replay.ps1` 播放被中断时应当报错~~ | **已做。** 第三道守卫：播放距离 vs clip 长度（80%），**不用 coverage 判断**。`B256` 曾在 34.2s/180s 上报 `coverage 5/5 (100%)`。6 次回放 3 次被掐断，三次的秒数写进了错误信息 |
| **3** | 回放中断的成因 | **未做。** 掐断的是到字幕窗口那条 WebSocket，6 次里断 3 次（11.0s / 48.6s / 34.4s），**同一个签名**。守卫只负责不让它出数，没找原因。这是**当前所有测量的头号障碍** |
| **4** | `audio_end_to_paint` / `e2e_first` 要重复测量才能引用 | **未做。** 同配置连跑散布 157ms / 9 个点（53-a），所以单次运行的 A/B 差值全部作废。要引用就每个臂至少 3 次 |
| **5** | 「B 跳字」的机制、块状 vs 流式 | **未做。** 假设已记（REPORT 50），**日志缺每次上屏的增量与文本**，所以不可测。显示层，与识别侧那条链无关 |
| **6** | `e_3min.wav` 上的 A/B 对（真实语音压在配乐下） | **未做，从头到尾没跑过。** 你最初列的三项测量之一。`lecture_mix` 是合成的，而它上面的 coverage 差距已经归因到识别侧——**真实素材上会不会一样，没测过** |

**A/B 在 2.53.0 上「同时重测」这件事，严格说从未完成。** 跑过 4 次，但全部是单次运行，
而 53-a 已经证明单次运行的差值落在散布之内。**所以 B 的判决仍然是 INDETERMINATE，
不是因为说不清，是因为要判的那一项还没被重复测量过。**

## 队列

| # | 做什么 | 判断标准 |
|---|---|---|
| 1 | 合并 `gemma-audio` → `main` | **已完成**（`6f84d9d → 56ce447`，tag `pre-merge-2026-10-07`） |
| 2 | 错误分桶：真实录音录屏回放，记 15~20 条 | 见 `samples/BUCKETING.md` |
| 3 | 时间轴加 `covered_span` 字段 | 回放前必做，否则 e_3min 前 31.5s 会被算成"未覆盖" |
| 4 | partial 250 → **750 ms**（两格都录屏） | 风扇安静？字幕慢？**partial 阶段文本是否更完整？** |
| 5 | 显示层 `settle_until` 单因素（`TRANSLATION_SETTLE_MS` 可注入） | 上屏首字 / 被挡 / 覆盖率 |

**本轮新增（2026-10-07 晚，REPORT 21 有数）：上下文注入已接上并测过，质量提升是真的；
但对照 LiveTranslate 之后，**切片下限**成为翻译质量的第一嫌疑，优先级高于 #4。**

| # | 做什么 | 判断标准 |
|---|---|---|
| 6 | **切片下限：挂起并合并**（不是抬门槛丢弃）<br>`TRANSLATION_VAD_MIN_SPEECH_MS=1000` + `TRANSLATION_VAD_MERGE_WINDOW_MS=2000` | ✅ **已采纳为决定**（2026-10-08，REPORT 29 + 逐句核对）。**默认值尚未翻**——见下方「#6 的决定」 |
| **17** | ~~`vad.end` 日志在合并臂下不是段数~~ | **已完成**（REPORT 29.8）：加 `delivered=0/1`。三条独立的路（事件配对 / `vad.end − vad.held` / `delivered=1` 计数）在真实音频上给同一个数 27/24。**不能用那个减法**——它依赖"非合并臂 held 恒为 0"，而 `vad.held` 在默认臂根本不产生 |
| **18** | 追 `speech_ms > buffer` 的根因 | A 臂 10/27、B 臂 9/24 出现"报告的语音时长大于交付的缓冲区"。A 臂没有挂起逻辑，**所以与 #6 无关**，是既有问题。而 REPORT 多处拿 `speech_ms` 当"交付了多少内容"的代理。#17 修完后同一数据源更好查 |
| **19** | ~~gate 取中间值 500 / merge window 1000~~ | **已完成，M5 失败，不翻默认值**（REPORT 34）。M1 三条都过（meeting 掉 2 句 < 臂内散布）、M2 两条可测的过（blog +119ms）、M4 三条都改善 7~9 点。**但 M5（char 缩字/分钟）在 250/500/1000 三档全平** ⇒ `min_speech_ms` 只推迟第一条 partial、不减少条数，对撤回没有杠杆。**撤回的旋钮是 `partial_interval_ms`（#4），不是 gate** |
| **20** | **撤回（char 缩字）的两个真旋钮**——#19 证明 gate 不是 | ① partial_interval_ms 250→750（队列 #4）：唯一真正减少改口机会的旋钮，每段 partial 约 11→4 条；② 显示层对 char 动作的策略（#5 一带）：限流或 settle 期内合并。⚠️ 先看密度：char 缩字只有 2.4~7.2 次/分，够不够被注意到由用户判断 |


| 7 | **ASR 后过滤 + ≤8 字符短句合并**（无损，不动 VAD） | 碎片条目数 |
| 8 | 上下文条数 2 → 6 是否有额外收益 | prefill 代价 +30.7ms，换到多少质量（REPORT 21.4 只有 2 与 6 两档） |
| **9** | ~~真实管线的 VAD 微碎段~~ | **已结案**（REPORT 23）：**回环采集与 socket PCM 混进同一个 VAD**。`TRANSLATION_LOOPBACK=0` 下 48 段/96ms → **31 段/1824ms**（探针 32 段/1632ms）<br>⚠️ **那个布尔开关已经不存在了**，见 [[#6 的决定]] 后的采集段。设了也没用 |
| **11** | ~~回环在服务器进程内没送达 VAD~~ | **已结案**（REPORT 26）：守卫放错层——`push_pcm` 是回环唯一入口，却被我用来拒绝"所有音频"。修后 `vad.end 6 / enqueue 113`，实测译出"再见" |
| **12** | ~~删掉死代码 `extension/pcm-worklet.js`（无任何引用）~~ | **已完成**（`431686e`）：确认无 `addModule` / `AudioWorkletNode` 引用后删除。顺带发现上一轮往这个没人加载的文件里写了 13 行 `captureEnabled` 门控 |
| **13** | ~~`start.ps1` 里过期的 MTP 注释~~ | **已完成**：按版本重写（2.47 会 503；2.53 不会但净负 49.6 vs 59.2 tok/s） |
| **14** | **片段长度 ↔ 首字延迟的取舍**（REPORT 27.3） | 12s 片段 e2e p50 970ms；2s 片段 104ms。软切上限是否该按"内容类型"调 |
| **15** | 锁定 `en` 重跑四条素材 | 现有旧基线（asr 占墙钟 16.6%、首字 374~756ms）是 en + 无上下文 + 2.47，与本轮前提不同，不能相减 |
| **16** | **`runtime.json` 与实际读取它的键对不上** | `start.ps1` 读 `llama_pinned_path`、`resolve_llama_runtime` 读 `memory_total_mib`，而文件里**两个都没有**——只有旧写入者留下的 `llama_server`，以及一个指向不存在模型的 `gguf`。显存数字来自旧探针。修法是重探卡，属测量不属清理 |

**#6 的形态已被证据改过两次**：原计划"门槛 250ms → 1.0s"会**多丢 4.9s 真语音**（REPORT 22.2），
所以改成"挂起并合并"；现在连它的**假设**也被实测改掉了——"更长片段→更好翻译"
不成立，实际是"更少中间态换更快首字"（REPORT 29.5）。
且合并有窗口上限、**默认关闭**（`VAD_MERGE_WINDOW_MS=0`），要开必须两个一起设。

**#7 是无损的，可以独立于 #6 先做。**

**#6/#7 是同一族改动（都在"切分/送翻译之前"），要么一起做，要么先做 #6。**
**#6 不要与 #4/#5 同时做**，否则分不清是切片下限还是 partial 节奏造成的差别。

**#4 与 #5 不要和别的改动同时做**，否则分不清是哪个造成的差别。

### #4 的观察点，以及它对 #5 的影响

```
原计划   ASR p50 / 首字 p50 / 风扇（耳朵）
新增     partial 阶段的文本质量 —— 回看用 samples/PARTIAL_QUALITY.md
         只盯"整句说完之前屏上出现过什么"，逐句并排比 250 / 750
```

**理由**：TTS 最干净的条件下，small 在短窗口上**没有一次给出完整正确的输出**
（0.3s 空、0.6s 认错、1.3s 截断），而 large 全部正确。管线喂给 ASR 的
恰恰就是这种窗口，所以 750ms 让窗口更长，**可能顺带改善 partial 文本质量**。

**⚠️ 但 #4 可能让 #5 变得更急，不是更不急。**

```
假设（错的）：#4 让字幕更准 → 显示层更轻松 → #5 不急
实际可能是：partial 更新变稀疏 → 屏上文本停留更久 → hold 次数上升
            → 被顶掉的句子变多
```

**方向可能反。**新内容更稀疏对显示层是**负担**不是减压——hold 的定义就是
"同一句停留多久"，更新越少停留越久。

**所以 #4 之后必须重看三个数**（第 14.4 节的门槛）：

```
hold p50        门槛 ≤800ms    当前 1012ms
hold p90        门槛 ≤1500ms   当前 2011ms
被覆盖句数       门槛 ≤15      当前 33
```

**如果 #4 之后 hold 变差，#5 从"不急"变成"更急"。**

## 待验证（不排期，触发才动）

| 项 | 触发条件 | 现状 |
|---|---|---|
| **音频直出端到端** | #4 之后风扇仍吵 | **测试未结束，不下结论。** 保留为可选低功耗模式。省电 71% 来自取消 partial，仅 8% 来自换模型 |
| **Gemma 换翻译**（非直出） | 自用后译文读着别扭 | 翻译不是瓶颈（ttft p50 30~36ms）。有代码与显存代价，需改提示词模板 |
| **ASR 换 large-v3-turbo**（已改，待定） | 电影/播客识别率不够 | **已改为默认，但 250ms 节奏下不可用**：`asr share of wall clock 96.0%`、`asr >250ms 100%`、`asr p50 821ms`、GPU 91W/75~77°C。识别质量确实到位（`er_3min` 16/16、coverage 84%）。**无 small 对照**——`logs/v1-er_3min.*` 窗口只 20.9s 且 GPU 上有别人的 llama-server，不可用。⇒ **自洽组合是 turbo + #4（750），不是 turbo 单独** |
| **pysbd 分句** | 译文常是半句 | **只放 final 路径，不放 partial。**放 partial 必须等句末才能翻 ⇒ 直接打在「慢半句」上；放 final（VAD 收尾后切句）质量提升不付延迟代价。`pysbd` **未装**，先别装 |
| **Hy-MT2 1.25-bit** | 显存爆了 / 要加新模块 | `Hy-MT2-1.8B-1.25bit-v2.gguf` 440MB 已下载，**但加载不了**（`tensor blk.0.attn_k_norm.weight has offset 203154464, expected 203572156`）。只省显存不提质 |
| **hy-mt2 的 MTP 净收益** | —— | **不成立，不排队**（见上方「MTP 这条线到此为止」）：Q4_K_M 权重里**没有任何 MTP 张量**（354 个张量 = 32 层 dense + embedding + norm），不是"未测"，是开不了 |

## 停止（每条带触发条件，别无条件复活）

| 项 | 理由 | 触发条件 |
|---|---|---|
| **whisper.cpp 后端** | CPU 单次 400~1260ms，跟不上 250ms 节奏 | 不换 ASR 就已堵死。**另需 CPU 侧功耗传感器**才能重估 |
| **large-v3 实时** | 显存 3954 MiB 与 hy-mt2 不能共存；222.wav 上并不更准且有幻听 | 显存不再是约束时 |
| **功耗测量线** | 插电形态，+10W 只是风扇曲线上的一个点 | 部署形态改为电池/无人值守 |
| **langlock 语种锁定** | 模块就绪（20 测试），卡在人工标注 | **需求先确认**：看完多语种素材，若手动选语种就够，则永久搁置 |
| **折叠去重修复** | 见下方待办 | 错误分桶之后 |

### whisper.cpp 关闭理由（区分实测与推断）

- **实测**：CPU 单次 400~1260ms（ctx 220 静默 576ms），进不了 250ms 节奏
- **实测**：ASR 上 GPU 的真实成本 **+10 W**（此前 +0.11W 是采样窗口错位，见 REPORT 17.4）
- **推断，未实测**：whisper.cpp 模型文件本身省显存（Q5_1 −284 MiB）、GPU 单次快（74ms），
  但整套后端（独立进程 + HTTP + Python 集成）是否让整机功耗反升，**从未测过**。
  CPU 侧无传感器，测不了。

### langlock 关闭依据（已更正）

**旧依据是错的**：曾写"检测在短片段上不可信"——那来自 **CT2 small** 的分片检测，
检出的 `ja/sl/bn/de` 里混着幻觉。

```
CT2 small         ja18/39，但含 sl / bn / de 幻觉      → 检测不可信
large-v3-turbo    ja18/39，ja 置信度 0.88-0.96，无幻觉 → 检测干净
```

**所以"检测在短片段上不可信"不成立**，成立的是"**small 的**分片检测不可信"。

**仍然不排期**，理由换成更朴素的一条：**默认链路用的是 small**，
要解决得先解决"默认链路的检测"，而不是先建锁定机制。
（REPORT 19.4 记为一次被错误前提带偏的判断。）

---

## 待办

```
1. 时间轴加 covered_span              回放前必做
2. 折叠去重：'哈哈哈哈哈哈'→'哈'      错误分桶之后
   原因：疑似来自 main 原有翻译侧"五次以上折成一个"规则，非本次合并引入
   修法：改为封顶 3，加哈/啦/啊单元测试
```

## 模型定位

```
large-v3-turbo / whisper.cpp large   仅离线文本校验，不进流水线
CT2 small                            唯一在跑的默认链路
whisper.cpp small Q8                 需要看音频"到底说了什么"时用
whisper.cpp small fp16               舍弃
```

**large 是对照，不是基准**——它能把日语解码成 `これは日本語で病風と呼びます`
这样干净的结果，所以能用来定位 small 漏了什么；但仍不能当真相。

## 素材时间轴（2026-10-07 全部重建）

四份原本用 `language='en'` 强制生成，**含非英语的素材因此整份是编的**：

```
meeting_6min_timing.json  165 句，含日/韩/汉字/西里尔/阿拉伯字符：0
er_3min_timing.json       26 句，含西里尔字符 0 句（"Shul shul" 是被译成英语的俄语）
clip222_90s_timing.json   原本不存在 → 222 当时根本没法回放
```

已用 `scripts/build_timing.py`（分片级判语种）重建。格式：

```json
{ "audio_total": 184.3, "covered_span": 67.6, "segments": [{i,start,end,lang,src,p,text}] }
```

| 素材 | 段数 | 覆盖 | 语种 |
|---|---|---|---|
| `e_3min` | 21 | **37%** | en21 |
| `er_3min` | 42 | 72% | en39/ru3 |
| `clip222_90s` | 33 | 81% | ja18/en8/ko4/zh2/it1 |
| `meeting_6min` | 140 | 76% | 11 语种 |

**`e_3min` 只有 37% 覆盖**，184 秒电影里只有 67.6 秒被判为可译语音。
可能是 VAD 在带音乐床的素材上过于保守，也可能是那段确实以配乐为主。
**判断方法是人耳听**——先别改 VAD，改 VAD 会影响所有素材。

覆盖率**口径未变**（仍是译出句数比），`covered_span` 只是让分母可见。

## 仓库

```
f93de9d  Keep diff_defaults.py
e763151  Rebuild the sample timelines
042f13e  Separate the notes by what has to be read
56ce447  Print which tree this process is
2598379  Stop tracking media files
main / HEAD / gemma-audio  同指（已快进）
回退点   tag pre-merge-2026-10-07 -> 6f84d9d
```

`gemma-audio` 保留。pytest 99 passed。`extension/` 与 Native Host 未变，主机无需重建。

**2026-10-07 晚更新**：pytest **107 passed**（新增 `tests/test_translation_context.py` 8 条，
钉住"上下文真进提示词"与"ctx=0 逐字节等于改动前"）。`extension/` 与 Native Host 仍未变，
主机无需重建——`start.ps1` 是文件调用，不在 exe 里。


## 实验日志（一行一条）

| 日期 | 问题 | 结果 | 决定 |
|---|---|---|---|
| 10-07 | ASR 放 CPU 能省 GPU 电吗 | GPU 侧 **+10W**（原 +0.11W 是错窗口）；CPU 单次 400~1260ms | 关 whisper.cpp |
| 10-07 | audio_ctx 能否缩窗提速 | 不缩放；<150 触发 5 秒级病理 | 加下限 150 |
| 10-07 | 量化能否省显存又提速 | GPU 不提速；Q5_1 省 284 MiB | 记下，未采用 |
| 10-07 | 语种锁定能否用置信度触发 | 置信度单峰，无 >0.9 段 | 不按置信度触发 |
| 10-07 | 边界处错语种解码有多糟 | 91% 垃圾；译文一致+相关 **25%** | "先上屏后覆盖"被证伪 |
| 10-07 | C 的软切在 whisper 链路生效吗 | **生效**（`why=soft_cut` 实测） | 12s 上限是唯一新旧差异 |
| 10-07 | 字幕侧折叠影响多少存量译文 | 82,522 条中改 140 条（0.17%） | 不阻合并，修复排待办 |
| 10-07 | 素材到底是不是多语种 | **222: ja18/en8/ko4/zh2/it1**<br>**会议: 11 语种** | 旧描述全错，已更正 |
| 10-07 | 强制 en 生成的时间轴可信吗 | 会议 165 句零非英语字符 | 四份全部重建 |
| 10-07 | 旧 e_3min 时间轴差在哪 | 不是语种，是切分：无 VAD | 新版 21 段干净 |
| 10-07 | 插件"开始"按钮为什么起不来 | `start.ps1` 解析就失败（第 98 行 `} else {` 错位），脚本一行未跑；扩展侧只看到 90 秒超时 | 修花括号 + 加解析自检；临时改为最新 llama.cpp 2.53.0 |
| 10-07 | 上下文注入影响有多大 | hy-mt2/hy-mt2+ctx 上质量确有提升（final pitch→推销、right check→右转、summit→登顶）；代价 ctx2 +15.4ms、ctx6 +30.7ms prefill | 接上上下文（此前被 `del context` 丢弃），默认 2 条 |
| 10-07 | Gemma4 能不能当文本翻译 | E2B 36 层上 32 层 + MTP 草稿可行（VRAM 2337 MiB，50 tok/s），译文质量好于 hy-mt2，但每行慢 2.5~3 倍 | 可用，是否转默认待定（REPORT 21.5） |
| 10-07 | LiveTranslate 怎么用 ASR | VAD 切分 + spawn 子进程 + 整段一次识别 + 短段并入下一句 + 10 轮上下文；**它也不降噪** | 可借鉴的是切片下限与短句合并，见队列 #6/#7 |
| 10-07 | 把 VAD 门槛抬到 1.0s 会怎样 | 额外丢 8 段 / 4.9s **真语音**（那些段带 p=0.99 的真实文本）；LiveTranslate 的同名参数是"并入"不是"丢" | 改成挂起并合并；门槛默认仍 250 |
| 10-07 | 挂起并合并值不值 | 无损：保留语音 +4.1s（e_3min）/ +2.1s（er_3min），保留 p50 不恶化 | 默认关闭，`VAD_MERGE_WINDOW_MS` 开启；待真实回放确认 |
| 10-07 | 真实回放能不能验证 #6 | **不能**：同一份音频离线 32 段/中位 1632ms，真实回放 52 段/中位 64ms，ASR 33 次全 `chars=0` | 记为新队列 #9，优先级高于 #6 |
| 10-07 | 脚本里的端口/进程探测可信吗 | 不可信：`Get-CimInstance` 与 `Get-NetTCPConnection` 都基于 WMI，受限会话拒绝访问；后者被 `SilentlyContinue` 包着被读成"没监听" | 新增 `scripts/probes.ps1`，改用 socket 与 `Get-Process` |
| 10-07 | `conda activate` 在脚本里生效吗 | 不生效：`cmd /c call conda.bat` 的子进程不回传 `CONDA_PREFIX`，回退到 base 环境（无 websockets） | start.ps1 改为按路径解析解释器 + import 预检 |
| 10-07 | #9 回放的 VAD 为什么比探针碎 25 倍 | **回环采集默认开**，`loopback.py:57` 把系统扬声器音频推进**同一个 session**，与 socket PCM 交错喂 VAD。回放不播声音 → 一半是静音 | 加 `TRANSLATION_LOOPBACK`（默认 1 不变）；`=0` 下 48段/96ms → **31段/1824ms**，与探针一致。⚠️ **该开关已被 `AUDIO_SOURCE` 取代**（431686e 后只剩增益调优那几个变量） |
| 10-07 | 回环对队列延迟的影响 | `pipeline.asr qwait_ms` 中位数 **113ms → 6.4ms**（关回环）。回环灌的静音让 VAD 产出假 partial 填满队列 | 历史 qwait 读数需在 `LOOPBACK=0` 下重测 |
| 10-08 | 能不能整体改走 WASAPI 回环 | 可以：`soundcard` 已装、设备匹配对、直连录放三次 peak 都 0.5137；但**进程内**两次结果相反（有电平不出段 / 连电平都 0） | 架构已切，**端到端未通过**，见 REPORT 24 |
| 10-08 | 回环衰减是 −26dB 吗 | **不是**。单音测得的 0.024 被直连语音测试推翻（0.8752 → 0.5137，约 4.6dB） | 不写死增益常数，改运行时自测（收敛到目标 0.20） |
| 10-08 | 扩展前端在做音频采集吗 | **不做**。`pcm-worklet.js` 无任何引用，`offscreen.js` 只有 WS + 控制消息 | 记为待删死代码（队列 #12） |
| 10-08 | 2.53 的音频路径 MTP 可用吗 | **可用**：5 个音频请求全 200，`draft=0/30,1/15,2/18,2/30,3/12`。**16.2 的 503 是 2.47 的行为** | 但净负（49.6 vs 59.2 tok/s），音频路径仍应关 |
| 10-08 | 音频直出为什么在 e_3min 上崩坏 | 不是 MTP/层数/投影/时长/增益/提示词/采样率——**是素材**：对白被响配乐压住时，whisper 听对、Gemma4 音频听成"大鸟" | 边界记入 REPORT 25，配乐素材走 whisper+文本 |
| 10-08 | 回环为什么没送达 VAD | 守卫放错层：`push_pcm` 是回环唯一入口却拒绝了全部音频 | 修后 `vad.end 6 / enqueue 113`，#11 结案 |
| 10-08 | 四条素材的实时指标 | asr p50 286–316ms（与素材无关）、ttft p50 30–35ms、qwait p50 40–84ms（#9 修复后） | 基线记入 REPORT 27；旧基线前提不同不可相减 |
| 10-08 | 代码里有多少无效 import / 死代码 | pyflakes 5 条（含 `pipeline.py` 重复导入 `chinese_share`）；全仓计数后 `server/` 清空：`server/utils.py`（无人 import）、`server/mt.py`（写死 False + 模型不在）、`config.py` 17 个常量、硬编码的 llama 版本候选表 | `431686e`。`GEMMA_TRANSLATOR_*` 是**第三份** `TRANSLATION_GEMMA_*`，而 `start.ps1` 读的是环境变量那份 |
| 10-08 | 显示层有没有契约测试 | `_self_check()` **没人调用**，而且**会失败**（两条期望早于 `speech_hold_factor`）。`classify_caption`/`step_caption`/`hold_ms`/`pace_scale` 零覆盖 | 搬成 `tests/test_caption_state_machine.py`（12 条，全过）。线 B 动 hold/settle 之前先有护栏 |
| 10-08 | 冒烟测试为什么失败 | **四个真 bug，都在我的清理之外**：① `smoke.ps1` 取目录里第一个 gguf → 选中加载不了的 1.25bit；② 两处用 `Get-NetTCPConnection`（受限会话读成"没服务"）；③ `replay.ps1` 用 `Sort-Object StartTime` → `Access is denied`，放音前就中止；④ **`replay.ps1` 从不设 `TRANSLATION_AUDIO_SOURCE`**，漏敲时运行干净但 `vad.end=0`、零译文 | 全部修掉，冒烟通过（86s，24 次 ASR / 13 条译文 / 29 次 paint）。**第 ④ 条影响的是所有历史回放的前提** |
| 10-08 | 跑 #6：抬 partial 门槛会不会让字幕变慢 | **反而变快**：e2e 首译上屏 p50 **188→52ms**，audio_end→上屏 755→487ms。机制是 ASR 调用少 67%，队列压力降。代价是 partial 每段 11.1→4.0、prefix 覆盖率 78%→61% | 自动判据通过，**进体感确认**（REPORT 29） |
| 10-08 | #6 的假设（更长片段→更好翻译）成立吗 | **不成立**：片段变多变短（中位 1952→1088），不是变长。#6 真正做的是救回今天被门槛丢掉的小段 | 假设与效果脱钩，判据按实测重写 |
| 10-08 | `vad.end` 行数能当段数用吗（合并臂） | **不能**。`vad.py` 在门槛判断前 emit，挂起时写一行 `vad.end` 而**没有 end 事件**。e_3min 37 行里 13 行是幻影，真段数 24（`vad.end − vad.held` 可在线验证） | **REPORT 22 的合并数算错了 population**，标为待重算；记为队列 #17 |
| 10-08 | `speech_ms` 能当"交付了多少内容"的代理吗 | **不能**。A 臂 10/27、B 臂 9/24 出现 `speech_ms > buffer`。A 臂没有挂起逻辑，所以与 #6 无关，是既有问题 | 记为队列 #17 的邻居 #18，根因未查（时间盒到） |
| 10-08 | #6 换到 blog_3min / meeting_6min 还成立吗 | **两条素材，一条不过**。blog 全过（e2e 710→509ms，prefix 78%→**94%**）；meeting **最终句掉 6.5 句**（臂内散布 1~2，臂间 6.5 → 真的），e2e 判 VOID（臂内散布 4~8 倍） | 不翻默认值。`min_speech_ms` 同时是 partial 门槛，抬到 1000 让短段落**整句不进翻译**。下一步取中间值 500/1000（#19） |
| 10-08 | 给 meeting 预登记的风险指标对吗 | **打错了**。我单列的 M5 是 prefix 覆盖率，理由是"partial 砍 67% 后覆盖率会跌"——结果**涨了 3 点**。真正掉的是最终句数。prefix 覆盖率对"句子根本没出现"不敏感 | 换成最终句数。教训：预登记风险时，**指标要能看见你担心的那个失败形态**，别拿上一份素材的失败项当本份的风险指标 |
| 10-08 | REPORT 27 能复现吗（三条逐条） | blog ✅ 16/16、e_3min ✅ 12/21、**meeting ❌ 83/140 vs 本轮 A 91/89** | 「部分解除」只对前两条成立。meeting 维持未解除，且多了一个未识别的差异。⚠️ 别把 `83/140 == B臂的83/140` 读成"REPORT 27 跑的是合并臂"，无证据 |
| 10-08 | 本机能读 CPU 温度吗 | **不能**。`MSAcpi_ThermalZoneTemperature` 返回空/类不存在（与之前"0 个 ACPI 热区"同一缺失）。Windows 无通用用户态 API，WMI 探针类在消费级笔记本上无效 | 记读不到，不找代理量代替。GPU 温度/功耗/throttle 已接进 `run.<name>.meta`，四臂全部 throttle=GpuIdle 无降频，两臂可比 |
| 10-08 | 显示层是流式还是块状（线 B 结案） | **grow 已经是逐字**：追加粒度中位 1 字、p90 2 字。块状感在 `swap`（占 18~35%），而 **swap 里 89~99% 换到的是从未显示过的状态** → 是推进/修正不是抖动 | **不做流式重构**。队列代码从未存在于 git 里。**B2 打偏**：它平顺 grow，而 grow 已逐字；定速输出不动 swap |
| 10-08 | swap 是抖动还是改口 | 用 `sig` 分：换到曾显示过的旧状态 = 抖动，换到新状态 = 推进/修正。抖动占比 **e_3min 6% / blog 1% / meeting 11%** | 日志层面没有抖动可修。⚠️ 但日志看不见位置/宽度 reflow、窗口移动、hold 造成的"停住→跳"——**用户若仍看到闪，是仪器不够** |

| 10-08 | 那四项 `sig` 看不见的，逐项测了 | 窗口高度随行数变（1→2 行时整体上移 ~35~40px；`lines` 算出来却没进埋点，已补），但 **e_3min 整段只动 4 次 / 222 次 paint**。间隔 17~27% >1s、5~9% >2s、p90 1.8s | **前三项排除，第四项（停住→突然跳）成立**。剩下的是**节奏**，归 VAD/partial 层 |
| 10-08 | "3-4 个字块撤回"的来源 | **`sig` 的盲点是真的**：它区分"曾出现过/没出现过"，**不区分"推进/替换"**。逐个归因后：**来源 A（ASR 改口）56 次、来源 B（同原文重译）0 次**。且 B 在 **8 次运行 / 4 条素材上共 0 次**——`PARTIAL_STABLE_REPEAT=1` 使同一原文不可能被译两次 | **来源是 A**。真正的撤回是 `char` 动作的缩字：占 char 的 51~69%，幅度 1~8 字（集中 3~5 字），**每分钟 2.0/6.8/7.2**。⇒ **ctx A/B 不必做；#19 多一条理由** |
| 10-08 | 那四项 `sig` 看不见的，逐项测了 | 窗口高度随行数变（1→2 行时整体上移 ~35~40px；`lines` 算出来却没进埋点，已补），但 **e_3min 整段只动 4 次 / 222 次 paint**。间隔 17~27% >1s、5~9% >2s、p90 1.8s | **前三项排除，第四项（停住→突然跳）成立**。剩下的体感问题是**节奏**，归 VAD/partial 层 ⇒ #4 与 #19，**不归 `caption_window.py`**。#19 因此多了一条价值 |

| 10-08 | #19 gate 500 能治"字块撤回"吗 | **不能**。M5（`char` 缩字/分钟）在 **250 / 500 / 1000 三档全平**（e_3min 2.4/2.4/2.8，blog 6.8/8.7/7.8，meeting 7.2/6.7/7.4）。M1 三条都过、M2 两条可测的过、M4 三条都改善 7~9 点 | **不翻默认值**——M5 是它被排进来的唯一理由。机制：`min_speech_ms` 只推迟**第一条** partial，不减少条数；条数由 `partial_interval_ms` 决定 ⇒ **旋钮是 #4，不是 gate**（队列 #20） |
| 10-08 | hy-mt2 的 MTP 能不能开 | **不能**。用 llama.cpp 官方 `gguf` 包读 Q4_K_M：**354 个张量、0 个带 mtp/draft/nextn**，前缀只有 `blk.0..blk.31` + `token_embd` + `output_norm`。`llama.err` 也报 `spec common_specu: no implementations specified`、`draft: 0.000 MiB` | **不成立，不排队**。⚠️ 二进制 grep 权重里的 "mtp" 无效——25 个命中全是量化权重里拼出的随机字节，必须真解析 gguf |
| 10-08 | 插件「终止后端后再按开始」启不动 | **根因是装着的宿主 exe 是 10-01 的旧代码**。`translation-native-host.exe` 时间戳 10/01 10:03，而 `native_host.py` 是 10/01 13:52；`git log` 显示之后有 `6651665`(10/01 10:04) 和 `65591a1`(10/06 16:24) 两次改动**一次都没进 exe**。`65591a1` 加的 `wait_for_existing_backend()` 正是"停了再起"的修复：旧版 `start_backend` 只看两个端口是否**同时**应答，`taskkill` 异步、两个端口死得不同步，窗口期里它会重复 spawn，新 llama-server 绑不上 8080 而 `backend_ready()` 又因旧进程还在应答而返回 ok | **已重建 exe 并验证**（`status` ×3 应答正确、未知命令报错正确、stderr 空）。⚠️ **方法论：`scripts/native-host/` 被 gitignore，改了 `native_host.py` 必须重建 exe，没有任何东西会提醒你** |
| 10-08 | 决定① 抽掉管线里额外加的滤波器 | **没有可抽的东西**。全仓（server/scripts/tools/extension）搜 `highpass\|lowpass\|butterworth\|sosfilt\|firwin\|lfilter\|scipy.signal\|bandpass\|notch\|apply_filter\|denoise\|webrtcvad\|audioop\|ratecv` = **零命中**，`requirements.txt` 无 scipy/noisereduce/webrtcvad。最像的是 `vad.py` 的 `NON_SPEECH_GAIN = 0.0`，但它不是滤波器且属 VAD（决定②不动 VAD） | **不是"抽掉"，是从来没有**。依赖检查确认：`pysbd` / `scipy` / `noisereduce` / `webrtcvad` **都不在 requirements.txt** |
| 10-08 | 回声去重 / ≤8 字符合并 有没有 | **都没有**。回声去重：无（`translate.py` 的 `collapse_translation_repeats` / `collapse_caption_repeats` 是治 LLM 复读的，不是回声）。≤8 字符合并：无（全仓无短句长度阈值，`CHAR_RUN_MAX=3`/`CLAUSE_RUN_MAX=3` 是重复折叠窗口）。`pipeline.py` 里**没有任何切句**（`split\|sentence\|clause\|punct` 只命中注释与常量名） | 记忆里的那条链整条不存在。**缺的不是滤波器，是四样东西同时不存在** |
| 10-08 | pysbd 分句是不是好东西 | **分 partial 是坏设计，分 final 才是**。支持它的理由是真的（一段 ASR 一段译文 ⇒ 翻译单元常是半句，而 hy-mt2 在句子级语料上训的）。但反对的理由一条就够：**分句要先知道"这句话结束了"才能翻 ⇒ 必须等句末 ⇒ 等停顿或 VAD 收尾 ⇒ 直接打在「慢半句」上** | **放 partial = 违背体感目标，不做。放 final（VAD 收尾后切句逐句翻）= 质量提升不付延迟代价，可做，但不是 #20 的解**（撤回发生在 partial 阶段的反复改口上）。且 `pysbd` 未装，**先别装** |
| 10-08 | ④ 换 large-v3-turbo 能治电影识别吗 | **能，但与风扇目标反向**。`er_3min` 回放：**最终句 16/16**、coverage 84%、识别到位。但 `asr share of wall clock 96.0%`、`asr >250ms 215/215 (100%)`、`asr p50 821ms`、`pipeline.drop 366`、**GPU 91.10W / 75~77°C**（同机空载 10.6W）。**turbo 单次 ASR 约 821ms，而 partial 节奏是 250ms，结构上跟不过来** | **turbo + 250 不兼容。turbo 需要更慢的 partial 节奏才可能划算，而那正是 #4（250→750）——所以自洽的组合是 turbo + 750，不是 turbo 单独。** ⚠️ 无 small 对照：`logs/v1-er_3min.*` 窗口只有 20.9s、asr n=34、启动时 GPU 上有别人的 llama-server（pid 18104），**不可用**。且用户报风扇的是**播客**，我测的是**电影**，播客 ASR 调用率更高 ⇒ turbo 在播客上只会更糟 |
| 10-08 | 决定③ 切换按钮放扩展 | **落地，但后端也得改**。先更正：`partial_interval_ms` 的热切**原先只在 UDP 路径**（`_listen_caption_state`，字幕窗那个开关）。扩展走的 WebSocket `config` 分支只读 `audioOnly`/`language`/api 字段，**这个字段被接受然后丢掉**——不报错，控件就是没反应。已给 `ws_server.py` 的 config 分支加 `if data.get("partial_interval_ms"): _switch_partial_interval(...)`。字幕窗黑框 UI 未动；扩展加两个按钮，值存 `chrome.storage.local` 所以下次开始也带着 | 7 条新测试（`tests/test_cadence_switch.py`）：2 条真行为测试（开关到达 session / 无 session 时仍记住），5 条接线断言。**含一条真不变量：扩展默认值必须等于 `config.PARTIAL_INTERVAL_MS`，否则一按开始就静默改行为** |
| 10-08 | meta 里没有 ASR 模型名 | `turbo-er` 是我起的臂名，**meta 里没有任何字段能证明 turbo 真跑了**——`thresholds` 只覆盖可调项，ASR 模型是路径不是阈值 | 已把 `asr_model` / `asr_fallback` 加进 meta 的 `thresholds`（`replay.ps1:356`）。**与「12 个 MTP 开全部是关的」同一形状：名字代替事实。以后读 `asr_model`，不读臂名** |

| 10-08 | 开始/终止的逻辑链条是否打通 | **不通，找到 5 个缺陷，其中 2 个各自单独就足以让「终止后端」永远杀不掉后端**。① **失败的 stop 是单向门**：
emove_pid() 排在 kill 被验证之前，端口没空时 pid 文件已经没了，下次 stop 只能答「没有由扩展启动的后端」。② **wait_for_existing_backend 分不清同伴与尸体**（上一节我自己引入的回归）：判据是「有端口开着 ⇒ 另有一个实例在起」，而 stop 失败留下的正是半开状态（llama 活着占 8080、python 已死），于是干等 90 秒再失败。③ **write_pid 的异常杀死已成功的启动**：它在 Popen 之后抛，ensure 回 ok:false，**但后端已经在起**——实测抓到一次：14:04:12 起了 powershell、14:04:17 起了 llama-server 和 python-server，而接口说「启动失败」。④ **根因**：write_pid 在 exe 里 Permission denied，而同一时刻同一路径普通 python.exe 写成功、PowerShell Set-Content 写成功、目录 ACL 是 FullControl ⇒ **不是目录权限问题**；而 pid 文件是扩展手里唯一的进程树把手。⑤ **	askkill 退出码被丢弃**，「拒绝访问」「没有那个进程」「没在 10 秒内退出」被压成同一句话 | **全部修完并实测两轮循环**：开始→终止→开始→终止，四步全对（stopped:true、端口 down、llama-server 真消失、stderr 空）。修法：pid 文件改**候选列表**（LOCALAPPDATA	ranslation-backend\server.pid → 回退 ROOT\logs\server.pid，已被 .gitignore:12 覆盖）；wait_for_existing_backend 以**有 pid 文件**为判据；write_pid 返回错误串不抛；	askkill 接 returncode。**11 条新测试，第一轮 4 失败、第二轮 5 失败——失败的那些就是缺陷本身**。⚠️ 顺带修掉一个新回归：缺陷①的修法让陈旧 pid 文件永远留下，已在 None 分支清理 |

| 10-08 | 开始无反应 + 显存 0 | **四个原因：两个在扩展里，两个在安装产物里**。① **「无反应」的机制找到了**：catch 把错误写进状态行，inally 里的 
efresh() 立刻覆盖成「未开始」⇒ **任何失败都显示几毫秒就被抹掉，与坏掉的按钮无法区分**——这就是前两轮拿不到任何错误信息的原因。② **无法区分「宿主不可达」和「后端起不来」**：ensure 超时 120 秒，而宿主根本起不来时用户等两分钟。加了 ping-backend → 
ativeRequest("status", 8000)，**宿主不可达 8 秒内报出来**。③ **装好的宿主清单带 UTF-8 BOM**（EF BB BF 7B，严格 utf-8 解析直接失败）：install_native_host.ps1 用 Set-Content -Encoding UTF8，**而 PS 5.1 的 UTF8 就是带 BOM 的**。Chrome 是否容忍读不出来，但解析不了 ⇒ connectNative 失败 ⇒ 弹窗无法显示任何信息。④ **onefile 的 bootloader 解包失败**：它跑任何 Python 前要先解包到 %TEMP%\_MEI<pid>，解包不了就 PYI-xxx:ERROR 退出 1，Chrome 看到端口死掉。实测 TEMP 不设/AppData 都失败、仓库内才成功 ⇒ **改 --onedir，没有解包步骤就没有这个失败模式** | 全部修完并在 **Chrome 的条件（不覆写 TEMP）** 下实测两轮循环四次操作全对。⚠️ 换 onedir **立刻踩出第七个缺陷**：project_root() 数父目录，onefile 时 parents[2] 是仓库根、onedir 多一层就变成 scripts/ ⇒ 冻结构建去找 scripts\scripts\start.ps1。改成**向上找标记文件 scripts/start.ps1**。⚠️ 顺带修掉**我自己新写的构建脚本**的两个缺陷：不检查 PyInstaller 退出码（上次构建的 exe 还在 ⇒ 「存在」检查通过 ⇒ 报 Built 但发的是旧代码，**与本节要修的陈旧 exe 同病**）、旧的 onefile exe 不删。**已确认扩展 ID 正确**（manifest 的 key 算出 mgnbcjdhnfiiooeldkpbndgmffgapdll，与 allowed_origins 一致） |
| 10-08 | 打包参数的否定文本断言 | **三条断言全失败，因为这三个文件里都有解释为什么不能这么做的注释，而注释里恰好写着被禁的那几个词**（--onedir, not --onefile、Set-Content -Encoding UTF8 is UTF8 with BOM、parents[2] was the root） | **在一个会记录自己错误的文件上做否定文本断言，永远会失败。**修法：断言前先剥掉注释行，只对可执行行断言。	est_root_discovery 相应改成断言「搜索在兜底之前」而不是「兜底不存在」——**那才是真正要保证的性质** |

| 10-08 | 「说话少 + 背景音乐」时有声音不翻译 | **机制是 VAD 没有前导预卷，不是遮蔽，也不是阈值**。新素材 samples/sparse_music.wav（90s，语音占 17.0%，15 句真实 1s 英文短句每 6s 一句，垫在 e_3min 的音乐/镜头音上，+10 dB）复现了症状：**完整送达 5/15、部分送达 9/15、完全没到 1/15，起始延迟中位 +0.19s / 最大 +0.72s**。① **降噪治不了**：逐句量得语音开头比底噪**高 11.5 dB**（最低 +2.9，0/15 低于 0 dB），根本没被盖住，把底噪弄更安静不会让检测器更早触发。② **阈值也不是**：VAD_THRESHOLD 0.5→0.2 起始延迟**一点不动**（最大始终 +0.72s），「完整」反而 5→4。③ 真正的位置：ad.py:64 的 VAD_SPEECH_PAD_MS=120 **只在 _trim 里补尾部**，ad.py:333 的 self.utterance.append(frame) 说明**段从 Silero 第一次判语音那一帧就开始，前面一点不留** ⇒ **检测器每延迟 1ms 就丢 1ms 的字**，1 秒的句子里中位丢 19%、最差 72%。**两处必须一起改**：前导帧按定义是非语音帧，而 NON_SPEECH_GAIN = 0.0 会把它们抹成零 ⇒ 只补预卷不改增益，补进来的是静音 | 四项的结论：**降噪无关 · 回声去重不存在 · 分句无关（治译文质量，漏检在 ASR 之前）· ≤8字符合并与症状反向（它把短句并掉，你要的是短句出得来）**。**merge_window 在这里也用不上**——这些片段是「被截断」而非「没送出」，没有可救的邻居。⚠️ 我先前说「降噪收益很小」是基于 er_3min，那是另一类故障（32~96ms 音乐毛刺），不迁移。⚠️ 第一个 SNR 测算是**错的**（15 个位置 RMS 完全相同，重建底噪的代码有 bug），已改为由构建脚本单独输出 _bed.wav / _speech.wav 后重测 |
| 10-08 | 换识别引擎（turbo → Qwen3-ASR） | **能，而且 turbo 慢是结构性的**。turbo 单次 823ms 而 partial 节奏 250ms ⇒ `asr share of wall clock 96.1%`、`asr >250ms 215/215 (100%)`、`pipeline.drop 366/630`。Qwen3-ASR 同素材（blog_3min 187s，一臂一次，`asr_backend` 记在 meta 里）：`asr p50 823→140ms`、`>250ms 100%→9%`、wall 占比 `96.1%→49.2%`、`drop 366→6`、`qlen p90 3.0→0.0`、`ttft p50 40.6→21.4ms`、coverage 两臂都是 41/49、终译 16/16。**翻译更快，队列从来不是瓶颈**。显存 1938+1286=3223/4096，从来不缺显存 | **`ASR_BACKEND` 开关落地并提交（`dfc1c23`），默认暂不翻**。⚠️ **代价在画字侧，预登记判据没覆盖**：`e2e_first_tr_to_paint 382→925ms`、`>1s 19%→44%`、改口 11→28 次、hold 819→1388ms——partial 从 215 涨到 579 = 改口多 2.7 倍。**所以 #4（250→750）不是后续项，是同一件事的一部分**：识别快了，慢节奏才买得起 |
| 10-08 | 翻译请求是突发成簇还是稳态 | **A 臂不是突发，但我第一次算错了**。先报 `enqueue−ttotal` 每窗 +19~+28、累计 **+431**（读作队列发散）——**错的量**：`enqueue` 对每个 partial 都发，多数作为被取代的候选丢掉（366/630），把注定不翻的候选算成了积压。改成「识别产出 vs 翻译完成」后：每窗净 −3~+1、累计 −16，间隔中位 852ms、**小于 100ms 的 0/198 = 0%**，**不成簇** | **突发假说在 blog_3min 上不成立，而这素材答不了这个问题**——它是 TTS、语音 93.4%、段长中位 10.9s，六份里最不容易突发的一份；症状在真实讲解/播客上。⚠️ `enqueue_qlen` 是 **ASR 侧输入队列**不是翻译队列。⚠️ 「没有报错」不是证据：`replay.ps1` 收不到 server 写到控制台句柄的输出，B 臂真正的错是 400（发裸 PCM 却声明 format=wav）被 print 到虚空，读起来像「模型坏了」 |
| 10-08 | Qwen3-ASR 接进管线的两个坑 | **两个都不是模型的问题，但都读作模型坏了**。① **400 `Failed to load image or audio file`**：`_wav_bytes` 现在包真容器，`_pcm_bytes` 保留给前缀比对（wav 头带长度，会让每段前 44 字节都不同，前缀测试就废了）。② **跨线程复用 `httpx.Client` 挂死、无异常**：连接池绑在构造时的循环上，而 `transcribe` 跑在执行器线程；改成每调用一个 client | 已修，183 测试过，进程外复验返回 `('Video because the.', 'en', 1.0)` |
| 10-08 | 字幕「卡一下上来又卡一下」是显示层的停顿，不是翻译队列 | **是显示层，而且换引擎完全没动它**。翻译队列在两臂都空（深度 p50 0 / max 3）、到达不成簇（0/198 间隔 <300ms）⇒ 从来不是队列。`dwell` 两臂几乎一致（74% / 72% 的间隔 >1s，停顿时丢 1282 / 1420 字）| **埋点先于调参**：dwell 加 `why=held\|empty` + `waiting`/`idle`。一臂（whisper/750）→ held 50 次 72.5%、empty 19 次 27.5%，而 empty 那批**最长**（p50 2253ms vs 1482ms、max 11377ms）且 100% idle ⇒ **改 hold 一动不了它**。⚠️ **判据必须按成因分组**，只看合并数会把素材的静默记成 hold 的功劳 |
| 10-08 | 降 `HOLD_ABS_MIN_MS` 能不能治停顿 | **不能，而且 600 让这个参数变成空转**。800→600 一臂：`held gap p50 1482→1502ms`、`waiting p50 808→820ms`、`empty` 组不变 ⇒ 全在噪声内；而 1~3 字那档**真付了 180ms**。机制：参考表最低 1000ms、最快 factor 0.62 ⇒ **两者的下限 620ms**，地板 600 **什么也不夹**（空转），800 时它还在夹 | **改回 800**，加了「地板不得低于 620」的测试。真正驱动 `held` 中位数的是 **9~18 字档**（13 字 → ref 2400 ×0.62 = 1488，几乎精确命中）⇒ **剩下的候选只有 factor 下限 0.62，而那是拿全部档的可读性换** |
| 10-08 | 我的两条推断错误（写进 REPORT 免得重犯） | ① **`waiting p50 ≈ 地板 800` 不是因果**：`hold_ms` 只是 deadline 的**候选之一**，`waiting` 测的是**余量**不是 hold——两次运行的 `waiting` 最小值是 54ms / 157ms。两个数挨得近 ≠ 一个解释另一个（与「拿延迟曲线读前缀缓存命中率」同形）。② **`dwell >1s` 合并计数会把 empty 组算进 hold 的功劳** | 两条都写进 REPORT 42 与 `caption_window.py` 注释 |
| 10-08 | Qwen3-ASR 换引擎 | **不采用，但开关留着**。ASR 单次 823→140ms（5.9×）、>250ms 100%→9%、占用 96%→43% 墙钟；**但 e2e 反向**：blog_3min 382→925ms、lecture_mix 55→434ms ⇒ 两次符号相反，**不是恒定倍数**。⚠️ 我先写的「瓶颈转移到翻译」是**错的**：翻译队列两臂都空（qlen p90 0、max 5），瓶颈在**改口次数 ×2.9**（136 vs 391 次识别）⇒ 字幕重写成为限制，**方向决定下次回头看哪一层** | 留在 `ASR_BACKEND` + 链路 `qwen3-hy`。**解锁条件 = 显示层能扛 2.9 倍改口，或给改口次数封顶**。另：5/149 次复读到填满上下文（30~33s/次），已用 128 token 上限 + 整条丢弃 + `asr.truncated` 埋点兜住，**病因仍未知**（六组假设全未复现） |
| 10-08 | hold 地板（`HOLD_ABS_MIN_MS`）能不能治画面停顿 | **不能，800→600 实测无收益**，已改回 800。`held gap p50 1482→1502ms`、`waiting p50 808→820ms`、`empty` 组不变 ⇒ 全在噪声内；而 1~3 字那档**真付了 180ms**。机制：参考表最低 1000ms、最快 factor 0.62 ⇒ **两者下限 620ms**，地板设 600 **什么也不夹**（空转）| **地板这条路收手**。真正驱动 `held` 中位数的是 **9~18 字档**（13 字 → ref 2400 ×0.62 = 1488，几乎精确命中）⇒ 剩下唯一候选是 **factor 下限 0.62**，那是**拿全部档的可读性换**，需要单独评估。⚠️ 加了「地板不得低于 620」的测试，否则它会再次静默失效 |
| 10-08 | 字幕卡顿到底在哪一层 | **在显示层，不在 ASR 也不在翻译队列**。翻译队列两臂都空、到达不成簇（0/198 间隔 <300ms）；`dwell` 两臂几乎一致（74% / 72% 的间隔 >1s）⇒ **换引擎没动它** | 埋点先行：`caption.dwell` 加 `why=held\|empty` + `waiting`/`idle`。**多数 held（72.5%）但 27.5% 的 empty 是最长的那批且 100% idle，改 hold 动不了** ⇒ **判据必须按成因分组**，只看合并数会把素材的静默记成 hold 的功劳 |
