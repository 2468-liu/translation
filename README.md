# 实时字幕翻译

浏览器采集标签页声音，本机识别后译成简体中文，字幕画在一块无边框置顶窗口上。

## 当前方案

同一批从易到难的字幕用例上，混元 Hy-MT2-1.8B 和 Qwen3.5-4B 质量打平，混元大约快 4 倍，所以默认用混元。

- 识别：faster-whisper small，float16，在显卡上
- 翻译：混元 Hy-MT2-1.8B Q4_K_M，llama.cpp 2.47.0，`-ngl 99 -c 1024`，层全部在显卡上
- 两者同时驻留时显存大约 3.2GB
- 插件里如果填了外部接口，翻译改走那个接口，识别仍在本机

`lid-lite-25` 和 `tongue` 只给文字分类，不能代替 Whisper。没有识别就没有字可译。把 Whisper 退回 CPU 会把“慢半句”加回来。

## 一次性安装本地启动桥接

Chrome 扩展不能直接创建本地进程。首次使用需要构建并注册 Native Messaging host；完成后日常使用不需要手动运行 PowerShell。

1. 在 Chrome 的扩展管理页打开开发者模式，加载 `extension`。Manifest 已固定扩展 ID，不需要手动复制 ID。
2. 在项目根目录安装构建工具：

```powershell
D:\minanconda\envs\pytorch\python.exe -m pip install pyinstaller
```

3. 构建无独立窗口的本地 host：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_native_host.ps1
```

4. 注册 host：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_native_host.ps1
```

如果以后更换扩展公钥，可用 `-ExtensionId` 覆盖默认值。host 只接受来自该扩展的本地消息，并以进程树方式管理 Python 服务和 llama-server。

## 兼容启动命令（排障）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

上面的命令仅用于排障或兼容旧流程。正常使用时，在 Chrome 重新加载 `extension` 后打开视频页，直接点扩展里的“开始”即可自动静默启动本地服务。

## 生命周期

- 点扩展里的“开始”时自动启动本地服务，不需要预先运行脚本。
- 自动启动必须静默，不能弹出 PowerShell 窗口。
- 字幕窗点 `X` 只关闭当前字幕/采集会话，保留后端服务和模型驻留，方便下一次快速开始。
- 扩展里的“终止”才停止本地后端并释放模型驻留；再次开始时自动重新启动。

扩展与 host 使用 Native Messaging 的 `ensure`、`status` 和 `shutdown` 消息，并检查 WebSocket 与 llama-server 两个端口都已就绪后才开始采集。项目代码本身没有声明额外开源许可证；分发给朋友时应同时保留 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 以及各模型、FFmpeg 和测试素材的原始许可说明。

## 测试

项目测试使用 `pytorch` Conda 环境中的 pytest：

```powershell
conda activate pytorch
python -m pytest -q
```

采样会先经过浏览器端的语音频段滤波，再由服务端 VAD 压低句中非语音帧。电影音乐和剪辑音效无法保证被完全分离；若背景声被模型误判为语音，仍应优先使用更清晰的音轨或外部降噪输入。

## 用例

写在 `scripts/bench_mt.py` 的 `CASES` 里。每句预热 1 次，再测 3 次，表内是中位数。测一个模型时只让它占显卡。

| 编号 | 难度 | 原文 |
| --- | --- | --- |
| en-hello | 易 | Hello. |
| en-subtitle | 易 | This is a live subtitle test. |
| en-colloquial | 中 | It's gonna be a long night, isn't it? |
| en-names | 中 | NASA launched Artemis II on April 3rd, 2026. |
| en-negation | 中 | Don't turn off the lights until everyone leaves. |
| en-partial | 难 | so the thing is we should probably |
| en-compound | 难 | If the train is late and the rain doesn't stop, we'll miss the opening and have to watch it online. |
| ja-easy | 易 | これは字幕のテストです。 |
| ja-negation | 难 | 彼はまだ来ていないから、先に始めないで。 |
| ko-easy | 易 | 안녕하세요, 자막 테스트입니다. |
| ko-negation | 难 | 아직 시작하지 마세요. 사람이 다 안 왔어요. |
| en-caption | 长 | If you look closely, the crack started on the left side of the ice... |
| en-narration | 长 | What you're looking at is the same glacier I filmed five years ago... |
| en-dialogue | 长 | I kept telling them we should leave before dark... |
| en-long-partial | 长 | the reason this matters is that once the valve opens... |
| ja-long | 长 | 五年前にここで撮った氷河は尾根まで届いていたんですが… |
| ko-long | 长 | 오 년 전 여기서 찍은 빙하는 능선까지 닿아 있었는데… |

质量按字幕来打：误译、漏译、多译、语序、把指令复读出来，各算失分。2 分是能直接上字幕，1 分是大意对但有明显瑕疵，0 分是意思错、漏掉后半句或重复崩坏。满分 22。

## 译文和耗时

### 混元 Hy-MT2-1.8B Q4（默认）

| 用例 | 耗时 | 译文 | 分 |
| --- | ---: | --- | ---: |
| en-hello | 44 ms | 你好。 | 2 |
| en-subtitle | 94 ms | 这是实时字幕测试。 | 2 |
| en-colloquial | 115 ms | 这将是一个漫长的夜晚，对吧？ | 2 |
| en-names | 249 ms | NASA于2026年4月3日发射了阿耳忒弥斯2号任务。 | 1 |
| en-negation | 127 ms | 在所有人离开之前，不要关灯。 | 2 |
| en-partial | 93 ms | 所以实际上，我们或许应该 | 2 |
| en-compound | 227 ms | 如果火车晚点且雨势不止，我们就会错过开播，只能在线观看。 | 1 |
| ja-easy | 90 ms | 这是字幕的测试。 | 2 |
| ja-negation | 128 ms | 他还没来，所以先别开始。 | 2 |
| ko-easy | 102 ms | 您好，正在测试字幕。 | 1 |
| ko-negation | 185 ms | 还不要开始。还没有人来呢。 | 1 |

合计 18/22。专名句多写了“任务”。`opening` 译成“开播”，更像开场或开幕式。韩语问候译成“正在测试”，禁令句把“人还没到齐”说成“还没有人来”。半句没有被补完。

### Qwen3.5-4B MTP Q4（质量基线）

| 用例 | 耗时 | 译文 | 分 |
| --- | ---: | --- | ---: |
| en-hello | 245 ms | 你好。 | 2 |
| en-subtitle | 381 ms | 这是一个实时字幕测试。 | 2 |
| en-colloquial | 461 ms | 这将会是一个漫长的夜晚，不是吗？ | 2 |
| en-names | 1104 ms | NASA 于 2026 年 4 月 3 日发射了阿尔忒弥斯二号任务。 | 1 |
| en-negation | 370 ms | 所有人离开前不要关灯。 | 2 |
| en-partial | 579 ms | 所以事情大概是，我们可能应该 | 2 |
| en-compound | 1034 ms | 如果火车晚点且雨不停，我们将错过首映，只能在线观看。 | 1 |
| ja-easy | 262 ms | 这是字幕测试。 | 2 |
| ja-negation | 460 ms | 他还没来，先别开始。 | 2 |
| ko-easy | 349 ms | 您好，这是字幕测试。 | 2 |
| ko-negation | 586 ms | 您现在还没开始吧。人都还没到呢。 | 0 |

合计 18/22。同样多写了“任务”，`opening` 译成“首映”。韩语问候比混元准。禁令句被改成问句“您现在还没开始吧”，这是这组里更重的误译。没有把思考过程漏进译文。

### NLLB-200-distilled-600M，CTranslate2 INT8

| 用例 | 耗时 | 译文 | 分 |
| --- | ---: | --- | ---: |
| en-hello | 134 ms | 您好,您好. | 0 |
| en-subtitle | 160 ms | 这是一个直播字幕测试. | 1 |
| en-colloquial | 199 ms | 这将是一个漫长的夜晚,不是吗? | 2 |
| en-names | 356 ms | 美国宇航局于2026年4月3日发射了"阿特米斯二号". | 2 |
| en-negation | 156 ms | 在大家离开之前,不要关灯. | 2 |
| en-partial | 118 ms | 所以问题是,我们应该 | 1 |
| en-compound | 261 ms | 如果火车迟到,雨不停,我们会错过开幕, | 0 |
| ja-easy | 244 ms | 现在,我们要做一个字幕测试. | 0 |
| ja-negation | 212 ms | 他还没有来,所以不要先开始. | 1 |
| ko-easy | 209 ms | 你好,这是一个字幕测试. | 2 |
| ko-negation | 181 ms | 别开始,没有人来. | 1 |

合计 12/22。英语短句重复，日语短句意思错，长句后半句“只能在线看”被截掉。速度和混元同一档，质量不够当默认。

### Opus-MT en-zh，CTranslate2 INT8

魔搭上没有日译中、韩译中，这两句记跳过。英语七句都出现重复或语序崩坏，例如 `Hello.` 被译成几十个“你好”，长句出现“火车火车”“开开开”。不能当字幕。最快的一句是 61 ms 的英语短句，译文仍然重复。

## 长句

视频字幕几乎都是这种长度。输出上限放到 256 个 token，避免后半句被截断后当成模型不会译。下面是同一次、显卡只跑翻译时的结果。

| 用例 | Hy-MT2 | Qwen3.5-4B | NLLB-600M |
| --- | --- | --- | --- |
| 冰上裂缝，镜头切走 | 439 ms，整句完整 | 388 ms，整句完整 | 400 ms，停在“摄像头切断之前” |
| 五年前的冰川，湖面翻倍 | 548 ms，整句完整 | 512 ms，整句完整 | 440 ms，湖面翻倍被丢掉 |
| 天黑前离开，油灯亮了二十分钟 | 565 ms，整句完整；“gas light”译成加油灯 | 565 ms，同样把油量灯译成加油灯 | 531 ms，停在“没有信号” |
| 阀门打开、压力掉到红线以下（半句） | 226 ms，阀门和红线都在 | 244 ms，写成“红色线” | 222 ms，阀门译成“门”，后面丢掉 |
| 日语同一段冰川 | 480 ms，整句完整 | 435 ms，整句完整 | 661 ms，意思在，说法生硬 |
| 韩语同一段冰川 | 515 ms，整句完整 | 425 ms，整句完整 | 529 ms，山脊译成边缘 |

Opus-MT 的长句要么重复同一段，要么在 1.4 秒里吐出上百个“你好”或“原因”，没有可用译文。

长句上混元和 Qwen 的意思对齐，都能译完。NLLB 经常在从句中间停下。Qwen 这次占了 3724 MiB，剩下约 239 MiB，Whisper 放不进去。混元大约 1.6GB，识别可以继续留在显卡上。

## 选择

短句上混元和 Qwen 都是 18/22。长句上两者质量也在同一档，NLLB 会漏掉后半句。

默认仍是混元：长句大约 0.4–0.6 秒译完，并且显存够同时放下识别。Qwen 长句质量没有拉开差距，却把 4GB 占满。

NLLB 和 Opus 更快的说法，在这台卡的单句延迟上没有成立。Opus 的短句可以到 61 ms，长句译文不能用。

外部接口仍然留着：插件里勾选「用外部接口翻译」，地址写到 `/v1`。

磁盘上只留识别用的 faster-whisper-small、静音检测，和混元 Hy-MT2-1.8B Q4。Q8 大约再多 700MB，和显卡上的识别挤在 4GB 里会把 Whisper 挤回内存，长句质量又没有拉开，所以不换。NLLB、Opus、Qwen2.5-3B、Qwen3.5-4B 的权重已删。对比记录还在 `logs/bench_quality.json`。

重跑当前模型：

```powershell
D:\minanconda\envs\pytorch\python.exe scripts\bench_mt.py hymt
```
