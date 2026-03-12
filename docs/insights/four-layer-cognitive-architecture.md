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

## Competitive Analysis — How Others Get It Wrong (and Right)

We studied four projects that attempt to solve the "persistent AI memory" problem.
Each reveals a different failure mode — and a few ideas worth stealing. The
journey through these systems is what shaped the four-layer decomposition above.

### 1. NornicDB — Knowledge with decay bolted on

**What it is:** Go-based graph+vector database (Neo4j-compatible). Co-locates
HNSW vectors, BM25 full-text, and Cypher graph traversal in one runtime.

**What they got right:**

- Co-location principle: each microservice boundary adds 1-1.5ms latency. Their
  benchmarks show 7.65ms end-to-end hybrid search by eliminating 6 service
  boundaries. We already follow this (single MCP process).
- Auto-relationship generation via embedding similarity, co-access patterns,
  temporal proximity, and transitive inference. Threshold: 0.82 cosine similarity.
- Compressed ANN indices for scaling single-process viability.

**What they got wrong:**

- 3-tier cognitive decay (Episodic: 7d, Semantic: 69d, Procedural: 693d) applied
  to knowledge graph nodes. This is the core category error that motivated our
  four-layer decomposition. A paper's findings don't become less true after 69
  days. What decays is relevance to the agent's current task — that's a
  memory/attention concern, not a knowledge concern.

**How we arrived at this conclusion:**
We asked: "If NornicDB's decay model is correct, then a paper indexed 70 days
ago would be 50% less retrievable than one indexed today — regardless of content
relevance. Is that ever desirable?" The answer is clearly no. Recency is a
query-time heuristic (`score *= recency_factor(year)`), not a storage property.
This distinction — query-time vs storage-level — became a litmus test for
correctly classifying features across layers.

---

### 2. claude-memory-mcp ("Dragon Brain V2") — Everything in one graph

**What it is:** FalkorDB (graph) + Qdrant (vectors) + BGE-M3 embedding server,
30 MCP tools, 904 tests. Docker Compose deployment. Created by a non-programmer
via "vibe coding" with Claude.

**What they got right:**

- **"The Librarian"**: Autonomous DBSCAN clustering to discover concept groups and
  synthesize summaries. They found it over-consolidates ("like an enthusiastic
  intern") and demoted it to manual-only. This failure mode is instructive for
  our dream-cycle consolidation — see Algorithmic Findings below.
- **No deletion**: Memories are never deleted, just pushed down in relevance.
  This is correct for both memory (relevance=0, not DELETE) and knowledge
  (superseded, not removed).
- **"Messages in a Bottle"**: At session end, Claude writes a narrative letter
  to its future self. Functionally just context injection at session start, but
  the _idea_ of end-of-session state serialization is valuable — see the
  OpenClaw analysis below for a better implementation.

**What they got wrong:**

- **Total layer conflation**: "User prefers dark mode" (memory) and "React 19
  uses compiler" (knowledge) live in the same graph with identical structure.
  No mechanism to distinguish facts-about-the-world from agent-experiences.
- **No wisdom layer at all**: No CLAUDE.md equivalent, no curated rules, no
  consolidated patterns. Everything is "remembered" equally.
- **60-second boot latency**: The model spends 60 seconds reading its own
  memories before doing work. Intelligence wasted on memory retrieval that
  should be injected automatically.

**How we arrived at this conclusion:**
The most damning critique came from Reddit user ShelZuuz: _"Claude Code already
has access to its own `.jsonl` conversation logs and is excellent at reading flat
text, so the marginal value of a full graph DB + vector DB stack over simple
file-based memory is debatable."_ This reframed the problem: the bottleneck
isn't storage sophistication — it's knowing **what to remember and when to
forget**. A simpler system with correct layer separation beats a complex system
without it.

---

### 3. Claudest (`claude-memory` plugin) — The right flow, wrong storage

**What it is:** Claude Code plugin providing SQLite-based conversation storage
with FTS5/BM25 search. Three hooks (SessionStart setup, SessionStart context
injection, Stop async sync). Two skills (keyword recall, learning extraction).

**What they got right:**

- **`extract-learnings` skill**: Analyzes conversations for non-obvious insights,
  routes them into CLAUDE.md / MEMORY.md hierarchy with approval gates. This is
  a crude but correct Memory → Wisdom pipeline. The _flow_ is exactly what
  Pipeline 1 describes: raw conversation → pattern detection → wisdom promotion.
- **Async subprocess spawn on Stop**: Session sync runs as a detached process,
  never blocking the model. This is the correct pattern for writing to
  memory-engine without adding latency to tool calls.
- **Dated daily logs + curated MEMORY.md**: Separation of raw episodes from
  consolidated summaries. Right intuition, wrong implementation (flat files
  instead of temporal DB).

**What they got wrong:**

- **Parallel memory system**: Creates its own SQLite DB (`~/.claude-memory/
conversations.db`) alongside Claude Code's native JSONL logs and MEMORY.md.
  Three competing "memories" with no reconciliation.
- **No semantic search**: FTS5/BM25 only. Keyword recall misses conceptual
  connections that vector search would find.
- **No decay or importance scoring**: Everything persists equally forever.

**Decommission plan:**
Claudest should be removed once memory-engine has MCP support. The
`extract-learnings` flow should be replicated in memory-engine's consolidation
pipeline with proper intelligence (semantic clustering, importance scoring,
decay-aware promotion).

---

### 4. OpenClaw — The most pragmatic approach

**What it is:** Open-source autonomous AI agent runtime (Node.js/TypeScript).
68,000+ GitHub stars. Connects to 20+ messaging platforms (WhatsApp, Telegram,
Discord, Slack, etc.). Runs 24/7 as a daemon with heartbeat-driven autonomy.

**Architecture:**

```
Messaging Platforms ──→ Gateway (WebSocket) ──→ Context Assembly
     ──→ ReAct Loop ──→ Tool Layer ──→ Skills System
     ──→ Memory System (Markdown files) ──→ Scheduler (heartbeat/cron/hooks)
```

**What they got right:**

**a) Pre-compaction memory flush ("save before you forget"):**
When context reaches ~70% capacity, OpenClaw injects a **silent agentic turn**
before compaction. The model gets one chance to externalize important context
to durable storage, _then_ the context is compressed. This is the critical
mechanism we were missing in our compaction strategy.

How we arrived at this: We had identified the compaction problem (context
eviction destroys insight-stream content) and proposed three options:

1. Proactive checkpointing (fragile — can't estimate context usage reliably)
2. Post-compaction re-injection (MEMORY.md already does this)
3. Two-stream architecture (activity hooks as durable backbone)

OpenClaw's approach is Option 3 **plus** a forced insight flush at the critical
moment. The model doesn't need to remember to log — it's forced to when it
matters most. This is strictly better than any of our original three options.

The implementation challenge: Claude Code doesn't expose a `PreCompaction` hook.
OpenClaw can do this because it controls its own inference loop. For us, this
requires either Anthropic adding the hook event, or a heuristic in the Stop
hook that estimates context usage and triggers a flush when >70% full.

**b) The Heartbeat pattern:**
Every 30 minutes, the agent wakes and reads `HEARTBEAT.md` (standing orders).
If nothing needs action, it responds `HEARTBEAT_OK` (costs ~100 tokens). If
something does, it acts. 48 inference calls/day, most returning immediately.

This is Strategy 4 (event-driven sessions) implemented concretely — elegant,
cost-effective autonomy. No "infinite context" illusion. Each wake is a fresh
context with injected state from durable memory.

**c) SOUL.md as wisdom layer:**
Agent identity, principles, constraints — closest thing to a proper wisdom layer
we've seen in any project. Combined with selective skill injection (only relevant
skills loaded per turn), this is smart context management.

**What they got wrong:**

- **No knowledge layer**: Everything is Markdown files. No structured
  relationships, no supersession, no graph. An insight discovered in January
  is just a line in `memory/2026-01-15.md`, not a queryable fact with provenance.
- **Compaction is lossy**: Summarization destroys nuance. The pre-flush
  mitigates but doesn't eliminate this. The scaling bug (hardcoded
  `softThresholdTokens` doesn't work for 1M context windows) shows limits of
  heuristic-based approaches.
- **No formal layer separation**: SOUL.md vs MEMORY.md vs daily logs is
  convention, not architecture. Nothing prevents the agent from writing
  knowledge into MEMORY.md or wisdom into daily logs.

---

### 5. Claude Code Native Memory — The baseline

**What it provides:**

- CLAUDE.md files (3 tiers: global, project, user-project) — pure wisdom layer
- Auto-memory (`MEMORY.md`) — model writes "remember X" on command
- JSONL session logs (`~/.claude/projects/*/<session-id>.jsonl`) — raw transcripts
- First 200 lines of MEMORY.md loaded into every session context

**What it lacks:**

- No search over past sessions (just `--continue`/`--resume` for the most recent)
- No FTS, no semantic search, no cross-session reasoning
- MEMORY.md is flat text, not structured or queryable
- No automatic learning extraction — user must explicitly say "remember this"
- JSONL files accumulate without pruning (50+ files, some >5MB each)
- No consolidation mechanism

**Assessment:** Claude Code's native memory is the wisdom layer's human-curated
surface (CLAUDE.md + MEMORY.md). It is not, and should not try to be, a memory
system. The JSONL logs are an untapped goldmine — see Bootstrapping Strategy.

---

### Comparative Summary

| Aspect               | NornicDB              | claude-memory-mcp | Claudest                      | OpenClaw                 | Our Stack                             |
| -------------------- | --------------------- | ----------------- | ----------------------------- | ------------------------ | ------------------------------------- |
| **Knowledge layer**  | Graph DB (but decays) | Graph + vectors   | None                          | None (Markdown)          | research-index (sqlite-vec + FTS5)    |
| **Memory layer**     | Same as knowledge     | Same as knowledge | SQLite + FTS5                 | Markdown files           | memory-engine (Rust, temporal, decay) |
| **Wisdom layer**     | None                  | None              | extract-learnings → CLAUDE.md | SOUL.md + AGENTS.md      | CLAUDE.md + skills + feedback files   |
| **Intelligence**     | N/A (database)        | ReAct via MCP     | N/A (plugin)                  | ReAct + heartbeat        | Model + MCP tool graph                |
| **Layer separation** | None (conflated)      | None (conflated)  | Partial (crude)               | Implicit (by convention) | **Explicit (by architecture)**        |
| **Decay model**      | Storage-level (wrong) | None              | None                          | None                     | Memory-level only (correct)           |
| **Compaction**       | N/A                   | N/A               | N/A                           | Pre-flush + summarize    | Two-stream + pre-flush (planned)      |
| **Autonomy**         | N/A                   | N/A               | N/A                           | Heartbeat + cron         | Event-driven sessions (planned)       |

---

## Context Window & Compaction Strategies

The context window is the fundamental constraint of every LLM-based system.
How you handle its exhaustion determines whether your agent degrades gracefully
or catastrophically. We studied five strategies across the projects above.

### Strategy Comparison

| Strategy                                 | How it works                                                                                                                  | Pros                                                                              | Cons                                                                                                     | Used by                         | Suitable for               |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ------------------------------- | -------------------------- |
| **1. Hierarchical delegation**           | Orchestrator spawns sub-agents with fresh context. Sub-agents return summaries.                                               | Context never fills at orchestrator level. Parallel execution.                    | Sub-agent spawning cost. Summarization lossy. Orchestrator can't see raw execution.                      | Claude Code (Agent tool), Devin | Coding assistant           |
| **2. Sliding window + summarization**    | When context fills, summarize everything into compressed state. Continue with summary.                                        | Simple to implement. No external state needed.                                    | Lossy — nuance dies in summarization. "Chinese whispers" over long runs. Compounding errors.             | AutoGPT (early), most chatbots  | Neither (fragile)          |
| **3. External memory + minimal context** | Agent keeps only current task in context. Everything else in external memory. Retrieves per turn.                             | Context never fills. Infinite effective memory.                                   | Retrieval quality determines everything. Cold-start problem.                                             | MemGPT/Letta                    | Both (with good retrieval) |
| **4. Event-driven sessions**             | No infinite session. Agent sleeps between events. Wakes on trigger, loads context from memory, works, checkpoints, sleeps.    | Clean session boundaries. No compaction needed. Cost-effective.                   | Requires robust memory system for state reconstruction. No "flow" state.                                 | OpenClaw (heartbeat)            | Autonomous agent           |
| **5. Two-stream + pre-flush** (ours)     | Activity stream from hooks (durable). Insight stream from model (opportunistic). Pre-compaction flush forces insight capture. | Nothing critical lost. Hooks survive compaction. Forced flush at critical moment. | Requires `PreCompaction` hook (not yet available in Claude Code). Insight stream degraded without flush. | Our design (planned)            | Both                       |

### How We Arrived at Strategy 5

The reasoning chain:

1. **Started with Strategy 2** (Claude Code's native compaction). Realized it's
   lossy — the model loses prompt nudges to keep logging after compaction.

2. **Considered Strategy 3** (external memory for everything). Recognized that
   the model must still _decide_ what to externalize. If it forgets to call the
   MCP, nothing gets stored. The model can't be both the surgeon and the note-taker.

3. **Invented the Surgeon vs Scrub Nurse principle**: Make the infrastructure
   (hooks) observe and log, not the model. This gave us the Activity Stream —
   durable, exhaustive, automatic, zero model involvement.

4. **Recognized the gap**: Hooks capture _what happened_ (tool calls, edits,
   test results) but not _why_ (decisions, reasoning, corrections). Only the
   model knows "I chose approach A over B because..." This gave us the Insight
   Stream — sparse, high-value, model-driven.

5. **Found the compaction hole**: The Insight Stream is vulnerable to compaction.
   After compaction, the model may forget to keep writing insights. The Activity
   Stream survives (hooks are config, not context), but insights are lost.

6. **Discovered OpenClaw's pre-flush**: A forced agentic turn before compaction
   gives the model one last chance to externalize. This plugs the compaction
   hole without relying on the model's continuous memory.

7. **Combined everything**: Activity Stream (backbone, always running) + Insight
   Stream (bonus, opportunistic) + Pre-compaction Flush (forced capture at the
   critical moment) = nothing important is ever lost.

### The Complete Compaction Flow

```
Session starts
  |
  v
MEMORY.md loaded (wisdom layer, first 200 lines)
memory-engine queried for recent context (memory layer)
  |
  v
+------------------------------------------------------------------+
|  Normal operation                                                 |
|                                                                   |
|  Activity Stream (hooks, automatic):                              |
|    PostToolUse:Bash  -> "ran pytest, 5 passed"                    |
|    PostToolUse:Edit  -> "modified search.py:42"                   |
|    PostToolUse:Write -> "created test_rerank.py"                  |
|                                                                   |
|  Insight Stream (model, voluntary):                               |
|    model calls memory-engine: "chose RRF over linear fusion       |
|    because paper X showed 12% improvement on heterogeneous        |
|    corpora"                                                       |
+------------------------------------------------------------------+
  |
  v
Context reaches ~70% capacity
  |
  v
+------------------------------------------------------------------+
|  PRE-COMPACTION FLUSH (if hook available)                         |
|                                                                   |
|  Silent agentic turn injected:                                    |
|    "Save any important context to memory before compaction."      |
|                                                                   |
|  Model writes:                                                    |
|    - Current task state: "implementing reranker, 3/5 tests pass"  |
|    - Open decisions: "still deciding between cross-encoder and    |
|      LLM-based reranking"                                         |
|    - User corrections received this session                       |
|    - Reasoning about current approach                             |
+------------------------------------------------------------------+
  |
  v
Compaction proceeds
  Old context summarized/dropped
  |
  v
+------------------------------------------------------------------+
|  Post-compaction state                                            |
|                                                                   |
|  Still available:                                                 |
|    - MEMORY.md (wisdom, always loaded)                            |
|    - Activity Stream (hooks still firing, unaffected)             |
|    - Pre-flush insights (now in memory-engine)                    |
|    - memory-engine queryable for any past context                 |
|                                                                   |
|  Lost:                                                            |
|    - Raw conversation history (summarized, lossy)                 |
|    - Any insights the model didn't externalize before flush       |
|                                                                   |
|  Net result: nothing critical was lost                            |
+------------------------------------------------------------------+
```

### Coding Assistant vs Autonomous Agent

The two use cases share the same four-layer architecture but diverge on
lifecycle, compaction strategy, and human interaction patterns.

| Aspect                 | Coding Assistant                                                                        | Autonomous Agent                                                                        |
| ---------------------- | --------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| **Session model**      | Bounded (user opens terminal → works → closes)                                          | Unbounded (daemon runs 24/7)                                                            |
| **Context strategy**   | Strategy 1 (hierarchical) + Strategy 5 (two-stream)                                     | Strategy 4 (event-driven) + Strategy 5 (two-stream)                                     |
| **Compaction**         | Happens during long sessions. Pre-flush if hook available, activity stream as backbone. | Avoided by design — each heartbeat wake is a fresh context. Memory provides continuity. |
| **Human interaction**  | Synchronous (user is present, reads output, gives feedback)                             | Asynchronous (user gets messages on Telegram/Slack, responds when available)            |
| **Memory granularity** | Per-session activity log + insight stream                                               | Per-wake activity log + heartbeat state                                                 |
| **Wisdom updates**     | Human reviews MEMORY.md directly in editor                                              | Agent proposes via side-channel (Telegram/Slack), human approves                        |
| **On-exit**            | No hook — use session timeout detection + activity summary                              | Clean: agent checkpoints before sleeping                                                |
| **Intelligence**       | Model + MCP tools + planning                                                            | Model + MCP tools + heartbeat standing orders                                           |

---

## Algorithmic Findings Catalog

Every project we studied uses specific algorithms for their retrieval,
deduplication, consolidation, and ranking operations. This catalog documents
each algorithm, what it does, where it was found, and how it could contribute
to our stack.

### 1. Reciprocal Rank Fusion (RRF)

**What it does:** Merges ranked lists from multiple retrieval systems into a
single ranking. For each document appearing in any list, compute:
`RRF(d) = Σ 1/(k + rank_i(d))` where `k` is a constant (typically 60) and
`rank_i` is the document's rank in the i-th list.

**Where we found it:** Already implemented in our `search.py`. Also used by
NornicDB and implicitly by claude-memory-mcp.

**Why it matters:** RRF is the standard for combining BM25 (keyword) and vector
(semantic) results without score normalization. The constant `k` dampens the
influence of high-ranked outliers.

**Current state in our stack:** Working. `search.py` merges FTS5 BM25 scores
with sqlite-vec cosine distances via RRF.

**Potential improvement:** RRF treats all retrieval systems equally. Weighted
RRF (`WRRF(d) = Σ w_i / (k + rank_i(d))`) allows tuning the balance between
keyword and semantic retrieval per use case. For research papers, semantic
should likely be weighted higher than keyword.

---

### 2. Cross-Encoder Reranking (Stage-2)

**What it does:** After RRF produces a merged candidate list (top-20), a
cross-encoder model scores each (query, document) pair independently. Unlike
bi-encoder embedding (encode query and doc separately, compute cosine), a
cross-encoder processes both together through full attention, capturing
fine-grained relevance signals.

**Where we found it:** NornicDB uses optional stage-2 reranking after its
hybrid search. We identified this as idea #4 worth stealing.

**Why it matters:** Bi-encoder (embedding) search is fast but coarse — it
compares compressed representations. Cross-encoder is slow but precise — it
sees the full text. Using cross-encoder only on the top-20 RRF candidates
gives precision without the latency of scoring the entire corpus.

**How it contributes to our stack:**
Insert between RRF merge and result return in `search.py`:

```python
# Current flow:
candidates = rrf_merge(fts_results, vec_results)
return candidates[:limit]

# Proposed flow:
candidates = rrf_merge(fts_results, vec_results)[:20]  # coarse top-20
reranked = cross_encode(query, candidates)              # precise rerank
return reranked[:limit]                                 # final top-K
```

**Model options:**

- `bge-reranker-v2-m3` via Ollama (consistent with our Ollama-only stack)
- `ms-marco-MiniLM-L-6-v2` via ONNX Runtime (fast, no Ollama dependency)
- LLM-based reranking via current model (expensive, uses intelligence layer
  for a knowledge-layer task — architecturally wrong per our framework)

**Trade-offs:**

- Ollama reranker: ~50-100ms per candidate pair × 20 = 1-2s additional latency
- ONNX reranker: ~5-10ms per pair × 20 = 100-200ms additional latency
- Quality gain: typically 5-15% improvement in nDCG@10 on academic benchmarks

**Issue:** #106

---

### 3. DBSCAN Clustering (The Librarian)

**What it does:** Density-Based Spatial Clustering of Applications with Noise.
Groups data points (memory entries) into clusters based on density in embedding
space. Points in sparse regions become noise (unclustered). Parameters: `eps`
(maximum distance between cluster members) and `min_samples` (minimum cluster
size).

**Where we found it:** claude-memory-mcp's "Librarian" agent uses DBSCAN to
discover concept groups in the memory graph, then synthesizes summaries per
cluster.

**Why it matters for consolidation:** Dream-cycle consolidation needs to detect
patterns in the activity/insight streams. DBSCAN is a natural fit because:

- It doesn't require specifying the number of clusters (unlike k-means)
- It identifies noise (one-off events that don't belong to any pattern)
- It works directly on embedding vectors (which we already compute)

**The Librarian's failure mode — and what we learn from it:**
claude-memory-mcp found that DBSCAN over-consolidates. The author describes it
as "like an enthusiastic intern" — it merges things that shouldn't be merged,
loses nuance, and was demoted to manual-only.

**Root cause:** DBSCAN clusters by spatial proximity in embedding space, but
proximity doesn't always imply semantic relatedness. Two memories about
"testing" and "test-driven development" cluster together, but one is about
running tests (activity) and the other is about a design philosophy (wisdom).
Merging them destroys the distinction.

**How it contributes to our stack:**
Use DBSCAN for cluster _detection_, not cluster _merging_:

1. Run DBSCAN on accumulated episodes (using their embeddings)
2. For each cluster, surface it as a "consolidation candidate"
3. Present the candidate to the human (or to a review step) rather than auto-merging
4. Human decides: promote to wisdom? Keep as separate episodes? Discard?

**Parameters for our use case:**

- `eps`: Should be calibrated on our embedding space. Too small = every episode
  is noise. Too large = everything clusters into one blob. Start with 0.3 in
  normalized cosine distance.
- `min_samples`: 3 (our consolidation trigger threshold). A pattern needs 3+
  similar episodes before it's worth surfacing.

---

### 4. Ebbinghaus Forgetting Curve (Decay)

**What it does:** Models memory retention as exponential decay:
`R(t) = e^(-t/S)` where `R` is retrievability, `t` is time since last review,
and `S` is stability (how well the memory is consolidated).

**Where we found it:** Already implemented in memory-engine. NornicDB
misapplies it to knowledge. OpenClaw and Claudest don't implement it at all.

**Why it matters:** Without decay, memory accumulates without bound. Every
episode ever recorded has equal weight, drowning signal in noise. Ebbinghaus
provides a principled mechanism: recent, frequently-accessed, and important
memories stay; old, neglected memories fade.

**How it contributes to our stack:**
Memory-engine already uses this. The key insight from this research: decay
should ONLY apply to the memory layer. Never to knowledge (supersession, not
decay) and never to wisdom (explicit revision, not gradual fading).

**The "no deletion" principle:**
Decay lowers retrievability, not existence. `relevance = 0.0` means the memory
is invisible to normal queries but still exists in the database. This enables:

- Auditability (what did the agent know and when)
- Pattern mining (re-promote faded memories if a cluster re-emerges)
- Reversibility (decay was wrong? Restore by bumping relevance)

---

### 5. Extract-Learnings (Claudest's Memory → Wisdom pipeline)

**What it does:** Analyzes raw conversation history for non-obvious insights,
classifies them by type (correction, preference, pattern, decision), and routes
them into the appropriate wisdom artifact (CLAUDE.md, MEMORY.md, topic files)
with human approval gates.

**Where we found it:** Claudest's `extract-learnings` skill.

**Why it matters:** This is the only implemented Memory → Wisdom pipeline we
found in any project. It's crude (no semantic analysis, no decay awareness, no
importance scoring), but the _flow_ is correct:

```
Raw conversations → Pattern detection → Classification → Routing → Approval → Wisdom
```

**How it contributes to our stack:**
memory-engine's consolidation pipeline should replicate this flow with proper
intelligence:

```
Activity + Insight streams
  -> DBSCAN clustering (detect patterns)
  -> Importance scoring (frequency × recency × user-confirmation weight)
  -> Classification:
     - Correction → feedback memory file
     - Preference → MEMORY.md update
     - Decision → CLAUDE.md amendment (if architectural)
     - Pattern → skill creation (if procedural)
  -> Human approval gate
  -> Write to wisdom layer
  -> Mark source episodes as "consolidated" (relevance -> 0)
```

**The approval gate is critical.** The Librarian over-consolidated because it
had no human review. Our pipeline must surface candidates, not auto-promote.

---

### 6. Spreading Activation (Graph Search)

**What it does:** Starting from a seed node, activation spreads to connected
nodes with decay proportional to edge distance. Used to find contextually
relevant nodes in a knowledge graph that aren't directly connected to the query.

**Where we found it:** claude-memory-mcp uses it for graph-augmented retrieval.
Combines vector similarity (find seed nodes) with graph traversal (spread to
neighbors).

**Why it matters:** Pure vector search finds documents similar to the query.
Spreading activation finds documents related _to documents similar to the
query_ — a form of multi-hop reasoning at the retrieval level.

**How it contributes to our stack:**
Our knowledge base has a `relationships` table with typed edges between papers.
Spreading activation would enhance search:

```
Query: "linear attention mechanisms"
  1. Vector search finds Paper A (FlashAttention-3)
  2. Spreading activation follows edges:
     Paper A --cites--> Paper B (FlashAttention-2)
     Paper A --extends--> Paper C (Linear Transformer)
     Paper C --competes_with--> Paper D (Mamba)
  3. Papers B, C, D surface even if their embeddings aren't close to the query
```

This is particularly powerful for research papers where citation chains carry
significant semantic weight. A paper two hops away via `cites` edges is often
more relevant than a paper with similar embeddings but no citation relationship.

**Complexity consideration:** For our current corpus size (<1000 papers),
spreading activation is likely overkill. The computational cost scales with
graph density. Worth implementing when the knowledge base grows past ~5000
papers with dense relationship networks.

---

### 7. Deduplication via Content Hashing

**What it does:** Compute SHA256[:16] of chunk content before insertion. If hash
exists, skip the duplicate. Prevents re-indexing the same content when a
document is re-ingested.

**Where we found it:** Already implemented in our `ingest.py`
(`content_hash TEXT NOT NULL UNIQUE` in the chunks table).

**Why it matters:** Without dedup, re-ingesting a paper doubles its chunks in
the vector table, skewing search results. Content hashing is O(1) lookup vs
O(n) embedding comparison.

**Current state:** Working. The hash covers raw text content. If the same text
appears in two different papers, it's stored once (which is correct — the chunk
is the same fact regardless of source).

**Potential improvement:** Add semantic dedup in addition to exact dedup.
Two chunks with different wording but identical meaning (e.g., abstract from
arXiv vs same abstract from conference proceedings) would have different hashes
but near-identical embeddings. A post-ingest job could detect near-duplicates
(cosine similarity > 0.98) and merge them.

---

### 8. Pre-Compaction Memory Flush

**What it does:** Before context window compaction, inject a silent agentic turn
that forces the model to externalize important state to durable storage.

**Where we found it:** OpenClaw's auto-compaction system.

**Why it matters:** Compaction is lossy by nature — old context is summarized
or dropped. Without a flush, any insights the model held in context but hadn't
yet externalized are lost. The flush gives the model one last chance to save.

**How it contributes to our stack:**
This is the missing piece in our two-stream architecture. The Activity Stream
(hooks) provides the durable backbone. The Insight Stream (model) provides
high-value observations. The pre-flush ensures the Insight Stream captures
everything at the critical moment, even if the model forgot to log earlier.

**Implementation challenge:** Claude Code doesn't expose a `PreCompaction` hook
event. Options:

- Feature request to Anthropic (ideal)
- Stop hook with context size estimation (heuristic, fragile)
- Periodic forced checkpoints via a timed hook (wasteful but guaranteed)

---

## Bootstrapping Strategy: The JSONL Goldmine

Claude Code stores full conversation transcripts as JSONL in
`~/.claude/projects/<path>/<session-id>.jsonl`. These files contain:

- Every message exchanged (user + assistant)
- Every tool call and response
- Timestamps, session metadata

You have 50+ files, some exceeding 5MB. This is months of conversational
history — decisions, corrections, debugging sessions, architectural discussions.

### Why This Matters

When memory-engine first deploys with MCP integration, it starts empty. Cold
start means no context, no patterns, no consolidation candidates. The JSONL
logs are the bootstrap source:

```
Phase 1: JSONL Import
  Parse all session JSONL files
  Extract episodes:
    - User corrections ("don't do X") → high-importance episodic facts
    - Tool call patterns → activity stream backfill
    - Architectural decisions → decision log backfill
    - Test results → project context backfill
  Load into memory-engine with accurate timestamps

Phase 2: Retroactive Consolidation
  Run dream-cycle on imported episodes
  DBSCAN clustering detects historical patterns
  Surface consolidation candidates for review
  Promoted patterns become wisdom layer entries

Phase 3: Steady State
  JSONL import was one-time bootstrap
  Going forward, hooks feed memory-engine directly
  JSONL logs become redundant (but keep for audit)
```

### Migration Pathway

The existing `~/.claude/projects/*/memory/` flat files are wisdom masquerading
as memory. During migration:

1. Import JSONL logs → memory-engine (as episodes)
2. Import flat memory files → memory-engine (as high-confidence consolidated facts)
3. Run dream-cycle → detect which flat-file entries should stay (still relevant)
   vs decay (outdated)
4. The flat files become read-only — memory-engine is now the source of truth
5. MEMORY.md continues as the wisdom layer's human-curated surface, refreshed
   by consolidation proposals from memory-engine

---

## The Human Approval Channel Problem

Memory → Wisdom consolidation requires human approval. The model detects a
pattern and proposes a promotion. But how does the human review and approve?

### The Problem Space

Two use cases, two different interaction models:

**Coding assistant (synchronous):**

- Human is present, reading output in a terminal
- But MEMORY.md updates happen _between_ sessions (consolidation runs at
  SessionStart or via cron)
- Interrupting the user's coding flow with "approve this memory promotion?"
  is a UX anti-pattern

**Autonomous agent (asynchronous):**

- Human is not present during agent operation
- Side-channel communication already exists (Telegram, Slack, etc.)
- Agent can message: "I noticed you've corrected me about X three times.
  Should I make this a permanent rule?" Human replies "yes" or "no"
- Natural fit — the approval gate is just another message

### Options for Coding Assistants

**Option A: SessionStart prompt**
At session start, if memory-engine has pending consolidation candidates:

```
memory-engine has 3 consolidation candidates:
  1. "Always run tests before claiming completion" (seen 5 times)
  2. "Prefer Edit tool over sed" (seen 3 times)
  3. "Don't add co-author to commits" (seen 4 times)
Approve all? [y/n/select]
```

Pros: Non-intrusive (only at session start). Clear approval gate.
Cons: Adds latency to session start. Candidates may accumulate if user
always skips.

**Option B: End-of-session summary**
The Stop hook (or session timeout detector) includes consolidation candidates
in its output:

```
Session summary:
  - Edited search.py, ingest.py
  - 12 tests passed, 2 failed
  - Pending memory promotions: 3 candidates
  Run `/memory approve` to review
```

Pros: Doesn't interrupt flow. User reviews when ready.
Cons: Easily ignored. No guarantee of review.

**Option C: Dedicated command**
A Claude Code slash command (`/memory review`) that surfaces candidates on
demand. The consolidation pipeline queues candidates; the user reviews when
they want.

Pros: User-initiated, no interruption. Integrates with existing slash
command patterns.
Cons: User may never run it. Candidates accumulate.

**Option D: MEMORY.md diff proposal**
memory-engine generates a diff to MEMORY.md and creates a PR-like review:

```
Proposed additions to MEMORY.md:
+ - Always run tests before claiming work is complete (observed 5 times)
+ - Prefer Edit tool over sed for file modifications (observed 3 times)

Accept? [y/n/edit]
```

Pros: Leverages a familiar review pattern (diffs). Shows exactly what changes.
Cons: Still needs a trigger point (when to show the diff).

**Recommended approach: Option A + Option C**

- At SessionStart, show pending candidates if any exist (≤3 lines, don't overload)
- Provide `/memory review` for on-demand deeper review
- If candidates are ignored for >7 days, auto-decay their priority (if the
  pattern wasn't important enough to approve, maybe it wasn't a real pattern)

---

## Embedding Architecture Findings

_(Covered in detail in issues #99 and #100. Summary here for completeness.)_

### The Reddit Mistake We Didn't Make

The r/Rag poster coupled chunking and embedding into one pipeline stage. When
they switched models, they had to re-process 5M raw documents through parsing +
chunking + embedding (~18h) instead of just re-embedding stored chunks (~2-3h).

**We store raw chunks in `chunks.content`** (TEXT column), separate from
embeddings in `chunks_vec` (sqlite-vec virtual table). Re-embedding reads from
`chunks.content` — no re-parsing, no re-chunking.

### Multi-Space Architecture

Instead of one `chunks_vec` table with all-or-nothing model swaps, maintain
multiple embedding spaces simultaneously:

```
chunks (raw text, permanent)
  +-- chunks_vec_bge_m3_1024      float[1024]  status=active
  +-- chunks_vec_qwen3_768       float[768]   status=populating
  +-- chunks_vec_nomic_v1_768    float[768]   status=deprecated
```

This enables zero-downtime migration, A/B comparison, and rollback without
re-embedding.

### Matryoshka Embeddings

Models trained with Matryoshka Representation Learning (MRL) encode information
in order of importance: the first N dimensions of a 1024-dim vector are
themselves a valid N-dim embedding. This means:

- Truncate 1024 → 512 for 2x storage savings (seconds, no re-embedding)
- Compose with int8 quantization for 8x total savings
- Experiment with dimension tradeoffs without touching Ollama

Supported by: Qwen3-Embedding (32-1024), nomic-embed-text-v2-moe (256-768).
Not supported by: BGE-M3 (fixed 1024).

### Dual Chunking Strategy

Two chunking strategies for different context windows:

- **Markdown-aware (8K models):** Current approach. Heading-aware splitting,
  1000 chars, 200 overlap. 15+ chunks per paper.
- **Semantic-aware (32K models):** Split at section boundaries (##). 3-5 chunks
  per paper. No overlap needed. Each chunk is a complete thought.

Qwen3-Embedding-0.6B's 32K context window makes section-level embedding viable.
pymupdf4llm Phase 2 already provides the heading structure — we just need to
consume it at section granularity.

---

## References

- [Reddit: I had to re-embed 5 million documents](https://www.reddit.com/r/Rag/comments/1rqw1oo/i_had_to_reembed_5_million_documents_because_i/)
- [Reddit: Near persistent memory PSA](https://www.reddit.com/r/claude/comments/1rr1xhn/public_service_announcement_near_persistent/)
- [Reddit: Two Claude Code features I slept on](https://www.reddit.com/r/ClaudeAI/comments/1rqxzlp/two_claude_code_features_i_slept_on_that/)
- [NornicDB](https://github.com/orneryd/NornicDB)
- [NornicDB Discussion #26: Co-location architecture](https://github.com/orneryd/NornicDB/discussions/26)
- [claude-memory-mcp (Dragon Brain V2)](https://github.com/iikarus/claude-memory-mcp)
- [Claudest plugin marketplace](https://github.com/gupsammy/claudest)
- [OpenClaw autonomous agent runtime](https://github.com/openclaw/openclaw)
- [OpenClaw compaction docs](https://docs.openclaw.ai/concepts/compaction)
- [OpenClaw memory docs](https://docs.openclaw.ai/concepts/memory)
- [Matryoshka Representation Learning (Kusupati et al., 2022)](https://arxiv.org/abs/2205.13147)
- [Ollama vs TEI performance for Qwen3-Embedding](https://github.com/ollama/ollama/issues/12088)
- [Claude Code memory documentation](https://docs.anthropic.com/en/docs/claude-code/memory)
- [DBSCAN (Ester et al., 1996)](https://www.aaai.org/Papers/KDD/1996/KDD96-037.pdf)
- [Ebbinghaus forgetting curve (1885)](https://en.wikipedia.org/wiki/Forgetting_curve)
