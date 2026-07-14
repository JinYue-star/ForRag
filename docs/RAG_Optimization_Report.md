# RAG 检索管线优化报告（论文素材）
**HKU Teacher-student Co-learning (SOLO) Bot**

> 本文档系统记录本项目检索增强生成（Retrieval-Augmented Generation, RAG）管线的一轮工程优化：**改了什么、以前是什么、现在是什么、为什么改、依据哪些工作、如何配置、如何验证**。所有条目均对应仓库中真实代码位置，可直接作为学位论文的方法与实现章节素材。
>
> 术语约定：*chunk*=文本块；*dense*=稠密向量检索；*sparse/BM25*=稀疏词频检索；*rerank*=重排；*grounding*=作答对检索证据的可依据性。

---

## 1. 摘要（Abstract-style）

本轮工作在不改变系统对外接口与"一门课一个共享知识库、教师可写学生只读"数据模型的前提下，对 RAG 的**查询理解、索引表示、召回、重排、作答门控、证据组织与自动评估**七个环节做了成体系的升级。核心变化包括：

1. 嵌入模型由中文小模型升级为多语种模型（默认 `BAAI/bge-m3`，低配回退 `bge-small-zh-v1.5`），并为 E5 系模型自动加检索前缀；
2. 引入 **Contextual Retrieval**（为每个 chunk 加"文档/位置语境头"用于检索）；
3. 召回阶段由"单一稠密检索"升级为 **多查询 + HyDE + 稠密/BM25 混合 + RRF 融合**；
4. 新增 **cross-encoder 重排**（`bge-reranker-v2-m3`，sigmoid 归一为相关概率），并具备运行期优雅降级；
5. 作答门控由"单一余弦阈值"升级为 **CRAG 风格三档判定**（none/weak/grounded）+ **证据精炼**；
6. 生成阶段强制 **句级引用（sentence-level citation）**，并按 **Lost-in-the-Middle** 规律重排证据顺序；
7. 新增 **RAGAS 风格离线评估工具**（LLM-as-judge：忠实度/答案相关性/上下文精确率/召回率/**命题级正确性**）。

在此基础上又补充了四项工程增强（见 §4.10–4.13）：**边界感知分块**、**单次纠错重查（CRAG 轻量版）**、**测验 Bloom 分层 + 干扰项过量再筛**、以及评估的**命题级 correctness 判官**。

所有增强项均可通过环境变量独立开关，默认开启但在模型不可用时自动退化到旧行为，保证系统鲁棒性与可复现性。

---

## 2. 系统基线（Before：优化前的形态）

优化前的检索问答链路是一个"最小可用"的经典 RAG：

| 环节 | 优化前实现 |
|---|---|
| 分块 | 固定长度切块（`CHUNK_SIZE`/`CHUNK_OVERLAP`），块文本即原文 |
| 嵌入 | 单一中文小模型 `BAAI/bge-small-zh-v1.5`，无查询/段落前缀 |
| 查询理解 | 无。用户原始问题直接送检索 |
| 召回 | 仅稠密向量检索（cosine top-k），无稀疏通道 |
| 重排 | 无（直接用向量相似度排序） |
| 作答门控 | 单一余弦阈值（`hits_are_relevant`：top 分数 < 阈值即判"无关"，走通识回答） |
| 证据顺序 | 按相似度降序原样喂给 LLM |
| 引用 | 段落级、弱约束 |
| 评估 | 无自动化评估 |

**基线的主要问题（改动动机）**：
- **语言错配**：中文小模型对英文/双语课件与英文提问的表示能力弱；
- **词面错配**：学生口语化提问与课件术语措辞不一致，纯稠密检索易漏召回；
- **排序不佳**："召回到了但排不到前面"，top-k 里混入弱相关块；
- **门控粗糙**：单阈值非黑即白，既会把弱相关材料硬套成结论，也会误杀本可作答的情形；
- **中段遗忘**：长上下文中 LLM 对中间证据注意力低（Lost-in-the-Middle）；
- **不可评估**：缺乏可量化的质量回归手段。

---

## 3. 改动总览（Before → After → Why）

| # | 环节 | 以前（Before） | 现在（After） | 为什么改（依据） | 开关（默认） |
|---|---|---|---|---|---|
| 1 | 嵌入模型 | `bge-small-zh-v1.5`（中文小模型） | `BAAI/bge-m3`（多语种，8192 长上下文）；低配回退小模型 | 多语种/双语课程表示更强；MTEB/MIRACL 上多语种检索显著更优 | `MS_EMBED_ID` |
| 2 | 检索前缀 | 无 | E5 系自动加 `query:`/`passage:` | E5 训练时要求非对称前缀，缺失会明显掉点 | 自动/`RAG_EMBED_*_PREFIX` |
| 3 | 语境头 | chunk=原文 | 为每块加 `Document/Location/meta` 语境头用于**检索**（不进 prompt） | Contextual Retrieval：缓解块脱离上下文导致的检索歧义 | `RAG_CONTEXTUAL_HEADERS`(1) |
| 4 | 查询改写 | 无 | Multi-query（1–3 条同义/拆分子查询） | RAG-Fusion：多视角召回覆盖更全 | `RAG_ENABLE_REWRITE`(1) |
| 5 | HyDE | 无 | LLM 先写"假想答案"，作额外稠密查询 | HyDE：以答案空间对齐资料措辞，缓解问-答词面差 | `RAG_ENABLE_HYDE`(1) |
| 6 | 混合检索 | 仅稠密 | 稠密 + BM25，**RRF** 融合 | 稀疏补足精确词/术语/编号；RRF 稳健融合免调权重 | `RAG_ENABLE_HYBRID`(1) |
| 7 | 重排 | 无 | cross-encoder `bge-reranker-v2-m3`，sigmoid→概率 | 交叉编码器建模 query-passage 交互，排序精度远高于双塔 | `RAG_ENABLE_RERANK`(1) |
| 8 | 作答门控 | 单一余弦阈值 | CRAG 三档（none/weak/grounded）+ 证据精炼 | 细粒度可信度判定；弱证据也能作答但显式标注局限 | 阈值可配 |
| 9 | 证据顺序 | 相似度降序 | Lost-in-the-Middle 首尾放强证据 | 长上下文中段注意力弱，首尾摆放最相关证据 | 内置 |
| 10 | 引用 | 段落级弱约束 | 句级引用（每个事实句尾标 `[k]`） | 可核对、抗幻觉，贴合学术答辩场景 | 内置 prompt |
| 11 | 评估 | 无 | RAGAS 风格 LLM-as-judge 离线工具 | 可量化回归：忠实度/相关性/上下文精确率/召回率 | `tools/rag_eval.py` |
| 12 | 鲁棒性 | — | 重排模型加载/推理失败→自动降级 + 门控切回余弦阈值 | 保证离线/弱网环境仍可用，不误判 | `rerank_active()` |
| 13 | 分块 | 定长硬切（可能截断句子/概念） | **边界感知分块**：在段落/句子/词边界就近切分 | 减少语义截断，改善嵌入与 BM25 表示 | 内置（`CACHE_VERSION=v4`） |
| 14 | 纠错检索 | 无 | **单次纠错重查**（CRAG 轻量版）：首轮偏弱则改写一次再检，取更优者，硬上限 1 次 | 省 token、多跳/措辞难例更准，Agentic 轻量版 | `RAG_ENABLE_CORRECTIVE`(1) |
| 15 | 测验质量 | 直接生成选项 | **Bloom 认知层级/难度标注 + 干扰项"过量生成再筛"**（单次调用内完成） | 差异化功能、低成本提质；干扰项打常见误解 | 内置 prompt |
| 16 | 评估 | 忠实度/相关性/精确率/召回率 | 增 **命题级 correctness 判官**（对比 ground_truth） | 抓 faithfulness 漏掉的"grounded but wrong" | `tools/rag_eval.py` |

---

## 4. 逐项详解

### 4.1 嵌入模型升级 + 非对称前缀

**问题**：基线用 `bge-small-zh-v1.5`，面向中文、模型小；本项目是英文/双语课程，英文提问与英文课件的表示质量不足。

**现在**：默认 `BAAI/bge-m3`——多语种、支持 8192 token 长上下文、同一模型可同时产出 dense/sparse/multi-vector（本项目用其 dense 表示）。低内存/离线机器可一键回退到已缓存的 `bge-small-zh-v1.5`。

**实现要点**：
- 换模型会以新的 `embed_model_id` 作为缓存键**自动重建向量缓存**，无需手动清理。
- E5 系模型（如 `multilingual-e5-*`）在训练时使用非对称前缀，查询加 `query: `、段落加 `passage: `；缺失会明显掉点。系统据模型名自动注入，`bge`/`gte` 不加。

代码位置：
- 默认模型：`rag_api/settings.py` `SERVER_EMBED_MODEL`
- 前缀逻辑：`doc_qa_assistant.py` `_embed_prefixes()`，并在 `_load_st_model()` 上挂 `_rag_query_prefix`/`_rag_passage_prefix`

```755:768:doc_qa_assistant.py
def _embed_prefixes(model_id: str) -> tuple[str, str]:
    ...
    if "e5" in (model_id or "").lower():
        return ("query: ", "passage: ")
    return ("", "")
```

**依据**：Chen et al., *BGE M3-Embedding* (2024)；Wang et al., *Multilingual E5* (2024)；MTEB 基准（Muennighoff et al., 2023）。

---

### 4.2 Contextual Retrieval（语境头）

**问题**：固定长度切块后，单块常丢失"这段属于哪份文档/哪一节"的上下文，导致检索歧义（尤其是代词、"该方法/上式"等指代）。

**现在**：为每个 chunk 生成一段**语境头**（`Document: <文件> | Location: <位置> | <meta>`），**仅拼接进用于嵌入与 BM25 的文本**，不进入送给 LLM 的证据正文（避免污染引用与配额）。

代码位置：`doc_qa_assistant.py`
- 字段：`TextChunk.context_header`（第 100 行）
- 生成：`_build_context_header()`（第 160 行），在 `_finalize_chunks()` 填充
- 嵌入使用：`chunk_embed_text()` = 语境头 + 正文；`_encode_chunks()` 用 `passage_prefix + chunk_embed_text(c)`
- BM25 也对 `chunk_embed_text(c)` 分词（`rag_pipeline.py` `_bm25_top_indices`）
- 缓存版本升到 `CACHE_VERSION = "rag_cache_v3"` 以触发重建

**依据**：Anthropic, *Introducing Contextual Retrieval* (2024) —— 官方报告该法可将检索失败率相对降低约 35%（结合重排更高）。本项目采用其**轻量确定性版本**（元数据语境头），未引入逐块 LLM 改写以控成本。

---

### 4.3 多查询改写 + HyDE（查询理解）

**问题**：学生问法口语化、发散，与课件术语措辞不一致，单一 query 召回不全。

**现在**：**一次 LLM 调用**同时产出：
- `queries`：1–3 条同义扩展/拆分子查询（Multi-query / RAG-Fusion）；
- `hypothetical`：2–4 句"假想答案"（HyDE），仅作**额外稠密查询**（不进 BM25，避免长文本噪声）。

原始问题始终保留在查询集合中；失败则退回仅用原问。以 JSON 模式约束输出。

代码位置：`rag_pipeline.py` `_expand_queries_llm()`（第 66–112 行），在 `hybrid_retrieve()` 中调用。

**依据**：Gao et al., *Precise Zero-Shot Dense Retrieval without Relevance Labels (HyDE)* (2022)；Rackauckas, *RAG-Fusion* (2024)；多查询检索为 LangChain/LlamaIndex 等主流框架标配。

---

### 4.4 稠密 + BM25 混合检索与 RRF 融合

**问题**：纯稠密检索对**精确词、专有名词、编号、公式符号**不敏感；纯稀疏又缺语义泛化。

**现在**：并行跑
- **稠密**：对每条查询（含 HyDE）取 top-`DENSE_PER_QUERY`，跨查询取每块最高分；
- **稀疏**：自实现 **BM25**（Okapi，k1=1.5、b=0.75）over 语境头文本；

再用 **Reciprocal Rank Fusion（RRF, k=60）** 融合两路排名，得到候选池（`HYBRID_POOL`≈36）。RRF 只用名次、无需归一化两路异构分数，稳健且免调权。

代码位置：`rag_pipeline.py` `_dense_best_scores()`、`_bm25_top_indices()`、`_bm25_scores_for_doc()`、`_rrf_fuse()`、`hybrid_retrieve()`。

**依据**：Robertson & Zaragoza, *BM25* (2009)；Cormack et al., *Reciprocal Rank Fusion* (SIGIR 2009)；混合检索为 Azure AI Search、Elastic 等生产系统默认推荐。

---

### 4.5 Cross-encoder 重排

**问题**：双塔（bi-encoder）检索为效率牺牲精度，"召回到但排序不佳"。

**现在**：对融合候选池用 **cross-encoder**（`BAAI/bge-reranker-v2-m3`，多语种）逐一对 `(question, passage)` 联合打分，原始 logit 经 **sigmoid** 归一到 0–1 概率后取 top-k。截断 passage 到 1000 字以控延迟。

**鲁棒性（关键工程点）**：重排模型加载/推理失败（如离线、显存不足）时，捕获异常、退回 RRF 融合顺序与稠密余弦分，并将进程级标志 `_rerank_runtime_ok=False`；`rerank_active()` 随之返回 False，使下游门控**自动切回余弦阈值**，避免把余弦分误当作 0–1 概率而误判。

代码位置：`rag_pipeline.py` `_get_reranker()`、`_sigmoid()`、`_rerank()`（第 201–249 行）、`rerank_active()`（第 49–55 行）。

**依据**：Nogueira & Cho, *Passage Re-ranking with BERT* (2019)；Xiao et al., *C-Pack / BGE Reranker* (2023–2024)；"检索→重排"两阶段是工业界（Cohere Rerank、bge-reranker 等）标准范式。

---

### 4.6 CRAG 风格作答门控 + 证据精炼

**问题**：单一阈值非黑即白：阈值高→误杀可答问题；阈值低→把弱相关材料硬编成"有依据"的结论，产生幻觉。

**现在**：三档判定 `classify_grounding()`：
- **none**：top 分低于下限 → 走通识回答，不强套 RAG；
- **weak**：有相关但偏低（对应 CRAG 的 *Ambiguous*）→ 仍依据材料作答，但附"依据有限"提示（`no_kb_notice`）；
- **grounded**：足够可信 → 正常作答。

阈值口径随评分方式切换：**重排开启用概率阈值 `RERANK_*`，否则用余弦阈值 `KB_*`**（由 `_grounding_thresholds()` 决定），这正是 §4.5 降级标志的意义所在。

**证据精炼** `refine_evidence()`：丢弃相对最高分低于 `EVIDENCE_KEEP_RATIO`（默认 0.25）的候选块，减少喂给 LLM 的噪声证据；至少保留 1 条。

阈值默认值（`rag_api/settings.py`）：
- 余弦：`KB_MIN_SCORE=0.28`、`KB_SINGLE_HIT_MIN_SCORE=0.40`
- 重排概率：`RERANK_MIN_SCORE=0.05`、`RERANK_SINGLE_HIT_MIN_SCORE=0.12`、`RERANK_STRONG_SCORE=0.5`
- 证据保留比：`EVIDENCE_KEEP_RATIO=0.25`

代码位置：`rag_api/qa_llm.py` `_grounding_thresholds()`、`classify_grounding()`、`refine_evidence()`（第 196–247 行）。

**依据**：Yan et al., *Corrective Retrieval-Augmented Generation (CRAG)* (2024)；Asai et al., *Self-RAG* (2023) 的自反思/评估思想。

---

### 4.7 Lost-in-the-Middle 证据重排

**问题**：LLM 在长上下文中对**中间位置**信息利用率显著低于首尾。

**现在**：`reorder_lost_in_the_middle()` 将按相关度降序的证据重排为"最强放首尾、最弱放中间"（秩序如 0,2,4,…,5,3,1）后再拼进 prompt。

代码位置：`rag_api/qa_llm.py` `reorder_lost_in_the_middle()`（第 250 行起），在 `run_qa_pipeline()` 中对 `refine_evidence` 结果应用。

**依据**：Liu et al., *Lost in the Middle: How Language Models Use Long Contexts* (TACL 2023)。

---

### 4.8 句级引用（Sentence-level Citation）

**问题**：段落级引用难以逐句核对，学术答辩/作业场景需要可追溯到具体证据的断言。

**现在**：强化生成 prompt，要求 **Answer 中每个事实性句/分句结尾紧跟其依据的证据编号** `[k]`（k 与检索证据块序号一致），并单列 `- Evidence k` 说明。系统解析 `[k]` 回填结构化 citation（含来源、位置、摘录、chunk/KB 溯源 id）。

代码位置：`rag_api/qa_llm.py` `build_strategy_prompt()`（第 82–128 行）；`rag_pipeline.py` `parse_citation_refs()` / `build_citations()`（第 296–332 行）。

**依据**：Gao et al., *Enabling Large Language Models to Generate Text with Citations (ALCE)* (2023)；对齐 attributed QA 的可核查性要求。

---

### 4.9 RAGAS 风格离线评估

**问题**：优化缺少可量化验证，改动易造成"感觉变好实则回归"。

**现在**：新增独立评估工具 `tools/rag_eval.py`，以 **LLM-as-judge** 计算四项指标：
- **Faithfulness**（忠实度：答案能否被检索上下文支撑）
- **Answer Relevancy**（答案与问题相关性）
- **Context Precision**（检索上下文精确率）
- **Context Recall**（相对 ground truth 的上下文召回率）

流程：读文档目录 → 建索引 → 跑完整 QA 管线 → 对每题打分 → 输出 JSON 报告与汇总。附样例 `tools/eval_set.example.json`。

**依据**：Es et al., *RAGAS: Automated Evaluation of Retrieval Augmented Generation* (2023)。

---

### 4.10 边界感知分块（Structure-aware Chunking）

**问题**：基线 `chunk_by_chars` 按固定字符数**硬切**，常在句中/词中/概念中间截断，导致块的嵌入与 BM25 表示语义受损、检索命中率下降。

**现在**：改为**边界感知**切分——在目标长度 `max_chars` 附近的回看窗口（约本块长度 35%）内，按优先级就近寻找自然断点：**段落（`\n\n`/`\n`）> 句子（中英文句末标点）> 词/空白**；找不到才退回硬切。接口、页码标签、重叠（overlap）逻辑不变，仅改变"在哪里切"，并对每块做 `strip()`。缓存版本升至 `rag_cache_v4` 触发重建。

**为何不做全语义分块**：基于嵌入相似度的语义分块需在解析期额外跑一遍编码，成本与复杂度显著上升、且对页码映射有回归风险。边界感知版以近零成本获得绝大部分收益，是更优的工程折中；全语义/版面（表格、标题层级）感知列为未来工作。

代码位置：`doc_qa_assistant.py` `_find_split()`、`chunk_by_chars()`、`CACHE_VERSION`。

**依据**：LlamaIndex `SentenceSplitter`、LangChain `RecursiveCharacterTextSplitter` 等主流实现均以"递归边界优先"为默认策略。

---

### 4.11 单次纠错重查（Corrective Re-query，CRAG 轻量版）

**问题**：即便有多查询+HyDE，仍有措辞冷僻或需多跳的问题首轮召回偏弱；无纠错机制时只能带着弱证据作答。

**现在**：在 `hybrid_retrieve` 内置**带硬上限的纠错闭环**——首轮检索+重排后，若 top 分低于触发阈值 `_corrective_trigger()`（重排口径用 `RERANK_STRONG_SCORE=0.5`，余弦口径用 `KB_SINGLE_HIT_MIN_SCORE=0.40`），调用一次 LLM 产出**一条更具体、更贴课件术语**的改写查询并**重检一次**，取 top 分更高者返回。通过递归守卫 `allow_correction=False` 确保**最多重查一次**，成本严格有界。若纠错后仍弱，交由下游 CRAG 门控走"通识回答/显式标注局限"（即 retrieve-or-skip 的落点）。

代码位置：`rag_pipeline.py` `_corrective_trigger()`、`_corrective_rewrite_llm()`、`hybrid_retrieve(..., allow_correction=True)`。开关 `RAG_ENABLE_CORRECTIVE`（默认 1）、`RAG_CORRECTIVE_TRIGGER`（可覆盖阈值）。

**依据**：Yan et al., *CRAG* (2024) 的"检索质量评估→纠正动作"思想（此处以查询改写重检替代其 Web 搜索）；Asai et al., *Self-RAG* (2023) 的按需检索/自反思。

---

### 4.12 测验：Bloom 认知层级 + 干扰项"过量生成再筛"

**问题**：测验易停留在记忆层（recall），干扰项常明显错误或风格不一致，区分度低——这是本系统面向师生的差异化功能，值得低成本提质。

**现在**（均在**单次 LLM 调用**内完成，不增额外成本）：
- **Bloom 认知层级**：要求题目跨 `remember/understand/apply/analyze/evaluate` 分布，且选择题至少一半为高阶（apply 及以上）；每题标注 `bloom`、`difficulty`（easy/medium/hard）与一句 `explanation`。
- **干扰项过量生成再筛**：要求模型先就常见误解**头脑风暴 5–6 个候选干扰项**，再**只保留最具迷惑性、区分度最高**的若干个作为最终选项；干扰项须与正确项长度/风格一致，禁止明显错误、玩笑、"以上皆是/皆非"式选项。

校验层 `normalize_quiz_items_flexible` 对新增 `bloom/difficulty/explanation` 做**可选透传**（存在且合法即保留，缺失不报错），不破坏既有题型校验与容错。

代码位置：`rag_api/qa_llm.py` `build_quiz_generation_prompt_v3()`、`normalize_quiz_items_flexible()`。

**依据**：Bloom's Taxonomy（Anderson & Krathwohl, 2001 修订版）；教育测量中干扰项应基于典型错误概念的通行原则；"over-generate then rank/filter"是 LLM 生成常用提质手法。

---

### 4.13 命题级正确性判官（Correctness Judge）

**问题**：`faithfulness` 只验证"答案是否被检索证据支持"，无法发现 **grounded but wrong**——当证据本身有误导、或模型误读证据时，答案可与证据自洽却与事实/标准答案相悖。

**现在**：在评估工具新增 `correctness` 指标：把答案拆成**原子命题**，逐条对照 `ground_truth` 判定 correct/incorrect/unverifiable，得分 = `correct / (correct + incorrect)`（无 ground_truth 时跳过）。与 faithfulness 互补——两者同时看，可区分"编得圆但错"和"确实对"。

代码位置：`tools/rag_eval.py` `_score_correctness()`，已并入逐题结果与汇总。

**依据**：RAGAS 的 answer correctness 思想（Es et al., 2023）；attributed/《事实核查》类工作对命题级评测的强调。

---

## 5. 优化后端到端管线（Pipeline）

```
文档 → 边界感知分块(段落/句子/词) → 语境头 → 嵌入+BM25 索引
                                                    │
用户问题                                             │
  │                                                 │
  ├─ 查询扩写：Multi-query(1–3) + HyDE 假想答案       │  [一次 LLM 调用]
  │                                                 ▼
  ├─ 混合召回：稠密(bge-m3) + BM25(Okapi) over 语境头 → RRF(k=60) → 候选池(≈36)
  │
  ├─ 重排：bge-reranker-v2-m3 → sigmoid → 相关概率 → top-k
  │     (失败则降级为 RRF 顺序 + 余弦分)
  │
  ├─ 纠错重查(硬上限1次)：top 分偏弱 → 改写一次查询重检 → 取更优者   [CRAG 轻量版]
  │
  ├─ 作答门控(CRAG)：none / weak / grounded
  │     └─ 证据精炼(丢弃 < 25%·top 的块)
  │
  ├─ 证据重排：Lost-in-the-Middle 首尾摆放
  │
  └─ 生成：句级引用 [k] + 结构化 citation 回填

（测验路径：同款检索证据 → Bloom 分层 + 干扰项过量再筛 → 结构化题目）
（评估路径：faithfulness / answer_relevancy / context_precision / context_recall / correctness）
```

---

## 6. 模型选型与硬件约束

| 用途 | 部署（5060 GPU）推荐 | 本机（8GB CPU）回退 | 备注 |
|---|---|---|---|
| 嵌入 | `BAAI/bge-m3`（~0.5B，~2.3GB 内存） | `BAAI/bge-small-zh-v1.5` | 换模型自动重建向量缓存 |
| 重排 | `bge-reranker-v2-m3`（~0.5B，~2.3GB） | 关闭（`RAG_ENABLE_RERANK=0`） | 关闭时用 RRF 融合序 |
| 生成 LLM | DashScope/Qwen API（`qwen-plus`） | 同（走 API，不占本机） | 不依赖本地显卡 |

**本机实测**：纯 CPU（torch CPU 版）、8 核、7.8 GB 内存、无 CUDA。`bge-m3` 可跑但慢且内存紧张；与重排模型同时加载（合计 ~4.5GB）在 7.8GB 机器上有 OOM 风险。故：**默认面向 5060 部署配 bge-m3+重排；本机测试用小模型+关重排**，二者通过 `.env` 一键切换。

嵌入/重排均为**免费开源、可自托管**；唯一付费项是生成 LLM 的 API 调用。

---

## 7. 配置矩阵（.env 关键项）

| 变量 | 部署值 | 本机值 | 含义 |
|---|---|---|---|
| `MS_EMBED_ID` | `BAAI/bge-m3` | `BAAI/bge-small-zh-v1.5` | 嵌入模型 |
| `RAG_ENABLE_RERANK` | `1` | `0` | 是否重排 |
| `RAG_RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | （关闭） | 重排模型 |
| `RAG_ENABLE_REWRITE` | `1` | `1` | 多查询改写 |
| `RAG_ENABLE_HYDE` | `1` | `1` | HyDE |
| `RAG_ENABLE_HYBRID` | `1` | `1` | 稠密+BM25 混合 |
| `RAG_CONTEXTUAL_HEADERS` | `1` | `1` | 语境头 |
| `RAG_ENABLE_CORRECTIVE` | `1` | `1` | 单次纠错重查 |

其它可调：`RAG_HYBRID_CANDIDATES`(36)、`RAG_DENSE_PER_QUERY`(12)、`RAG_BM25_TOP`(20)、`RAG_RRF_K`(60)、`RAG_CORRECTIVE_TRIGGER`(覆盖纠错触发阈值)、以及 §4.6 各阈值。分块为内置边界感知（`CACHE_VERSION=rag_cache_v4`）。

---

## 8. 验证与可复现性

- **静态**：全部改动文件 `py_compile` 通过。
- **端到端冒烟**：以缓存小模型跑通"建索引→混合召回→（重排缺失时）降级→CRAG 判定→LiM 重排"，top-1 命中正确、门控判为 grounded；重排离线失败时 `rerank_active()=False` 自动切余弦阈值。
- **降级路径**：离线环境下重排/大模型下载失败均被捕获并优雅退回旧行为，不影响可用性。
- **离线评估**：`tools/rag_eval.py` 可对固定 eval 集回归四项指标。

---

## 9. 局限与未来工作（论文 Discussion 可用）

- **成本/延迟**：查询扩写+HyDE 每问增加一次 LLM 调用；可加缓存或按问题长度自适应触发。
- **语境头**：当前为确定性元数据；未来可选逐块 LLM 语境改写（Anthropic 原版）以进一步降检索失败率，代价是离线索引成本。
- **评估**：LLM-as-judge 存在评审模型偏置；可引入人工标注子集做校准，或多评审投票。
- **重排延迟**：cross-encoder 对大候选池有开销；可用蒸馏/量化重排器或 late-interaction（ColBERT 类）折中。
- **本地硬件**：8GB CPU 机不适合长期跑大模型；生产建议 GPU 或云端嵌入/重排 API。
- **分块**：当前为边界感知（段落/句子/词）；未来可上全语义分块（嵌入相似度切段）与版面感知（标题层级、表格/列表整体性），代价是解析期额外编码开销。
- **纠错重查**：目前硬上限 1 次、以查询改写替代 Web 检索；多跳复杂问题可评估放宽为受限多轮或引入外部检索源。

---

## 10. 参考文献（选列）

1. Chen et al. *BGE M3-Embedding: Multi-Lingual, Multi-Functionality, Multi-Granularity Text Embeddings*. 2024.
2. Wang et al. *Multilingual E5 Text Embeddings*. 2024.
3. Muennighoff et al. *MTEB: Massive Text Embedding Benchmark*. 2023.
4. Anthropic. *Introducing Contextual Retrieval*. 2024.
5. Gao et al. *Precise Zero-Shot Dense Retrieval without Relevance Labels (HyDE)*. 2022.
6. Rackauckas. *RAG-Fusion*. 2024.
7. Robertson & Zaragoza. *The Probabilistic Relevance Framework: BM25 and Beyond*. 2009.
8. Cormack, Clarke & Büttcher. *Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning Methods*. SIGIR 2009.
9. Nogueira & Cho. *Passage Re-ranking with BERT*. 2019.
10. Xiao et al. *C-Pack: Packed Resources for General Chinese Embeddings (BGE)*. 2023–2024.
11. Yan et al. *Corrective Retrieval-Augmented Generation (CRAG)*. 2024.
12. Asai et al. *Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection*. 2023.
13. Liu et al. *Lost in the Middle: How Language Models Use Long Contexts*. TACL 2023.
14. Gao et al. *Enabling Large Language Models to Generate Text with Citations (ALCE)*. 2023.
15. Es et al. *RAGAS: Automated Evaluation of Retrieval Augmented Generation*. 2023.
16. Lewis et al. *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks*. 2020.
17. Anderson & Krathwohl (eds.). *A Taxonomy for Learning, Teaching, and Assessing: A Revision of Bloom's Taxonomy*. 2001.
18. LlamaIndex / LangChain. *Recursive & Sentence-based Text Splitting* (framework documentation). 2023–2024.

---

## 附录 A：代码位置速查

| 主题 | 文件 : 关键符号 |
|---|---|
| 嵌入默认/阈值 | `rag_api/settings.py` : `SERVER_EMBED_MODEL`, `KB_*`, `RERANK_*`, `EVIDENCE_KEEP_RATIO` |
| 前缀/语境头/缓存 | `doc_qa_assistant.py` : `_embed_prefixes`, `_build_context_header`, `chunk_embed_text`, `CACHE_VERSION` |
| 边界感知分块 | `doc_qa_assistant.py` : `_find_split`, `chunk_by_chars` |
| 查询扩写+HyDE | `rag_pipeline.py` : `_expand_queries_llm` |
| BM25/RRF/混合 | `rag_pipeline.py` : `_bm25_*`, `_rrf_fuse`, `hybrid_retrieve` |
| 重排/降级 | `rag_pipeline.py` : `_get_reranker`, `_sigmoid`, `_rerank`, `rerank_active` |
| 纠错重查 | `rag_pipeline.py` : `_corrective_trigger`, `_corrective_rewrite_llm`, `hybrid_retrieve(allow_correction)` |
| 门控/精炼/LiM | `rag_api/qa_llm.py` : `classify_grounding`, `refine_evidence`, `reorder_lost_in_the_middle` |
| 句级引用 | `rag_api/qa_llm.py` : `build_strategy_prompt`；`rag_pipeline.py` : `parse_citation_refs`, `build_citations` |
| 测验(Bloom/干扰项) | `rag_api/qa_llm.py` : `build_quiz_generation_prompt_v3`, `normalize_quiz_items_flexible` |
| 评估(含 correctness) | `tools/rag_eval.py`, `tools/eval_set.example.json` |
