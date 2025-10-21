"""
PersistenceStrategy interface for pluggable storage backends.

This module provides the abstract PersistenceStrategy interface and three concrete
implementations for storing simulation state. Persistence is OPTIONAL - simulations
can run entirely in-memory with no database or file system dependencies.

Core principle: "Library should work without any storage backend (in-memory default)."

Three included implementations:
1. InMemoryPersistence - Dict-based storage, data lost on exit (testing, prototyping)
2. JsonPersistence - File-based storage, human-readable JSON (small simulations)
3. PostgresPersistence - Database storage, scalable and queryable (production)

Key responsibilities:
- Save/retrieve WorldState snapshots by tick
- Store agent actions for replay and analysis
- Store agent memories for memory stream persistence
- Track run metadata (configuration, status, timing)
- Handle async operations (enables concurrent persistence during simulation)

Async design rationale:
- Persistence happens concurrently with simulation (don't block agent cognition)
- Database I/O can be slow - async prevents blocking entire tick
- initialize() and close() manage connection lifecycle (pools, files, etc.)

Usage pattern:
    # Choose persistence backend
    persistence = InMemoryPersistence()  # or JsonPersistence(), PostgresPersistence()

    # Initialize before simulation
    await persistence.initialize()

    # Use during simulation
    await persistence.save_state(run_id, tick, world_state)

    # Clean up after simulation
    await persistence.close()
"""

import asyncio
import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from uuid import UUID

from miniverse.schemas import AgentAction, AgentMemory, SimulationRun, WorldState

from .config import Config

try:  # Optional dependency (only needed for PostgresPersistence)
    import asyncpg
except (
    ImportError
):  # pragma: no cover - asyncpg may not be installed for json/memory usage
    asyncpg = None


class PersistenceStrategy(ABC):
    """Abstract base class for simulation state persistence.

    PersistenceStrategy defines the interface that all storage backends must implement.
    This enables pluggable persistence - swap backends without changing simulation code.

    Async interface rationale:
    - All methods are async to support I/O-bound operations (database, files)
    - Enables concurrent persistence (save state while running next tick)
    - initialize() and close() manage connection pools, file handles, etc.
    - Async is no-op for InMemoryPersistence but critical for database backends

    Method categories:
    1. Lifecycle: initialize(), close()
    2. Run metadata: save_run_metadata(), update_run_status()
    3. State snapshots: save_state(), get_state()
    4. Actions: save_action(), save_actions(), get_actions()
    5. Memories: save_memory(), get_recent_memories()

    Concrete implementations:
    - InMemoryPersistence: Fast, ephemeral, no dependencies (testing/prototyping)
    - JsonPersistence: Human-readable files, small simulations, easy debugging
    - PostgresPersistence: Scalable database, complex queries, production-ready
    - Custom: Implement this interface for S3, Redis, MongoDB, etc.

    Design pattern: Strategy pattern - behavior varies (in-memory vs database)
    but interface remains consistent. Orchestrator depends on interface, not implementation.
    """

    @abstractmethod
    async def initialize(self) -> None:
        """
        Initialize the persistence backend.

        Called once before simulation starts. Used to set up database
        connections, create tables, open files, etc.

        Raises:
            Exception: If initialization fails
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Close the persistence backend.

        Called once after simulation completes. Used to close database
        connections, flush buffers, close files, etc.

        Raises:
            Exception: If cleanup fails
        """
        pass

    @abstractmethod
    async def save_run_metadata(self, run: SimulationRun) -> None:
        """
        Save metadata about a simulation run.

        Args:
            run: SimulationRun object with run metadata

        Raises:
            Exception: If save fails
        """
        pass

    @abstractmethod
    async def update_run_status(
        self, run_id: UUID, status: str, end_time: Optional[datetime] = None
    ) -> None:
        """
        Update the status of a simulation run.

        Args:
            run_id: Unique run identifier
            status: New status (running, completed, failed)
            end_time: Optional end time if run completed

        Raises:
            Exception: If update fails
        """
        pass

    @abstractmethod
    async def save_state(self, run_id: UUID, tick: int, state: WorldState) -> None:
        """
        Save world state for a specific tick.

        Args:
            run_id: Unique run identifier
            tick: Tick number
            state: Complete world state to save

        Raises:
            Exception: If save fails
        """
        pass

    @abstractmethod
    async def get_state(self, run_id: UUID, tick: int) -> Optional[WorldState]:
        """
        Retrieve world state for a specific tick.

        Args:
            run_id: Unique run identifier
            tick: Tick number to retrieve

        Returns:
            WorldState if found, None otherwise

        Raises:
            Exception: If retrieval fails
        """
        pass

    @abstractmethod
    async def save_action(self, run_id: UUID, action: AgentAction) -> None:
        """
        Save an agent action.

        Args:
            run_id: Unique run identifier
            action: Agent action to save

        Raises:
            Exception: If save fails
        """
        pass

    @abstractmethod
    async def save_memory(self, run_id: UUID, memory: AgentMemory) -> None:
        """
        Save an agent memory.

        Args:
            run_id: Unique run identifier
            memory: Agent memory to save

        Raises:
            Exception: If save fails
        """
        pass

    @abstractmethod
    async def get_recent_memories(
        self, run_id: UUID, agent_id: str, limit: int = 10
    ) -> List[AgentMemory]:
        """
        Retrieve recent memories for an agent.

        Args:
            run_id: Unique run identifier
            agent_id: Agent identifier
            limit: Maximum number of memories to return

        Returns:
            List of recent memories (most recent first)

        Raises:
            Exception: If retrieval fails
        """
        pass

    @abstractmethod
    async def save_actions(
        self, run_id: UUID, tick: int, actions: List[AgentAction]
    ) -> None:
        """
        Save multiple agent actions for a tick.

        Convenience method for bulk saving. Implementations can optimize
        this for batch operations (e.g., single database transaction).

        Args:
            run_id: Unique run identifier
            tick: Tick number
            actions: List of actions to save

        Raises:
            Exception: If save fails
        """
        pass

    @abstractmethod
    async def get_actions(self, run_id: UUID, tick: int) -> List[AgentAction]:
        """
        Get all actions for a tick.

        Args:
            run_id: Unique run identifier
            tick: Tick number

        Returns:
            List of actions for the tick

        Raises:
            Exception: If retrieval fails
        """
        pass

    @abstractmethod
    async def clear_agent_memories(self, run_id: UUID, agent_id: str) -> None:
        """
        Clear all memories for a specific agent in a run.

        Args:
            run_id: Unique run identifier
            agent_id: Agent identifier

        Raises:
            Exception: If clearing fails
        """
        pass

    @abstractmethod
    async def delete_run(self, run_id: UUID) -> None:
        """
        Delete an entire simulation run and all associated data.

        Removes: run metadata, world states, actions, memories.

        Args:
            run_id: Unique run identifier

        Raises:
            Exception: If deletion fails
        """
        pass


class InMemoryPersistence(PersistenceStrategy):
    """In-memory persistence using Python dicts (no database, no files).

    InMemoryPersistence stores all simulation data in process memory using Python
    dictionaries. Data is ephemeral - lost when process exits. Zero dependencies,
    instant initialization, perfect for testing and prototyping.

    Storage structure:
    - runs: Dict[UUID, SimulationRun] - run metadata
    - states: Dict[(run_id, tick), WorldState] - state snapshots by (run, tick) key
    - actions: Dict[(run_id, tick), List[AgentAction]] - actions by (run, tick) key
    - memories: Dict[(run_id, agent_id), List[AgentMemory]] - memories by (run, agent) key

    Perfect for:
    - Rapid prototyping (no setup, just import and use)
    - Unit testing (fast, isolated, no cleanup needed)
    - Short simulations (< 100 ticks, few agents)
    - Environments without database/file access (sandboxed, containers)
    - Learning and experimentation

    NOT suitable for:
    - Long-running simulations (unbounded memory growth)
    - Large-scale simulations (100+ agents, 1000+ ticks)
    - Multi-process simulations (no shared memory)
    - Persistence across restarts (data lost on exit)
    - Analysis and replay (no query capabilities)

    Performance characteristics:
    - Save: O(1) dict insert
    - Retrieve: O(1) dict lookup
    - Memory: O(ticks * agents) - linear growth, no cleanup
    """

    def __init__(self):
        """Initialize in-memory storage."""
        self.runs: Dict[UUID, SimulationRun] = {}
        self.states: Dict[tuple[UUID, int], WorldState] = {}
        self.actions: Dict[tuple[UUID, int], List[AgentAction]] = {}
        self.memories: Dict[tuple[UUID, str], List[AgentMemory]] = {}

    async def initialize(self) -> None:
        """
        Initialize in-memory storage.

        No-op for in-memory implementation.
        """
        pass

    async def close(self) -> None:
        """
        Close in-memory storage.

        No-op for in-memory: we do NOT clear data on close so callers can
        perform post-run reads (e.g., debugging, analysis). Use delete_run()
        or clear_agent_memories() for explicit cleanup.
        """
        pass

    async def save_run_metadata(self, run: SimulationRun) -> None:
        """
        Save run metadata to memory.

        Args:
            run: SimulationRun metadata
        """
        self.runs[run.id] = run

    async def update_run_status(
        self, run_id: UUID, status: str, end_time: Optional[datetime] = None
    ) -> None:
        """
        Update run status in memory.

        Args:
            run_id: Run identifier
            status: New status
            end_time: Optional end time
        """
        if run_id in self.runs:
            self.runs[run_id].status = status
            if end_time:
                self.runs[run_id].end_time = end_time

    async def save_state(self, run_id: UUID, tick: int, state: WorldState) -> None:
        """
        Save world state to memory.

        Args:
            run_id: Run identifier
            tick: Tick number
            state: World state to save
        """
        self.states[(run_id, tick)] = state

    async def get_state(self, run_id: UUID, tick: int) -> Optional[WorldState]:
        """
        Retrieve world state from memory.

        Args:
            run_id: Run identifier
            tick: Tick number

        Returns:
            WorldState if found, None otherwise
        """
        return self.states.get((run_id, tick))

    async def save_action(self, run_id: UUID, action: AgentAction) -> None:
        """
        Save agent action to memory.

        Args:
            run_id: Run identifier
            action: Agent action to save
        """
        key = (run_id, action.tick)
        if key not in self.actions:
            self.actions[key] = []
        self.actions[key].append(action)

    async def save_memory(self, run_id: UUID, memory: AgentMemory) -> None:
        """
        Save agent memory to memory storage.

        Args:
            run_id: Run identifier
            memory: Agent memory to save
        """
        key = (run_id, memory.agent_id)
        if key not in self.memories:
            self.memories[key] = []
        self.memories[key].append(memory)

    async def get_recent_memories(
        self, run_id: UUID, agent_id: str, limit: int = 10
    ) -> List[AgentMemory]:
        """
        Retrieve recent memories from memory storage.

        Args:
            run_id: Run identifier
            agent_id: Agent identifier
            limit: Maximum memories to return

        Returns:
            List of recent memories (most recent first)
        """
        key = (run_id, agent_id)
        all_memories = self.memories.get(key, [])
        # Sort by tick descending, take limit
        sorted_memories = sorted(all_memories, key=lambda m: m.tick, reverse=True)
        return sorted_memories[:limit]

    async def save_actions(
        self, run_id: UUID, tick: int, actions: List[AgentAction]
    ) -> None:
        """
        Save multiple agent actions for a tick.

        Convenience method for saving multiple actions at once.

        Args:
            run_id: Run identifier
            tick: Tick number
            actions: List of agent actions to save
        """
        for action in actions:
            await self.save_action(run_id, action)

    async def get_actions(self, run_id: UUID, tick: int) -> List[AgentAction]:
        """
        Retrieve actions for a tick from memory.

        Args:
            run_id: Run identifier
            tick: Tick number

        Returns:
            List of actions (empty if none)
        """
        return self.actions.get((run_id, tick), [])

    async def clear_agent_memories(self, run_id: UUID, agent_id: str) -> None:
        """
        Clear all memories for a specific agent.

        Args:
            run_id: Run identifier
            agent_id: Agent identifier
        """
        key = (run_id, agent_id)
        if key in self.memories:
            self.memories[key] = []

    async def delete_run(self, run_id: UUID) -> None:
        """
        Delete entire simulation run and all associated data.

        Args:
            run_id: Run identifier
        """
        # Remove run metadata
        self.runs.pop(run_id, None)

        # Remove states
        state_keys = [key for key in self.states if key[0] == run_id]
        for key in state_keys:
            del self.states[key]

        # Remove actions
        action_keys = [key for key in self.actions if key[0] == run_id]
        for key in action_keys:
            del self.actions[key]

        # Remove memories
        memory_keys = [key for key in self.memories if key[0] == run_id]
        for key in memory_keys:
            del self.memories[key]


class PostgresPersistence(PersistenceStrategy):
    """PostgreSQL-backed persistence for production simulations.

    PostgresPersistence stores all simulation data in a PostgreSQL database using
    async connection pooling (asyncpg). Enables complex queries, historical analysis,
    and multi-process simulations sharing the same database.

    Database schema:
    - simulation_runs: Run metadata (id, start_time, status, config)
    - world_states: State snapshots (run_id, tick, state JSONB)
    - agent_actions: Action history (run_id, agent_id, tick, action JSONB)
    - agent_memories: Memory streams (run_id, agent_id, tick, content, importance)

    Schema setup:
    - Run scripts/init_db.py to create tables
    - Or use CREATE TABLE statements from schema file
    - Connection pool managed by asyncpg (max connections, automatic reconnect)

    Perfect for:
    - Production simulations (100+ ticks, 10+ agents)
    - Historical analysis (SQL queries, aggregations, time-series)
    - Multi-process simulations (shared database, no race conditions)
    - Persistence across restarts (resume failed simulations)
    - Complex queries (find all runs where X happened)

    NOT suitable for:
    - Quick prototypes (requires database setup)
    - Testing (slower than in-memory, requires cleanup)
    - Offline environments (needs running Postgres instance)

    Performance characteristics:
    - Save: O(1) database insert with connection pooling
    - Retrieve: O(1) indexed query (run_id + tick index)
    - Memory: Minimal (data stored in database, not process memory)
    - Disk: JSONB compression (smaller than JSON files)

    Connection management:
    - initialize() creates connection pool (reusable connections)
    - close() releases pool (clean shutdown)
    - Pool size configurable via asyncpg.create_pool(max_size=...)
    """

    def __init__(self, database_url: Optional[str] = None):
        if (
            asyncpg is None
        ):  # pragma: no cover - handled during runtime when dependency missing
            raise ImportError(
                "asyncpg is required for PostgresPersistence. Install with `uv add asyncpg`."
            )

        self.database_url = database_url or Config.DATABASE_URL
        self.pool: Optional[asyncpg.Pool] = None

    async def initialize(self) -> None:
        if self.pool is None:
            self.pool = await asyncpg.create_pool(self.database_url)

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def save_run_metadata(self, run: SimulationRun) -> None:
        assert self.pool is not None, "Persistence not initialized"

        payload = run.model_dump(mode="json")
        config_json = json.dumps(payload["config"])

        query = """
            INSERT INTO simulation_runs (
                id, start_time, end_time, num_ticks, num_agents, status, config, created_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)
            ON CONFLICT (id) DO UPDATE
            SET start_time=$2, end_time=$3, num_ticks=$4, num_agents=$5, status=$6, config=$7::jsonb
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                run.id,
                run.start_time,
                run.end_time,
                run.num_ticks,
                run.num_agents,
                run.status,
                config_json,
                run.created_at,
            )

    async def update_run_status(
        self, run_id: UUID, status: str, end_time: Optional[datetime] = None
    ) -> None:
        assert self.pool is not None, "Persistence not initialized"

        query = """
            UPDATE simulation_runs
            SET status = $2, end_time = $3
            WHERE id = $1
        """

        async with self.pool.acquire() as conn:
            await conn.execute(query, run_id, status, end_time)

    async def save_state(self, run_id: UUID, tick: int, state: WorldState) -> None:
        assert self.pool is not None, "Persistence not initialized"

        state_json = state.model_dump_json()
        query = """
            INSERT INTO world_states (run_id, tick, state)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (run_id, tick) DO UPDATE SET state = $3::jsonb
        """

        async with self.pool.acquire() as conn:
            await conn.execute(query, run_id, tick, state_json)

    async def get_state(self, run_id: UUID, tick: int) -> Optional[WorldState]:
        assert self.pool is not None, "Persistence not initialized"

        query = """
            SELECT state
            FROM world_states
            WHERE run_id = $1 AND tick = $2
        """

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, run_id, tick)

        if not row:
            return None

        return WorldState.model_validate_json(row["state"])

    async def save_action(self, run_id: UUID, action: AgentAction) -> None:
        await self.save_actions(run_id, action.tick, [action])

    async def save_actions(
        self, run_id: UUID, tick: int, actions: List[AgentAction]
    ) -> None:
        assert self.pool is not None, "Persistence not initialized"

        if not actions:
            return

        query = """
            INSERT INTO agent_actions (run_id, agent_id, tick, action)
            VALUES ($1, $2, $3, $4::jsonb)
        """

        records = [
            (run_id, action.agent_id, tick, action.model_dump_json())
            for action in actions
        ]

        async with self.pool.acquire() as conn:
            await conn.executemany(query, records)

    async def get_actions(self, run_id: UUID, tick: int) -> List[AgentAction]:
        assert self.pool is not None, "Persistence not initialized"

        query = """
            SELECT action
            FROM agent_actions
            WHERE run_id = $1 AND tick = $2
            ORDER BY created_at
        """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, run_id, tick)

        return [AgentAction.model_validate_json(row["action"]) for row in rows]

    async def clear_agent_memories(self, run_id: UUID, agent_id: str) -> None:
        assert self.pool is not None, "Persistence not initialized"

        query = """
            DELETE FROM agent_memories
            WHERE run_id = $1 AND agent_id = $2
        """

        async with self.pool.acquire() as conn:
            await conn.execute(query, run_id, agent_id)

    async def delete_run(self, run_id: UUID) -> None:
        assert self.pool is not None, "Persistence not initialized"

        queries = [
            "DELETE FROM agent_memories WHERE run_id = $1",
            "DELETE FROM agent_actions WHERE run_id = $1",
            "DELETE FROM world_states WHERE run_id = $1",
            "DELETE FROM simulation_runs WHERE id = $1",
        ]

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                for query in queries:
                    await conn.execute(query, run_id)

    async def save_memory(self, run_id: UUID, memory: AgentMemory) -> None:
        assert self.pool is not None, "Persistence not initialized"

        query = """
            INSERT INTO agent_memories
            (
                run_id, agent_id, tick, memory_type, content, importance, tags, metadata,
                embedding_key, branch_id
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7::text[], $8::jsonb, $9, $10
            )
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                run_id,
                memory.agent_id,
                memory.tick,
                memory.memory_type,
                memory.content,
                memory.importance,
                memory.tags,
                json.dumps(memory.metadata),
                memory.embedding_key,
                memory.branch_id,
            )

    async def get_recent_memories(
        self, run_id: UUID, agent_id: str, limit: int = 10
    ) -> List[AgentMemory]:
        assert self.pool is not None, "Persistence not initialized"

        query = """
            SELECT id, tick, memory_type, content, importance, tags, metadata, embedding_key, branch_id, created_at
            FROM agent_memories
            WHERE run_id = $1 AND agent_id = $2
            ORDER BY tick DESC
            LIMIT $3
        """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, run_id, agent_id, limit)

        memories = []
        for row in rows:
            memories.append(
                AgentMemory(
                    id=row["id"],
                    run_id=run_id,
                    agent_id=agent_id,
                    tick=row["tick"],
                    memory_type=row["memory_type"],
                    content=row["content"],
                    importance=row["importance"],
                    tags=row["tags"] or [],
                    metadata=row["metadata"] or {},
                    embedding_key=row["embedding_key"],
                    branch_id=row["branch_id"],
                    created_at=row["created_at"],
                )
            )

        return memories


class JsonPersistence(PersistenceStrategy):
    """File-based persistence using JSON for human-readable storage.

    JsonPersistence stores simulation data as JSON files in a directory structure.
    Files are human-readable, easy to inspect, and work well for small simulations.
    No database required - just filesystem access.

    Directory structure:
    ```
    {base_path}/
      {run_id}/
        run.json                  # SimulationRun metadata
        states/
          00000.json              # WorldState at tick 0
          00001.json              # WorldState at tick 1
          ...
        actions/
          00000.json              # List[AgentAction] at tick 0
          00001.json              # List[AgentAction] at tick 1
          ...
        memories/
          alice.jsonl             # JSONL stream of memories for alice
          bob.jsonl               # JSONL stream of memories for bob
          ...
    ```

    File format details:
    - States/actions: Pretty-printed JSON (indent=2) for readability
    - Memories: JSONL (JSON Lines) - one memory per line, append-only
    - Tick padding: 5 digits (00000-99999) for lexicographic sorting

    Perfect for:
    - Small simulations (< 100 ticks, few agents)
    - Debugging (inspect state at any tick by opening JSON file)
    - Version control (commit scenario runs to git)
    - Sharing results (zip directory, send to colleague)
    - No database setup (just filesystem)

    NOT suitable for:
    - Large simulations (100+ agents, 1000+ ticks → thousands of files)
    - High performance (file I/O slower than database)
    - Concurrent access (no locking, race conditions possible)
    - Complex queries (need to parse all files)

    Performance characteristics:
    - Save: O(1) file write, async to avoid blocking
    - Retrieve: O(1) file read with known tick number
    - Memory: Minimal (data on disk, not in memory)
    - Disk: Larger than database (pretty-printed JSON, no compression)

    Async operations:
    - All file I/O runs in thread pool (asyncio.to_thread)
    - Prevents blocking simulation during disk writes
    - initialize() creates base directory
    - close() is no-op (no cleanup needed)
    """

    def __init__(self, base_path: Path | str = "simulation_runs"):
        self.base_path = Path(base_path)

    async def initialize(self) -> None:
        await asyncio.to_thread(self.base_path.mkdir, parents=True, exist_ok=True)

    async def close(self) -> None:
        # Nothing to clean up for JSON persistence
        return None

    async def save_run_metadata(self, run: SimulationRun) -> None:
        run_dir = self._run_dir(run.id)
        await asyncio.to_thread(run_dir.mkdir, parents=True, exist_ok=True)
        path = run_dir / "run.json"
        data = run.model_dump(mode="json")
        await asyncio.to_thread(path.write_text, json.dumps(data, indent=2), "utf-8")

    async def update_run_status(
        self, run_id: UUID, status: str, end_time: Optional[datetime] = None
    ) -> None:
        path = self._run_dir(run_id) / "run.json"
        if not path.exists():  # Nothing to update yet
            return

        def _update() -> None:
            payload = json.loads(path.read_text("utf-8"))
            payload["status"] = status
            payload["end_time"] = end_time.isoformat() if end_time else None
            path.write_text(json.dumps(payload, indent=2), "utf-8")

        await asyncio.to_thread(_update)

    async def save_state(self, run_id: UUID, tick: int, state: WorldState) -> None:
        path = self._run_dir(run_id) / "states" / f"{tick:05d}.json"
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        payload = state.model_dump(mode="json")
        await asyncio.to_thread(path.write_text, json.dumps(payload, indent=2), "utf-8")

    async def get_state(self, run_id: UUID, tick: int) -> Optional[WorldState]:
        path = self._run_dir(run_id) / "states" / f"{tick:05d}.json"
        if not path.exists():
            return None

        payload = await asyncio.to_thread(json.loads, path.read_text("utf-8"))
        return WorldState.model_validate(payload)

    async def save_action(self, run_id: UUID, action: AgentAction) -> None:
        await self.save_actions(run_id, action.tick, [action])

    async def save_actions(
        self, run_id: UUID, tick: int, actions: List[AgentAction]
    ) -> None:
        path = self._run_dir(run_id) / "actions" / f"{tick:05d}.json"
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        payload = [action.model_dump(mode="json") for action in actions]
        await asyncio.to_thread(path.write_text, json.dumps(payload, indent=2), "utf-8")

    async def get_actions(self, run_id: UUID, tick: int) -> List[AgentAction]:
        path = self._run_dir(run_id) / "actions" / f"{tick:05d}.json"
        if not path.exists():
            return []

        payload = await asyncio.to_thread(json.loads, path.read_text("utf-8"))
        return [AgentAction.model_validate(item) for item in payload]

    async def clear_agent_memories(self, run_id: UUID, agent_id: str) -> None:
        path = self._run_dir(run_id) / "memories" / f"{agent_id}.jsonl"
        if path.exists():
            await asyncio.to_thread(path.unlink)

    async def delete_run(self, run_id: UUID) -> None:
        import shutil

        run_dir = self._run_dir(run_id)
        if run_dir.exists():
            await asyncio.to_thread(shutil.rmtree, run_dir)

    async def save_memory(self, run_id: UUID, memory: AgentMemory) -> None:
        directory = self._run_dir(run_id) / "memories"
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        path = directory / f"{memory.agent_id}.jsonl"
        payload = memory.model_dump(mode="json")

        def _append() -> None:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload))
                handle.write("\n")

        await asyncio.to_thread(_append)

    async def get_recent_memories(
        self, run_id: UUID, agent_id: str, limit: int = 10
    ) -> List[AgentMemory]:
        path = self._run_dir(run_id) / "memories" / f"{agent_id}.jsonl"
        if not path.exists():
            return []

        def _read() -> List[str]:
            return path.read_text("utf-8").splitlines()

        lines = await asyncio.to_thread(_read)
        memories = [AgentMemory.model_validate_json(line) for line in lines if line]

        memories.sort(key=lambda m: m.tick, reverse=True)
        return memories[:limit]

    def _run_dir(self, run_id: UUID) -> Path:
        return self.base_path / str(run_id)
