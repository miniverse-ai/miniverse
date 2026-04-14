"""
MemoryStrategy interface for agent memory systems.

This module provides the abstract base class for implementing how agents
remember and recall past experiences. Based on Stanford Generative Agents
research on memory streams.

Key responsibilities:
- Store agent observations, actions, and reflections
- Retrieve relevant memories based on recency, importance, relevance
- Manage memory capacity (forgetting old/unimportant memories)
- Support different memory architectures

Design principle: Start simple (FIFO), enable sophisticated (weighted retrieval).
"""

from abc import ABC, abstractmethod
from collections import Counter
import math
import re
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID
from datetime import datetime
import logging

from miniverse.schemas import AgentMemory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BM25 Scoring Utilities
# ---------------------------------------------------------------------------

def tokenize(text: str) -> List[str]:
    """Simple tokenizer: lowercase, split on non-alphanumeric, filter short tokens."""
    return [t for t in re.split(r'[^a-z0-9]+', text.lower()) if len(t) > 1]


def compute_bm25_scores(
    query_tokens: List[str],
    documents: List[Tuple[str, List[str]]],  # (content, tokens)
    k1: float = 1.5,
    b: float = 0.75,
) -> List[Tuple[str, float]]:
    """
    Compute BM25 scores for documents given a query.

    Returns list of (content, score) tuples sorted by score descending.
    """
    if not documents or not query_tokens:
        return []

    # Calculate corpus statistics
    corpus_size = len(documents)
    doc_lengths = [len(tokens) for _, tokens in documents]
    avgdl = sum(doc_lengths) / corpus_size if corpus_size > 0 else 1.0

    # Calculate document frequencies for IDF
    doc_freqs: Dict[str, int] = Counter()
    for _, tokens in documents:
        unique_terms = set(tokens)
        for term in unique_terms:
            doc_freqs[term] += 1

    # Calculate IDF for query terms
    idf: Dict[str, float] = {}
    for term in query_tokens:
        df = doc_freqs.get(term, 0)
        # BM25 Okapi IDF formula with floor at 0
        idf[term] = max(0, math.log((corpus_size - df + 0.5) / (df + 0.5) + 1))

    # Score each document
    scored: List[Tuple[str, float]] = []
    for i, (content, tokens) in enumerate(documents):
        if not tokens:
            continue

        doc_len = doc_lengths[i]
        term_freqs = Counter(tokens)
        score = 0.0

        for term in query_tokens:
            if term not in term_freqs:
                continue
            tf = term_freqs[term]
            term_idf = idf.get(term, 0)
            # BM25 Okapi TF formula
            numerator = tf * (k1 + 1)
            denominator = tf + k1 * (1 - b + b * doc_len / avgdl)
            score += term_idf * (numerator / denominator)

        if score > 0:
            scored.append((content, score))

    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


class MemoryStrategy(ABC):
    """
    Abstract base class for agent memory systems.

    This interface allows different memory architectures:
    - SimpleMemoryStream: FIFO queue (recent memories only)
    - ImportanceWeightedMemory: Weight by recency + importance
    - RelevanceMemory: Semantic search for relevant memories
    - ReflectionMemory: Periodic higher-level summaries

    Based on Stanford Generative Agents (2023) memory architecture:
    - Memory Stream: Sequential record of observations
    - Retrieval: Recency + importance + relevance scoring
    - Reflection: Periodic summarization of memories
    """

    @abstractmethod
    async def initialize(self) -> None:
        """
        Initialize the memory backend.

        Called once before simulation starts. Used to set up connections,
        allocate resources, load data, etc.

        Raises:
            Exception: If initialization fails
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Close the memory backend.

        Called once after simulation completes. Used to close connections,
        flush buffers, cleanup resources, etc.

        Raises:
            Exception: If cleanup fails
        """
        pass

    @abstractmethod
    async def add_memory(
        self,
        run_id: UUID,
        agent_id: str,
        tick: int,
        memory_type: str,
        content: str,
        importance: int = 5,
        *,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding_key: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> AgentMemory:
        """
        Add a new memory for an agent.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent who owns this memory
            tick: Tick when memory was created
            memory_type: Type (observation, action, communication, reflection)
            content: Memory content (natural language)
            importance: Importance score 1-10 (5 = neutral)
            tags: Optional labels that retrieval engines can use for
                filtering/boosting (e.g., topics, entities)
            metadata: Structured payload for advanced retrieval engines
            embedding_key: Optional pointer to an external embedding entry
            branch_id: Optional branching timeline identifier

        Returns:
            The created AgentMemory object

        Raises:
            Exception: If memory cannot be stored
        """
        pass

    @abstractmethod
    async def get_recent_memories(
        self, run_id: UUID, agent_id: str, limit: int = 10
    ) -> List[str]:
        """
        Retrieve recent memories for an agent as strings.

        Used to build agent perception (recent_observations field).
        Returns natural language strings, not full AgentMemory objects.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier
            limit: Maximum number of memories to return

        Returns:
            List of memory content strings (most recent first)

        Raises:
            Exception: If retrieval fails
        """
        pass

    @abstractmethod
    async def get_relevant_memories(
        self,
        run_id: UUID,
        agent_id: str,
        query: str,
        limit: int = 5,
    ) -> List[str]:
        """
        Retrieve memories relevant to a query.

        Used for context-aware memory retrieval. Advanced implementations
        can use semantic similarity, keyword matching, etc.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier
            query: Query string to find relevant memories
            limit: Maximum number of memories to return

        Returns:
            List of relevant memory content strings

        Raises:
            Exception: If retrieval fails
        """
        pass

    @abstractmethod
    async def clear_agent_memories(self, run_id: UUID, agent_id: str) -> None:
        """
        Clear all memories for an agent.

        Used for testing or resetting agent state.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier

        Raises:
            Exception: If clearing fails
        """
        pass


class SimpleMemoryStream(MemoryStrategy):
    """
    Simple FIFO memory stream implementation.

    Stores all memories and returns the N most recent when queried.
    No importance weighting, no semantic search, no reflection.

    Good for:
    - Initial prototyping
    - Short simulations (<100 ticks)
    - Testing basic agent behavior

    Limitations:
    - No importance-based retrieval
    - No semantic relevance
    - Memory grows unbounded (should add capacity limit)
    - Delegates storage to persistence layer
    """

    def __init__(self, persistence):
        """
        Initialize memory stream with persistence backend.

        Args:
            persistence: PersistenceStrategy instance for storing memories
        """
        self.persistence = persistence

    async def initialize(self) -> None:
        """
        Initialize memory backend.

        For SimpleMemoryStream, this is a no-op since we delegate
        to the persistence layer which handles its own initialization.
        """
        pass

    async def close(self) -> None:
        """
        Close memory backend.

        For SimpleMemoryStream, this is a no-op since we delegate
        to the persistence layer which handles its own cleanup.
        """
        pass

    async def add_memory(
        self,
        run_id: UUID,
        agent_id: str,
        tick: int,
        memory_type: str,
        content: str,
        importance: int = 5,
        *,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding_key: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> AgentMemory:
        """
        Add a memory to the stream.

        Stores via persistence layer. Importance is recorded but not
        used for retrieval in this simple implementation.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent who owns this memory
            tick: Tick when memory was created
            memory_type: Type of memory
            content: Memory content
            importance: Importance score (recorded but not used)
            tags: Optional labels for future retrieval engines
            metadata: Arbitrary key/value payload for retrievers
            embedding_key: Pointer into external embedding store (optional)
            branch_id: Timeline identifier for branching simulations (optional)

        Returns:
            The created AgentMemory object
        """
        import uuid

        memory = AgentMemory(
            id=uuid.uuid4(),
            run_id=run_id,
            agent_id=agent_id,
            tick=tick,
            memory_type=memory_type,
            content=content,
            importance=importance,
            tags=tags or [],
            metadata=metadata or {},
            embedding_key=embedding_key,
            branch_id=branch_id,
            created_at=datetime.now(),
        )

        await self.persistence.save_memory(run_id, memory)
        return memory

    async def get_recent_memories(
        self, run_id: UUID, agent_id: str, limit: int = 10
    ) -> List[str]:
        """
        Get N most recent memories as strings.

        Simple FIFO retrieval: just get the most recent N memories
        by tick number, regardless of importance or relevance.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier
            limit: Maximum memories to return

        Returns:
            List of memory content strings (most recent first)
        """
        memories = await self.persistence.get_recent_memories(run_id, agent_id, limit)
        return [m.content for m in memories]

    async def get_relevant_memories(
        self,
        run_id: UUID,
        agent_id: str,
        query: str,
        limit: int = 5,
    ) -> List[str]:
        """
        Get relevant memories (simple implementation: just recent).

        This simple implementation doesn't do semantic search,
        just returns recent memories. Advanced implementations
        would use embeddings, keyword matching, etc.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier
            query: Query string (unused in simple implementation)
            limit: Maximum memories to return

        Returns:
            List of recent memory content strings
        """
        query = query.lower().strip()
        if not query:
            return await self.get_recent_memories(run_id, agent_id, limit)

        terms = [term for term in query.replace(",", " ").split() if term]
        if not terms:
            return await self.get_recent_memories(run_id, agent_id, limit)

        # Fetch a broader window so we can compute a lightweight score.
        candidate_memories = await self.persistence.get_recent_memories(
            run_id, agent_id, max(limit * 5, limit)
        )
        if not candidate_memories:
            return []

        most_recent_tick = candidate_memories[0].tick
        scores: List[tuple[float, str]] = []

        for mem in candidate_memories:
            text = mem.content.lower()
            tag_text = " ".join(mem.tags).lower()
            score = 0.0

            for term in terms:
                if term in text:
                    score += 2.0
                if term in tag_text:
                    score += 1.0

            if score <= 0.0:
                continue

            # Favor fresher memories without ignoring high-importance items.
            recency_delta = max(most_recent_tick - mem.tick, 0)
            recency_boost = 1.0 / (1.0 + recency_delta)
            score += recency_boost

            # Importance gives a gentle push so high-salience items stay near the top.
            score += mem.importance * 0.1

            scores.append((score, mem.content))

        if not scores:
            return await self.get_recent_memories(run_id, agent_id, limit)

        scores.sort(key=lambda item: item[0], reverse=True)
        return [content for _, content in scores[:limit]]

    async def clear_agent_memories(self, run_id: UUID, agent_id: str) -> None:
        """
        Clear all memories for an agent.

        Delegates to persistence layer which handles the actual deletion.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier
        """
        await self.persistence.clear_agent_memories(run_id, agent_id)


class ImportanceWeightedMemory(MemoryStrategy):
    """Memory retrieval weighted by both recency and importance."""

    def __init__(
        self,
        persistence,
        *,
        recency_weight: float = 0.65,
        importance_weight: float = 0.35,
        window: int = 100,
    ) -> None:
        self.persistence = persistence
        self.recency_weight = recency_weight
        self.importance_weight = importance_weight
        self.window = max(window, 1)

    async def initialize(self) -> None:  # pragma: no cover - delegate to persistence
        pass

    async def close(self) -> None:  # pragma: no cover - delegate to persistence
        pass

    async def add_memory(
        self,
        run_id: UUID,
        agent_id: str,
        tick: int,
        memory_type: str,
        content: str,
        importance: int = 5,
        *,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding_key: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> AgentMemory:
        import uuid

        memory = AgentMemory(
            id=uuid.uuid4(),
            run_id=run_id,
            agent_id=agent_id,
            tick=tick,
            memory_type=memory_type,
            content=content,
            importance=importance,
            tags=tags or [],
            metadata=metadata or {},
            embedding_key=embedding_key,
            branch_id=branch_id,
            created_at=datetime.now(),
        )

        await self.persistence.save_memory(run_id, memory)
        return memory

    async def get_recent_memories(
        self, run_id: UUID, agent_id: str, limit: int = 10
    ) -> List[str]:
        memories = await self.persistence.get_recent_memories(run_id, agent_id, limit)
        return [m.content for m in memories]

    async def get_relevant_memories(
        self,
        run_id: UUID,
        agent_id: str,
        query: str,
        limit: int = 5,
    ) -> List[str]:
        # Grab a fixed window and score each entry using a convex combination of
        # normalized recency and importance. A non-empty query boosts keyword matches.
        candidate_memories = await self.persistence.get_recent_memories(
            run_id, agent_id, self.window
        )
        if not candidate_memories:
            return []

        most_recent_tick = candidate_memories[0].tick
        normalized_query = query.lower().strip()
        terms = [term for term in normalized_query.replace(",", " ").split() if term]

        scored: List[tuple[float, str]] = []
        for mem in candidate_memories:
            # Recency normalized to [0, 1].
            recency_delta = max(most_recent_tick - mem.tick, 0)
            recency = 1.0 / (1.0 + recency_delta)
            importance = mem.importance / 10.0
            base_score = (
                self.recency_weight * recency
                + self.importance_weight * importance
            )

            if not terms:
                scored.append((base_score, mem.content))
                continue

            text = mem.content.lower()
            tag_blob = " ".join(mem.tags).lower()
            keyword_score = 0.0
            for term in terms:
                if term in text:
                    keyword_score += 1.5
                if term in tag_blob:
                    keyword_score += 0.75

            if keyword_score <= 0.0:
                continue

            scored.append((base_score + keyword_score, mem.content))

        if not scored:
            # Fall back to generic recency/importance ordering if no keyword match.
            scored = [
                (
                    self.recency_weight
                    * (1.0 / (1.0 + max(most_recent_tick - mem.tick, 0)))
                    + self.importance_weight * (mem.importance / 10.0),
                    mem.content,
                )
                for mem in candidate_memories[: limit * 2]
            ]

        scored.sort(key=lambda item: item[0], reverse=True)
        return [content for _, content in scored[:limit]]

    async def clear_agent_memories(self, run_id: UUID, agent_id: str) -> None:
        """
        Clear all memories for an agent.

        Delegates to persistence layer which handles the actual deletion.

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier
        """
        await self.persistence.clear_agent_memories(run_id, agent_id)


class BM25MemoryStrategy(MemoryStrategy):
    """
    Memory retrieval using BM25 ranking combined with recency and importance.

    This strategy implements proper information retrieval scoring:
    - BM25 for term relevance (handles term frequency, document frequency, length normalization)
    - Recency decay (fresher memories get a boost)
    - Importance weighting (high-salience memories surface)

    Good for:
    - Medium to long simulations (>50 ticks)
    - Scenarios where context relevance matters
    - Research requiring realistic memory recall

    Based on:
    - BM25 Okapi (Robertson et al.)
    - Stanford Generative Agents memory architecture
    """

    def __init__(
        self,
        persistence,
        *,
        bm25_weight: float = 0.5,
        recency_weight: float = 0.3,
        importance_weight: float = 0.2,
        window: int = 200,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        """
        Initialize BM25 memory strategy.

        Args:
            persistence: PersistenceStrategy instance for storing memories
            bm25_weight: Weight for BM25 relevance score (0-1)
            recency_weight: Weight for recency (0-1)
            importance_weight: Weight for importance score (0-1)
            window: Maximum memories to consider for retrieval
            k1: BM25 term frequency saturation parameter
            b: BM25 document length normalization parameter
        """
        self.persistence = persistence
        self.bm25_weight = bm25_weight
        self.recency_weight = recency_weight
        self.importance_weight = importance_weight
        self.window = max(window, 1)
        self.k1 = k1
        self.b = b

    async def initialize(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def add_memory(
        self,
        run_id: UUID,
        agent_id: str,
        tick: int,
        memory_type: str,
        content: str,
        importance: int = 5,
        *,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding_key: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> AgentMemory:
        import uuid

        memory = AgentMemory(
            id=uuid.uuid4(),
            run_id=run_id,
            agent_id=agent_id,
            tick=tick,
            memory_type=memory_type,
            content=content,
            importance=importance,
            tags=tags or [],
            metadata=metadata or {},
            embedding_key=embedding_key,
            branch_id=branch_id,
            created_at=datetime.now(),
        )

        await self.persistence.save_memory(run_id, memory)
        return memory

    async def get_recent_memories(
        self, run_id: UUID, agent_id: str, limit: int = 10
    ) -> List[str]:
        memories = await self.persistence.get_recent_memories(run_id, agent_id, limit)
        return [m.content for m in memories]

    async def get_relevant_memories(
        self,
        run_id: UUID,
        agent_id: str,
        query: str,
        limit: int = 5,
    ) -> List[str]:
        """
        Retrieve memories relevant to a query using BM25 + recency + importance.

        The scoring formula is:
            final_score = bm25_weight * normalized_bm25
                        + recency_weight * recency_score
                        + importance_weight * normalized_importance

        Args:
            run_id: Simulation run identifier
            agent_id: Agent identifier
            query: Query string to find relevant memories
            limit: Maximum number of memories to return

        Returns:
            List of relevant memory content strings
        """
        # Fetch candidate memories
        candidate_memories = await self.persistence.get_recent_memories(
            run_id, agent_id, self.window
        )
        if not candidate_memories:
            return []

        # Tokenize query
        query_tokens = tokenize(query)
        if not query_tokens:
            # No valid query tokens - fall back to recency + importance
            return await self._score_without_query(candidate_memories, limit)

        # Build document corpus for BM25: (content, tokens)
        documents: List[Tuple[str, List[str]]] = []
        memory_map: Dict[str, AgentMemory] = {}
        for mem in candidate_memories:
            # Include tags in searchable text
            full_text = mem.content + " " + " ".join(mem.tags)
            tokens = tokenize(full_text)
            documents.append((mem.content, tokens))
            memory_map[mem.content] = mem

        # Compute BM25 scores
        bm25_results = compute_bm25_scores(query_tokens, documents, self.k1, self.b)

        if not bm25_results:
            # No BM25 matches - fall back to recency + importance
            return await self._score_without_query(candidate_memories, limit)

        # Normalize BM25 scores to [0, 1]
        max_bm25 = max(score for _, score in bm25_results) if bm25_results else 1.0
        bm25_scores = {content: score / max_bm25 for content, score in bm25_results}

        # Calculate recency and importance for matched documents
        most_recent_tick = candidate_memories[0].tick
        scored: List[Tuple[float, str]] = []

        for content in bm25_scores:
            mem = memory_map[content]

            # Recency: exponential decay from most recent
            recency_delta = max(most_recent_tick - mem.tick, 0)
            recency_score = 1.0 / (1.0 + recency_delta)

            # Importance: normalized to [0, 1]
            importance_score = mem.importance / 10.0

            # BM25 score (already normalized)
            bm25_score = bm25_scores[content]

            # Combined weighted score
            final_score = (
                self.bm25_weight * bm25_score
                + self.recency_weight * recency_score
                + self.importance_weight * importance_score
            )

            scored.append((final_score, content))

        # Sort by final score and return top results
        scored.sort(key=lambda x: x[0], reverse=True)
        return [content for _, content in scored[:limit]]

    async def _score_without_query(
        self, memories: List[AgentMemory], limit: int
    ) -> List[str]:
        """Score memories using only recency and importance when no query matches."""
        if not memories:
            return []

        most_recent_tick = memories[0].tick
        scored: List[Tuple[float, str]] = []

        for mem in memories[:limit * 2]:
            recency_delta = max(most_recent_tick - mem.tick, 0)
            recency_score = 1.0 / (1.0 + recency_delta)
            importance_score = mem.importance / 10.0

            # Adjust weights when no BM25: recency + importance only
            adjusted_recency_weight = self.recency_weight / (
                self.recency_weight + self.importance_weight
            )
            adjusted_importance_weight = self.importance_weight / (
                self.recency_weight + self.importance_weight
            )

            final_score = (
                adjusted_recency_weight * recency_score
                + adjusted_importance_weight * importance_score
            )
            scored.append((final_score, mem.content))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [content for _, content in scored[:limit]]

    async def clear_agent_memories(self, run_id: UUID, agent_id: str) -> None:
        await self.persistence.clear_agent_memories(run_id, agent_id)


class SemanticMemoryStrategy(MemoryStrategy):
    """
    Stanford-style three-factor memory retrieval: recency + importance + relevance.

    Combines:
    - Semantic similarity via sentence-transformer embeddings (relevance)
    - BM25 keyword matching (lexical recall for exact terms)
    - Exponential recency decay (Stanford: 0.995^hours, here: configurable per tick)
    - Importance with access-based refresh (memories fade unless re-accessed)

    This is the research-grade memory strategy for experiments where emergent
    behavior depends on agents drawing connections across experiences.
    """

    def __init__(
        self,
        persistence,
        *,
        embedding_model: str = "all-MiniLM-L6-v2",
        relevance_weight: float = 0.4,
        recency_weight: float = 0.3,
        importance_weight: float = 0.2,
        bm25_weight: float = 0.1,
        window: int = 200,
        decay_rate: float = 0.95,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        """
        Args:
            persistence: PersistenceStrategy for storage
            embedding_model: sentence-transformers model name
            relevance_weight: Weight for semantic similarity (0-1)
            recency_weight: Weight for recency decay (0-1)
            importance_weight: Weight for importance score (0-1)
            bm25_weight: Weight for BM25 lexical match (0-1)
            window: Max memories to consider per retrieval
            decay_rate: Recency decay per tick (Stanford uses 0.995/hour)
            k1, b: BM25 parameters
        """
        self.persistence = persistence
        self.embedding_model_name = embedding_model
        self.relevance_weight = relevance_weight
        self.recency_weight = recency_weight
        self.importance_weight = importance_weight
        self.bm25_weight = bm25_weight
        self.window = max(window, 1)
        self.decay_rate = decay_rate
        self.k1 = k1
        self.b = b

        # Lazy-loaded embedding model and cache
        self._model = None
        self._embedding_cache: Dict[str, list] = {}

    def _get_model(self):
        """Lazy-load the sentence transformer model."""
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.embedding_model_name)
                logger.info(f"Loaded embedding model: {self.embedding_model_name}")
            except ImportError:
                raise ImportError(
                    "sentence-transformers required for SemanticMemoryStrategy. "
                    "Install with: uv add sentence-transformers"
                )
        return self._model

    def _embed(self, texts: List[str]) -> list:
        """Compute embeddings with caching."""
        model = self._get_model()
        uncached = [t for t in texts if t not in self._embedding_cache]
        if uncached:
            embeddings = model.encode(uncached, show_progress_bar=False)
            for text, emb in zip(uncached, embeddings):
                self._embedding_cache[text] = emb.tolist()
        return [self._embedding_cache[t] for t in texts]

    def _cosine_similarity(self, a: list, b: list) -> float:
        """Compute cosine similarity between two vectors."""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    async def initialize(self) -> None:
        # Pre-load model on init so first retrieval isn't slow
        self._get_model()

    async def close(self) -> None:
        self._embedding_cache.clear()

    async def add_memory(
        self,
        run_id: UUID,
        agent_id: str,
        tick: int,
        memory_type: str,
        content: str,
        importance: int = 5,
        *,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding_key: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> AgentMemory:
        import uuid

        memory = AgentMemory(
            id=uuid.uuid4(),
            run_id=run_id,
            agent_id=agent_id,
            tick=tick,
            memory_type=memory_type,
            content=content,
            importance=importance,
            tags=tags or [],
            metadata=metadata or {},
            embedding_key=embedding_key,
            branch_id=branch_id,
            created_at=datetime.now(),
            last_accessed_tick=None,
        )

        # Pre-compute embedding on add (amortize cost)
        self._embed([content])

        await self.persistence.save_memory(run_id, memory)
        return memory

    async def get_recent_memories(
        self, run_id: UUID, agent_id: str, limit: int = 10
    ) -> List[str]:
        memories = await self.persistence.get_recent_memories(run_id, agent_id, limit)
        return [m.content for m in memories]

    async def get_relevant_memories(
        self,
        run_id: UUID,
        agent_id: str,
        query: str,
        limit: int = 5,
    ) -> List[str]:
        """
        Three-factor retrieval: relevance (semantic + BM25) + recency + importance.

        Stanford formula adapted:
            score = relevance_weight * semantic_sim
                  + bm25_weight * normalized_bm25
                  + recency_weight * decay_score
                  + importance_weight * decayed_importance
        """
        candidate_memories = await self.persistence.get_recent_memories(
            run_id, agent_id, self.window
        )
        if not candidate_memories:
            return []

        if not query or not query.strip():
            return await self._score_without_query(candidate_memories, limit)

        most_recent_tick = candidate_memories[0].tick

        # Compute semantic similarity
        query_embedding = self._embed([query])[0]
        memory_contents = [m.content for m in candidate_memories]
        memory_embeddings = self._embed(memory_contents)

        # Compute BM25 scores
        query_tokens = tokenize(query)
        documents = [
            (m.content, tokenize(m.content + " " + " ".join(m.tags)))
            for m in candidate_memories
        ]
        bm25_results = compute_bm25_scores(query_tokens, documents, self.k1, self.b)
        max_bm25 = max((s for _, s in bm25_results), default=1.0) or 1.0
        bm25_map = {content: score / max_bm25 for content, score in bm25_results}

        # Score each memory
        scored: List[Tuple[float, str, AgentMemory]] = []
        for mem, mem_emb in zip(candidate_memories, memory_embeddings):
            # Semantic relevance (cosine similarity, already in [0,1] for normalized embeddings)
            semantic_score = max(0.0, self._cosine_similarity(query_embedding, mem_emb))

            # BM25 lexical relevance
            bm25_score = bm25_map.get(mem.content, 0.0)

            # Recency: exponential decay from most recent tick
            ticks_ago = max(most_recent_tick - mem.tick, 0)
            recency_score = self.decay_rate ** ticks_ago

            # Importance with access-based decay:
            # Base importance decays from creation tick, but resets on access
            reference_tick = mem.last_accessed_tick if mem.last_accessed_tick is not None else mem.tick
            ticks_since_access = max(most_recent_tick - reference_tick, 0)
            importance_decay = self.decay_rate ** (ticks_since_access * 0.5)  # slower decay than recency
            importance_score = (mem.importance / 10.0) * importance_decay

            final_score = (
                self.relevance_weight * semantic_score
                + self.bm25_weight * bm25_score
                + self.recency_weight * recency_score
                + self.importance_weight * importance_score
            )

            scored.append((final_score, mem.content, mem))

        # Sort by score, return top results
        scored.sort(key=lambda x: x[0], reverse=True)

        # Mark retrieved memories as accessed (refresh their decay clock)
        for _, _, mem in scored[:limit]:
            mem.last_accessed_tick = most_recent_tick

        return [content for _, content, _ in scored[:limit]]

    async def _score_without_query(
        self, memories: List[AgentMemory], limit: int
    ) -> List[str]:
        """Score using recency + importance only (no query)."""
        if not memories:
            return []

        most_recent_tick = memories[0].tick
        scored: List[Tuple[float, str]] = []

        for mem in memories[:limit * 2]:
            ticks_ago = max(most_recent_tick - mem.tick, 0)
            recency_score = self.decay_rate ** ticks_ago

            reference_tick = mem.last_accessed_tick if mem.last_accessed_tick is not None else mem.tick
            ticks_since_access = max(most_recent_tick - reference_tick, 0)
            importance_decay = self.decay_rate ** (ticks_since_access * 0.5)
            importance_score = (mem.importance / 10.0) * importance_decay

            # Redistribute relevance weight to recency + importance
            total = self.recency_weight + self.importance_weight
            adj_recency = self.recency_weight / total if total > 0 else 0.5
            adj_importance = self.importance_weight / total if total > 0 else 0.5

            final_score = adj_recency * recency_score + adj_importance * importance_score
            scored.append((final_score, mem.content))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [content for _, content in scored[:limit]]

    async def clear_agent_memories(self, run_id: UUID, agent_id: str) -> None:
        await self.persistence.clear_agent_memories(run_id, agent_id)
