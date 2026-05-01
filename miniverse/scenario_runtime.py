"""Runtime loading helpers for scenario-local rules, cognition, and actions."""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from miniverse import AgentCognition, AgentProfile, SimulationRules
    from miniverse.scenario_actions import ScenarioActions


def _load_module(module_path: Path, module_label: str) -> Optional[ModuleType]:
    """Load a Python module from an explicit file path."""
    spec = importlib.util.spec_from_file_location(module_label, module_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime_section(runtime: Optional[Dict[str, Any]], key: str) -> Dict[str, Any]:
    """Return normalized runtime subsection mapping."""
    if not isinstance(runtime, dict):
        return {}
    section = runtime.get(key)
    if not isinstance(section, dict):
        return {}
    return section


def load_scenario_rules(
    scenario_dir: Path,
    *,
    seed: Optional[int] = None,
    runtime: Optional[Dict[str, Any]] = None,
) -> Optional["SimulationRules"]:
    """Dynamically load SimulationRules from scenario-local `rules.py`."""
    from miniverse import SimulationRules

    rules_cfg = _runtime_section(runtime, "rules")
    module_name = rules_cfg.get("module", "rules.py")
    rules_path = scenario_dir / str(module_name)
    if not rules_path.exists():
        return None

    module = _load_module(rules_path, f"scenario_rules_{scenario_dir.name}")
    if module is None:
        return None

    rules_class = None
    class_name = rules_cfg.get("class")
    if isinstance(class_name, str):
        candidate = getattr(module, class_name, None)
        if (
            isinstance(candidate, type)
            and issubclass(candidate, SimulationRules)
            and candidate is not SimulationRules
        ):
            rules_class = candidate

    if rules_class is None:
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (
                isinstance(obj, type)
                and issubclass(obj, SimulationRules)
                and obj is not SimulationRules
            ):
                rules_class = obj
                break

    if rules_class is None:
        return None

    kwargs = rules_cfg.get("kwargs", {})
    if not isinstance(kwargs, dict):
        kwargs = {}
    ctor_kwargs = dict(kwargs)
    if seed is not None and "rng" not in ctor_kwargs:
        ctor_kwargs["rng"] = random.Random(seed)

    for attempt_kwargs in (
        ctor_kwargs,
        {k: v for k, v in ctor_kwargs.items() if k != "rng"},
        {},
    ):
        try:
            return rules_class(**attempt_kwargs)
        except TypeError:
            continue
    return None


def load_scenario_cognition(
    scenario_dir: Path,
    profiles: Dict[str, "AgentProfile"],
    *,
    use_llm: bool = False,
    runtime: Optional[Dict[str, Any]] = None,
) -> Dict[str, "AgentCognition"]:
    """Load scenario-local cognition config from `cognition.py` or `rules.py`.

    Fallback is library defaults (deterministic or LLM-backed).
    """
    from miniverse import AgentCognition
    from miniverse.cognition import Scratchpad

    cognition_cfg = _runtime_section(runtime, "cognition")
    builder_name = cognition_cfg.get("builder", "build_cognition")
    if not isinstance(builder_name, str) or not builder_name:
        builder_name = "build_cognition"

    module_candidates: list[str] = []
    configured_module = cognition_cfg.get("module")
    if isinstance(configured_module, str) and configured_module:
        module_candidates.append(configured_module)
    else:
        module_candidates.extend(["cognition.py", "rules.py"])

    kwargs = cognition_cfg.get("kwargs", {})
    if not isinstance(kwargs, dict):
        kwargs = {}

    for filename in module_candidates:
        module_path = scenario_dir / filename
        if not module_path.exists():
            continue
        module = _load_module(
            module_path,
            f"scenario_cognition_{scenario_dir.name}_{module_path.stem}",
        )
        if module is None:
            continue
        builder = getattr(module, builder_name, None)
        if not callable(builder) and builder_name != "build_cognition":
            builder = getattr(module, "build_cognition", None)
        if not callable(builder):
            continue

        for call in (
            lambda: builder(profiles, use_llm=use_llm, **kwargs),
            lambda: builder(profiles, use_llm=use_llm),
            lambda: builder(profiles, **kwargs),
            lambda: builder(profiles),
        ):
            try:
                result = call()
            except TypeError:
                continue
            if isinstance(result, dict):
                return result

    if use_llm:
        from miniverse import AgentCognition
        from miniverse.cognition import (
            LLMPlanner,
            LLMReflectionEngine,
            Scratchpad,
        )
        from miniverse.cognition.llm import LLMExecutor

        available_actions = [
            {"action_type": "work", "description": "Work on current task"},
            {"action_type": "communicate", "description": "Send message to another agent"},
            {"action_type": "move", "description": "Move to different location"},
            {"action_type": "rest", "description": "Rest to recover energy"},
            {"action_type": "analyze", "description": "Analyze situation or data"},
            {"action_type": "monitor", "description": "Monitor systems or environment"},
        ]

        cognition_map: Dict[str, AgentCognition] = {}
        for agent_id in profiles:
            cognition_map[agent_id] = AgentCognition(
                planner=LLMPlanner(template_name="plan"),
                executor=LLMExecutor(
                    template_name="default",
                    available_actions=available_actions,
                ),
                reflection=LLMReflectionEngine(template_name="reflect_diary"),
                scratchpad=Scratchpad(),
            )
        return cognition_map

    from miniverse.cognition.runtime import build_default_cognition

    cognition_map = {}
    for agent_id in profiles:
        cognition_map[agent_id] = build_default_cognition()
    return cognition_map


def load_scenario_actions(
    scenario_dir: Path,
    *,
    runtime: Optional[Dict[str, Any]] = None,
) -> Optional["ScenarioActions"]:
    """Dynamically load ScenarioActions from scenario-local ``actions.py``.

    Follows the same discovery pattern as :func:`load_scenario_rules`:
    load the module, scan for a :class:`ScenarioActions` subclass,
    instantiate with kwargs from the runtime config.
    """
    from miniverse.scenario_actions import ScenarioActions

    actions_cfg = _runtime_section(runtime, "actions")
    module_name = actions_cfg.get("module", "actions.py")
    actions_path = scenario_dir / str(module_name)
    if not actions_path.exists():
        return None

    module = _load_module(actions_path, f"scenario_actions_{scenario_dir.name}")
    if module is None:
        return None

    actions_class = None
    class_name = actions_cfg.get("class")
    if isinstance(class_name, str):
        candidate = getattr(module, class_name, None)
        if (
            isinstance(candidate, type)
            and issubclass(candidate, ScenarioActions)
            and candidate is not ScenarioActions
        ):
            actions_class = candidate

    if actions_class is None:
        for attr_name in dir(module):
            obj = getattr(module, attr_name)
            if (
                isinstance(obj, type)
                and issubclass(obj, ScenarioActions)
                and obj is not ScenarioActions
            ):
                actions_class = obj
                break

    if actions_class is None:
        return None

    kwargs = actions_cfg.get("kwargs", {})
    if not isinstance(kwargs, dict):
        kwargs = {}

    for attempt_kwargs in (kwargs, {}):
        try:
            return actions_class(**attempt_kwargs)
        except TypeError:
            continue
    return None
