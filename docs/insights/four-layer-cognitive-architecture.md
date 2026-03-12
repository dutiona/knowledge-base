# The Four-Layer Cognitive Architecture for Tooled LLMs

> Origin: Brainstorming session analyzing NornicDB's memory model, the Reddit
> re-embedding post (r/Rag), and our own stack (research-index + memory-engine).
> Date: 2026-03-12

## Core Thesis

NornicDB (and most "AI memory" systems) conflate knowledge with memory by
applying cognitive decay to knowledge graph nodes. This is a category error.
We propose a four-layer decomposition where each layer has distinct persistence
semantics, ownership scope, and update mechanisms.

---

## Layer Definitions

### 1. Knowledge — "What is true about the world"

Factual, structural, permanent. Represents the objective state of the world as
understood through evidence: papers, data, relationships between concepts,
experimental results.

**Properties:**

- **Persists indefinitely** — facts don't expire, they get _superseded_ by newer evidence
- **Shared across agents** — any model querying the knowledge base sees the same facts
- **Source-attributed** — every claim carries provenance (who said it, when, based on what)
- **Append-only with supersession** — you never delete "attention is O(n²)". You add
  "linear attention achieves O(n) [Paper B, 2025]" and mark the relationship as a
  supersession. The old fact remains for historical context

**What it is NOT:**

- Not "what the agent remembers" — that's memory
- Not "what works in practice" — that's wisdom
- Does not decay — a paper's findings don't become less true after 69 days

**Example:**

```
Paper A (2023): "LoRA achieves 95% of full fine-tuning quality at 0.1% parameters"
Paper B (2025): "DoRA achieves 97% at 0.08% parameters"

Knowledge base state:
  - Claim 1: LoRA ~ 95% quality (source: Paper A, active)
  - Claim 2: DoRA ~ 97% quality (source: Paper B, active)
  - Relationship: Claim 2 supersedes Claim 1 (same domain, better result)
  - Both claims remain queryable. Neither decayed.
```

**Materialization:** `research-index` — papers, chunks, entities, relationships,
conclusions, datasets, methods, metrics. SQLite + sqlite-vec + FTS5.

---

### 2. Memory — "What happened, what I was told, what I learned"

Experiential, per-agent, ephemeral by default. The running log of an agent's
interactions: corrections received, decisions made, mistakes observed, user
preferences discovered.

**Properties:**

- **Per-agent** — my memory of "user doesn't like sycophancy" is mine
- **Decays naturally** — unconsolidated episodes fade over time (Ebbinghaus curve)
- **Consolidates into wisdom** — repeated patterns get promoted to durable storage
- **Context-scoped** — memory about project A shouldn't bleed into project B

**The consolidation pipeline:**

```
Episode (single event):
  "User said: don't mock the database in tests"
  -> Stored in memory-engine as episodic fact
  -> Relevance: high for 1 week, medium for 1 month

If repeated 3+ times:
  Consolidation trigger -> pattern detected
  -> Promoted to feedback memory file / CLAUDE.md
  -> Now durable wisdom: "integration tests must hit real DB"
  -> Original episodes can decay -- the pattern is preserved
```

**Materialization:** `memory-engine` — Rust, SQLite, bi-temporal facts,
Ebbinghaus forgetting, dream-cycle consolidation, scoped contexts.

---

### 3. Wisdom — "What works, learned from experience (mine or others')"

Pre-compiled patterns — generalized lessons extracted from experience. Wisdom
comes from _someone else's mistakes_ as much as your own. A trained model's
weights contain the distilled wisdom of millions of developers' code.

**Properties:**

- **Doesn't decay** — "never store secrets in git" doesn't become less wise
- **Updates via explicit revision** — not gradual fading, but conscious correction
- **Multi-source:** model weights, CLAUDE.md, skills, feedback memories
- **Accelerates everything else** — wisdom determines _how_ you query knowledge
  and _what_ you store in memory

**The acceleration example:**

```
Without wisdom:
  Agent sees paper about "FlashAttention-3"
  -> Indexes it like any other paper

With wisdom (model knows "attention is quadratic, alternatives are active research"):
  Agent sees paper about "FlashAttention-3"
  -> Recognizes high-relevance to attention complexity problem
  -> Auto-suggests relationships to FlashAttention-1, -2, linear attention papers
  -> Prioritizes structure extraction
  -> Intelligence using wisdom to curate knowledge
```

**Materialization:**

- Model weights (frozen, updated via fine-tuning — not in our control)
- CLAUDE.md files (user-curated rules and preferences)
- Skills in ~/.claude/skills/ (structured, reusable procedures)
- Feedback memory files in ~/.claude/projects/\*/memory/ (auto-promoted)

---

### 4. Intelligence — "The capacity to reason, plan, and act"

Ephemeral — exists only at inference time. The model's ability to orchestrate
the other three layers: query knowledge, recall memory, apply wisdom, use tools,
and synthesize a response.

**Properties:**

- **Ephemeral** — exists only during a session
- **Tool-mediated** — amplified by access to tools (MCP servers, shell, search)
- **The orchestration layer** — decides _what_ to query, _how_ to interpret, _what_ to do

**The "naked model" thought experiment:**

```
Model with nothing (just weights = wisdom):
  "What papers discuss linear attention?"
  -> Can only answer from training data
  -> Still intelligent -- reasons coherently, just limited

Model with full stack:
  -> Checks memory: "user is researching transformer efficiency"
  -> Applies wisdom: "linear attention is O(n), relevant to user's focus"
  -> Queries knowledge: research-index search("linear attention")
  -> Synthesizes answer with context the naked model couldn't have
```

**Materialization:** The model itself + MCP tool graph + planning/reasoning.
Not a system you build — it's what the model _does_.

---

## Inter-Layer Pipelines

```
                    +---------------------------------------------+
                    |          INTELLIGENCE                        |
                    |   (inference-time orchestration)             |
                    |                                              |
                    |   Queries v        Queries v    Applies v    |
                    +----+------------------+-------------+-------+
                         |                  |             |
              +----------v------+  +--------v------+  +--v-----------+
              |   KNOWLEDGE     |  |    MEMORY     |  |   WISDOM     |
              |   (shared)      |  |  (per-agent)  |  |  (durable)   |
              |                 |  |               |  |              |
              | research-index  |  | memory-engine |  | weights +    |
              |                 |  |               |  | CLAUDE.md +  |
              |                 |  |               |  | skills       |
              +---------+-------+  +---+-------+---+  +------^------+
                        |              |       |              |
                        |              |       +--------------+
                        |              |        consolidation
                        |              |       (memory -> wisdom)
                        +--------------+
                         intelligence writes
                         outputs into both
```

### Pipeline 1: Memory -> Wisdom (Consolidation)

**Trigger:** Repeated pattern detected in memory
**Direction:** memory-engine -> CLAUDE.md / feedback memory files
**Effect:** Ephemeral experience becomes durable rule

```
Day 1:  User says "don't add co-author"     -> episodic memory (high relevance)
Day 5:  User says "I said no co-author"      -> episodic memory (pattern: 2x)
Day 12: User says "stop adding co-author!"   -> consolidation trigger (3+)
        -> memory-engine promotes to feedback memory file
        -> CLAUDE.md updated: "do not add yourself as a co-author"
        -> original episodes can now decay -- the wisdom is preserved
```

This is the pipeline NornicDB lacks entirely. They decay knowledge instead of
consolidating memory into wisdom.

### Pipeline 2: Wisdom -> Knowledge (Guided Curation)

**Trigger:** Intelligence applies wisdom while processing knowledge
**Direction:** Wisdom informs how knowledge is indexed, prioritized, connected
**Effect:** Knowledge base becomes smarter without extra model effort

```
Wisdom (in weights): "attention is O(n^2), linear alternatives are active research"

Intelligence ingests paper: "Sub-Quadratic Attention via Sparse Mixing"
  -> Wisdom fires: relevant to the attention complexity problem
  -> Intelligence auto-suggests relationships:
      - cites: FlashAttention-2
      - extends: Linear Transformer (Katharopoulos 2020)
      - competes_with: Mamba (state-space alternative)
  -> Knowledge base gains connections the user never manually requested
```

Without wisdom, the same paper gets indexed as an isolated node.

### Pipeline 3: Intelligence -> Memory (Experience Capture)

**Trigger:** Every interaction
**Direction:** Model's actions and observations -> memory-engine
**Effect:** Agent builds experiential context over time

```
Session: User asks to fix a bug in search.py
  Intelligence: reads code, runs tests, identifies root cause, applies fix
  -> Memory records:
    - "search.py hybrid merge had off-by-one in RRF k parameter"
    - "user preferred fixing root cause over workaround"
    - "test_search_ranking was the relevant test"
  -> These episodes inform future debugging sessions
```

### Pipeline 4: Intelligence -> Knowledge (Extraction)

**Trigger:** Model processes information and extracts structured facts
**Direction:** Model's analysis -> knowledge base
**Effect:** Raw documents become structured, queryable knowledge

```
Intelligence reads a PDF:
  -> Extracts structure (sections, headings, methods)
  -> Identifies entities (models, datasets, metrics)
  -> Records conclusions with evidence chains
  -> Suggests relationships to existing papers
  -> All stored in knowledge base as permanent knowledge
```

### Pipeline 5: Knowledge -> Intelligence (Retrieval)

**Trigger:** Model needs facts to answer a question
**Direction:** knowledge base search results -> model context
**Effect:** Model gains access to facts beyond its training data

This is the standard RAG pipeline. The quality of this pipeline depends on the
knowledge layer's retrieval quality — which is why reranking belongs in the
knowledge layer, not the intelligence layer. The knowledge base should be
self-sufficient at returning good results; consumers shouldn't compensate
for weak retrieval.

### Pipeline 6: Wisdom -> Intelligence (Behavioral Guidance)

**Trigger:** Every inference
**Direction:** CLAUDE.md rules, skills, model weights -> model behavior
**Effect:** Model acts according to accumulated experience

```
Wisdom (CLAUDE.md): "NEVER merge PRs without explicit user approval"

Intelligence: super-review completes, all checks pass
  -> Without wisdom: model might auto-merge (efficient!)
  -> With wisdom: model stops and waits (correct!)
```

---

## Pipeline Status in Our Stack

| Pipeline                  | Status            | Gap                                                         |
| ------------------------- | ----------------- | ----------------------------------------------------------- |
| Memory -> Wisdom          | **Manual**        | No automated promotion to CLAUDE.md                         |
| Wisdom -> Knowledge       | **Implicit**      | Model uses wisdom when ingesting, but no explicit mechanism |
| Intelligence -> Memory    | **Not connected** | memory-engine not yet MCP-integrated                        |
| Intelligence -> Knowledge | **Working**       | ingest, extract, record, relate — all via MCP               |
| Knowledge -> Intelligence | **Working**       | search, get_paper, get_relationships — all via MCP          |
| Wisdom -> Intelligence    | **Working**       | CLAUDE.md, skills, feedback memories auto-loaded            |

---

## Why NornicDB Gets Memory Wrong

NornicDB's 3-tier cognitive decay (Episodic: 7d, Semantic: 69d, Procedural: 693d)
applies _memory_ semantics to _knowledge_:

- A paper's findings don't become less true after 7 days
- A relationship between concepts doesn't decay
- A dataset's schema doesn't have a half-life

What decays is **relevance to the agent's current task** — a memory/attention
concern, not a knowledge concern. NornicDB conflates "I haven't accessed this
recently" with "this is less valuable." Those are categorically different.

Their model makes sense only if the graph stores conversational context and agent
actions (memory). Applying it to a general-purpose knowledge graph poisons the well.

---

## The Query Order Heuristic

```
wisdom (instant) -> memory (fast) -> knowledge (retrieval) -> web (slow)
```

This mirrors how humans think: check what you know (wisdom), then what you
remember (memory), then look it up (knowledge), then ask someone (web).
The latency ordering reflects confidence ordering too.

---

## Operationability — How Each Layer Materializes in Our Stack

Theory without implementation is philosophy. Here's the concrete mapping.

### Current Stack Map

```
+---------------------------------------------------------------------+
|                        INTELLIGENCE                                  |
|                                                                      |
|  Claude Opus 4.6 + MCP tool graph + planning                        |
|                                                                      |
|  Tools:                                                              |
|  +-- research-index MCP server ............ (knowledge)              |
|  +-- memory-engine ........................ (memory) [planned MCP]   |
|  +-- serena ............................... (code analysis)          |
|  +-- context7 ............................. (library docs)           |
|  +-- firecrawl ............................ (web scraping)           |
|  +-- playwright ........................... (browser automation)     |
|  +-- codegraph ............................ (code relationships)     |
|                                                                      |
|  Query order:                                                        |
|    wisdom (instant) -> memory (fast) -> knowledge (retrieval)        |
|    -> web (slow)                                                     |
+--------+------------------------+-----------------------+------------+
         |                        |                       |
  +------v-----------+   +--------v--------+   +----------v----------+
  |    KNOWLEDGE     |   |     MEMORY      |   |      WISDOM         |
  |                  |   |                 |   |                     |
  | research-index   |   | memory-engine   |   | Model weights       |
  |                  |   |                 |   | (frozen)            |
  | SQLite +         |   | SQLite +        |   |                     |
  | sqlite-vec       |   | Rust core       |   | CLAUDE.md           |
  | + FTS5           |   |                 |   | (user-curated)      |
  |                  |   | Ebbinghaus      |   |                     |
  | Papers           |   | forgetting      |   | Skills/             |
  | Chunks           |   |                 |   | ~/.claude/skills/   |
  | Entities         |   | Dream-cycle     |   |                     |
  | Relations        |   | consolidation   |   | Feedback memories   |
  | Conclusions      |   |                 |   | ~/.claude/projects/ |
  | Datasets         |   | Bi-temporal     |   | */memory/           |
  | Methods          |   | facts           |   |                     |
  | Metrics          |   |                 |   | debate-defaults.md  |
  | Figures          |   | Scoped          |   | RTK.md              |
  |                  |   | contexts        |   |                     |
  | Embedding:       |   |                 |   |                     |
  | BGE-M3 1024d     |   | Embedding:      |   |                     |
  | via Ollama       |   | trait-based     |   |                     |
  |                  |   | (consumer       |   |                     |
  | Re-embed:        |   |  provides)      |   |                     |
  | embed_swap.py    |   |                 |   |                     |
  +---------+--------+   +------+------+---+   +----------^----------+
            |                   |      |                   |
            |                   |      +-------------------+
            |                   |       consolidation
            |                   |      (not yet automated)
            +-------------------+
             intelligence writes
             outputs into both
```

### Gaps and Missing Pipelines

| Pipeline                  | Status            | Gap                                                                                                                                                                                                    |
| ------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Memory -> Wisdom          | **Manual**        | memory-engine has dream-cycle consolidation, but no automated promotion to CLAUDE.md/feedback files. A human currently does this manually.                                                             |
| Wisdom -> Knowledge       | **Implicit**      | The model uses wisdom when ingesting (e.g., recognizing relevant work), but there's no explicit mechanism. `suggest_relationships` is LLM-driven (implicit wisdom), but user-triggered, not automatic. |
| Intelligence -> Memory    | **Not connected** | memory-engine isn't yet wired as an MCP server. The `~/.claude/projects/*/memory/` flat files are a workaround — wisdom masquerading as memory.                                                        |
| Intelligence -> Knowledge | **Working**       | `ingest`, `extract_structure`, `extract_figures`, `record_conclusion`, `add_relationship` — all operational via MCP.                                                                                   |
| Knowledge -> Intelligence | **Working**       | `search`, `get_paper`, `get_relationships`, `get_conclusions` — all operational via MCP.                                                                                                               |
| Wisdom -> Intelligence    | **Working**       | CLAUDE.md loaded every session, skills invoked on demand, feedback memories auto-loaded.                                                                                                               |

### The Big Gap: Memory <-> Intelligence Integration

The `~/.claude/projects/*/memory/` flat files are a **shim** — they approximate
memory using the wisdom layer's storage format. Real memory would flow through
memory-engine with:

- Temporal decay (Ebbinghaus)
- Consolidation (dream cycle)
- Scope isolation (project A != project B)
- Bi-temporal tracking (when did I learn this vs when was it true)

Currently: Claude writes a markdown file. That file lives forever. No decay,
no consolidation, no temporal reasoning. It's wisdom pretending to be memory.

---

## NornicDB Competitive Analysis

### What NornicDB Is

A high-performance graph+vector database in Go, targeting AI agents and
knowledge systems. Provides Neo4j compatibility (Bolt/Cypher) while integrating
vector search, memory management, and a temporal ledger in one runtime.

### Feature Comparison

| Feature                  | NornicDB                          | research-index                          |
| ------------------------ | --------------------------------- | --------------------------------------- |
| **Language**             | Go                                | Python                                  |
| **Vector storage**       | Built-in HNSW                     | sqlite-vec (vec0)                       |
| **Full-text search**     | BM25 (built-in)                   | FTS5 (SQLite)                           |
| **Hybrid search**        | Vec + BM25 + **reranking**        | RRF of FTS5 + vec (no reranking)        |
| **Embedding providers**  | Ollama, OpenAI, local             | Ollama only                             |
| **Default embed model**  | BGE-M3 (1024d)                    | BGE-M3 (1024d)                          |
| **Re-embedding**         | Not first-class                   | First-class: staging table, atomic swap |
| **Model versioning**     | Provider config in YAML           | Config table (current model + dim)      |
| **Graph relationships**  | Native graph DB (Cypher)          | SQLite `relationships` table            |
| **Auto-relationships**   | Similarity + co-access + temporal | LLM-based `suggest_relationships`       |
| **Memory decay**         | 3-tier cognitive model            | None (belongs in memory-engine)         |
| **Temporal versioning**  | Canonical Graph Ledger            | None                                    |
| **Paper registration**   | N/A (general purpose)             | First-class: DOI, authors, venue, year  |
| **BibTeX export**        | N/A                               | Yes, collision-safe keys                |
| **Structure extraction** | N/A                               | LLM-based section/heading extraction    |
| **Figure extraction**    | N/A                               | Vision model + OmniParser               |
| **Conclusion chains**    | N/A                               | Yes, with supersession tracking         |
| **Compressed vectors**   | Yes (ANN compression)             | No                                      |
| **Protocol surface**     | Bolt/Cypher, REST, GraphQL, gRPC  | MCP                                     |
| **GPU acceleration**     | Metal, CUDA, Vulkan               | None                                    |
| **Background jobs**      | N/A                               | Deferred extraction queue               |

### Why NornicDB Gets Memory Wrong

Their 3-tier cognitive decay applies _memory_ semantics to _knowledge_:

- A paper's findings don't become less true after 7 days (Episodic half-life)
- A relationship between concepts doesn't fade after 69 days (Semantic half-life)
- A dataset's schema doesn't expire after 693 days (Procedural half-life)

What decays is **relevance to the agent's current task** — a memory/attention
concern, not a knowledge concern. They conflate "I haven't accessed this
recently" with "this is less valuable." Those are categorically different.

Their model makes sense only if the graph stores conversational context and
agent actions (i.e., memory). Applying it to a general-purpose knowledge
graph poisons the well.

Our stack handles this correctly by decorrelating:

- **memory-engine** owns decay (Ebbinghaus forgetting, dream-cycle consolidation)
- **research-index** owns persistence (append-only with supersession, no decay)

---

## Ideas Worth Stealing from NornicDB

Reclassified through the four-layer lens.

| #   | Idea                                           | Layer                     | Verdict          | Rationale                                                                                                                                                                 |
| --- | ---------------------------------------------- | ------------------------- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **Auto-relationship via embedding similarity** | Knowledge                 | **Adopt**        | Background job computing pairwise similarities above threshold (0.82). Uses existing `jobs.py` deferred queue. Makes the knowledge graph self-organizing.                 |
| 2   | **Compressed vectors** (int8/bit quantization) | Knowledge (storage)       | **Adopt**        | sqlite-vec supports `int8` and `bit` types. float32 -> int8 = 4x savings. Composable with Matryoshka: 1024->512 + int8 = 8x total. Config change, no architecture impact. |
| 3   | **Pluggable embedding providers**              | Knowledge (infra)         | **Adopt**        | Abstract `_embed_with_config()` behind provider interface. Enables OpenAI, ONNX, future backends. Single integration point.                                               |
| 4   | **Stage-2 reranking**                          | Knowledge                 | **Adopt**        | Cross-encoder after RRF merge in `search.py`. Improves precision for nuanced queries. Internal to search pipeline, not external reasoning.                                |
| 5   | **Cognitive memory decay**                     | ~~Knowledge~~ Memory      | **Reject**       | Category error. Belongs in memory-engine, which already implements Ebbinghaus forgetting. We have a better model.                                                         |
| 6   | **Co-location principle**                      | Architecture              | **Already done** | Single MCP process. Each service boundary = 1-1.5ms latency.                                                                                                              |
| 7   | **Relevance scoring by recency**               | Intelligence (query-time) | **Reframe**      | Not a storage property. If needed, apply as query-time boost: `score *= recency_factor(paper.year)`. Knowledge stores neutrally; consumer decides weighting.              |

### Priority Order

1. **Auto-relationship discovery** (#1) — highest knowledge-graph value, existing infra
2. **Compressed vectors** (#2) — easiest win, config change
3. **Pluggable providers** (#3) — future-proofing, moderate effort
4. **Stage-2 reranking** (#4) — quality gain, requires model selection
5. **Recency boost** (#7) — optional, query-time only

---

## The Query Order Heuristic

```
wisdom (instant) -> memory (fast) -> knowledge (retrieval) -> web (slow)
```

This mirrors how humans think: check what you know (wisdom), then what you
remember (memory), then look it up (knowledge), then ask someone (web).
The latency ordering reflects confidence ordering too.

---

## Key Insight: The ~/.claude/projects/\*/memory/ Shim

The flat memory files are wisdom masquerading as memory — no decay, no
consolidation, no temporal reasoning. When memory-engine gets MCP integration,
these files become the migration source for bootstrapping the engine's initial
state. The consolidation pipeline (memory -> wisdom) would then work in reverse
during migration: wisdom -> memory, then let the engine re-evaluate what should
stay vs decay.

---

## The Memory Logging Gap — How Intelligence Writes to Memory

### The Problem

The obvious approach: "log everything like a personal diary via memory-engine
MCP calls." The model actively writes its observations, decisions, and
corrections to memory-engine during every session.

This has three fatal flaws:

**1. Compaction kills intent.**
Claude Code compacts prior messages as context fills. After compaction, the
model loses the system prompt nudge to keep logging. The diary entries stop
mid-session. This is structural — no amount of prompt engineering fixes a
message that gets compacted away.

**2. Active logging burns context and latency.**
Every MCP call to memory-engine costs ~100-200ms and consumes context for the
response. If the model logs every tool call, that's 30-50% overhead on a busy
session. The granularity tradeoff is brutal: too fine = expensive noise, too
coarse = missed episodes.

**3. There's no on-exit hook.**
Claude Code has no `on_session_end` callback. The model can't write a session
summary when the user closes the terminal.

### The Solution: Observer Pattern, Not Active Logging

Instead of making the model log, make the **infrastructure** log:

```
Option A: Claude Code hook (post-tool-use)
  Every tool call -> hook fires -> writes to memory-engine MCP
  The model never needs to "remember" to log
  Survives compaction (hooks are config, not context)
  Granularity: every tool call (filterable server-side)

Option B: MCP proxy/middleware
  Sits between Claude Code and all MCP servers
  Observes all tool calls and responses passively
  Writes observations to memory-engine
  Zero model involvement

Option C: Hybrid (recommended)
  Infrastructure logs tool calls (automatic, exhaustive)
  Model logs decisions and insights (active, selective)
  "Why I chose approach X over Y" can only come from the model
  But the raw activity log doesn't require model cooperation
```

**Option A is the most practical today.** Claude Code hooks already exist and
fire reliably. The hook script calls memory-engine's MCP endpoint with the tool
name, arguments, and timestamp. The model never needs to be told to log. It
survives compaction because hooks live in `.claude/settings.json`, not in the
conversation context.

### Two-Stream Architecture

memory-engine would receive two distinct streams:

```
+-----------------------+     +-------------------------+
| Activity Stream       |     | Insight Stream          |
| (from hooks)          |     | (from model)            |
|                       |     |                         |
| - Exhaustive          |     | - Sparse                |
| - Automatic           |     | - Active (model calls)  |
| - Low value per event |     | - High value per event  |
| - Survives compaction |     | - May be lost to        |
|                       |     |   compaction             |
| Examples:             |     | Examples:               |
| - "read file X"       |     | - "chose approach A     |
| - "ran tests"         |     |    over B because..."   |
| - "edited line 42"    |     | - "user prefers X"      |
| - "search query Y"    |     | - "this bug was caused  |
|                       |     |    by Z"                |
+-----------+-----------+     +------------+------------+
            |                              |
            +---------- both feed ---------+
                           |
                    +------v------+
                    | Dream-cycle |
                    | consolid.   |
                    +------+------+
                           |
              +------------v-----------+
              | Promoted patterns      |
              | -> CLAUDE.md           |
              | -> feedback memories   |
              | -> wisdom layer        |
              +------------------------+
```

The dream-cycle consolidation processes both:

- Activity patterns: "model searches for X 10 times" -> "user frequently works on X"
- Explicit insights: direct high-value observations promoted when durable

### On-Exit Problem

For end-of-session summaries without an `on_exit` hook:

- A cron job detects stale sessions (no activity for N minutes)
- Reads the activity stream for that session
- Generates a summary from the activity log alone (no model needed)
- Stores as a consolidated episodic memory

This is inferior to a model-written summary but captures the essential timeline.

### Key Insight: Surgeon vs Scrub Nurse

Making the model responsible for logging its own diary is like asking a surgeon
to take notes during surgery — the primary task suffers, and the notes are
incomplete. An observer (scrub nurse, hook script) captures everything without
burdening the actor. The model's active diary entries are reserved for
high-value observations that only it can provide: decisions, reasoning, and
corrections that the infrastructure cannot observe.

---

## References

- [Reddit: I had to re-embed 5 million documents](https://www.reddit.com/r/Rag/comments/1rqw1oo/i_had_to_reembed_5_million_documents_because_i/)
- [NornicDB](https://github.com/orneryd/NornicDB)
- [NornicDB Discussion #26: Co-location architecture](https://github.com/orneryd/NornicDB/discussions/26)
- [Matryoshka Representation Learning (Kusupati et al., 2022)](https://arxiv.org/abs/2205.13147)
- [Ollama vs TEI performance for Qwen3-Embedding](https://github.com/ollama/ollama/issues/12088)
