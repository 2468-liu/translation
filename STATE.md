# STATE

一页速查表。**每次会话必读全文（<80 行）。**  
细节、证据与历史排障全部归档于 `REPORT.md`，通过 `python scripts/rag.py query "..."` 检索。  
规则宪章见 `AGENT.md`，推进路线见 `PLAN.md`，系统技术规格见 `ARCHIVE.md`。

## 1. 核心体感目标

1. **字幕不慢声音一句半句**（端到端首字上屏 p50 < 300ms）。
2. **长时间看视频风扇不狂转**（GPU 负载合理，无无谓的并发空转与重复计算）。
3. **字幕不乱跳、看得清**（锁定单调推进，抑制长句撤回与抖动，高对比度置顶显示）。

## 2. 核心红线与约束

- **默认链路**：`qwen3-in` (Qwen3-ASR 0.6B + Index-Translate 2B)，切片步长锁定 1000ms 黄金甜点位。
- **物理路径隔离**：模型权重统一锁定为 `D:\TranslationDesktop\models\`，不依赖仓库内临时相对路径。
- **Fail-Fast 原则**：模型权重缺失或服务端口不可达必须抛出致命错误并退出，**绝对禁止静默降级**为旧模型。
- **上屏闭环原则**：任何评测以 `caption.paint > 0` 为前置门禁；未被消费渲染的数据一律判为 VOID。
- **活体内核验证**：参数生效必须以操作系统内核命令行（`Win32_Process` / `/proc/<pid>/cmdline`）与引擎遥测为准。
- **语种与回放策略**：产品默认 `auto`。评测回放默认通过 socket PCM 注入，需语种对照时显式设 `TRANSLATION_REPLAY_LANGUAGE=en`。

## 3. 当前默认生效值（2026-10-16 状态 SSOT）

| 模块 | 生效配置 / 规格 | 说明 |
|---|---|---|
| **ASR 引擎** | Qwen3-ASR (0.6B Q8_0, 8099 端口, **-c 2048 -np 4 四槽位并发**) | SSOT 显存预算；全局注入 `--cache-ram 512 --ctx-checkpoints 1 --no-cache-idle-slots` 严控内存；ASR P50 174ms |
| **VAD 调度** | 连续静音密度调控曲线 `limit(ratio)` (4s~16s 动态伸缩) + 双引擎热切换 | 默认 Silero VAD (0.139ms/帧, 高抗BGM误报)；UI 无感切换 FireRedVAD (0.835ms/帧, 弱音耳语提升4~12%召回)；配置持久化 |
| **翻译引擎** | Index-Translate-2B.Q4_K_M.gguf (8080 端口, **-c 2048 -np 3 三槽位并发**) | 关闭 reasoning 杜绝空转；全局注入 `--cache-ram 512 --ctx-checkpoints 1 --no-cache-idle-slots` 严控内存；MT TTFT P50 80ms |
| **流水线调度** | Block 块状直出 + 30s 持续静音自动缓存清理/GC | 杜绝长时间运行内存暗漏；端到端综合延迟 ~255ms |
| **UI 呈现** | 原生置顶字幕窗 + 连续动态下潜消散 `DismissDelay(N)` (1.5s~6.0s) | 基于生理阅读速率自适应消散，短词不霸屏，长句不焦虑；双行滚动常驻 |
| **音频采集** | WASAPI 系统回环 + Linux PipeWire/PulseAudio 支持 | 跨平台进程级精准音频抓取与增强 |
| **自动化门禁** | pytest 365+ passed (100% 绿) + 活体命令行内核校验 + 红队自检工作流 | 编译部署直达 `D:\TranslationDesktop` 并产出 `TranslationDesktop-Release.zip` 及桌面源码包 |

## 4. 当前推进队列

1. **[测试 G 已完成] 双 VAD 引擎 A/B 矩阵横评与 UI 无感热重载**：17 项全场景评测完成，UI 菜单无感切换落盘。
2. **[测试 H 已完成] llama.exe / llama-server 全局 SSOT 内存硬约束**：`--cache-ram 512 --ctx-checkpoints 1 --no-cache-idle-slots` 全局注入，实测生效。
3. **[文档生态重构已完成] 治愈上下文漂移与行数门禁**：`AGENT.md` 精简至 <60 行纯指令；案例全部下沉至 `REPORT.md`（§60.9/§60.10）；红队工作流固化活体内核命令行检验门禁。

## 5. 核心证伪与警示（Top 5 铁律）

1. **拒绝假证据**：名字写着不等于生效；断言必须落在操作系统内核进程物理命令行（`Win32_Process`）与底层引擎日志上。
2. **消费端未通即为零**：没有 `caption.paint` 记录的 ASR/MT 产出都是自嗨，不可计入体感指标。
3. **分母过大稀释 Dice**：长音频块与短台词对比会导致召回率假性清零，匹配器必须结合前缀冻结分句对齐。
4. **拒绝静默降级**：缺少依赖直接报错，绝不能在评测中用备选模型偷换主测模型。
5. **历史查询走 RAG**：查阅实验记录运行 `python scripts/rag.py query "关键词"`，严禁往工作契约堆砌历史日记。
