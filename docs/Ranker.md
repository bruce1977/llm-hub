# Ranker（Reranker）配置说明

面向 **Qwen3-Reranker-4B** 生成式 reranker，覆盖两侧配置：

1. **Ollama 侧** —— Modelfile（决定模型本体参数、显存占用）
2. **llm-hub 侧** —— `data/config.json` 的 `rerank` 段（决定网关怎么调用）

> 实测环境：Ollama 0.33.3 / Windows / RTX 独显，模型 `qwen3-reranker:4b`（由 `dengcao/Qwen3-Reranker-4B:Q4_K_M` 派生）。
> 调参日期：2026-09-10。

---

## 0. 结论速查

**llm-hub 的 Python 代码无需任何改动** —— `app/rerank.py` 的逻辑已经和最优参数对齐。需要动的只有三处配置：

| # | 位置 | 改动 | 原因 |
|---|------|------|------|
| 1 | Ollama Modelfile | `PARAMETER num_ctx 4096` | 基座默认 40960，直接用会吃 **9.3 GB** 显存 |
| 2 | `config.json` | `rerank.options` **留空/删除** | 传了 `num_ctx` 会与模型值打架，每次冷启触发 **~4.4s** KV cache 重分配 |
| 3 | `config.json` | `instruction` 用严格版 | 写成"Return a relevance score"会让模型输出数字而非 yes/no |

---

## 1. 调用链路

```
客户端 POST /v1/rerank
        │
        ▼
llm-hub  rerank.py:451   model = rerank.models[0]        ← 未指定 model 时取首项
        │
        ├─ cfg.resolve(model) → find_upstream_for_model() ← 按 upstream.models 列表精确匹配
        │                                                  取 priority 最小者
        ▼
    命中 upstream（如 ollama-ipex-llm / 11435）
        │
        ▼
    /api/generate  (raw:true + logprobs:true + top_logprobs)
        │
        ├─ 无 logprobs → /api/chat (think:false)
        └─ 仍无        → /v1/chat/completions → 文本兜底 0.9/0.1
```

**关键点**：reranker 跑在哪个 Ollama 实例，**完全由 `upstreams[].models` 列表决定**，跟模型实际装在哪台机器无关。就算两个实例都装了同名模型，没在 `models` 里声明的那个也永不被路由。

---

## 2. Ollama 侧：Modelfile

文件位置：`D:\ollama\models\modelfile\qwen3-reranker-4b-q4k-3g.modelfile`

```dockerfile
# 参数实测结论见文末「实测数据」一节
FROM dengcao/Qwen3-Reranker-4B:Q4_K_M

TEMPLATE """<|im_start|>system
Judge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>
<|im_start|>user
{{ .Prompt }}<|im_end|>
<|im_start|>assistant
<think>

</think>

"""
PARAMETER num_ctx 4096
PARAMETER temperature 0
PARAMETER num_predict 4
PARAMETER stop "<|im_end|>"

# SYSTEM 会从基座继承（"Text embedding model..."，语义错误，且本 TEMPLATE 未引用
# {{ .System }}，故不生效）。空值无法覆盖，这里显式写成与 TEMPLATE 一致的判定指令。
SYSTEM """Judge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no"."""
```

### 2.1 指令说明

| 指令 | 值 | 作用 |
|------|-----|------|
| `FROM` | `dengcao/Qwen3-Reranker-4B:Q4_K_M` | 基座模型。Q4_K_M 量化，权重 **2.33 GiB** |
| `TEMPLATE` | 完整 ChatML | ⚠️ 本模型**实际不生效**，见下方「四个易混淆点」 |
| `SYSTEM` | 判定指令 | 从基座继承，需非空才能覆盖，见 ③ |
| `PARAMETER num_ctx` | **4096** | **显存主控项** |
| `PARAMETER temperature` | 0 | 打分必须确定性，否则同文档两次分数不同 |
| `PARAMETER num_predict` | 4 | 生成 token 预算。答案只有 1 个 token，>1 是防首个 `\n`/`<think>` 吃掉预算 |
| `PARAMETER stop` | `<\|im_end\|>` | 防模型继续编造解释 |

### 2.2 `num_ctx` 与显存对照（实测）

| num_ctx | 显存 | 说明 |
|---------|------|------|
| 40960（基座默认） | **9.3 GB** | ❌ 千万别直接用 |
| 8192 | 3.3 GB | |
| **4096** | **2.9 GB** | ✅ 当前配置 |
| 2048 | 2.7 GB | 省 0.2 GB，但长文档会被截断 |

> Ollama **一次性预分配**整个 KV cache，所以 `num_ctx` 直接决定显存，跟实际输入长度无关。
> Qwen3-4B：36 层、8 KV heads、head_dim 128 → KV cache ≈ 144 KB/token。

### 2.3 创建命令

配套脚本：`qwen3-reranker-4b_create.bat`

```bat
REM ollama rm qwen3-reranker:4b
ollama create qwen3-reranker:4b -f qwen3-reranker-4b-q4k-3g.modelfile
```

层全部复用，秒级完成。改完 Modelfile 必须重新 `create` 才生效。

### 2.4 四个易混淆点

**① `ollama show --modelfile` 里的 `FROM D:\...\sha256-xxx` 不是错误**

```dockerfile
FROM D:\ollama\models\blobs\sha256-143cbc3a4b2ebbd0b4022931c22a257c1c501cf20eeae0e65b1131183506ce65
```

Ollama 存储层只记 layer digest，反查时自然显示成本地绝对路径。任何模型都这样（包括用 `FROM bge-m3:latest` 建的）。Ollama 自己在输出顶部提示了正确写法：

```
# To build a new Modelfile based on this, replace FROM with:
# FROM qwen3-reranker:4b
```

**② `TEMPLATE` 写了不生效（本模型特有）**

基座 GGUF 内嵌了 `tokenizer.chat_template`（4116 B），Ollama 0.33.3 **优先用 GGUF 内嵌模板**，Manifest 的 template 层被无视。表现是非 raw 调用会输出 `<think>`。

- 验证：`ollama show <模型> --template` vs `ollama show <模型> --modelfile`，两者不一致即被覆盖
- **与本 Modelfile 无关**：换 `FROM qwen3.5:9b` 写 TEMPLATE 就正常生效
- **解法**：请求侧 `"raw": true`，自己拼完整 prompt 并在末尾补 `<think>\n\n</think>\n\n`（llm-hub 的 `raw_prompt=true` 已这么做）
- ❌ 无效方案：`PARAMETER enable_thinking false`（报 unknown parameter）、请求侧 `options.enable_thinking`（被忽略）

**③ `SYSTEM` 会从基座继承，且无法用空值清空**

基座自带 `SYSTEM Text embedding model. Outputs a vector based on input text.`（语义还是错的）。实测：

| 写法 | 结果 |
|------|------|
| 不写 `SYSTEM` | 继承基座的 `Text embedding model...` |
| `SYSTEM ""` | ❌ **空值被忽略**，仍然继承 |
| `SYSTEM """..."""` 非空 | ✅ 成功覆盖 |

当前 Modelfile 已显式写成与 TEMPLATE 一致的判定指令。需要说明的是：本 TEMPLATE 里没有 `{{ .System }}`，llm-hub 又走 raw 模式，所以这个字段**实际不参与渲染**，纯粹是为了避免 `ollama show` 里出现误导性的描述。

**④ `qwen3.context_length = 40960` 不等于生效值**

`/api/show` 的 `model_info` 里显示的是 **GGUF 元数据中的原始值**，不是运行时值。真正生效的是 `parameters` 段：

```
parameters:
num_ctx                        4096      ← 这个才是生效值
...
model_info:
qwen3.context_length = 40960             ← GGUF 原始元数据，仅参考
```

---

## 3. llm-hub 侧：`data/config.json`

### 3.1 完整参数表

| 参数 | 默认值 | 当前值 | 作用 |
|------|--------|--------|------|
| `enabled` | `true` | `true` | 总开关 |
| `endpoint` | `/v1/rerank` | `/v1/rerank` | 对外路径 |
| `mode` | `logprobs` | `logprobs` | **唯一支持的模式**。embedding 模式已移除，BGE 类交叉编码器不可用 |
| `models` | `[]` | `["qwen3-reranker:4b"]` | 候选模型，未指定 model 时取**首项**。每一项都必须在某 upstream 的 `models` 里声明过 |
| `instruction` | 严格版（见下） | 严格版 | 判定标准，注入模板的 `<Instruct>` 位 |
| `template` | ChatML 见 3.2 | 默认 | prompt 模板，含 `{instruction}`/`{query}`/`{document}` |
| `yes_token` / `no_token` | `yes` / `no` | 同 | 判定的目标 token |
| `yes_aliases` / `no_aliases` | `[]` | `[]` | 额外可接受的拼写，如中文 reranker 填 `["是"]`/`["否"]` |
| `temperature` | `0.0` | `0.0` | 必须为 0，否则分数不可复现 |
| `top_logprobs` | `20` | **`20`** | 每个位置返回的候选 token 数，上限 20 |
| `max_tokens` | `4` | `4` | → `options.num_predict`，预算给 1 太紧、给 32 会生成废话 |
| `token_scan_depth` | `4` | `4` | 在多少个生成位置里找 yes/no |
| `stop` | `[]` | `[]` | 传给 upstream 的停止序列 |
| `options` | `{}` | **`{}`（已清空）** | ⚠️ 原为 `{"num_ctx": 8192}`，与模型 4096 冲突，已删 |
| `keep_alive` | `-1` | `3m` | Ollama 侧空闲卸载时间。`-1` = 用服务端默认（本机 2h）；`0` = 打完即卸 |
| `logprobs_fallback` | `true` | `true` | 无 logprobs 时是否尝试下一种 endpoint |
| `fallback_yes` / `fallback_no` / `fallback_unknown` | `0.9` / `0.1` / `0.0` | 同 | 文本兜底分。`unknown` 故意为 0，避免"解析不出"伪装成"半相关" |
| `normalize` | `false` | **`false`** | min-max 归一化。**保持 false**：会把兜底分伪装成 1.0/0.0，且跨请求不可比 |
| `max_concurrency` | `8` | `8` | 并发打分上限 |
| `return_documents` | `true` | `true` | 响应里是否回带原文 |
| `model_modes` | `{}` | `{}` | 按模型覆盖 mode |
| `upstream_api` | `auto` | `auto` | `auto`→Ollama 走 `/api/generate`；`generate`/`chat`/`openai` 可强制 |
| `raw_prompt` | `true` | `true` | ⭐ **核心**。原样发送渲染好的模板，绕过模型 chat 模板 |
| `disable_thinking` | `true` | `true` | chat 路径下传 `think: false` |
| `max_documents` / `max_document_chars` | `0` / `0` | `0` / `0` | 护栏，0 = 不限 |
| `on_error` | `score_zero` | `score_zero` | 单文档失败时记 0 分。**全部失败仍抛 502**，此项不兜底 |

### 3.2 默认模板（`DEFAULT_RERANK_TEMPLATE`）

```
<|im_start|>system
Judge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>
<|im_start|>user
<Instruct>: {instruction}
<Query>: {query}
<Document>: {document}<|im_end|>
<|im_start|>assistant
<think>

</think>

```

最后两行是**关键**：预填空的 `<think></think>` 把思考位消耗掉，让模型直接吐 yes/no。

### 3.3 默认 instruction（`DEFAULT_RERANK_INSTRUCTION`）

```
Given a web search query, retrieve relevant passages that answer the query.
A passage is relevant only if it directly answers the specific question asked.
Passages that merely share the same broad topic, mention related keywords, or
talk about the same field or entity without answering the question are NOT relevant.
```

⚠️ **不要改成"打分版"**。若写成 `...Return a relevance score for each passage...`，模型会真的输出数字：

| instruction | 模型实际输出 |
|-------------|-------------|
| 打分版 | `'1'` / `'0'` / `'1000'` / `'0.0'` ❌ |
| 严格版 | `'yes.'` / `'No.'` ✅ |

打分版现在还能算出分，纯属 `top_logprobs` 里碰巧捞到 yes/no；换个 query 就可能掉出候选，直接退化成 0.9/0.1 粗兜底。

### 3.4 实际发出的请求

```jsonc
// 首选：POST {upstream}/api/generate
{
  "model": "qwen3-reranker:4b",
  "prompt": "<|im_start|>system\n...<|im_end|>\n<|im_start|>user\n<Instruct>: ...\n<Query>: ...\n<Document>: ...<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n",
  "stream": false,
  "raw": true,                 // ← rerank.raw_prompt
  "logprobs": true,            // ← 顶层字段，非 options
  "top_logprobs": 20,          // ← rerank.top_logprobs，上限 20
  "keep_alive": "3m",          // ← rerank.keep_alive
  "options": {
    "temperature": 0.0,        // ← rerank.temperature
    "num_predict": 4           // ← max(1, rerank.max_tokens)
    // + rerank.options 的内容（当前为空 → 不传 num_ctx ✅）
    // + stop（若配置了 stop）
  }
}
```

**注意 `logprobs` / `top_logprobs` 是顶层字段**，不是 `options` 里的 —— 这是 Ollama API 的规定。

### 3.5 降级链

`upstream_api: "auto"` + `logprobs_fallback: true` 时：

```
/api/generate (raw)
   ↓ 无 logprobs
/api/chat (think: false)          ← disable_thinking 在此生效
   ↓ 无 logprobs
/v1/chat/completions (top_logprobs ≤ 20)
   ↓ 仍无
文本兜底 0.9 / 0.1 / 0.0
```

实测（192.168.10.2:11434，同一对文档）：

| 路径 | 相关文档 | 无关文档 | 输出 | 可用 |
|------|---------|---------|------|------|
| `generate` + `raw:true` | 0.9992 | 0.0043 | `yes.` / `No.` | ✅ 主路径 |
| `generate` 非 raw | 1.0000 | 0.0000 | `<think>\nOkay,` | ❌ 进 thinking，分数极化 |
| `/api/chat` 默认 | 1.0000 | 0.0000 | 空串 | ❌ |
| chat + `think:false` | 0.9992 | 0.0042 | `yes.` / `No.` | ✅ 与 raw 几乎一致 |

> 注：chat + `think:false` 早期在本机（127.0.0.1:11434）测得"分数极化"，在新实例上复测结果与 raw 基本无差异。降级链的兜底质量比原先记录的更好，但**主路径仍应是 raw** —— 它不依赖上游对 `think` 字段的支持。

---

## 4. 实测数据（6 用例 × 多组配置，排序 100% 正确）

| 参数 | 扫描值 | 结论 |
|------|--------|------|
| `num_predict` | 1 / 2 / 4 / 8 / 32 | **打分完全一致**（命中位置恒为 0，首个 token 就是答案）。但 32 会继续生成 `yes\n<Instruct>\nOkay, the user...`，耗时 0.66s vs 0.20s → **取 4** |
| `top_logprobs` | 3 / 5 / 10 / 20 | 3、5 让分数**极化成 1.0000 / 0.0000**（候选太少缺对冲项，多个相关文档拿同一满分、丢失区分度）；10、20 保留细腻度 → **取 20** |
| `num_ctx` | 2048 / 4096 / 8192 | 短文档分数无差异，显存 2.7 / 2.9 / 3.3 GB → **取 4096** |

**`top_logprobs` 对比（同一测试集）**

```
top_logprobs=5   →  0.9999 / 1.0000 / 0.0000    ← 两个相关文档都满分，分不出高下
top_logprobs=20  →  0.9991 / 0.9244 / 0.0010    ← 保住区分度
```

### ⚠️ 最大的性能坑：切换 `num_ctx` 触发重载

每次请求里传的 `num_ctx` 与当前实例不一致时，Ollama 会重新分配 KV cache：

```
首次 / 切换 ctx  →  ~4.4 s    ← 卡在这
稳定后          →  0.15 ~ 0.22 s / 文档
```

所以 `rerank.options` 必须留空，让它沿用模型的 4096。

---

## 5. 验证方法

```bash
# 1. 模型在目标实例上存在
curl -X POST http://<host>:<port>/api/show -d '{"name":"qwen3-reranker:4b"}'

# 2. 显存与 ctx
ollama ps
#   qwen3-reranker:4b    2.9 GB    100% GPU    4096

# 3. 端到端打分
curl -X POST http://<gateway>:8000/v1/rerank -H "Content-Type: application/json" \
  -d '{"model":"qwen3-reranker:4b","query":"What is Python?",
       "documents":["Python is a programming language.","The cat sat on the mat."]}'
```

响应里的 `meta` 段是排障第一手信息：

| 字段 | 含义 |
|------|------|
| `meta.upstream` | 实际命中的 upstream 名 |
| `meta.model` | 实际使用的模型 |
| `meta.logprobs_ok` | **是否拿到 logprobs**。false = 已退化 |
| `meta.text_fallbacks` | 文本兜底次数。**> 0 就说明有问题** |

---

## 6. 故障排查

| 现象 | 原因 | 处理 |
|------|------|------|
| 502，模型不存在 | `rerank.models` 首项未在 upstream 声明 / 模型没装 | 检查 `upstreams[].models` 与 `ollama list` |
| 500，接口报错 | 用了 BGE 类交叉编码器（BERT 架构） | 不支持。Ollama 无原生 rerank 端点，只能走生成式 |
| 显存 9.3 GB | 没用 Modelfile 压 `num_ctx` | 重建模型 |
| 每次打分卡 ~4.4s | `rerank.options` 传了与模型不一致的 `num_ctx` | 清空 `options` |
| 输出 `1` / `0` / `1000` | instruction 写成了"打分版" | 换回严格版 |
| 分数全是 1.0 / 0.0 | `top_logprobs` 太小 或 `normalize: true` | 改 20 + 关 normalize |
| 响应带 `<think>` | 走了非 raw 路径 | 确认 `raw_prompt: true` |
| `text_fallbacks` > 0 | upstream 不支持 logprobs（如归档版 IPEX-LLM 0.9.3） | 换 upstream；0.33.3 已正常 |

---

## 7. 实例测试脚本

`rerank_instance_test.mjs`（Node.js，零依赖，Node 18+ 全局 `fetch`）

```bash
node rerank_instance_test.mjs                                    # 默认 192.168.10.2:11434
node rerank_instance_test.mjs --host http://127.0.0.1:11434
node rerank_instance_test.mjs --only A,B                         # 只跑指定组
node rerank_instance_test.mjs --skip C,D                         # 跳过指定组
```

| 组 | 内容 | 判定标准 |
|---|------|---------|
| A | 连通性 + 参数核对 | `num_ctx=4096` / `num_predict=4` / `temperature=0`；SYSTEM 不是基座的错误描述 |
| B | 打分质量（中英 × 相关/部分相关/无关） | 三档排序正确且跨度 > 0.5 |
| C | 参数对比（instruction / top_logprobs / num_ctx） | 打印对照，人工看 |
| D | 性能（串行 ×5、并发 4/8） | 打印耗时 |
| E | 边界（空文档/超长/特殊字符/模板注入） | 不崩、不被注入劫持 |
| F | 调用路径对比 | 见 3.5 表 |

### 演示脚本

`rerank_demo.mjs`（Node.js）—— 走真实网关跑 5 组数据，打印「请求 → 结果」对照：

```bash
node rerank_demo.mjs                                  # 默认 192.168.10.2:11434
node rerank_demo.mjs --case 2                         # 只跑第 2 组
node rerank_demo.mjs --full                           # 不截断 instruction
```

| 用例 | 内容 | 看点 |
|---|---|---|
| 1 | 英文三档 | 区分度是否健康 |
| 2 | 中文知识库 4 篇 | 同主题但未直接回答的文档会被判多低 |
| 3 | 长 vs 短文档 | 极短文档是否被高估 |
| 4 | instruction 对照 | 严格版 / 打分版 / 用网关配置 三者差异 |
| 5 | 边界 | 空文档、特殊字符、纯数字 |

### 实测基线（192.168.10.2:11434，2026-09-10）

```
A  ✅ num_ctx 4096 / num_predict 4 / temperature 0 / stop <|im_end|>，2.70 GiB 100% GPU
B  ✅ EN 0.9992 / 0.9182 / 0.0043；ZH 0.9994 / 0.0017 / 0.0000
C  ✅ 打分版 instruction 输出 "1000" / "0.0"；传 num_ctx=8192 耗时 2.28s vs 0.30s
D  ✅ 串行 0.169s/篇；并发 4 与 8 均约 0.29s/篇（GPU 串行排队，并发无加速）
E  ✅ 空文档 0.0088、特殊字符 0.0035、模板注入 0.0046（未被劫持）、超长 3.19s
F  ✅ raw 与 chat+think:false 均 0.9992 / 0.004
```

### 网关侧端到端（G 组，192.168.10.2:11434）

```
G1  /health                    → 200 {"ok":true}
G2  /v1/rerank                 → 0.9992 / 0.6921 / 0.0043
    meta: upstream=ollama-ipex-llm  endpoint=generate
          logprobs_ok=true  text_fallbacks=0  normalized=false  took≈990ms
G3  配置指纹                    → ✅ 网关当前已是严格版 instruction
G4  Ollama 兼容层               → /api/tags /api/show /api/generate /api/chat /api/embed /api/ps 全部 200
```

> **端口说明**：llm-hub 在 Docker 内监听 8000，对外映射为 **11434**，且自带 Ollama API 兼容层
> —— 同一个端口上 `/health`、`/v1/*` 由网关处理，`/api/*` 被代理成 Ollama，外部应用可直接把它当 Ollama 用。

⚠️ **两个实测发现**

**① 中文场景判定偏严**：严格版 instruction 对"部分相关"很严（"路由器工作在 OSI 第三层" → 0.0029，因为只是同主题、没直接回答）。若你的知识库希望"主题相关"也算命中，把最后一句 `are NOT relevant.` 改成 `are only weakly relevant.`，改完重跑 B 组看三档分布。

**② 极短文档会被高估**：query「公司报销流程是怎样的？」下，仅含"报销"两个字的文档拿到 **0.78**，远高于无关文档（0.018）。RAG 场景若分块里有标题/关键词碎片，可能抢占长文排名。缓解办法：对过短分块（如 < 30 字）做过滤或与相邻块合并后再送入 rerank。

## 8. 相关文件

| 文件 | 说明 |
|------|------|
| `D:\ollama\models\modelfile\qwen3-reranker-4b-q4k-3g.modelfile` | 模型定义 |
| `D:\ollama\models\modelfile\qwen3-reranker-4b_create.bat` | 创建脚本 |
| `D:\ollama\models\modelfile\qwen3-reranker-4b_template.txt` | 导出的 GGUF 内嵌模板（4116 B），改模板时参考 |
| `data\config.json` → `rerank` 段 | 网关配置 |
| `app\config.py` → `RerankConfig` | 配置 schema 与默认值（权威来源） |
| `app\rerank.py` | 打分实现，**无需改动** |
| `rerank_instance_test.mjs` | 实例测试脚本（Node.js） |
