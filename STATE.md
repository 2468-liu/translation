# STATE

一页。**每次会话读全文。** 细节/证据在 `REPORT.md`（`python scripts/rag.py query "…"` 取片段，不要通读）。
规则在 `AGENT.md`，推进顺序在 `PLAN.md`。

> 2026-10-09 精炼：五区块顺序不变（目标 / 约束 / 当前默认 / 队列 / 停止+待验证），信息未删。
> 两条逐字重复的实验日志行已并成一行（本轮合并）；编号与区块位置一律不动。
> 括号里没写模型的旧结论：引用前先核当时跑的哪一版 ASR/llama。

## 目标（体感，不是数字）

1. 字幕不慢声音一句半句
2. 长时间看视频风扇不狂转
3. 字幕不乱跳、看得清

## 约束

- 默认链路 = **whisper(CT2, cuda/float16) + hy-mt2**，4GB 卡
  · ASR 权重 = `faster-whisper-large-v3-turbo`，回退 `faster-whisper-small`（`config.py:99-100`，2026-10-09 核盘）
- **只调流水线 / 窗口 / VAD / 显示层**；除非卡住，不换模型
- 换翻译模型（Hy-MT2 → Gemma）= **独立于音频直出的决策**，不随直出一起否决

## 当前默认（生效值，非源码常量）

```
ASR_DEVICE cuda · ASR_COMPUTE_TYPE float16
ASR 权重   faster-whisper-large-v3-turbo（config.py:99；回退 small，:100）
           ⚠ 旧结论的前提括号里多数没写 ASR 模型 ⇒ 引用前先核
翻译权重   Hy-MT2-1.8B-StreamRevise-v4-Q4_K_M.gguf（10-08 20:54 起在用）
           流式 LoRA 微调，与基线 Q4_K_M 视作同一模型（用户判定 10-09）
           基线那份已不在 models\hy-mt2-1.8b\（只剩 v4 + README_CN.md）
           ⇒ 10-08 20:16 之前的绝对值与之后**不可并列**；同权重之间照常
           10-09 全部运行在 v4 上，含 BLK1/2/3、PUNCT1~4
PARTIAL_INTERVAL_MS 750  ← 生效值。此行原写 250，而这是「生效值」表（最不该错的字段）
           理由不是"更快"：823ms 的 ASR 在 250 节奏下 215 次全 overrun，750 下占墙钟 57.1%
           750 让 turbo *能用*，不是让它*好*。popup 滑杆 250~1000
AUDIO_NATIVE 0（音频直出关）
HOLD_ABS_MIN_MS 800（caption_window.py:70）参考表最低 1000 × 最快 factor 0.62 = 620
           = 这张表能产出的最低值 ⇒ 地板 ≤620 是空操作；试过 600 无效已回退（REPORT 42）
默认链路   whisper-hy（config.DEFAULT_CHAIN）；可选 whisper-hy / qwen3-hy / gemma-e2e（切换 = 全拆 + 重启）
投影层     CPU（--mmproj-device none）
翻译后端   已装（cuda12-avx2）：2.33.0 / 2.47.0 / 2.51.0 / 2.52.0 / 2.53.0 / **2.55.0**（10-09 08:52 装，2026-10-09 核盘）
           缺省无 pin = 本机已装最新（现为 2.55.0，**在任何东西上都没测过**）
           生效值 = **2.53.0**：runtime.json 的 llama_version 是有意钉的，保 REPORT 47–54 可比
             删掉该键即回到最新；replay.ps1 不读它，自己钉 MEASUREMENT_LLAMA_VERSION=2.47.0
           唯一解析器 scripts\resolve_llama.ps1（-Version / 环境变量 / runtime.json → 否则最新）
             没装的那版**报错不退回**；曾有两份方向相反的解析器，已合成（REPORT 45）
           ⚠ 引用旧数必须带 build 号：REPORT 里的延迟数 = 2.47.0；10-09 的 A/B 数 = 2.53.0
             我说过「hy-mt2 仍在 2.47.0」是错的（REPORT 44）；start.ps1:563 的过期注释已改
翻译模型   MT_BACKEND=hy-mt2（默认，8080）｜gemma4（8082，文本翻译，不带 --mmproj）
           gemma4 = 36 层上 32 层 + MTP 草稿 5/5 层，VRAM 2337 MiB，50 tok/s
           ngl 99 + 投影放 CPU：75.4 tok/s（更快，REPORT 25.3 脚注）
音频采集   AUDIO_SOURCE=loopback（默认，WASAPI 系统回环，自测增益）；=socket 回退扩展/回放 PCM 注入
           **回放自己设这个**：replay.ps1 缺省 socket 并打印（431686e 前无人设过，见「已证伪」）
音频直出   Gemma4 音频路径：对白被配乐压住的素材不可用（REPORT 25）；适用 = 干净旁白；音频路径 MTP 净负应关
上下文注入 MT_CONTEXT_SENTENCES 2（0 = 对照臂）
           此前从未生效：translate.build_messages 把 context 删掉了（REPORT 21.3）
VAD        THRESHOLD 0.5 / MIN_SPEECH 250 / MIN_SILENCE 400 / PAD 120 / MERGE_WINDOW 0（合并默认关）
           PREROLL_MAX 0（config.py:311；前导预卷的旋钮 =「有声音不翻译」那题的修复位，默认关）
切分       whisper 路：MAX_UTTERANCE 12000 / SOFT_CUT 5000ms 语音 + 200ms 停顿（vad.py:31/61-62）
           音频直出路：MAX_UTTERANCE 26000 / SOFT_CUT 12000 + 700（config.py:351-356）
显示层     SETTLE 450 / HOLD_MIN 800 / HOLD_MAX 4200 / READABLE 0.7；SLIDE_MS 0（位移动画关）
输出方式   **路线的属性**（`config.CHAINS[...]["output"]`），不是第二个开关：
           whisper-hy 流式 ｜ qwen3-hy **块输出（按标点分块）** ｜ gemma-e2e 流式
           谁在跑：优先读 start.ps1 导出的 `TRANSLATION_CHAIN_RUNNING`（**观测值**，回退之后也更新），
           没有才按识别器/翻译器推 ⇒ 显示层只读 `config.BLOCK_OUTPUT`
           ⚠ 独立的 `TRANSLATION_BLOCK_BY_PUNCT` 开关**已不存在**（§59.6 记的"默认改 0"是那个开关
           时代的事；行为现在由路线决定，qwen3 那条自 cf2dbc0 起就是块状）
           ⚠ "qwen3 需要块状"的前提是用户报的闪烁、**不是测量**；块状的代价量过（首字 +3.0s，REPORT 58.9）
```

跑的是哪一版：启动日志含 `git_rev`；**llama 版本与 Python 环境在 `logs\start.log`**。
后者比前者要紧：`start.ps1` 的选择只写进那个文件，而 native host 把 stdout 丢进 DEVNULL
——"起了哪一版"曾经只存在于被丢掉的控制台里。

---

## MTP 这条线到此为止（2026-10-08）

```
用户指出 mtp_draft 只读命令行有没有外挂草稿文件（--model-draft），不覆盖内置 MTP 层 —— 正确。
字段旁已有注释说明它答哪一问，另有 spec_args 管"推测解码到底开没开"。
```
**用 llama.cpp 官方 `gguf` 包读 `Hy-MT2-1.8B-Q4_K_M.gguf`：**

```
general.architecture = hunyuan-dense      block_count = 32
元数据 36 键，张量 354 个
带 mtp / draft / nextn / medusa / eagle / speculat / predict 的张量：0
张量名前缀 34 种 = blk.0..blk.31（各 11 个）+ token_embd + output_norm
354 = 32 × 11 + 2
```
⇒ **纯 dense，没有 MTP 头。hy-mt2 的 MTP 不是"未测"，是这份额重开不了。**
运行时证据同结论：`logs/llama.err` 报 `common_specu: no implementations specified for speculative decoding`、
`prompt_save: … (draft: 0.000 MiB)`。曾设想的两个方向（开更快 → 重估一片数 / 关更快 → 写进已证伪）都不会发生。

**另一条已排除的担心**：推测解码**按构造无损**（验证通过才接受，与不开逐 token 相同）
⇒ 它**不可能**让同一句原文产出两个译文；REPORT 33 的"来源 B = 0"与 MTP 开关无关，不需要复核。

⚠ **记一个坑**：二进制 grep 权重里的 "mtp" 无效 —— 25 个命中全是量化权重里拼出的随机字节（各 1 次）。
对照组（能扫出 `blk.0.attn_k.weight`）证明方法有效，所以这是"信号被噪声淹没" ⇒ **必须真解析 gguf**。

## 已证伪（假设清单，不是错误清单）

**这些看着合理，最容易被重新提出。**每条都写了它错在哪、为什么当时看着成立。

| 假设 | 证伪方式 | 为什么它当时看着成立 |
|---|---|---|
| **`total time` = 单次成本** | 它含 `load time`（本机 656–809ms/进程） | whisper 自己就打印这个字段，名字没骗人 |
| **`total − load` = 边际成本** | 常驻实测与该减法差 11 倍（同 ctx 660：CLI 1001ms vs 常驻 90ms） | 减法在数学上成立，问题在两个程序的 `-ac` 行为不同 |
| **缩窗（`-ac`）买速度** | ctx 77→660（8.6×）encode 只 111.8→122.7ms | 名字叫 audio_ctx，直觉上就该缩计算量 |
| **`vad.end` 行数 = VAD 事件数** | `perf.emit` 在 `MIN_SPEECH_MS` 门控**之前**，日志含被丢弃项 | 行数与事件数看着是同一件事 |
| **日志里 `speech_ms` 短 = 碎段** | 53 行里 50 行 <500ms，但那是门控前的记录 | 按 audio_ms 统计时结论相反 |
| **`language=auto` 导致空输出** | 三方对照：`small auto` 与 `small en` 输出**逐字相同** | auto 慢 2 倍，看着就像坏了 |
| **小窗口 ASR 慢 = 检测开销** | 慢在编码，不在检测 | 两者都慢，混在一起归因 |
| **回放锁 `en` = 在测产品** | 产品默认 auto；回放曾写死 `en` | 素材大多是英语，写死 en 看着无害 |
| **旧延迟基线能判 auto 下的表现** | auto 下 asr p50 281ms，已超 250ms 节奏 | 两组数都叫"ASR 耗时"，量纲一样 |
| **量化省 GPU 功耗** | CUDA 74.3 / 85.6 / 74.3ms，差异在噪声内 | 量化必然更快是常识 |
| **RMS 阈值能判断停顿** | 底噪是响处 20.5% 的素材报"0 次 ≥1s 停顿"，管线埋点实测 35 次 | RMS 确实测能量，只是阈值定错了位置 |
| **GPU-only 读数能判"挪 CPU 是否省电"** | GPU 降 10W / CPU 升 5W 与 CPU 升 12W 在本机读数完全一样 | 差分法看着是标准做法 |
| **clip 级 `language=None` 能验多语种素材** | 222 被整段按英语解码，产出 `How do you speak Japanese?` 这类幻觉 | 参数就叫 `language=None`，看着就是"自动" |
| **臂名/字段名说明了配置** | 12 格 "MTP 开" 全部是关的；关闭态的接受率字段根本不存在 | 名字写在结果表里，看着就是证据 |
| **`hold_ms()` 能被 SETTLE_MS 调小** | 它恒 ≥800ms，任何低于 800 的值不可能生效 | 两个都是"等待相关"的常量 |
| **`clocks_event_reasons` 可当风扇代理** | 它是代理的代理，且本机 `fan.speed` 返回 `[N/A]` | 它确实记录 GPU 进出活跃态 |
| **`VAD_MIN_SPEECH_MS` 是"最短语音"** | 它同时是**丢弃**门槛：`_drop()` 无条件执行；LiveTranslate 同名参数是"并入下一段" | 两个项目参数同名，语义相反 |
| **"抬门槛 = 合并短段"** | 抬到 1.0s 额外丢 8 段 / 4.9s **真语音**（`e_3min_timing.json` 那些段带 p=0.99 真文本）；合并要单独实现 | 两者都让"段变长"，看着是同一件事 |
| **单次运行的 VAD 分布可直接引用** | 同一文件报 32/35/38/39 段：`load_vad()` 返回全局实例，Silero 跨帧带 LSTM 状态且段间不清；加 `engine.reset()` 后三次都 32 | 同一份代码同一份音频，看着必然确定 |
| **`conda activate` 在 PowerShell 脚本里能生效** | `cmd /c "call conda.bat activate pytorch"` 里的 cmd 是子进程，`CONDA_PREFIX` 不回传 ⇒ 回退 **base**（无 `websockets`）⇒ 后端 `ModuleNotFoundError`，扩展只看到 90s 超时。上轮"实测通过"是 shell 里恰好已有 conda 变量，把空值路径盖住了 | 命令确实执行了、也没报错 |
| **`Get-NetTCPConnection` 能判端口在听** | 它基于 CIM/WMI，受限会话**拒绝访问**；`-ErrorAction SilentlyContinue` 后返回空值被读成"没监听"（实测 8080 在服务而为 0 条） | 名字里就是"TCP 连接"，看着就是端口检查 |
| **`Get-CimInstance` 失败会显式报错** | 受限会话里它是终止错误（`Stop`）⇒ `replay.ps1` 死在第 37 行一行未跑；被 `SilentlyContinue` 包住处**静默变成"没有草稿"** | 同一条命令在两种 context 下表现不同 |
| **`Get-NetTCPConnection` 的问题已修完** | 只修了 `replay.ps1`；`smoke.ps1` 两处、`sweep_*.ps1` 九处仍在用，本轮 `smoke.ps1` 因此在 8080 有服务时读成"没服务" | 结论已进这张表，看着就像处理过了 |
| **`Get-Process` 的属性都能读** | `Sort-Object StartTime` 抛 `Access is denied`（受限会话无此权限）⇒ `replay.ps1` 放音之前中止。`Get-Process` 本身能用，**它的 `StartTime` 不能** | `Get-Process` 不走 WMI，推不出它也被拒 |
| **模型目录里"第一个 gguf"就是要跑的那个** | 两个 gguf，`Select-Object -First 1` 选中 `1.25bit-v2`，llama-server 加载失败退出；`start.ps1` 用 `*Q4_K_M*` 过滤所以没事 | 长期只有一个文件时，"取第一个"="取对的那个" |
| **`USE_NLLB = False` 是可调配置** | 写死 False，背后的 `server/mt.py` 与两个模型目录都不在 ⇒ 整链**不可能运行**，只可能被误认成活路径 | 常量名叫开关，看着还有"开"的那一侧 |
| **回放的音频源是 shell 环境变量** | **没有任何脚本设 `TRANSLATION_AUDIO_SOURCE`**；`PLAN.md` 写成操作者要敲的一行 ⇒ 口径取决于有没有人记得敲。漏敲实测：运行干净、无报错、`vad.end=0`、零译文 | 环境变量本来就是给 shell 的；写进文档就等于已生效 |
| **`_self_check()` 一直在跑** | 没人调用它（`__main__` 也没有），而且**它会失败**（两条期望早于 `speech_hold_factor`）⇒ 显示层状态机零覆盖 | 函数名叫 `_self_check`，读起来像启动时会执行 |

## 结论台账（每条带前提）

**前提变了，下面每条自动作废。**括号是它成立所依赖的条件。

```
ASR 上 GPU 成本 +10W
  前提：投影层 CPU / partial 250ms / CT2 small cuda float16 / hy-mt2 空闲 / 后端 2.47.0
  ⚠ 任何一条变了，这条数就不能引用

ttft p50 30~36ms
  前提：语种锁定 en。auto 下每次调用要带检测，成本不同

上下文注入的质量收益（REPORT 21.4）
  前提：hy-mt2 / 2.53.0 / ngl 60 ctx 1024 / e_3min 的 21 句成句原文 / temperature 0 /
        逐句喂入且上下文取自本臂自己的译文
  ⚠ 原文是**成句**时测的；真实管线喂的是 250ms 起切出的碎片，那种输入下能救多少**没测过**，不可外推

hy-mt2 prefill 代价：ctx0 15.7ms / ctx2 31.1 / ctx6 46.4（中位，冷前缀）
  前提：同上。decode 105→114ms 基本不动
  ⚠ 探针同批句子跑时前缀被缓存，中位会低估；冷前缀那两个数才是真的

gemma4 文本翻译：prefill 103~160ms + decode 220~250ms，50 tok/s，VRAM 2337 MiB
  前提：E2B Q4_K_XL / 36 层上 32 层 / ctx 2048 / MTP 草稿 n-max 3 / 2.53.0
  ⚠ 解码 50 tok/s（20ms/token）；曾记到的 46ms/token 是**冷启动首请求**（权重还在分页），不是稳态

asr 占墙钟 16.6% / asr>250ms 15%（er_3min 那格）      前提：语种锁定 en
首字上屏延迟 p50 374~756ms                            前提：语种锁定 en
partial 节奏 250/500/750/1000ms 的省电曲线            前提：语种锁定 en。auto 下 250ms 本身跑不满（asr p50 281ms）

回放语种
  2026-10-07 起默认 auto，与产品一致（扩展默认"自动检测"）
  代价：同一素材 asr p50 140~158ms → 281ms，asr>250ms 8~19% → 58%
  ⇒ **上面几条锁定 en 的基线不可直接与 auto 的结果对比**；要对照：TRANSLATION_REPLAY_LANGUAGE=en

回放的音频源 = socket PCM 注入
  ⚠ 431686e 之前这条前提没有任何脚本保证，只写在 PLAN.md 里让人手动敲；漏掉时回放不报错、
    只跑出零事件（vad.end=0）。现在由 replay.ps1 缺省设置并打印在 code stamp 旁边
  🚩 ⇒ REPORT 24 / 25 / 26 / 27 的数字全部标「待复核」（见下方专节）

whisper.cpp CPU 不可用（单次 400~1260ms）
  前提：ctx 220 / 6 线程 / Q8_0 / 1.4s 片段；⚠ 前提变了要重测
audio_ctx 悬崖在 150（<150 时 77→5215ms）
  前提：whisper.cpp / CUDA / 1.4s 片段 / 贪心无温度回退；⚠ 只对 whisper.cpp 成立，CT2 未测
Q5_1 在 CUDA 上同速但省 284MiB（74.3ms vs fp16 74.3ms）
  前提：CUDA / ctx 220 / 1.4s 片段；CPU 上 Q5_1 反而比 Q8 慢
分片级语种检测：large 干净 / small 不可信
  前提：large-v3-turbo
  ⚠ 修正：默认 ASR 已是 large-v3-turbo（config.py:99），small 只是回退 ⇒ 这条现在只用来定位问题
先上屏后覆盖不可行（错语种译文一致+相关仅 25%）
  前提：clip222_90s / hy-mt2 / 边界后首个 partial
C 的软切生效（5000ms 语音 + 200ms 停顿）
  前提：whisper 路线；音频直出用 GEMMA_SOFT_CUT（12000 + 700）

#6 合并臂：两套独立机制 + 一个被证伪的假设（REPORT 29.9）
  前提：gate 1000 / merge window 2000 / 语言 auto / partial 250 / ctx=2
        hy-mt2 / 2.47.0 / socket 注入 / e_3min 各跑满 184.3s 各 23 条译文
  ① 少中间态 → 队列压力降 → 首字更早   partial 263→86，e2e 首译 188→52ms
  ② 片段合并 → 识别上下文更完整 → 识别更准
     A「Sesh」/「into what it」错  →  B「Sash」/「until when it」对
  ③ 原假设「更长片段→更好翻译」**已证伪**：片段变多变短
  内容不丢：交付音频 90.0s→99.2s，ASR 词数 264→268（离线逐段判）
  代价：partial 每段 11.1→4.0，prefix 覆盖率 78%→61%
  ⚠ ① 是延迟机制、② 是准确度机制，**分开记**（将来可分别被否证）
  ⚠ ② 闭环了 REPORT 21.6：碎片输入**先让识别差**，收益在 ASR 阶段就发生
  ⚠ 只有 e_3min 在线跑过；blog/er/meeting 只有离线结论

新旧的唯一差异是 MAX_UTTERANCE 20000→12000
  前提：对比 pre-merge-2026-10-07 (6f84d9d) 与 e763151；改动 gemma-audio 之外的东西则不成立
折叠去重改 82,522 条中的 140 条（0.17%）        前提：旧时间轴的 82,522 条存储文本
字幕侧折叠把 '哈哈哈哈哈哈' 削成 '哈'
  前提：CHAR_RUN_MAX=3；该上限对不同长度行为不一致（4 个削到 1，10 个削到 3）
```

---

## 线 B 结案：显示层没有可做的问题（2026-10-08，REPORT 31/32/33）

```
grow  追加粒度中位 1 字、p90 2 字                → 已逐字，无粒度问题
swap  占 18~35% 的 paint，89~99% 换到**从未显示过**的状态 → 推进/修正，不是抖动/回吐
char  占 1~6%，其中 **51~69% 是缩字** ← 这就是"3-4 个字块撤回"
        幅度 1~8 字、集中 3~5 字；每分钟 2.0（e_3min）/ 6.8（blog）/ 7.2（meeting）
队列代码 从未存在于 git（所有分支 + tag 零命中）→ 要做只能重写
窗口   高度随行数变（1→2 行整体上移约 35~40px），但 e_3min 一整段只 4 次（222 paint 的 2%）→ 不是闪烁
间隔   17~27% >1s、5~9% >2s、p90 1.8s、e_3min 最长 11.24s
```

**"字块撤回"的来源是 ASR 改口（来源 A），不是上下文注入。**
「同一句原文被重译」在 8 次运行 / 4 条素材上 **0 次** —— `PARTIAL_STABLE_REPEAT=1` 决定了每个
utterance 只译一次、之后只增长 ⇒ **ctx=0 vs ctx=2 的 A/B 不必做**（REPORT 33.1）。

**归属链条**：gate ↑ → 每段 partial 少 → 识别器改口机会少 → `char` 缩字少 → 字幕不抖。
⇒ #19（gate 500）是这个体感项的候选解之一；加上 ④ 节奏那条，共两条理由。

**B2（定速逐字输出）打偏了**：它要平顺 `grow`，而 `grow` 已是 1~2 字/次；可见的跳跃是 `swap` 与
`char`，定速输出**不动这两个**。

⚠ **全部是代理量。**用户若仍觉得闪，**那是这些数不够，不是代码没问题**（AGENT 体感优先）。
而且**用户看的是哪条素材未知**：若是 `e_3min`（撤回 2.0 次/分），体感可能主要来自 ④（停住 1.8 秒）。

## #6 的决定（2026-10-08）

**采纳合并臂（gate 1000 / merge window 2000）。逐句核对：没有整句漏掉。**

```
整句核对（词集覆盖率 <34% 判无对应）
  A 有而 B 完全缺失 0 条 ｜ B 有而 A 完全缺失 1 条 = A 两句的合并 + 一句可疑内容
  去重词量 A 172 → B 189
  B 顺手把 A 的两句碎片并成完整句，且更准
    A  I'm sorry, Sesh. / I'm just playing a bit of a suck.
    B  I'm sorry, Sash. I'm just playing a bit of a suck.
    A  climbing up into what it wasn't  →  B  climbing up until when it wasn't
```

**代价明确，不是零**：prefix 覆盖率 78%→61%（数的是 partial 级命中，而 B 的 partial 少 67%，
两者机制上绑定）；B 另有 2 句重复上屏。

**默认值还没翻**（`VAD_MERGE_WINDOW_MS=0`，2026-10-09 核盘）。闸 3 已跑完（REPORT 30）：

```
blog_3min    过   最终句 16/16 持平，e2e 710→509ms，prefix 78%→94%
e_3min       过   最终句 12/21 持平，e2e 188→52ms，prefix 78%→61%（代价）
meeting_6min 不过 最终句 90/140→83.5/140（臂内散布 1~2，臂间 6.5 → 真的）
                  e2e 判 VOID（臂内散布 4~8 倍，n=16 分辨不出 150ms 门槛）
```

**为什么不过**：`min_speech_ms` 同时是 **partial 的门槛**。抬到 1000ms 后，本来就短的段落在闭合前
发不出任何 partial ⇒ **整句不进翻译**（不是译文变差，是句子没出现）。`meeting_6min` 的
`asr_ratio p50 0.3` 正是"识别器本来就吐不出多少文字"，门槛抬到 1000 雪上加霜。

⚠ **预登记的风险打错了指标**：我给 meeting 单列的 M5 是 prefix 覆盖率，理由是"partial 砍 67% 后
覆盖率可能跌到不可用" —— **覆盖率反而涨了 3 点**。真正掉的是最终句数。**prefix 覆盖率对"句子根本
没出现"不敏感。** 判据要能看见你担心的那个失败形态，别拿上一份素材的失败项当本份的风险指标。

⇒ 下一步是 gate 取中间值 500 / merge window 1000（队列 #19）。1000 太钝（伤 asr_ratio 低的素材）、
250 太碎（e_3min 上 prefix 掉 17 点），500 是两条失败各自机制给出的区间。
（**#19 后来跑完：M5 失败，不翻默认值**，见队列。）

### 「风扇会变安静」是推断，不是实测

ASR 调用少 67% ⇒ GPU 占用时间少 ⇒ 平均功耗应当下降。**没测过**，且按已定边界：ASR 上 GPU 的
全部成本是 **+10W**，而插电形态下 +10W 只是风扇曲线上的一个点 ⇒ **即使这条推断成立，也不改变
已关闭的功耗测量线**。不要写成结论；它是「哪天改回电池形态，第一件该先验的事」。

**为什么不能从"调用少 67%"直接推**：+10W 是"负载运行时 vs 空转"的差，调用变少改变的是**占空比**；
功耗还取决于频率、内存时钟、是否有其他负载并发 —— REPORT 17.4 那次错就栽在窗口上。

### 本机可读与不可读的温度

```
GPU 温度 ✅ nvidia-smi --query-gpu=temperature.gpu，精度 0.1°C，无需安装
          已接进 replay.ps1 的 run.<name>.meta（连 power.draw / throttle_reasons / utilization
          一起，采样在回放之后 = 静止态）
CPU 温度 ❌ **本机读不到**：MSAcpi_ThermalZoneTemperature 返回空/类不存在（与"0 个 ACPI 热区"同一缺失）
          Windows 没有通用的用户态 CPU 温度 API；WMI 的 Win32_TemperatureProbe 在 Win10/11 基本无效
          只有第三方（Open Hardware Monitor / HWiNFO64）能读，需管理员权限，笔记本 EC 兼容性因品牌而异
```

**记读不到，不找代理量代替**（与 `fan.speed [N/A]` 同类）。CPU 温度**不作为阻塞项**。
`nvidia-smi` 的温度 + 功耗 + 是否降频三个量合起来已经能回答"#6 让 GPU 更忙还是更闲"。

---

## 🚩 待复核：REPORT 24~27（431686e 之后）

**不是错数，是前提没被保证过。**标出来是因为它们**看起来**可引用（表格齐整、口径写明、
和前面几节对得上），而这正是最容易被直接拿去用的情况。

```
受影响   REPORT 24/25/26/27 的全部回放实测数字
原因     那几节回放没有由脚本保证 TRANSLATION_AUDIO_SOURCE=socket；前提只以"操作者要敲的一行"
         写在 PLAN.md（28.2 第 4 条）
两种可能 (a) 当时那个 shell 里确实设了 → 数字有效，只是前提靠人记
         (b) 没设 → 那些运行是"干净但零事件"，而它们带着非零的数字 ⇒ (b) 大概没发生
         —— 但**这是推断，不是核对**
为什么不直接判废
         一个 vad.end=0 的运行不会产生 asr p50 286~316ms；数字非零 ⇒ 那次确实有语音进来。
         仍需重跑：非零能排除的失败形态有限
重跑成本 四条素材 × 一臂 ≈ 20 分钟（blog 187s / e 184s / er 206s / meeting 361s）
怎样算复核完
         重跑后与 REPORT 27 的表逐格对照：差异 ≤10% 视为口径一致并解除标记；
         更大则说明当时的环境与现在不同，改口径而不是改数字
```

**解除标记前，引用这四节的数字必须带上这行前提。**

### 🚩 REPORT 22（#6 的离线依据）：数算在了错误的 population 上

`probe_vad_gate.py` 只读 `perf` 行，而合并臂里挂起一个片段会写一行 `vad.end` 却没有 end 事件。
REPORT 22 报的"合并后 37 段 / 保留语音 79.3s"里，**37 段有 13 段不存在**，真段数是 24
（REPORT 29.2，离线与在线两条路都验过）。

⇒ 22.3 / 22.6 的**方向**仍可信（**挂起比直接丢弃多保住语音**，由 ASR 逐段判过：B 交付音频 99.2s
vs A 90.0s），但**具体数值不可引用**。

```
#6b  修埋点（队列 #17，已完成）→ 重算 REPORT 22 的表   ← 重算这一步**未做**
```

### 部分解除（2026-10-08）：只对 blog_3min 与 e_3min 成立

```
blog_3min      REPORT 27 = 16/16     本轮 A = 16/16              ✅ 复现
e_3min         REPORT 27 = 12/21     本轮 A = 12/21              ✅ 复现
meeting_6min   REPORT 27 = 83/140    本轮 A = 91/140 与 89/140   ❌ 不复现（差 6~8 句）
```

⇒ 前两条说明那次运行的前提确实成立（socket 注入当时生效），#6 的 A 臂在 `e_3min` 上完全复现了
REPORT 27 的 12/21。**仍未解除**：另外三条素材没有重跑，且下面的 `vad.end` 缺陷会让任何按日志读
段数的对照失真。

**meeting_6min 维持未解除**，而且现在多一个新解释：那次运行与本轮之间存在某个**未识别的差异**，
不只是"前提靠人记"。⚠ **别把两件事混起来**：`83/140` 恰好等于本轮 B 臂的 `83/140`，容易读成
"REPORT 27 其实跑的是合并臂"。**没有证据**（那次的 partial 是 250、门槛是 250，与 B 臂 meta 不符）。
更可能是巧合，或这个指标本身容易落在 83 附近。**记下来，不解释。**

### 撤回：`e2e_first_tr_to_paint`（2026-10-09）

```
撤回：〈Qwen3-ASR 的 A/B e2e —— lecture_mix B 434ms（= B 慢 8 倍）、p99/max 996ms（= B 尾部干净）〉
      原在 REPORT（perf_compare.py 硬编码读 samples/blog_3min_timing.json，而 A/B 全跑在 lecture_mix）
它曾支持的下游决定：〈B 不采纳〉（AGENT §5 第十三条）
因此被跳过的检查项：coverage 那 5 个点（**已做**，当天用现有日志查完：asr.truncated A 0 对 B 5、
      asr_finals 25 对 23）、dwell/地板那条（**已做**，预登记失败条件触发）、
      块状 vs 流式（**未做**；10-09 的 BLK1/2/3 是另一条问题「降上屏频率」，不是这条）
有效数：e2e p50 真值 47ms（434 是错的）、p99/max 真值 2433ms
有效经验：单次运行的 A/B 差值落在同配置散布内就不能判（53-a）；一个数能不能信，
      与它算得对不对无关，与它被喂了什么有关
```

未核：同一次对账里的 `382→925ms` 是否同源 —— **未能确认 ⇒ 382 与 925 一律按不可引用处理**，
要引用先重跑。相关日志行（10-08「换识别引擎」「Qwen3-ASR 换引擎」）里的 e2e 数字都带这条注。

---

## 队列

**接下来（前 5 条；编号沿用两份旧编号，`旧#N` 是 10-07 那份；其余 11 条见下方「后备」）**

1. **回放中断的成因**（10-09 #3）：掐断的是到字幕窗口那条 WebSocket，6 次里断 3 次
   （11.0s / 48.6s / 34.4s），**同一个签名**。守卫只负责不让它出数，没找原因 ⇒ **当前所有测量的头号障碍**
2. **B 把日语系统性地写成汉字并标成中文**（10-09 #1'a）：zh 占 21.7%（A 0.7%），34 条 zh 文本
   **全部无假名**；素材 ja 53%。与 1' 的两个 utterance 退化是**两件事，修法不同**
3. **「B 跳字」与上条的连接**（10-09 #1'b）：**未验证，而且方向是反的**（hold/char 计数 B 5~6 对 A 7）。
   缺 `caption.paint` 的文本 —— **同一件事第三次卡住**（REPORT 50/53-b/55）
4. **`audio_end_to_paint` / `e2e_first` 要重复测量才能引用**（10-09 #4）：同配置连跑散布 157ms / 9 个点
   （53-a），单次运行的 A/B 差值全部作废；要引用就每个臂至少 3 次
5. **「B 跳字」的机制、块状 vs 流式**（10-09 #5）：假设已记（REPORT 50），**日志缺每次上屏的增量与文本**
   ⇒ 不可测。显示层，与识别侧那条链无关

### 后备（按顺序，未做）

**编号沿用，不重排** —— STATE/PLAN 里的「队列 #7/#8/#17/#19」「旧#N」都指这些号。

6. **`e_3min.wav` 上的 A/B（真实语音压在配乐下）**（10-09 #6）：**从头到尾没跑过**，你最初列的三项
   测量之一。`lecture_mix` 是合成的，而它上面的 coverage 差距已归因到识别侧 —— 真实素材上会不会一样，没测过
7. **错误分桶**（旧#2）：真实录音录屏回放，记 15~20 条 → 见 `samples/BUCKETING.md`
8. **时间轴 `covered_span`**（旧#3，**部分完成**）：e_3min / er_3min / clip222_90s / lecture_mix /
   meeting_6min 五份已带（2026-10-09 核盘）；**`blog_3min_timing.json` 仍是裸 list（16 段，无 covered_span）**，
   `smoke_10s` 同 ⇒ 回放前补齐 blog，否则该素材的未覆盖段会被算错
9. **ASR 后过滤 + ≤8 字符短句合并**（旧#7）：无损、不动 VAD，可独立先做；看碎片条目数
10. **上下文 2 → 6 是否有额外收益**（旧#8）：prefill 代价 +30.7ms，换到多少质量（REPORT 21.4 只有 2 与 6 两档）
11. **片段长度 ↔ 首字延迟的取舍**（旧#14，REPORT 27.3）：12s 片段 e2e p50 970ms、2s 片段 104ms；
    软切上限是否该按"内容类型"调
12. **锁定 `en` 重跑四条素材**（旧#15）：现有旧基线（asr 占墙钟 16.6%、首字 374~756ms）是
    en + 无上下文 + 2.47，与本轮前提不同，**不能相减**
13. **`runtime.json` 与读取方的键**（旧#16）：`start.ps1` 读 `llama_pinned_path`、
    `resolve_llama_runtime` 读 `memory_total_mib`。2026-10-09 核盘：`memory_total_mib` **已在**（4096），
    另有 `fit_target_mib 768` / `vram_used_mib_after_load 2178`；`llama_pinned_path` 存在但为空串；
    `_deprecated` 块保留旧写入者的值且无人读 ⇒ 原判"两个都没有"**已部分过时**；
    那两个显存数是否仍来自旧探针**未核**
14. **`speech_ms > buffer` 的根因**（旧#18）：A 臂 10/27、B 臂 9/24 出现"报告语音时长大于交付缓冲区"。
    A 臂没有挂起逻辑 ⇒ **与 #6 无关**，是既有问题。而 REPORT 多处拿 `speech_ms` 当"交付了多少内容"的代理
15. **撤回（char 缩字）的两个真旋钮**（旧#20）：① `partial_interval_ms` 250→750（= 旧#4，**已是生效值**）
    ② 显示层对 `char` 动作的策略（= 旧#5 一带：`settle_until` 单因素，`TRANSLATION_SETTLE_MS` 可注入；
    原判据 = 上屏首字 / 被挡 / 覆盖率）：限流或 settle 期内合并。⚠ 先看密度：char 缩字只有 2.4~7.2 次/分，
    够不够被注意到由用户判断
16. **2.55.0 要不要测**：出货缺省（无 pin 时）已浮到它，而所有数在 2.47.0（replay pin）/
    2.53.0（serving pin）上 —— **待用户定**

**已完成 / 已结案（台账，一行一条）**

- 旧#1 合并 `gemma-audio` → `main`：**已完成**（`6f84d9d → 56ce447`，tag `pre-merge-2026-10-07`）
- 旧#6 切片下限 = **挂起并合并**（不是抬门槛丢弃）：**已采纳为决定**（2026-10-08，REPORT 29 + 逐句核对）——
  `TRANSLATION_VAD_MIN_SPEECH_MS=1000` + `TRANSLATION_VAD_MERGE_WINDOW_MS=2000`，**默认值尚未翻**
  （`VAD_MERGE_WINDOW_MS=0`）⇒ 要开必须两个一起设，见〈#6 的决定〉
- 旧#4 partial 250 → **750 ms**（原判据：风扇安静？字幕慢？partial 阶段文本是否更完整？两格都录屏）：
  **已生效**（`config.py:163`）—— 250→750 只付 +18ms 首字、750→1000 付 +224ms；文本质量见〈#4 的观察点〉
- 10-09 #49-a manifest 的 `wav` 生产端（`blog_video_test.py:469` 记 `WAV_PATH` 常量而跑的是 `_sample_wav()`）：
  **已修** —— 现在同时记 `wav` 与 `timing`，不一致就拒绝出数（**从 `wav` 推导抓不住说谎的 `wav`**）
- 10-09 #49-b `perf_compare` 只处理裸 list 而 `build_timing.py` 产出 dict：**已修**（喂 dict 会抛 TypeError
  ⇒ **这条路径从来没被真正执行过**，唯一被读过的文件正好是唯一能被读懂的那个形状）
- 10-09 #51 coverage 差 5 个点的归因：**路径 B 证伪**（VAD 切分逐位相同 31/31、25/25、11/11）；
  **路径 A 也不是**（14 条 drop 全是 `kind=partial`，丢的是中间帧）。真链：截断 A 0 / B 5 →
  `asr_finals` 25 / 23 → coverage 96% / 91%
- 10-09 #53-a 同配置连跑两次测散布：**可复现** coverage 21/23、asr by kind 137/11、window 207.1s；
  **不可复现** `audio_end_to_paint` p50 548↔705、>1000ms 29%↔38%，e2e/dwell 同类
- 10-09 #53-b 抬上限前先把判据做成能读的：`asr.truncated` 现在带**有界的 `head`/`tail`**。
  数字式重复度分数**试过并撤掉**（给"引述自己"的长转写 0.75、给重复循环 0.89，分不开）
- 10-09 #1 `TRANSLATION_QWEN3_ASR_MAX_TOKENS` 128 → 256：**已做，判据四步走完（REPORT 54）**。
  守卫放行（206.9s）→ truncated 4 条（不是 0）→ coverage 21/23（第三次逐位相同）→ `head`/`tail`
  确认重复填满。**上限不是太紧**：抬上去没改任何一项，只把丢弃的垃圾从 193 变成 363 字符
- 10-09 #1' 为什么这 2 个 utterance 让语言判定出错：**已做，REPORT 55，而且前提是错的** ——
  语言一直在日志里（`pipeline.py:281` 的 `lang=`），从没卡在工具上
- 10-09 #2 `replay.ps1` 播放被中断时应当报错：**已做**。第三道守卫 = 播放距离 vs clip 长度（80%），
  **不用 coverage 判断**（`B256` 曾在 34.2s/180s 上报 `coverage 5/5 (100%)`）。6 次回放 3 次被掐断，
  三次的秒数写进了错误信息
- 旧#9 真实管线的 VAD 微碎段：**结案**（REPORT 23）—— 回环采集与 socket PCM 混进同一个 VAD；
  `TRANSLATION_LOOPBACK=0` 下 48 段/96ms → **31 段/1824ms**（探针 32 段/1632ms）。
  ⚠ **那个布尔开关已不存在**，设了也没用（已被 `AUDIO_SOURCE` 取代）
- 旧#11 回环在服务器进程内没送达 VAD：**结案**（REPORT 26）—— 守卫放错层（`push_pcm` 是回环唯一
  入口却被用来拒绝"所有音频"）；修后 `vad.end 6 / enqueue 113`，实测译出"再见"
- 旧#12 删死代码 `extension/pcm-worklet.js`：**已删**（`431686e`，确认无 `addModule`/
  `AudioWorkletNode` 引用）；顺带发现上一轮往这个没人加载的文件里写了 13 行 `captureEnabled` 门控
- 旧#13 `start.ps1` 过期 MTP 注释：**已改**（按版本重写：2.47 会 503；2.53 不会但净负 49.6 vs 59.2 tok/s）
- 旧#17 `vad.end` 在合并臂下不是段数：**已完成**（REPORT 29.8）—— 加 `delivered=0/1`；三条独立的
  路在真实音频上给同一个数 27/24（**不能用 `vad.end − vad.held`**：它依赖"非合并臂 held 恒为 0"）
- 旧#19 gate 500 / merge 1000：**已完成，M5 失败，不翻默认值**（REPORT 34）—— M1 三条都过、
  M2 两条可测的过（blog +119ms）、M4 三条都改善 7~9 点，**但 M5（char 缩字/分钟）三档全平**
  ⇒ `min_speech_ms` 只推迟第一条 partial、不减少条数 ⇒ **撤回的旋钮是 `partial_interval_ms`（旧#4），不是 gate**

**仍不做**：不建阅读地板（48-b 失败条件已触发，地板是空操作）、不调 `hold`（结构上够不着）、
不动 `partial_interval_ms`（#19 已证）、不动 `_is_char_edit` 的 0.72、
**不引用单次运行的 `audio_end_to_paint` / `e2e_first_to_paint` 差值**、
**不再调 128 上限**（REPORT 54/55：治不了，病在识别侧；且"B 退化更严重"在文本层面是反的 ——
内部重复 B 2.5% 对 A 6.0%）。

**10-09 #53-a 撤掉了 48-c 的部分结论**：`audio_end_to_paint` 的 A/B 差值（690 对 548）**落在同配置
连跑的散布之内，作废**；`2.47 的 600 对 2.53 的 548` 同理。**A 与 B 唯一站得住的差异只剩 coverage**
（96% 对 91%），机制已定位（128-token 上限，5 次截断 ⇒ 2 个 utterance ⇒ 少 2 条终稿，1:1）。

**B 的判决 = `INDETERMINATE`**（不是"说不清"，是要判的那一项需要重复测量）。A/B 在 2.53.0 上
「同时重测」严格说**从未完成**：跑过 4 次，全是单次运行，而 53-a 已证明单次差值落在散布之内。

**#6 的形态被证据改过两次**：原计划"门槛 250ms → 1.0s"会**多丢 4.9s 真语音**（REPORT 22.2）
⇒ 改成"挂起并合并"；现在连它的**假设**也被实测改掉 —— "更长片段→更好翻译"不成立，
实际是"更少中间态换更快首字"（REPORT 29.5）。合并有窗口上限、**默认关闭**，要开必须两个一起设。

**先后关系**：#7 无损，可独立于 #6 先做。**#6/#7 是同一族**（都在"切分/送翻译之前"），要么一起做、
要么先做 #6。**#6 不要与 #4/#5 同时做**；**#4 与 #5 也不要和别的改动同时做**（否则分不清是哪个造成的差别）。

### #4 的观察点，以及它对 #5 的影响

```
原计划   ASR p50 / 首字 p50 / 风扇（耳朵）
新增     partial 阶段的文本质量 —— 回看用 samples/PARTIAL_QUALITY.md
         只盯"整句说完之前屏上出现过什么"，逐句并排比 250 / 750
```

**理由**：TTS 最干净的条件下，small 在短窗口上**没有一次给出完整正确的输出**（0.3s 空、0.6s 认错、
1.3s 截断），而 large 全部正确。管线喂给 ASR 的恰恰就是这种窗口，所以 750ms 让窗口更长，
**可能顺带改善 partial 文本质量**。（这是 10-07 在 small 上得出的判断；默认 ASR 已换成 turbo。）

**⚠ #4 可能让 #5 变得更急，不是更不急。**

```
假设（错的）：#4 让字幕更准 → 显示层更轻松 → #5 不急
实际可能是：partial 更新变稀疏 → 屏上文本停留更久 → hold 次数上升 → 被顶掉的句子变多
```

**方向可能反**：新内容更稀疏对显示层是**负担**不是减压（hold 的定义就是"同一句停留多久"）。
**所以 #4 之后必须重看三个数**（14.4 节的门槛）：hold p50 ≤800ms（当前 1012ms）、
hold p90 ≤1500ms（当前 2011ms）、被覆盖句数 ≤15（当前 33）。**若变差，#5 从"不急"变成"更急"。**

---

## 停止 / 待验证

### 待验证（不排期，触发才动）

| 项 | 触发条件 | 现状 |
|---|---|---|
| **音频直出端到端** | #4 之后风扇仍吵 | **测试未结束，不下结论。**保留为可选低功耗模式。省电 71% 来自取消 partial，仅 8% 来自换模型 |
| **Gemma 换翻译**（非直出） | 自用后译文读着别扭 | 翻译不是瓶颈（ttft p50 30~36ms）。有代码与显存代价，需改提示词模板 |
| **ASR 换 large-v3-turbo** | 电影/播客识别率不够 | **已改为默认**（`config.py:99`，2026-10-09 核），但 250ms 节奏下不可用：`asr share of wall clock 96.0%`、`asr >250ms 100%`、`asr p50 821ms`、GPU 91W/75~77°C。识别质量到位（`er_3min` 16/16、coverage 84%）。**无 small 对照**（`logs/v1-er_3min.*` 窗口只 20.9s 且 GPU 上有别人的 llama-server）⇒ **自洽组合是 turbo + 旧#4（750），不是 turbo 单独** —— 而这两条**现在都是生效值** |
| **pysbd 分句** | 译文常是半句 | **只放 final 路径，不放 partial**（放 partial 必须等句末 ⇒ 直接打在"慢半句"上；放 final 质量提升不付延迟代价）。`pysbd` **未装**，先别装 |
| **Hy-MT2 1.25-bit** | 显存爆了 / 要加新模块 | `Hy-MT2-1.8B-1.25bit-v2.gguf` 440MB 已下载，**但加载不了**（`tensor blk.0.attn_k_norm.weight has offset 203154464, expected 203572156`）。只省显存不提质 |
| **hy-mt2 的 MTP 净收益** | **换一份带 MTP 张量的权重**（现权重 0 个带 mtp/draft/nextn 的张量） | **不成立、不排队**（见〈MTP 这条线到此为止〉） |
| **2.55.0 上的任何测量** | 用户定 / 要在 2.55.0 上出数 | 出货缺省（无 pin 时）已浮到它；所有数在 2.47.0（replay pin）与 2.53.0（serving pin）上 |

### 停止（每条带触发条件，别无条件复活）

| 项 | 理由 | 触发条件 |
|---|---|---|
| **whisper.cpp 后端** | CPU 单次 400~1260ms，跟不上 250ms 节奏 | 不换 ASR 就已堵死。**另需 CPU 侧功耗传感器**才能重估 |
| **large-v3 实时** | 显存 3954 MiB 与 hy-mt2 不能共存；222.wav 上并不更准且有幻听 | 显存不再是约束时 |
| **功耗测量线** | 插电形态，+10W 只是风扇曲线上的一个点 | 部署形态改为电池/无人值守 |
| **langlock 语种锁定** | 模块就绪（20 测试），卡在人工标注 | **需求先确认**：看完多语种素材，若手动选语种就够，则永久搁置 |
| **折叠去重修复** | `'哈哈哈哈哈哈'→'哈'`；疑似来自 main 原有翻译侧"五次以上折成一个"规则，非本次合并引入。修法：改为封顶 3，加哈/啦/啊单元测试 | 错误分桶（队列 #7）之后 |

### whisper.cpp 关闭理由（区分实测与推断）

- **实测**：CPU 单次 400~1260ms（ctx 220 静默 576ms），进不了 250ms 节奏
- **实测**：ASR 上 GPU 的真实成本 **+10W**（此前 +0.11W 是采样窗口错位，见 REPORT 17.4）
- **推断，未实测**：whisper.cpp 模型文件本身省显存（Q5_1 −284 MiB）、GPU 单次快（74ms），
  但整套后端（独立进程 + HTTP + Python 集成）是否让整机功耗反升，**从未测过**；CPU 侧无传感器，测不了

### langlock 关闭依据（已更正）

**旧依据是错的**：曾写"检测在短片段上不可信" —— 那来自 **CT2 small** 的分片检测，检出的
`ja/sl/bn/de` 里混着幻觉。

```
CT2 small         ja18/39，但含 sl / bn / de 幻觉      → 检测不可信
large-v3-turbo    ja18/39，ja 置信度 0.88-0.96，无幻觉 → 检测干净
```

⇒ "检测在短片段上不可信"**不成立**，成立的是"**small 的**分片检测不可信"。
**仍然不排期**，理由换成更朴素的一条：默认链路曾经是 small，要解决得先解决"默认链路的检测"，
而不是先建锁定机制 —— 而默认已换成 turbo（2026-10-09 核），这条理由**要重新评估**。
（REPORT 19.4 记为一次被错误前提带偏的判断。）

---

## 已知代价（明示接受，不是待修）

```
字幕会闪
  现状  94 paint/分钟（两臂 92~98），其中 29 次爆发/分钟，12 次/分钟 连着清空 ≥3 次，
        单次最多 35 帧 / 272ms，帧间隔 p50 2ms 而 p90 1846ms（双峰）
  成因  到达速率，不是渲染。四条渲染路径全部休眠或不可见（REPORT 58）
  旋钮  partial_interval_ms 750 → 1000：paint −25%，首字 +224ms
  档位  250 → 750 那一步只付 +18ms；750 → 1000 付 +224ms ⇒ **不是一个量级的取舍**
  决定  不动。已知代价（会闪）比未知代价（慢半拍 224ms）好接受
        ⚠ 这个旋钮**不对应**「字流速涌现」那个现象 —— 那个在翻译侧切片上（见日志）

字幕会慢半拍
  块状输出是唯一能取消行内改动的手段，代价是首字延迟中位 +3.0s、p75 +7.7s，与体感目标
  「字幕不慢」直接对立。便宜的等价手段是 partial_interval。 决定 不做

按标点分块（TRANSLATION_BLOCK_BY_PUNCT，默认 0）
  三轮实测（blog_3min，同链同素材）：
            baseline   BLK1     BLK2     BLK3
    上屏/分     93.7     68.1     33.9     36.1
    首字p50     37ms    366ms    354ms    342ms   ← 超 200ms 底线
    ≥3连画      11.7      0.0      0.0      0.0
    帧间隔p50    2ms    827ms   1224ms   1206ms
    兜底占比      —     100%     100%      60%
  收益真实（≥3连画与帧间隔，正是「闪」被测到的那一项），代价也真实（首字 9.2 倍，落在
  ">300ms = 兜底在主导、判据没修好"那一档）。**默认关闭**；代码、开关、四份基线
  （perf_caption.CATCH1/.BLK1/.BLK2/.BLK3）都留着
  未解决：首字 342ms 的根因；子句标点为何这么晚到；登记的 17 次/分两种判据都不是
  ⚠ 块状路径**完全绕过 hold 与 dwell**（hold_wait n=0、dwell_gap n=0、defer 0），节奏从此只由
     块状时机决定 —— 实现副作用，非设计意图

块状由 ASR_BACKEND 派生（whisper=流式 / qwen3=块状 / gemma 不动）
  **代价来自「块状」这个模式，不是来自 qwen3**：whisper+块状 首字 p50 342/354/366ms，
  qwen3+块状 373ms，同一档 ⇒ 以后不要把「块状的代价」和「qwen3 的代价」混在一起归因
  根因是三层里「只换该换的字」那一层没做（每子句一个 canvas item）；应用层的子句比对已落地
  （server/caption_clauses.py），渲染层那一半还没有
  ⚠ 块状在实践中并未实现「子句内不改写」：兜底仍占 60~68%，每次画的是正在变长的文本
```

## 字幕行数：上限是硬编码的 2，没有设置

```
查证（三处）
  config.py       零个行数字段
  wrap_caption    max_lines: int = 2   ← 默认值，不是从哪读来的
  生产调用点      wrap_caption(body, wrap, self.font_line.measure)  ← 没传实参
  扩展 UI / popup 零个 lines / 行数 ｜ 状态广播 没有行数字段
```

**不是「设置说无限、代码说 2」—— 那个设置不存在。**与 `fit_caption_width` 同类：不是空设置，
是**根本没有那个设置**。记忆里的「无限行」大概是窗口高度 —— 高度确实由行数算
（`30 + lines*line_h + 8`），而 `lines` 永远是 2，所以框看起来永远两行高。

### 第三行到来时走哪条路径：是 A，不是 B

```
body → wrap_caption(默认 2) → display_lines（最后 2 行）
        wrap_all              → all_lines（真实需要几行）
        dropped = len(all_lines) - len(display_lines)
        start_y = 25 + dropped*line_h
        _draw(...)  ← 一次 create_text
        for stale in find_all(): if stale != fresh: delete(stale)   ← 只删刚换掉的那个
        if dropped: after(16, _step_slide) → 只动 coords，缓动回 25
```

**没有「清屏重画」分支。**`canvas.delete` 全文件只有两处：空 body 时清画布、删掉刚被替换掉的旧
item。**画布在整个过程中非空** —— 这正是 `6c156aa` 的目的。实测位移量：`dropped=1` 时
`start_y=66 → 25`，4 帧，只动 `coords` ⇒ **是「第二行滚动到第一行」，不是清屏。**

⚠ **但这条路径在 blog_3min 上从未触发过**：

```
CATCH1（流式）   1053 次绘制，需要 3 行以上 0
BLK1/2/3（块状） 1086~1088 次绘制          0
qwen3+块状       1250 次绘制              0
```

**用户报告的「被清屏」不在这条路径上** —— 要么发生在别的素材/别的宽度字号下，要么是别的现象。
**要复现需要用户实际的窗口宽度与字号。**

**直播侧的确认（2026-10-09，用户）**：「直播也很难出现 3 行，即使有也会滚动为 2 行」——
与上面五次运行零触发一致；**且他确认滚动为 2 行的观感可接受** ⇒ **上限维持 2，不做成设置。**

### 多 item（每子句一个 canvas item）暂时不需要

```
应用层已落地  server/caption_clauses.py（cf2dbc0，纯函数，16 条测试）
              能算出哪几个子句未变 / 哪个变了 / 哪个新增
渲染层未做    算出来了但没被消费 —— 与 fit_caption_width「算完丢掉」同形
不做它的理由  整行重绘的代价远低于一帧：wrap_all 扫 100 个中文字 0.072ms vs 一帧 16.7ms，差三个数量级
它能解决的    「丢首行导致内容缺失」—— 那有更简单的解法（放宽上限），且窗口长高的代码已支持
              （height 按 lines 算、底边钉住向上扩）
```

**结论：先定上限（2 / 3 / 不限 / 由窗口高度决定 / 做成设置），再决定要不要补渲染层。**
顺序反了会做一件不需要做的事。

## 块状的 final 从来不上屏 —— coverage 47% 的根因（2026-10-09）

**一个机制解释三个症状**（结论级）：coverage 47%（一半最终译文没上过屏）、
`audio_end_to_paint` p50 1362ms / >1000ms 56%（BLK2/3 是 816/789ms、41%）、「卡一下才出现」。

### 定性 + 机制（只用现有日志，30 分钟时间盒）

```
manifest 49 条 final   字幕窗画过 98 次、不同 sig 93 个
A 被 collapse_repeats 吃掉 0 条 ｜ B 根本没发出去 0 条（26 条都在 manifest = 都发过）
C 被兜底覆盖 26 条 ← 全部在这里
流式包到达 → 带新标点的包触发边界 → 画下（内容尚不完整）
继续来的包标点数不再增加 → 边界不触发 → 落 400ms 兜底 → 又画一次 → _clause_fell_back 置位
final 到达 → 它的标点数 == 已画出那行 → 边界不触发；_clause_fell_back 已置位 → 永不重画
```

**判据「标点总数增加」在 49 行里一次都没成立过**（final 标点 > 行内最大标点 = 0/49）：final 之前，
同一句的中间包已经把标点带出来了。**final 是这一句最完整、最该被看见的版本，而它恰恰是唯一触发不了
边界的那一版 ⇒ 覆盖率掉的原因不是丢包，是"最后那句话被前面的残句挡住了"。**
`audio_end_to_paint` 量到 1362ms 也是同一机制 —— 它量的是「兜底那次绘制」，不是「final 上屏」；
屏幕上最后停住的是中途的残句，也就是"卡一下才出现"。

### 推翻的两个判断

```
我曾说「块状既卡又没有真正块状」  结论对，但机制不是门太宽，而是 final 上不了屏
我曾说「③ 只关兜底，边界触发保留」 错。③ 会让它更糟：pipeline 只发带标点的包
                                 ⇒ 标点更早进 self.line ⇒ final 更不可能超过
                                 ⇒ 边界触发更少 ⇒ final 更画不上
⇒ ③ 暂缓，理由不是"延迟不如内容重要"，而是它会加剧造成 47% 的那个机制
```

### 两臂对照与未确认的边界

whisper+块状 `BLK1/2/3` coverage **48/49、40/49、42/49**（82~98%）｜qwen3+块状 `PUNCT/PUNCT2`
**23/49、23/49**（47%）⇒ **两次逐位相同 ⇒ 不是波动，是这条臂上的稳定行为。**

**final 到达时 `self.line` 是否可能已含下一句的开头**（若含，强制上屏 final 会丢下一句；取决于
pipeline 发 final 的时机）—— **仍未确认**。

### 修法（方向二：final 强制上屏）

```
判据  if is_final and cleaned != self.line:  画 + 清 _clause_fell_back
```
不需要「这是不是同一句」的判据（`promote` 不跨句）。改动四处，都在字幕窗内：
① `_send(kind, value, final)` final 进 UDP payload ② `update_caption` 转发 `payload["final"]`
③ `_listen/commands.put` 队列带第五个元素 ④ `_pump → set_text → _punct_gate`。
**pipeline 不用改**（final 已在 translation 包里，只是从未被转发）。`caption.block` 新增 `why=final`。

**测试抓到一处实现漏改**：`_listen` 的 `commands.put` 因缩进不符没被打上，仍是 4 元组，而 `_pump`
按 5 个解包 ⇒ 第一个包就 ValueError。**全量测试发现不了这类**，是那条断言源码里
`bool(message.get("final"))` 的存在性检查抓到的。

### 实测与复现（PUNCT3 改后 / PUNCT4 复现；BLK2/3 = whisper+块状）

```
                 PUNCT2(改前)  PUNCT3   PUNCT4   BLK2/3
coverage         23/49  47%    49/49    49/49    82~98%
caption.paint        98        132      142      ← 稳态 130+
audio_end_to_paint p50 1367ms  p50 194ms         816/789ms
audio_end >1000ms    56%         8%       8%       41%
block why         b33/t65   b28/t63/final41
timeout 绝对数       65         63       65       ← 一件没少
timeout 占比        66%        48%      46%       ← 降了，纯分母效应
```

三本账互相对上：`caption.send text final=1` 49 条 → 字幕窗 `why=final` 41 次 → 另 8 条与当前行同文本
所以不重画（sig 本来就在屏上，coverage 仍 49/49）。**两次独立运行都 49/49，而改前两次都是 23/49
逐位相同；机制事先预测 + 幅度对得上 + 两次复现 ⇒ 这个结论可以引用了。**

**代价明知**：绘制 98→132（+34）=「残句被完整句替换」那一次重绘，测不出体感，只能看。
**这次改动没有削弱兜底，只是额外加了一次画**：65 次兜底画出残句 → 38 次在几百 ms 后被 final 覆盖
⇒ 那 38 次是用户看见又立刻被替换的内容；**③（关兜底）的价值没有被这次改动吃掉。**

### 两个不能当改善引用的数（单次运行；差值不单独作结论）

```
paint_since_send p50 362→55.8ms   分母构成变了（改前 98 次全是边界/兜底；改后 132 次里 41 次是
                                  final 到达即画，since_send 天然只有几十 ms）⇒ 跨区间不可比
audio_end_to_paint 1367→194ms     一半是口径位移（改前量"兜底那次绘制"，改后量"final 上屏"）
无歧义的是 coverage：同一臂、同一口径、47%→100%，且机制事先预测过
```

**体感判定（只有眼睛能判，埋点测不出）**：final 覆盖残句 → 可见的跳，体感是「一个人说话断点续」，
勉强可接受；**比改前好**（改前屏幕永远停在残句上、final 从不上屏），**但那一次跳真实存在，别当成已消除。**

### 「只有字」模式修复（10-09 晚；上半场留了一个崩溃，下半场才修掉）

**上一轮写下的三条，两条是错的，第三条漏了。先纠正，否则下一个人照错理由改回去：**

```
① 上一轮未提交的那版把 apply_position_lock 的两行尾巴留在了 _set_input_mode 里
   ⇒ 那个方法调用自己 ⇒ __init__ 里的 _apply_colors 直接 RecursionError
   ⇒ 字幕窗进程起不来，"caption window ready" 都打不出来，一个字幕都没有
   = 用户说的"把字幕给我弄没了"。已实测复现（构造 _Caption → RecursionError）
② 截图里的黑框不是"背景没去掉"，是锁定 checkbox 自己：_apply_colors 只把「只有字」那个控件
   重新着色成 _KEY，锁定那个没被重新着色 ⇒ 它在透明窗口上留一个 bg=#000000 的方块。删掉即消
③ 「extension 侧完全没听 8767」是错的，通道一直在：
   caption --UDP8767--> ws_server._listen_caption_state --WS--> offscreen.js
     --storage.captionTextOnly--> popup 的复选框
   反向：popup --storage--> background.applyCaptionStyle --caption-style--> offscreen
     --WS style--> ws_server --UDP8766--> caption
   真正坏的是那个监听器**每个连接起一个线程**：第二个 bind 失败（WSAEADDRINUSE），新线程死在
   stderr，帧继续发给已经关闭的连接 ⇒ 第一次重连之后 caption 的勾选再也到不了扩展，且不报错
```

**本轮实际修法**

```
1. 锁定整体删除：caption 的 checkbox / 常量 / _press 判断、ws_server 的 positionLocked、
   offscreen.js 的 storage 写入、WS_EX_TRANSPARENT 全部删掉
   ⇒ 只有字 = 无框 + 透明 + 可拖动，没有"锁死"这条路（用户 2026-10-09 确认）
2. 只有字 在窗口**已经显示**时也走一次 _strip_frame()：透明靠 WS_EX_LAYERED，而只有
   _strip_frame 会设它，已显示的窗口不会再跑 show()
3. _broadcast_state 只发变化值：扩展收到 state 会回一个同值的 style，回到 caption 又是
   apply_text_only ⇒ 两边都不比较，这个环在 WS+UDP 上一直来回跑
4. 8767 监听器改成进程一个，连接只换发送目标；bind 失败会打印一行并允许下一个连接重试
   —— 原来它只往 stderr 丢 traceback，症状是"点了没反应"
```

**下半场（10-09 晚）：只有字 连表头一起隐藏；同步靠状态源，不靠把控件留在屏幕上。**

用户定义的验收（照做，别自作主张改）：
```
框里点「只有字」→ 黑框和「只有字」三个字一起消失（全屏下只剩字幕，舒服）
插件的对勾同时勾上（这叫同步）
插件里点一下 → 黑框回来，对号框和「只有字」重新出现（一次点击，不是两次）
```

**「按两次」的成因**：扩展勾选框只写 storage，转发靠 `storage.onChanged`；
storage 由"最后动的一方"写入 ⇒ 值没变时 onChanged 不触发、什么都不发。
而字幕窗的表头在只有字模式下是**隐藏的**（那是这个模式的本意）⇒ 插件那一面是唯一回头路，
它的对勾必须是**字幕窗的真实状态**，否则第一次点击花在窗口并不处于的状态上。

四处：
1. caption 的「只有字」控件在只有字模式下 `place_forget`，与黑框一起隐藏 —— **刻意如此**。
   当天试过相反的改法（控件常驻表头）并回滚：那样视频上永远挂着一个对号框和一小块黑指示框
2. **服务端不存这份状态**（`_CAPTION_TEXT_ONLY` 已删）。它先是 WebSocket handler 的局部变量
   （只被扩展写的帧更新 ⇒ `get_state` 答"扩展上次要的"），改成本进程一份、两端都写之后
   **仍然会过期** —— 用户当场看到"屏幕在只有字、插件的对勾却没勾上"。缓存就是第二个状态源，
   **能问就不要存**：`get_state` 改成 `request_caption_state()`，把问题转给窗口
3. caption 新增 `kind == "state?"` 应答：收到就**强制广播一次**（`force=True` 绕过去重）。
   "这边没变"不等于"没人需要知道" —— 窗口是这份状态的唯一持有者
4. popup 打开时问一次（`caption-state`）。勾选时**直接发 `caption-style` 帧**，意图不搭
   storage 的顺风车。**窗口没答（旧版本 / 进程死了）就在状态行说明** —— "问了没答"是故障，
   "没发出去"（没在跑 / 仅音频）不是，别误报

实测（`scripts/probe_caption_text_only.py`，灰底衬在下 + 真 UDP，照用户流程走）：
```
1. 有框            非背景 99.7%  控件在    var=False
2. 框里点只有字     非背景  2.7%  控件隐藏  var=True   恰好汇报 1 帧 [True]
3. 插件点一下       非背景 99.7%  控件回来  var=False  ← 一次点击
   有框时可拖动 drag=True
   logs/probe_header_textonly.png = 纯灰（框和控件都没了）
   logs/probe_header_back.png     = 对号框 + 「只有字」
scripts/probe_caption_state_channel.py（真进程）：style(True)→1 帧 / 同值再发→0 帧 /
   style(False)→1 帧 / **state? 且状态没变 → 1 帧**（连问两次两次都答）
logs/popup_sync_probe.js（stub chrome 真跑 popup.js）：打开即问状态 / 点击发帧+写 storage /
   反向跟着走且不回发
python -m pytest -q → 385 passed / 1 skipped / 10 failed（10 条仍是沙箱拒写临时目录）
```

**教训（同一天、同一处，改了三次方向）**
```
① 抱怨"不同步"时，我先让控件留在屏幕上（修像素）—— 坏掉的是这个模式本身
② 改成"两端都写同一份缓存" —— 缓存照样会过期，用户第二次看到"屏幕在只有字、对勾没勾"
③ 最终：不存。窗口是唯一权威，别人要就**问它**（UDP 一句话，50ms 内答）
⇒ 判据：**同一个状态出现第二份副本时，先问"这一份能不能删掉、改成问一次"**；
   缓存只在"问不到"时才成立，而这里问得到。写下来，否则下一个人会再缓存一次。
```

## UI ↔ 字幕窗 的协调（三条状态、一个权威）

**规则只有一句：屏幕上的东西由字幕窗说了算；`chrome.storage.local` 只是"下次开始时用什么"。**

```
① 窗口在跑    复选框 = 问出来的真实值；hint「字幕窗当前：只有字 / 黑底框」
② 窗口没在跑  复选框 = storage（下次开始时生效）；hint「字幕窗未运行 —— 下面是设置」，不是错误
③ 服务没连上  不报错，等重连；连上后自动重问一次
```

**谁改谁**

```
插件改  → ①写 storage（意图）②直接发 style 帧（当前窗口）；复选框先乐观更新，
          窗口的回报到达后以窗口为准
窗口改  → 广播 → 服务端转发 → offscreen 写 storage（意图同步）+ popup 对勾跟着勾
只有字  → 窗口表头（含它自己的控件）随黑框一起隐藏 = 这个模式的本意
          ⇒ 回头路只有插件 ⇒ 插件必须显示窗口的真实状态（就是①那个"问"）
```

**不许用定时器猜"当前状态"**

```
唯一的"有没有窗口"权威是服务端（它持 CAPTION_PORT）：
  get_state 时窗口在跑 → request_caption_state() 问它；没跑 → 回 caption-absent
踩过的坑：用 1.5s 超时猜"窗口没答"就报警。而线路切换会重启后端 ——
  socket 已开、窗口还没起 ⇒ 每次切换都误报，用户看到"一次有一次没有"。
⇒ 判据：**能问就问，问不到要说"没有对象"，不要用超时替对方回答。**
```

**线路切换（"一次有一次没有"的另一半）**

```
切换时 popup 进"重启中"（不问、不报）
后端被杀 ⇒ 字幕窗是它的子进程，一起没了
offscreen 检测到断开**自己重连**（1.2s 一次，最长 200s；重发 config/arm/style）
连上后 popup 重问一次 ⇒ 用户不需要再按"开始"，字幕自己回来
（用户主动"终止"时不重连）
```

实测：`logs/popup_sync_probe.js` 三条状态逐条断言；`logs/offscreen_reconnect_probe.js`
断开→1.2s 后新 socket→开场帧重发→通知 popup；用户停止后不再重连。

## 模型定位

```
large-v3-turbo（CT2）   **默认链路在用**（config.py:99，2026-10-09 核）；也能当离线文本校验
whisper.cpp large       仅离线文本校验，不进流水线
CT2 small               回退（config.py:100）—— 历史基线多数在它上面测的
whisper.cpp small Q8    需要看音频"到底说了什么"时用
whisper.cpp small fp16  舍弃
```

**large 是对照，不是基准** —— 它能把日语解码成 `これは日本語で病風と呼びます` 这样干净的结果，
所以能用来定位 small 漏了什么；但仍不能当真相。

## 素材时间轴（2026-10-07 全部重建）

四份原本用 `language='en'` 强制生成，**含非英语的素材因此整份是编的**：

```
meeting_6min_timing.json  165 句，含日/韩/汉字/西里尔/阿拉伯字符：0
er_3min_timing.json       26 句，含西里尔字符 0 句（"Shul shul" 是被译成英语的俄语）
clip222_90s_timing.json   原本不存在 → 222 当时根本没法回放
```

已用 `scripts/build_timing.py`（分片级判语种）重建。格式：
`{ "audio_total": …, "covered_span": …, "segments": [{i,start,end,lang,src,p,text}] }`

| 素材 | 段数 | 覆盖 | 语种 |
|---|---|---|---|
| `e_3min` | 21 | **37%** | en21 |
| `er_3min` | 42 | 72% | en39/ru3 |
| `clip222_90s` | 33 | 81% | ja18/en8/ko4/zh2/it1 |
| `meeting_6min` | 140 | 76% | 11 语种 |

**`e_3min` 只有 37% 覆盖**，184 秒电影里只有 67.6 秒被判为可译语音。可能是 VAD 在带音乐床的素材上
过于保守，也可能那段确实以配乐为主。**判断方法是人耳听** —— 先别改 VAD（改 VAD 会影响所有素材）。
覆盖率**口径未变**（仍是译出句数比），`covered_span` 只是让分母可见。
（2026-10-09 核盘：`lecture_mix_timing.json` 也有 `covered_span`；`blog_3min` 与 `smoke_10s` 还没有，见队列 #8。）

## 仓库

```
f93de9d  Keep diff_defaults.py
e763151  Rebuild the sample timelines
042f13e  Separate the notes by what has to be read
56ce447  Print which tree this process is
2598379  Stop tracking media files
main / HEAD / gemma-audio  同指（已快进） ｜ 回退点 tag pre-merge-2026-10-07 -> 6f84d9d
```

`gemma-audio` 保留。**pytest（2026-10-07 晚）107 passed**（新增 `tests/test_translation_context.py` 8 条，
钉住"上下文真进提示词"与"ctx=0 逐字节等于改动前"）；此前 99 passed。
`extension/` 与 Native Host 未变 ⇒ 主机无需重建（`start.ps1` 是文件调用，不在 exe 里）。
**最近一次（2026-10-09 晚，输出方式归并到路线）：385 passed / 1 skipped / 10 failed** —— 10 条全在
`test_backend_cycle.py`，全是 DSH 沙箱拒写临时目录下的 `server.pid`，与改动无关。
（REPORT 60.6 记的是更早那次 370；中途几版分别是 375 / 377 / 378 / 379 / 380 / 382。）

## 实验日志（一行一条）

**一条都不删；两条逐字重复的 10-08 行（"那四项 sig 看不见的"、"hold 地板"）已并成一行，
并时把两行的决定都保留（本轮合并）。**

| 日期 | 问题 | 结果 | 决定 |
|---|---|---|---|
| 10-07 | ASR 放 CPU 能省 GPU 电吗 | GPU 侧 **+10W**（原 +0.11W 是错窗口）；CPU 单次 400~1260ms | 关 whisper.cpp |
| 10-07 | audio_ctx 能否缩窗提速 | 不缩放；<150 触发 5 秒级病理 | 加下限 150 |
| 10-07 | 量化能否省显存又提速 | GPU 不提速；Q5_1 省 284 MiB | 记下，未采用 |
| 10-07 | 语种锁定能否用置信度触发 | 置信度单峰，无 >0.9 段 | 不按置信度触发 |
| 10-07 | 边界处错语种解码有多糟 | 91% 垃圾；译文一致+相关 **25%** | "先上屏后覆盖"被证伪 |
| 10-07 | C 的软切在 whisper 链路生效吗 | **生效**（`why=soft_cut` 实测） | 12s 上限是唯一新旧差异 |
| 10-07 | 字幕侧折叠影响多少存量译文 | 82,522 条中改 140 条（0.17%） | 不阻合并，修复排待办 |
| 10-07 | 素材到底是不是多语种 | **222: ja18/en8/ko4/zh2/it1**；**会议: 11 语种** | 旧描述全错，已更正 |
| 10-07 | 强制 en 生成的时间轴可信吗 | 会议 165 句零非英语字符 | 四份全部重建 |
| 10-07 | 旧 e_3min 时间轴差在哪 | 不是语种，是切分：无 VAD | 新版 21 段干净 |
| 10-07 | 插件"开始"按钮为什么起不来 | `start.ps1` 解析就失败（第 98 行 `} else {` 错位），脚本一行未跑；扩展侧只看到 90 秒超时 | 修花括号 + 加解析自检；临时改为最新 llama.cpp 2.53.0 |
| 10-07 | 上下文注入影响有多大 | 质量确有提升（final pitch→推销、right check→右转、summit→登顶）；代价 ctx2 +15.4ms、ctx6 +30.7ms prefill | 接上（此前被 `del context` 丢弃），默认 2 条 |
| 10-07 | Gemma4 能不能当文本翻译 | E2B 36 层上 32 层 + MTP 草稿可行（VRAM 2337 MiB，50 tok/s）；质量好于 hy-mt2 但每行慢 2.5~3 倍 | 可用，是否转默认待定（REPORT 21.5） |
| 10-07 | LiveTranslate 怎么用 ASR | VAD 切分 + spawn 子进程 + 整段一次识别 + 短段并入下一句 + 10 轮上下文；**它也不降噪** | 可借鉴切片下限与短句合并，见队列 #6/#7 |
| 10-07 | 把 VAD 门槛抬到 1.0s 会怎样 | 额外丢 8 段 / 4.9s **真语音**（那些段带 p=0.99 的真实文本）；LiveTranslate 同名参数是"并入"不是"丢" | 改成挂起并合并；门槛默认仍 250 |
| 10-07 | 挂起并合并值不值 | 无损：保留语音 +4.1s（e_3min）/ +2.1s（er_3min），保留 p50 不恶化 | 默认关闭（`VAD_MERGE_WINDOW_MS`），待真实回放确认 |
| 10-07 | 真实回放能不能验证 #6 | **不能**：同一份音频离线 32 段/中位 1632ms，真实回放 52 段/中位 64ms，ASR 33 次全 `chars=0` | 记为新队列 #9，优先级高于 #6 |
| 10-07 | 脚本里的端口/进程探测可信吗 | 不可信：`Get-CimInstance` 与 `Get-NetTCPConnection` 都基于 WMI，受限会话拒绝访问；后者被 `SilentlyContinue` 包着被读成"没监听" | 新增 `scripts/probes.ps1`，改用 socket 与 `Get-Process` |
| 10-07 | `conda activate` 在脚本里生效吗 | 不生效：`cmd /c call conda.bat` 的子进程不回传 `CONDA_PREFIX`，回退 base（无 websockets） | start.ps1 改为按路径解析解释器 + import 预检 |
| 10-07 | #9 回放的 VAD 为什么比探针碎 25 倍 | **回环采集默认开**，`loopback.py:57` 把系统扬声器音频推进**同一个 session**，与 socket PCM 交错喂 VAD；回放不播声音 ⇒ 一半是静音 | 加 `TRANSLATION_LOOPBACK`（默认 1）；`=0` 下 48段/96ms → **31段/1824ms**，与探针一致（探针 32 段/1632ms）。⚠ **该开关已被 `AUDIO_SOURCE` 取代** |
| 10-07 | 回环对队列延迟的影响 | `pipeline.asr qwait_ms` 中位数 **113ms → 6.4ms**（关回环）；回环灌的静音让 VAD 产出假 partial 填满队列 | 历史 qwait 读数需在 `LOOPBACK=0` 下重测 |
| 10-08 | 能不能整体改走 WASAPI 回环 | 可以：`soundcard` 已装、设备匹配、直连录放三次 peak 都 0.5137；但**进程内**两次结果相反（有电平不出段 / 连电平都 0） | 架构已切，**端到端未通过**，见 REPORT 24 |
| 10-08 | 回环衰减是 −26dB 吗 | **不是**。单音测得的 0.024 被直连语音测试推翻（0.8752 → 0.5137，约 4.6dB） | 不写死增益常数，改运行时自测（收敛到目标 0.20） |
| 10-08 | 扩展前端在做音频采集吗 | **不做**。`pcm-worklet.js` 无任何引用，`offscreen.js` 只有 WS + 控制消息 | 记为待删死代码（队列 #12） |
| 10-08 | 2.53 的音频路径 MTP 可用吗 | **可用**：5 个音频请求全 200，`draft=0/30,1/15,2/18,2/30,3/12`。**16.2 的 503 是 2.47 的行为** | 但净负（49.6 vs 59.2 tok/s），音频路径仍应关 |
| 10-08 | 音频直出为什么在 e_3min 上崩坏 | 不是 MTP/层数/投影/时长/增益/提示词/采样率——**是素材**：对白被响配乐压住时，whisper 听对、Gemma4 听成"大鸟" | 边界记入 REPORT 25，配乐素材走 whisper+文本 |
| 10-08 | 回环为什么没送达 VAD | 守卫放错层：`push_pcm` 是回环唯一入口却拒绝了全部音频 | 修后 `vad.end 6 / enqueue 113`，#11 结案 |
| 10-08 | 四条素材的实时指标 | asr p50 286–316ms（与素材无关）、ttft p50 30–35ms、qwait p50 40–84ms（#9 修复后） | 基线记入 REPORT 27；旧基线前提不同不可相减 |
| 10-08 | 代码里有多少无效 import / 死代码 | pyflakes 5 条（含 `pipeline.py` 重复导入 `chinese_share`）；清空 `server/utils.py`、`server/mt.py`、`config.py` 17 个常量、硬编码的 llama 版本候选表 | `431686e`。`GEMMA_TRANSLATOR_*` 是**第三份** `TRANSLATION_GEMMA_*`，而 `start.ps1` 读的是环境变量那份 |
| 10-08 | 显示层有没有契约测试 | `_self_check()` **没人调用**且**会失败**（两条期望早于 `speech_hold_factor`）；`classify_caption`/`step_caption`/`hold_ms`/`pace_scale` 零覆盖 | 搬成 `tests/test_caption_state_machine.py`（12 条全过）。线 B 动 hold/settle 之前先有护栏 |
| 10-08 | 冒烟测试为什么失败 | **四个真 bug**：① `smoke.ps1` 取目录里第一个 gguf → 选中加载不了的 1.25bit ② 两处用 `Get-NetTCPConnection` ③ `replay.ps1` 用 `Sort-Object StartTime` → `Access is denied` ④ **`replay.ps1` 从不设 `TRANSLATION_AUDIO_SOURCE`**（漏敲 → 运行干净但 `vad.end=0`、零译文） | 全部修掉，冒烟通过（86s，24 次 ASR / 13 条译文 / 29 次 paint）。**第 ④ 条影响所有历史回放的前提** |
| 10-08 | 跑 #6：抬 partial 门槛会不会让字幕变慢 | **反而变快**：e2e 首译上屏 p50 **188→52ms**，audio_end→上屏 755→487ms；机制是 ASR 调用少 67%、队列压力降；代价 partial 每段 11.1→4.0、prefix 78%→61% | 自动判据通过，**进体感确认**（REPORT 29） |
| 10-08 | #6 的假设（更长片段→更好翻译）成立吗 | **不成立**：片段变多变短（中位 1952→1088） | 假设与效果脱钩，判据按实测重写 |
| 10-08 | `vad.end` 行数能当段数用吗（合并臂） | **不能**。`vad.py` 在门槛判断前 emit，挂起时写一行 `vad.end` 而**没有 end 事件**。e_3min 37 行里 13 行是幻影，真段数 24 | **REPORT 22 的合并数算错了 population**，标待重算；记为队列 #17 |
| 10-08 | `speech_ms` 能当"交付了多少内容"的代理吗 | **不能**。A 臂 10/27、B 臂 9/24 出现 `speech_ms > buffer`。A 臂没有挂起逻辑 ⇒ 与 #6 无关，是既有问题 | 记为队列 #18，根因未查（时间盒到） |
| 10-08 | #6 换到 blog_3min / meeting_6min 还成立吗 | **两条素材一条不过**：blog 全过（e2e 710→509ms，prefix 78%→**94%**）；meeting **最终句掉 6.5 句**（臂内散布 1~2，臂间 6.5 → 真的），e2e 判 VOID（臂内散布 4~8 倍） | 不翻默认值。`min_speech_ms` 同时是 partial 门槛，抬到 1000 让短段落**整句不进翻译**。下一步取中间值 500/1000（#19） |
| 10-08 | 给 meeting 预登记的风险指标对吗 | **打错了**。单列的 M5 是 prefix 覆盖率（理由"partial 砍 67% 后覆盖率会跌"）——结果**涨了 3 点**；真正掉的是最终句数。prefix 覆盖率对"句子根本没出现"不敏感 | 换成最终句数。教训：预登记风险时**指标要能看见你担心的那个失败形态** |
| 10-08 | REPORT 27 能复现吗（三条逐条） | blog ✅ 16/16、e_3min ✅ 12/21、**meeting ❌ 83/140 vs 本轮 A 91/89** | 「部分解除」只对前两条成立；meeting 维持未解除，且多一个未识别差异。⚠ 别把 `83/140 == B臂` 读成"REPORT 27 跑的是合并臂"，无证据 |
| 10-08 | 本机能读 CPU 温度吗 | **不能**。`MSAcpi_ThermalZoneTemperature` 返回空/类不存在（与"0 个 ACPI 热区"同一缺失）。Windows 无通用用户态 API，WMI 探针类在消费级笔记本上无效 | 记读不到，不找代理量代替。GPU 温度/功耗/throttle 已接进 `run.<name>.meta`，四臂全部 throttle=GpuIdle 无降频，两臂可比 |
| 10-08 | 显示层是流式还是块状（线 B 结案） | **grow 已经是逐字**（中位 1 字、p90 2 字）；块状感在 `swap`（18~35%），而 swap 里 **89~99% 换到从未显示过的状态** → 推进/修正不是抖动 | **不做流式重构**；队列代码从未存在于 git；**B2 打偏**：它平顺 grow，而定速输出不动 swap |
| 10-08 | swap 是抖动还是改口 | 用 `sig` 分：抖动占比 **e_3min 6% / blog 1% / meeting 11%** | 日志层面没有抖动可修。⚠ 但日志看不见位置/宽度 reflow、窗口移动、hold 造成的"停住→跳"——**用户若仍看到闪，是仪器不够** |
| 10-08 | 那四项 `sig` 看不见的，逐项测了（两行重复，已并） | 窗口高度随行数变（1→2 行整体上移 ~35~40px；`lines` 算出来却没进埋点，已补），但 **e_3min 整段只动 4 次 / 222 次 paint**；间隔 17~27% >1s、5~9% >2s、p90 1.8s | **前三项排除，第四项（停住→突然跳）成立**。剩下的是**节奏**，归 VAD/partial 层 ⇒ #4 与 #19，**不归 `caption_window.py`**；#19 因此多一条价值 |
| 10-08 | "3-4 个字块撤回"的来源 | **`sig` 的盲点是真的**：它区分"曾出现过/没出现过"，**不区分"推进/替换"**。逐个归因：**来源 A（ASR 改口）56 次、来源 B（同原文重译）0 次**；B 在 **8 次运行 / 4 条素材共 0 次**（`PARTIAL_STABLE_REPEAT=1`） | **来源是 A**。真正的撤回是 `char` 缩字（占 char 51~69%，幅度 1~8 字集中 3~5，每分钟 2.0/6.8/7.2）⇒ **ctx A/B 不必做；#19 多一条理由** |
| 10-08 | #19 gate 500 能治"字块撤回"吗 | **不能**。M5（`char` 缩字/分钟）在 **250/500/1000 三档全平**（e_3min 2.4/2.4/2.8、blog 6.8/8.7/7.8、meeting 7.2/6.7/7.4）。M1 三条都过、M2 两条可测的过、M4 三条都改善 7~9 点 | **不翻默认值**——M5 是它被排进来的唯一理由。机制：`min_speech_ms` 只推迟**第一条** partial，条数由 `partial_interval_ms` 决定 ⇒ **旋钮是 #4，不是 gate**（队列 #20） |
| 10-08 | hy-mt2 的 MTP 能不能开 | **不能**。官方 `gguf` 包读 Q4_K_M：**354 个张量、0 个带 mtp/draft/nextn**，前缀只有 `blk.0..blk.31` + `token_embd` + `output_norm`；`llama.err` 也报 `no implementations specified`、`draft: 0.000 MiB` | **不成立，不排队**。⚠ 二进制 grep 权重里的 "mtp" 无效（25 个命中全是随机字节），必须真解析 gguf |
| 10-08 | 插件「终止后端后再按开始」启不动 | **根因：装着的宿主 exe 是 10-01 的旧代码**。`translation-native-host.exe` 时间戳 10/01 10:03，而 `native_host.py` 是 10/01 13:52；`6651665`(10/01 10:04) 与 `65591a1`(10/06 16:24) 两次改动**一次都没进 exe**。`65591a1` 加的 `wait_for_existing_backend()` 正是"停了再起"的修复（旧版 `start_backend` 只看两个端口是否同时应答，`taskkill` 异步、端口死得不同步 ⇒ 窗口期里重复 spawn） | **已重建 exe 并验证**（`status` ×3 应答正确、未知命令报错、stderr 空）。⚠ **`scripts/native-host/` 被 gitignore：改了 `native_host.py` 必须重建 exe，没有任何东西会提醒你** |
| 10-08 | 决定① 抽掉管线里额外加的滤波器 | **没有可抽的东西**：全仓搜 `highpass\|lowpass\|butterworth\|sosfilt\|firwin\|lfilter\|scipy.signal\|bandpass\|notch\|apply_filter\|denoise\|webrtcvad\|audioop\|ratecv` = **零命中**，requirements 无 scipy/noisereduce/webrtcvad；最像的是 `vad.py` 的 `NON_SPEECH_GAIN = 0.0`（不是滤波器且属 VAD） | **不是"抽掉"，是从来没有。**依赖检查：`pysbd`/`scipy`/`noisereduce`/`webrtcvad` 都不在 requirements |
| 10-08 | 回声去重 / ≤8 字符合并 有没有 | **都没有**。回声去重无（`collapse_translation_repeats`/`collapse_caption_repeats` 治 LLM 复读）；≤8 字符合并无；`pipeline.py` 里**没有任何切句** | 记忆里的那条链整条不存在 —— **缺的不是滤波器，是四样东西同时不存在** |
| 10-08 | pysbd 分句是不是好东西 | **分 partial 是坏设计，分 final 才是**。反对一条就够：**分句要先知道"这句话结束了"才能翻 ⇒ 必须等句末 ⇒ 等停顿或 VAD 收尾 ⇒ 直接打在「慢半句」上** | **放 partial = 违背体感目标，不做**；放 final 可做，但不是 #20 的解（撤回发生在 partial 阶段的反复改口上）。且 `pysbd` 未装，**先别装** |
| 10-08 | ④ 换 large-v3-turbo 能治电影识别吗 | **能，但与风扇目标反向**。`er_3min` 回放：**最终句 16/16**、coverage 84%，但 `asr share of wall clock 96.0%`、`asr >250ms 215/215 (100%)`、`asr p50 821ms`、`pipeline.drop 366`、**GPU 91.10W / 75~77°C**（同机空载 10.6W） | **turbo + 250 不兼容**；自洽组合 = turbo + 旧#4（750）—— **这两条现在都是生效值**（config.py:99 + PARTIAL_INTERVAL_MS 750）。⚠ 无 small 对照（`logs/v1-er_3min.*` 窗口只 20.9s、asr n=34、启动时 GPU 上有别人的 llama-server pid 18104，**不可用**）。且用户报风扇的是**播客**，我测的是**电影**，播客 ASR 调用率更高 ⇒ turbo 在播客上只会更糟 |
| 10-08 | 决定③ 切换按钮放扩展 | **落地，但后端也得改**。`partial_interval_ms` 的热切**原先只在 UDP 路径**（`_listen_caption_state`）；扩展走的 WebSocket `config` 分支只读 `audioOnly`/`language`/api 字段，**这个字段被接受然后丢掉**（不报错，控件就是没反应）。已给 config 分支加 `if data.get("partial_interval_ms"): _switch_partial_interval(...)` | 7 条新测试（`tests/test_cadence_switch.py`）：2 条真行为测试 + 5 条接线断言，**含一条真不变量：扩展默认值必须等于 `config.PARTIAL_INTERVAL_MS`**，否则一按开始就静默改行为 |
| 10-08 | meta 里没有 ASR 模型名 | `turbo-er` 是我起的臂名，**meta 里没有任何字段能证明 turbo 真跑了**（`thresholds` 只覆盖可调项，ASR 模型是路径不是阈值） | 已把 `asr_model`/`asr_fallback` 加进 meta 的 `thresholds`（`replay.ps1:356`）。**与"12 个 MTP 开全部是关的"同一形状：名字代替事实。以后读 `asr_model`，不读臂名** |
| 10-08 | 开始/终止的逻辑链条是否打通 | **不通，5 个缺陷，其中 2 个各自单独就足以让「终止后端」永远杀不掉后端**。① 失败的 stop 是单向门（`remove_pid()` 排在 kill 被验证之前 ⇒ 端口没空时 pid 文件已没了，下次 stop 只能答"没有由扩展启动的后端"）② `wait_for_existing_backend` 分不清同伴与尸体（判据"有端口开着"，而 stop 失败留下的正是半开状态：llama 活着占 8080、python 已死 ⇒ 干等 90 秒）③ `write_pid` 的异常杀死已成功的启动（Popen 之后抛，ensure 回 ok:false **但后端已经在起**：实测 14:04:12 起了 powershell、14:04:17 起了 llama-server 和 python-server，接口却说"启动失败"）④ 根因：`write_pid` 在 exe 里 Permission denied，而同一路径普通 python.exe / Set-Content 都写得进、目录 ACL 是 FullControl ⇒ **不是目录权限**，而 pid 文件是扩展手里唯一的进程树把手 ⑤ `taskkill` 退出码被丢弃（三种失败压成同一句） | **全部修完并实测两轮循环**（开始→终止→开始→终止，四步全对：stopped:true、端口 down、llama-server 真消失、stderr 空）。修法：pid 文件改**候选列表**（`LOCALAPPDATA\translation-backend\server.pid` → 回退 `ROOT\logs\server.pid`，已被 `.gitignore:12` 覆盖）；以**有 pid 文件**为判据；`write_pid` 返回错误串不抛；`taskkill` 接 returncode。**11 条新测试，第一轮 4 失败、第二轮 5 失败——失败的那些就是缺陷本身**。⚠ 顺带修掉新回归：陈旧 pid 文件永远留下，已在 None 分支清理 |
| 10-08 | 开始无反应 + 显存 0 | **四个原因：两个在扩展里，两个在安装产物里**。① "无反应"的机制：catch 把错误写进状态行，`finally` 里的 `refresh()` 立刻覆盖成"未开始" ⇒ **任何失败都只显示几毫秒，与坏按钮无法区分**（= 前两轮拿不到错误信息的原因）② 无法区分"宿主不可达"与"后端起不来"（ensure 超时 120 秒）⇒ 加 `ping-backend` → `nativeRequest("status", 8000)`，**宿主不可达 8 秒内报出来** ③ 装好的宿主清单带 UTF-8 BOM（EF BB BF 7B，严格 utf-8 直接失败）：`install_native_host.ps1` 用 `Set-Content -Encoding UTF8`，**而 PS 5.1 的 UTF8 就是带 BOM 的** ⇒ connectNative 失败 ⇒ 弹窗无信息 ④ onefile bootloader 解包到 `%TEMP%\_MEI<pid>` 失败（PYI-xxx:ERROR 退出 1）；实测 TEMP 不设/AppData 都失败、仓库内才成功 | 全修完并在 **Chrome 的条件（不覆写 TEMP）** 下实测两轮循环四次操作全对；**改 --onedir**（没有解包步骤就没有这个失败模式）。⚠ 换 onedir **立刻踩出第七个缺陷**：`project_root()` 数父目录（onefile 时 `parents[2]` 是仓库根，onedir 多一层变成 scripts/）⇒ 冻结构建去找 `scripts\scripts\start.ps1`，改成**向上找标记文件 `scripts/start.ps1`**。⚠ 顺带修掉**我自己新写的构建脚本**两个缺陷：不检查 PyInstaller 退出码（上次 exe 还在 ⇒ 报 Built 但发的是旧代码）、旧 onefile exe 不删。**扩展 ID 已确认**（key 算出 `mgnbcjdhnfiiooeldkpbndgmffgapdll`，与 allowed_origins 一致） |
| 10-08 | 打包参数的否定文本断言 | **三条断言全失败，因为这三个文件里都有解释为什么不能这么做的注释，而注释里恰好写着被禁的那几个词**（`--onedir, not --onefile`、`Set-Content -Encoding UTF8 is UTF8 with BOM`、`parents[2] was the root`） | **在一个会记录自己错误的文件上做否定文本断言，永远会失败。**修法：断言前先剥掉注释行，只对可执行行断言。`test_root_discovery` 相应改成断言"搜索在兜底之前"而不是"兜底不存在"——**那才是真正要保证的性质** |
| 10-08 | 「说话少 + 背景音乐」时有声音不翻译 | **机制是 VAD 没有前导预卷，不是遮蔽，也不是阈值**。新素材 `samples/sparse_music.wav`（90s，语音占 17.0%，15 句真实 1s 英文短句每 6s 一句，垫在 e_3min 的音乐/镜头音上，+10 dB）复现：**完整送达 5/15、部分送达 9/15、完全没到 1/15，起始延迟中位 +0.19s / 最大 +0.72s**。① 降噪治不了（语音开头比底噪**高 11.5 dB**，最低 +2.9，0/15 低于 0 dB）② 阈值也不是（0.5→0.2 起始延迟**一点不动**，最大始终 +0.72s，"完整"反而 5→4）③ 真位置：`vad.py:64` 的 `VAD_SPEECH_PAD_MS=120` **只在 `_trim` 里补尾部**，`vad.py:333` 的 `self.utterance.append(frame)` 说明**段从 Silero 第一次判语音那一帧就开始，前面一点不留** ⇒ 检测器每延迟 1ms 就丢 1ms 的字（1 秒的句子里中位丢 19%、最差 72%）。**两处必须一起改**（前导帧按定义是非语音帧，而 `NON_SPEECH_GAIN = 0.0` 会把它们抹成零） | 四项结论：**降噪无关 · 回声去重不存在 · 分句无关（漏检在 ASR 之前）· ≤8字符合并与症状反向**。**merge_window 在这里也用不上**（这些片段是"被截断"而非"没送出"）。⚠ 先前"降噪收益很小"基于 er_3min，那是另一类故障（32~96ms 音乐毛刺），不迁移。⚠ 第一个 SNR 测算是**错的**（15 个位置 RMS 完全相同，重建底噪的代码有 bug），已改为由构建脚本单独输出 `_bed.wav`/`_speech.wav` 后重测 |
| 10-08 | 换识别引擎（turbo → Qwen3-ASR） | **能，而且 turbo 慢是结构性的**：turbo 单次 823ms vs partial 节奏 250ms ⇒ `asr share 96.1%`、`>250ms 215/215`、`pipeline.drop 366/630`。Qwen3-ASR 同素材（blog_3min 187s，一臂一次，`asr_backend` 记在 meta）：`asr p50 823→140ms`、`>250ms 100%→9%`、wall `96.1%→49.2%`、`drop 366→6`、`qlen p90 3.0→0.0`、`ttft p50 40.6→21.4ms`、coverage 两臂都 41/49、终译 16/16。**翻译更快，队列从来不是瓶颈**；显存 1938+1286=3223/4096，从来不缺 | **`ASR_BACKEND` 开关落地并提交（`dfc1c23`），默认暂不翻**。⚠ **代价在画字侧，预登记判据没覆盖**：`e2e_first_tr_to_paint 382→925ms`（⚠ 见〈撤回〉，382/925 按不可引用处理）、`>1s 19%→44%`、改口 11→28 次、hold 819→1388ms（partial 215→579 = 改口多 2.7 倍）⇒ **#4（250→750）不是后续项，是同一件事的一部分** |
| 10-08 | 翻译请求是突发成簇还是稳态 | **A 臂不是突发，但我第一次算错了**。先报 `enqueue−ttotal` 每窗 +19~+28、累计 **+431**（读作队列发散）——**错的量**：`enqueue` 对每个 partial 都发，多数作为被取代的候选丢掉（366/630）。改成"识别产出 vs 翻译完成"后：每窗净 −3~+1、累计 −16、间隔中位 852ms、**小于 100ms 的 0/198 = 0%** | **突发假说在 blog_3min 上不成立，而这素材答不了这个问题**（TTS、语音 93.4%、段长中位 10.9s，六份里最不容易突发）。⚠ `enqueue_qlen` 是 **ASR 侧输入队列**不是翻译队列。⚠ "没有报错"不是证据：`replay.ps1` 收不到 server 写到控制台句柄的输出，B 臂真正的错是 400（发裸 PCM 却声明 format=wav）被 print 到虚空，读起来像"模型坏了" |
| 10-08 | Qwen3-ASR 接进管线的两个坑 | **都不是模型的问题，但都读作模型坏了**。① **400 `Failed to load image or audio file`**：`_wav_bytes` 包真容器，`_pcm_bytes` 保留给前缀比对（wav 头带长度，会让每段前 44 字节都不同，前缀测试就废了）。② **跨线程复用 `httpx.Client` 挂死、无异常**（连接池绑在构造时的循环上，而 `transcribe` 跑在执行器线程）⇒ 改成每调用一个 client | 已修，183 测试过，进程外复验返回 `('Video because the.', 'en', 1.0)` |
| 10-08 | 字幕「卡一下上来又卡一下」是显示层的停顿，不是翻译队列 | **是显示层，而且换引擎完全没动它**：翻译队列两臂都空（深度 p50 0 / max 3）、到达不成簇（0/198 <300ms）；`dwell` 两臂几乎一致（74%/72% 的间隔 >1s，停顿时丢 1282/1420 字） | **埋点先于调参**：dwell 加 `why=held\|empty` + `waiting`/`idle`。一臂（whisper/750）→ held 50 次 72.5%、empty 19 次 27.5%，而 empty 那批**最长**（p50 2253ms vs 1482ms、max 11377ms）且 100% idle ⇒ **改 hold 一动不了它**。⚠ **判据必须按成因分组**，只看合并数会把素材的静默记成 hold 的功劳 |
| 10-08 | 降 `HOLD_ABS_MIN_MS` 能不能治停顿（两行重复，已并） | **不能，而且 600 让这个参数变成空转**。800→600 一臂：`held gap p50 1482→1502ms`、`waiting p50 808→820ms`、`empty` 组不变 ⇒ 全在噪声内；而 1~3 字那档**真付了 180ms**。机制：参考表最低 1000ms、最快 factor 0.62 ⇒ **下限 620ms**，地板 600 **什么也不夹**（空转），800 时它还在夹 | **改回 800**，加了"地板不得低于 620"的测试。真正驱动 `held` 中位数的是 **9~18 字档**（13 字 → ref 2400 ×0.62 = 1488，几乎精确命中）⇒ 剩下唯一候选是 **factor 下限 0.62**，那是**拿全部档的可读性换**，需要单独评估。"地板这条路收手" |
| 10-08 | 我的两条推断错误（写进 REPORT 免得重犯） | ① **`waiting p50 ≈ 地板 800` 不是因果**：`hold_ms` 只是 deadline 的**候选之一**，`waiting` 测的是**余量**不是 hold（两次运行的 `waiting` 最小值是 54ms / 157ms）。两个数挨得近 ≠ 一个解释另一个 ② **`dwell >1s` 合并计数会把 empty 组算进 hold 的功劳** | 两条都写进 REPORT 42 与 `caption_window.py` 注释 |
| 10-08 | Qwen3-ASR 换引擎 | **不采用，但开关留着**。ASR 单次 823→140ms（5.9×）、>250ms 100%→9%、占用 96%→43% 墙钟；**但 e2e 反向**：blog_3min **382→925ms**、lecture_mix **55→434ms**（⚠ **434 已作废，真值 47ms；382/925 同源未核**，见〈撤回〉）⇒ 两次符号相反、**不是恒定倍数**。⚠ 我先写的"瓶颈转移到翻译"是**错的**：翻译队列两臂都空（qlen p90 0、max 5），瓶颈在**改口次数 ×2.9**（136 vs 391 次识别）⇒ 字幕重写成为限制，**方向决定下次回头看哪一层** | 留在 `ASR_BACKEND` + 链路 `qwen3-hy`。**解锁条件 = 显示层能扛 2.9 倍改口，或给改口次数封顶**。另：5/149 次复读到填满上下文（30~33s/次），已用 128 token 上限 + 整条丢弃 + `asr.truncated` 埋点兜住，**病因仍未知**（六组假设全未复现） |
| 10-08 | 字幕卡顿到底在哪一层 | **在显示层，不在 ASR 也不在翻译队列**：翻译队列两臂都空、到达不成簇（0/198 <300ms）；`dwell` 两臂几乎一致（74%/72% >1s）⇒ **换引擎没动它** | 埋点先行（同上一行的 dwell 埋点）：**多数 held（72.5%）但 27.5% 的 empty 是最长的那批且 100% idle，改 hold 动不了** ⇒ **判据必须按成因分组** |
| 10-09 | 「同一位置的字被改过几次」能不能测 | **能，不用新埋点**：manifest 里 `translation` 事件带 `text` 与 `wall`（783~995 条）= 发往显示层的全部文本，缺的只是分组成行片段（那从 `final` 能推） | **我三次记"`caption.paint` 无文本所以测不了"是错的**，三次都错在同一个方向。判据建在 `scripts/probe_position_revisions.py` / `probe_line_collapse.py`（probe 按 `.gitignore:47` 不进仓） |
| 10-09 | 保留率能不能判 A/B | **不能，均值把双峰糊成一个数**：p50=p90=**1.000**（一半以上更新完全不动），塌陷只占 9~11%，而均值 79.5%/79.9% 描述了两个峰中的任何一个都不像 | 只用分布。塌陷率 A 28.0/27.7 每分钟 对 B 28.4/30.0 —— **几乎一样** |
| 10-09 | 「同一位置的字被改过几次」A/B | **A/B 第一次在这个数上分开**：≥3 次的占比 A 29.8%/31.0% 对 B 41.0%/37.4%（**1.21~1.34 倍**），均值 2.35/2.38 对 3.00/2.72，单位置最多被改 11/10 对 14/12。**四次运行、两个批次、两个指标方向一致** | **这是用户自己给的那个观察（"前面 5-10 个字被改"）的字面形式**，B 的最高值 12/14 落在那个区间里。⚠ **仍未与"B 更难看"建立因果**：塌陷率两臂相同，而这是显示侧唯一分开的数 |
| 10-09 | 语言跳有没有变成屏幕上的替换 | **没有**：语言跳 A 25 对 B 36（B 多 1.44 倍），可见 swap A 64 对 B 68 | **21.7% 的语言误判是识别层缺陷，对屏幕的传导率远低于 1:1**（这一条之前从未被明确说出） |
| 10-09 | asr 塌陷两臂成因一样吗 | **不一样**：塌陷率 A 40%（49/123）对 B 28%（37/133），但其中伴随语言跳的占 A **51%** 对 B **84%** | B 塌陷**更少**，但塌陷的那些里成因高度集中。asr 侧差 12 个点、translation 侧差 2 个点，**幅度差一个量级** ⇒ 译文侧的保留率不是识别原文的函数 |
| 10-09 | 「B 的 partial 延续性低于 A」 | **说错了，口径选错**：前缀关系 A 26.0% 对 B 18.8%（支持那句话），但 LCS≥0.6 是 A 39.0% 对 B **50.4%**、LCS≥0.4 是 A 46.3% 对 B **60.2%** | **撤回。**用更严的尺子量出更差的数然后当结论——这是"把坏数说成好数"的反面。A 更常往后长、B 更常在行内改，两者都不算更连续 |
| 10-09 | 「行级闪烁」的渲染路径 | **四条全部休眠或不可见**（REPORT 58）：① `canvas.delete("all")` 每帧都发生，但空窗 1~3.5ms < 一帧 16.7ms ② 行数变化致窗口上移，`lines` 全程 =1 ③ delete 后 `update_idletasks` 只在高度变化时 ④ 长/韩文空窗达 169ms，但上屏 chars 最大 51 | **代码里指不出一条能造成可见空白的路径。**主因是到达速率不是渲染。已修 `_paint` 顺序（`6c156aa`），**该修复对当前现象无影响** |
| 10-09 | 空画布能有多长 | **最长 169ms**（127 字韩文，60Hz 下 10 帧）；中文 0.5~3.5ms。成因是 `font.measure` 对主字体外字形走字体回退：**韩文 1.33ms/字 对 中文 0.066ms/字，20 倍**，而 `wrap_caption` 每个候选断点都调一次 | `6c156aa` 先建后删，画布全程非空。空 body 分支**保留先清**（段落切换靠它抹掉上一行，那里的清空是有意的） |
| 10-09 | 「字流速很快涌现」是 ASR 间隔不匀吗 | **不是**：到达间隔 p50 A 835ms 对 B 811ms、p10 766/707，**<300ms 的 A 0/123、B 2/133** | 真机制在**翻译侧**：每句话的译文被切成 **6.4~7.5 片**（783~995 条 translation 对 123~134 个 asr partial），片与片之间无间隔约束，来了就画；hold 把它并成 94 paint/分钟 |
| 10-09 | 块状输出能不能替代调 ASR 旋钮 | **能，但要 3 秒**：首个 partial→final 的间隔 p25/p50/p75 = A 2362/3889/8543ms、B 2829/4043/8657ms，最长 25.9s。块状把首字从约 835ms 推到整句说完，**中位多等 3.0 秒** | **不做。**便宜的版本是 `partial_interval`（+0.2s），贵的版本是块状（+3.0s），换的是同一件事。块状与体感目标"字幕不慢"**直接对立**，不是互补 |
| 10-09 | 块状 + 行内 diff 能不能治跳字 | **不能，两者互斥**：块状下屏上是**上一句**的终稿，与新块 LCS/较长者 p50 A 0.152 对 B 0.000，公共子串≥3 字只有 29%/14% ⇒ **71~86% 会走"退回整行替换"**；而 diff 唯一能干活的地方是同句内相邻 partial（60%/62%），**块状正好删掉那批** | 两者目标是两件事：块状治"取消改动"，行内 diff 治"只换字不换人"。且**单个 canvas text item 做不到逐字符替换**（`itemconfig` 只能整体换 text），要真做需换 `tk.Text` 或每字符一个 item |
| 10-09 | 按标点分块能不能降上屏频率 | **能降，但不是登记的那个数**：上屏 93.7 → 36.1/分，≥3 连画 11.7 → 0，帧间隔 p50 2ms → 1206ms（三轮稳定）。登记的 **17 次/分两轮都没到**（集合口径 13.5/分太少，计数口径 14.2/分也不多） | 收益真实但是"闪"那一项；代价见下行 |
| 10-09 | 按标点分块的代价 | **首字 37ms → 342ms**，超过 200ms 底线，落在用户定的">300ms = 兜底在主导、判据没修好"那一档。兜底仍占 60%，**所以这个数是兜底的 400ms，不是等一个子句的必然代价** | **`TRANSLATION_BLOCK_BY_PUNCT` 默认改 0**，加断言守住。代码/开关/四份基线留着。⚠ 根因未定：**子句的标点为什么这么晚到，没查** |
| 10-09 | 计数口径能把边界触发提上去吗 | **不能，是中性改动**：预测 63.5/89.2 次每分，实测 **14.2/分**。原因：**离线测量是"每包对上一包"，实现是"每包对最后绘制的行"，基准不同** | 两种判据都不是登记的 17。已知一条漏网："你说，"→"你说。"计数不变也算边界，占比未量化。这是"口径由测量定"被我自己违反的第二次（第一次是前缀 vs LCS） |
| 10-09 | 判据只覆盖一半却读成全部 | **`caption.block` 只在兜底路径发**：BLK2 的 104 次绘制里 42 次来自边界，`why` 却显示 `{'timeout': 62}` —— 读成"边界从不触发"，而它其实 13.5/分 | 边界路径补发事件后立刻看穿。**看不见一半的 `why` 比没有 `why` 更坏** |
| 10-09 | 兜底的「重置」是不是一行 | **不是**：`_flush_clause` 清 `_clause_t0` 使无标点的长句每 400ms 重画一次（3 秒的句子画 7 次）。**只去掉那行清零会让 `wait` 算成 `max(50, 负数) = 50ms`，变成每 50ms 画一次，比原来更糟** ⇒ 必须加 `_clause_fell_back` 挡住排程 | 变异检验：退回旧写法失败 2 条。判据照出来的第一个真缺陷 |
| 10-09 | 指标能不能跨区间读 | **不能，会反向**：爆发定义是"间隔 <100ms"，而帧间隔 p50 从 2ms 变成 827ms 之后每次绘制自成一组 ⇒ 爆发从 28.9"涨到"66.8。真正可比的是 **≥3 连画（11.7 → 0）与帧间隔 p50（2ms → 1224ms）** | 与"口径由测量定"同级：指标是为旧区间设计的 |
| 10-09 | 「只看子句文本」的行级 diff 能否落在应用层 | **能，而且这才是该做的位置**：`_punct_gate` 里算出了 `introduces_boundary(...)` 就把边界扔了，`self.line` 是扁平字符串 ⇒ 渲染层无从知道哪几个子句没变 | **分层与"每子句一个 canvas item"是同一方案的两半**（只分层收益是测试性，只多项算不出"哪些没变"）。逻辑焊在渲染单元上是那晚三次写假画布替身的根源。`server/caption_clauses.py` 已落地（纯函数，不 import tkinter，16 条测试 0.05s），**渲染层那一半还没有** |
| 10-09 | 342ms 是块状的代价还是 qwen3 的代价 | **是块状的代价，与后端无关**：whisper+块状 342/354/366ms、qwen3+块状 **373ms** —— 同一档，qwen3 既没变好也没变坏 | **写进已知代价栏，避免以后把两者混在一起归因。**根因是"只换该换的字"那一层没做。qwen3+块状这一格此前从未跑过（`run.BQ-BLK.meta` code=cf2dbc0） |
| 10-09 | 启动日志能当臂身份用吗 | **不能，它在说谎**：`ws_server` 在加载识别器的分支上无条件打印 `pipeline: whisper + text model`，而该分支同时覆盖 whisper 与 qwen3 ⇒ qwen3-hy 链下这行说的是 whisper | **已修**（`eafd0b7`）：报实际引擎类名 + `asr_backend`。同"十二个 MTP on 实测全 off"同形——从可能说谎的行里读臂。另补：`build_asr` 对未知后端抛错而非静默退回 whisper |
| 10-09 | 设置里的行数上限是无限吗 | **没有这个设置**：`config.py` 零个行数字段；`wrap_caption` 的 `max_lines: int = 2` 是硬编码默认值；唯一生产调用点不传实参；扩展 UI 与状态广播都不含行数 | 与 `fit_caption_width` 同类：不是空设置，是根本没有。记忆里的"无限行"大概是窗口高度，而高度由行数算、`lines` 恒为 2，所以框永远是两行高 |
| 10-09 | 两行满、第三行来时是清屏还是滚动 | **是滚动**（丢首行 + `coords` 缓动），没有清屏分支；`canvas.delete` 全文件只有两处；画布全程非空（`6c156aa` 的目的）。实测 `dropped=1` 时 `start_y=66 → 25`、4 帧、只动 coords | ⚠ **但五次运行零次触发**：CATCH1 1053 次、BLK1/2/3 各约 1087 次、qwen3+块状 1250 次绘制，**需要 3 行以上的都是 0**。用户报告的"被清屏"不在这条路径上，复现需要其实际宽度与字号 |
| 10-09 | 每子句一个 canvas item 现在有必要吗 | **暂时没有**：整行重绘代价远低于一帧（`wrap_all` 扫 100 个中文字 0.072ms vs 一帧 16.7ms，差三个数量级）。它能解决的是"丢首行导致内容缺失"，而那有更简单的解法（放宽上限），且窗口长高的代码已支持（`height` 按 `lines` 算、底边钉住向上扩） | **先定上限，再决定要不要补渲染层。**顺序反了会做一件不需要做的事 |
| 10-09 | 块状该由独立开关还是由后端派生 | **派生**：两个开关会打架，而这一周的日志就是打架的代价。`ASR_BACKEND` 只有 whisper/qwen3 两个合法值（`asr.py` 对其他值抛错） | whisper→流式、qwen3→块状。⚠ 这等于**把一个已测量的代价（首字 373ms）默认配上一个来自体感而非测量的收益**；依据是用户报告的"前面不跳、这个跳" |

