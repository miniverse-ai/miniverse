"""Action execution for LLMBazaar (Kōen Market) scenario.

4 vendor agents compete across multi-day market cycles.
Day structure: Planning → Market (timed) → Planning → Dream → advance.
Orders placed during planning arrive 2 calendar days later. If the
arrival date falls while the market is closed, stock is available at the
next open market session.

Key artifacts:
- Ledger: system-generated, append-only transaction log (per vendor)
- Plan: LLM-written strategy notes (per vendor, pinned to system prompt)
- List notes: LLM-written shopping notes (per customer, pinned to system prompt)
- Dream memories: LLM-compressed active context windows (stored in semantic memory)
"""

from __future__ import annotations

import time
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import yaml

from miniverse.scenario_actions import ScenarioActions
from miniverse.schemas import ActionResult


VENDOR_IDS = {"vendor_a", "vendor_b", "vendor_c", "vendor_d"}
CUSTOMER_IDS = {"haruki", "yuki", "kenji", "mei", "tomoko", "ryo"}
SUPPLIER_ID = "supplier"


class BazaarActions(ScenarioActions):
    """Manages Kōen Market state across multi-day market cycles."""

    def __init__(self, state_path: str | Path | None = None):
        super().__init__()
        if state_path is None:
            state_path = Path(__file__).parent / "state.yaml"
        with open(state_path) as f:
            data = yaml.safe_load(f)

        # Shared wholesale catalog
        self.catalog: Dict[str, dict] = data.get("catalog", {})

        # Market timing config
        market_cfg = data.get("market", {})
        self.open_hour: int = market_cfg.get("open_hour", 9)
        self.close_hour: int = market_cfg.get("close_hour", 13)
        self.real_min_per_sim_hour: float = market_cfg.get("real_minutes_per_sim_hour", 1)
        self.simulation_days: int = int(market_cfg.get("simulation_days", 5))
        configured_fee = market_cfg.get("daily_operating_fee")
        self.daily_operating_fee: float = (
            float(configured_fee)
            if configured_fee is not None
            else self._infer_daily_fee(data)
        )
        self.planning_timeout_seconds: float = float(
            market_cfg.get("planning_timeout_seconds", 90)
        )
        self.current_date: date = datetime.strptime(
            market_cfg.get("start_date", "2026-10-11"), "%Y-%m-%d"
        ).date()
        weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        self.open_weekdays: Set[int] = {
            weekday_names.index(day)
            for day in market_cfg.get("days_open", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"])
        }
        self._day_dates: Dict[int, date] = {0: self.current_date}

        # Vendor state
        self.vendors: Dict[str, dict] = {}
        for vid, vdata in data.get("vendor_inventory", {}).items():
            self.vendors[vid] = {
                "cash": vdata["starting_cash"],
                "stock": dict(vdata["stock"]),
                "listed_prices": {},   # item_id -> price (set during planning)
                "ledger": [],          # system-generated: {day, time, customer, item, listed, final, margin}
                "plan": "",            # LLM-written plan artifact
                "pending_orders": [],  # {items, cost, ordered_day, ordered_date, arrives_date}
                "active": True,
                "fees_paid": 0,
            }

        # Customer state
        self.customers: Dict[str, dict] = {}
        customer_data = data.get("customers", {})
        for cid, cdata in customer_data.items():
            self.customers[cid] = {
                "name": cdata["name"],
                "budget": cdata["budget"],
                "remaining_budget": cdata["budget"],
                "shopping_list": list(cdata["shopping_list"]),
                "still_need": list(cdata["shopping_list"]),
                "purchased": [],
                "list_context": cdata.get("list_context", ""),
                "written_list": "",  # LLM-written list artifact
                "next_shopping_list": [],
            }

        # Cache originals for daily reset
        self._original_customers = {
            cid: {
                "budget": cdata["budget"],
                "shopping_list": list(cdata["shopping_list"]),
                "list_context": cdata.get("list_context", ""),
            }
            for cid, cdata in customer_data.items()
        }

        # Arrival waves
        self._arrival_template: List[dict] = data.get("arrival_waves", [])

        # Active visits: {customer_id: vendor_id}
        self.active_visits: Dict[str, str] = {}
        # Local stall dialogue and formal offers. Dialogue is scenario state,
        # not inbox/mail: agents see it through stall tools.
        self.stall_chats: Dict[tuple[str, str], List[dict]] = {}
        self.market_chats: List[dict] = []
        self.supplier_chats: Dict[str, List[dict]] = {}
        self._supplier_waiting_vendors: Set[str] = set()
        self.formal_offers: Dict[tuple[str, str], List[dict]] = {}

        # ── Phase / round state ──
        self.current_day: int = 0  # Day 0 = pre-market planning
        self.phase: str = "planning"  # "planning" or "market"
        self._market_start_time: Optional[float] = None  # wall clock when market opened
        self._planning_start_time: Optional[float] = None  # wall clock when planning began
        self._vendors_done: Set[str] = set()
        self._customers_done: Set[str] = set()
        self._arrived_customers: Set[str] = set()
        self._triggered_waves: Set[int] = set()
        self._step_in_phase: int = 0
        self._simulation_complete: bool = False
        self._completion_reason: str = ""

        # pending_messages, pending_memories, context_resets initialized by super().__init__()

        # Send initial planning phase messages
        self._start_planning_phase(is_day_zero=True)

    def _infer_daily_fee(self, data: Dict[str, Any]) -> float:
        """Default daily fee: starting cash divided over the configured week."""
        market_cfg = data.get("market", {})
        simulation_days = int(market_cfg.get("simulation_days", 5))
        vendor_data = data.get("vendor_inventory", {})
        if not vendor_data or simulation_days <= 0:
            return 0.0
        starting_cash_values = [
            float(v.get("starting_cash", 0)) for v in vendor_data.values()
        ]
        if not starting_cash_values:
            return 0.0
        return min(starting_cash_values) / simulation_days

    def _active_vendor_ids(self) -> Set[str]:
        return {
            vid
            for vid in VENDOR_IDS
            if self.vendors.get(vid, {}).get("active", True)
        }

    def _is_vendor_active(self, vendor_id: str) -> bool:
        return bool(self.vendors.get(vendor_id, {}).get("active", True))

    def _close_vendor(self, vendor_id: str, reason: str) -> None:
        vendor = self.vendors.get(vendor_id)
        if not vendor or not vendor.get("active", True):
            return
        vendor["active"] = False
        self._queue_context(
            vendor_id,
            "Market Administration",
            f"Your Kōen Market business is closed: {reason}. "
            "You may review your ledger, but you may not trade or order supplier stock.",
        )
        if not self._active_vendor_ids():
            self.phase = "complete"
            self._simulation_complete = True
            self._completion_reason = (
                f"All Kōen Market vendors are closed after {self.current_day} market sessions."
            )

    def _queue_context(self, target: str, sender: str, content: str) -> None:
        self.pending_context_markers.append({
            "to": target,
            "content": f"{sender}: {content}",
        })

    def _charge_daily_fees(self) -> None:
        """Charge each active vendor's operating fee before market entry."""
        if self.daily_operating_fee <= 0 or self.current_day <= 0:
            return
        for vid in sorted(VENDOR_IDS):
            vendor = self.vendors.get(vid)
            if not vendor or not vendor.get("active", True):
                continue
            if vendor["cash"] < self.daily_operating_fee:
                self._close_vendor(
                    vid,
                    (
                        "cash balance "
                        f"${vendor['cash']:.2f} was below the "
                        f"${self.daily_operating_fee:.2f} operating fee required to enter today"
                    ),
                )
                if self._simulation_complete:
                    break
                continue
            vendor["cash"] -= self.daily_operating_fee
            vendor["fees_paid"] += 1
            vendor["ledger"].append({
                "day": self.current_day,
                "time": "market entry",
                "type": "operating_fee",
                "amount": self.daily_operating_fee,
                "cash_after": vendor["cash"],
            })

    # ── Time mechanics ──

    def _get_simulated_time(self) -> str:
        """Get current simulated market time based on wall clock."""
        if self.phase != "market" or self._market_start_time is None:
            return ""
        elapsed_real_min = (time.time() - self._market_start_time) / 60.0
        elapsed_sim_hours = elapsed_real_min / self.real_min_per_sim_hour
        current_hour = self.open_hour + elapsed_sim_hours
        if current_hour >= self.close_hour:
            return f"{self.close_hour}:00 (market closing)"
        hour_int = int(current_hour)
        minutes = int((current_hour - hour_int) * 60)
        return f"{hour_int}:{minutes:02d}"

    def _format_date(self, value: Optional[date] = None) -> str:
        if value is None:
            value = self.current_date
        return f"{value.strftime('%A')}, {value.strftime('%B')} {value.day}, {value.year}"

    def _market_hours_text(self) -> str:
        return f"{self.open_hour}:00-{self.close_hour}:00"

    def _next_open_date(self, value: Optional[date] = None) -> date:
        if value is None:
            value = self.current_date
        candidate = value + timedelta(days=1)
        while candidate.weekday() not in self.open_weekdays:
            candidate += timedelta(days=1)
        return candidate

    def _date_for_market_day(self, day_number: int) -> date:
        if day_number in self._day_dates:
            return self._day_dates[day_number]
        current = self._day_dates[max(self._day_dates)]
        for day in range(max(self._day_dates) + 1, day_number + 1):
            current = self._next_open_date(current)
            self._day_dates[day] = current
        return self._day_dates[day_number]

    def _order_arrival_date(self) -> date:
        """Supplier delivery after two calendar days, not market sessions."""
        return self.current_date + timedelta(days=2)

    def _parse_order_arrival_date(self, order: dict) -> date:
        raw = order.get("arrives_date")
        if raw:
            return datetime.strptime(str(raw), "%Y-%m-%d").date()
        if "arrives_day" in order:
            return self._date_for_market_day(int(order["arrives_day"]))
        return self._order_arrival_date()

    def _format_order_arrival(self, order: dict) -> str:
        return self._format_date(self._parse_order_arrival_date(order))

    def _is_market_expired(self) -> bool:
        """Check if the timed market phase has ended."""
        if self.phase != "market" or self._market_start_time is None:
            return False
        elapsed_real_min = (time.time() - self._market_start_time) / 60.0
        sim_hours_elapsed = elapsed_real_min / self.real_min_per_sim_hour
        return sim_hours_elapsed >= (self.close_hour - self.open_hour)

    # ── Phase management ──

    def _start_planning_phase(self, is_day_zero: bool = False) -> None:
        """Begin planning phase."""
        if (
            not is_day_zero
            and self.simulation_days > 0
            and self.current_day >= self.simulation_days
        ):
            self.phase = "complete"
            self._simulation_complete = True
            self._completion_reason = (
                f"Kōen Market run complete after {self.current_day} market sessions."
            )
            self.active_visits.clear()
            for vid in sorted(VENDOR_IDS):
                v = self.vendors.get(vid)
                if not v:
                    continue
                self._queue_context(
                    vid,
                    "Market Administration",
                    f"{self._completion_reason} Final cash: ${v['cash']:.2f}. "
                    f"Stall status: {'open' if v.get('active', True) else 'closed'}.",
                )
            return
        self.phase = "planning"
        self._step_in_phase = 0
        self._planning_start_time = time.time()
        self._vendors_done.clear()
        self._customers_done.clear()
        self._arrived_customers.clear()
        self.active_visits.clear()
        if not is_day_zero:
            # Any offer not accepted before the bell expires with that market day.
            self.formal_offers.clear()

        if is_day_zero:
            # Initial planning before the first market session.
            next_market_date = self._next_open_date()
            for vid in VENDOR_IDS:
                catalog_str = self._format_catalog()
                self._queue_context(
                    vid,
                    "Market Administration",
                    f"Welcome to Kōen Market. Today is {self._format_date()}. "
                    f"The market operates Monday-Friday, {self._market_hours_text()}.\n"
                    f"The next market session opens {self._format_date(next_market_date)}.\n\n"
                    f"MARKET BOUNDARY:\n"
                    f"- This market session contains only the named vendors and customers shown by your tools.\n"
                    f"- Do not refer customers to unseen aisles, outside stalls, or imaginary vendors.\n"
                    f"- If a customer wants something no active vendor has, you may honestly say so, offer substitutes, or plan to source it from Hayashi Supply after market close.\n\n"
                    f"BUSINESS ECONOMICS:\n"
                    f"- Starting cash: ${self.vendors[vid]['cash']:.2f}\n"
                    f"- Daily operating fee: ${self.daily_operating_fee:.2f}, charged before each market session opens\n"
                    f"- If you cannot pay the entry fee, your business cannot open.\n\n"
                    f"WHOLESALE CATALOG (shared pricing):\n{catalog_str}\n\n"
                    f"Make routine market decisions yourself from your goals, cash, "
                    f"inventory, ledger, and available market information. Use "
                    f"respond/respond_to only for speech to people in the market or "
                    f"private supplier negotiation; do not use speech to summarize "
                    f"tool use or ask for operating instructions.\n\n"
                    f"Review your inventory and set your listed prices for "
                    f"the next market session using set_prices. Write your strategy using "
                    f"write_plan. Use order_from_supplier for standard catalog stock. "
                    f"For specialty items, write privately to Hayashi Supply during "
                    f"preparation and use place_supplier_order after you agree on "
                    f"item, quantity, and unit cost. Customers may ask for goods "
                    f"you do not currently carry; supplier orders arrive after two "
                    f"calendar days and are available at the next open market "
                    f"session if they arrive while the market is closed. "
                    f"Negotiation with the supplier is normal.\n\n"
                    f"When done, use wait_for_next_day.",
                )
            # Customers do not plan before the first market session. Their
            # first list is seeded in state.yaml and inserted into the first
            # market-day context reset when the market opens.
            for cid in CUSTOMER_IDS:
                self._customers_done.add(cid)
        else:
            # Market close is an episode boundary. Preserve the lived market
            # context for dreaming before resetting active prompts into the
            # preparation action contract.
            self._capture_day_context_for_dream()
            # End of day planning
            for vid in VENDOR_IDS:
                v = self.vendors[vid]
                if not v.get("active", True):
                    continue
                # A fresh preparation phase requires a fresh strategy note.
                # Listed prices persist, but the plan must be rewritten daily
                # so stale holds or closing-hour tactics do not carry forward.
                v["plan"] = ""
                today_sales = [
                    e for e in v["ledger"]
                    if e.get("day") == self.current_day and e.get("type", "sale") == "sale"
                ]
                revenue = sum(e["final_price"] for e in today_sales)
                items_sold = len(today_sales)
                self._queue_context(
                    vid,
                    "Market Bell",
                    f"Kōen Market is now CLOSED for {self._format_date()}.\n"
                    f"Today: {items_sold} items sold, ${revenue:.2f} revenue.\n\n"
                    f"Cash on hand: ${v['cash']:.2f}. Tomorrow's market-entry fee: "
                    f"${self.daily_operating_fee:.2f}. If you cannot pay it, your business cannot open.\n"
                    f"This market session contains only the named vendors and customers shown by your tools. "
                    f"Do not refer customers to unseen aisles, outside stalls, or imaginary vendors. "
                    f"If no active vendor has an item, say so, offer substitutes, or source it from Hayashi Supply after market close.\n"
                    f"Make routine market decisions yourself from your goals, cash, inventory, ledger, "
                    f"and available market information. Use respond/respond_to only for speech to "
                    f"people in the market or private supplier negotiation; do not use speech to "
                    f"summarize tool use or ask for operating instructions.\n"
                    f"PREPARATION TIME:\n"
                    f"- Review your ledger (check_ledger)\n"
                    f"- Set prices for the next market session (set_prices)\n"
                    f"- Write your strategy (write_plan)\n"
                    f"- Order standard catalog stock if needed (order_from_supplier)\n"
                    f"- Negotiate specialty stock by writing privately to Hayashi Supply, then place quoted order (place_supplier_order)\n"
                    f"  Orders placed now arrive after two calendar days and are available at the next open market session if the market is closed on the arrival date.\n\n"
                    f"Use wait_for_next_day when done.",
                )
                self.context_resets[vid] = self._build_day_prompt(vid, [], "")
            for cid in CUSTOMER_IDS:
                self._queue_context(
                    cid,
                    "Market Bell",
                    f"Kōen Market has closed for the day: {self._format_date()}.\n"
                    f"The market contains only the named vendors and customers shown by your tools. "
                    f"Do not assume unseen aisles, outside stalls, or imaginary vendors exist.\n"
                    f"Make routine shopping decisions yourself from your preferences, budget, "
                    f"shopping list, and what you learned today.\n"
                    f"Create a new shopping list for the next market session using write_list. "
                    f"Choose items that match your preferences, what you want to eat/cook/buy, "
                    f"what you learned today, and your budget. Pick 3-6 items total, with at most "
                    f"2 unusual or specialty wants. Use ordinary item names. If you want "
                    f"something unusual, write a clear name for it.\n"
                    f"Writing the list completes preparation; you will wait until the market opens.",
                )
                self.context_resets[cid] = self._build_day_prompt(cid, [], "")

    def _capture_day_context_for_dream(self) -> None:
        """Snapshot active market-day context before preparation resets it."""
        for agent_id in sorted(VENDOR_IDS | CUSTOMER_IDS):
            snapshot = self._render_context_window_for_dream(agent_id)
            if agent_id in VENDOR_IDS and agent_id in self.vendors:
                self.vendors[agent_id]["_dream_context_snapshot"] = snapshot
            elif agent_id in CUSTOMER_IDS and agent_id in self.customers:
                self.customers[agent_id]["_dream_context_snapshot"] = snapshot

    def _start_market_phase(self) -> None:
        """Begin a new market day."""
        self.current_date = self._next_open_date()
        self.current_day += 1
        self._day_dates[self.current_day] = self.current_date
        self._charge_daily_fees()
        if self._simulation_complete:
            return
        self.phase = "market"
        self._step_in_phase = 0
        self._market_start_time = time.time()
        self._vendors_done.clear()
        self._customers_done.clear()
        self._arrived_customers.clear()
        self._triggered_waves.clear()
        self.active_visits.clear()

        # Deliver orders arriving today
        for vid, v in self.vendors.items():
            if not v.get("active", True):
                continue
            delivered = []
            still_pending = []
            for order in v["pending_orders"]:
                if self._parse_order_arrival_date(order) <= self.current_date:
                    for item_id, qty in order["items"].items():
                        v["stock"][item_id] = v["stock"].get(item_id, 0) + qty
                    delivered.append(order)
                else:
                    still_pending.append(order)
            v["pending_orders"] = still_pending
            if delivered:
                lines = []
                for order in delivered:
                    for item_id, qty in order["items"].items():
                        name = self.catalog.get(item_id, {}).get("name", item_id)
                        lines.append(f"  {name}: +{qty}")
                self._queue_context(vid, "Supplier", "Delivery arrived!\n" + "\n".join(lines))

        for cid, c in self.customers.items():
            orig = self._original_customers[cid]
            c["remaining_budget"] = orig["budget"]
            next_list = c.get("next_shopping_list") or list(orig["shopping_list"])
            c["shopping_list"] = list(next_list)
            c["still_need"] = list(next_list)
            c["purchased"] = []
            c["next_shopping_list"] = []

        # Notify vendors
        for vid in VENDOR_IDS:
            v = self.vendors[vid]
            if not v.get("active", True):
                continue
            prices_str = ""
            if v["listed_prices"]:
                prices_str = "\nYour listed prices:\n" + "\n".join(
                    f"  {self.catalog.get(k, {}).get('name', k)}: ${p:.2f}"
                    for k, p in sorted(v["listed_prices"].items())
                )
            self._queue_context(
                vid,
                "Market Bell",
                f"Kōen Market is now OPEN for {self._format_date()} "
                f"({self._market_hours_text()}){prices_str}\n\n"
                f"Market-entry fee paid: ${self.daily_operating_fee:.2f}. "
                f"Cash now: ${v['cash']:.2f}.\n"
                f"Customers will be arriving. Check customer activity and "
                f"serve customers as they engage your business.",
            )

    def _advance_day(self) -> None:
        """All agents done planning. Start next market day.

        ``_start_market_phase`` increments the internal market-session index.
        After that, queue context resets so agents wake up for the next
        market session with a fresh prompt that references the current
        calendar date. If a dream phase just ran, those memories are
        included; the first market day uses empty memories because customers
        start from a pre-set shopping plan rather than a Sunday planning turn.
        """
        self._start_market_phase()
        # Queue resets now that current_day reflects the new day.
        for vid in VENDOR_IDS:
            if not self.vendors.get(vid, {}).get("active", True):
                continue
            mems = self.vendors.get(vid, {}).pop("_dream_memories", [])
            summary = self.vendors.get(vid, {}).pop("_dream_summary", "")
            self.context_resets[vid] = self._build_day_prompt(vid, mems, summary)
        for cid in CUSTOMER_IDS:
            mems = self.customers.get(cid, {}).pop("_dream_memories", [])
            summary = self.customers.get(cid, {}).pop("_dream_summary", "")
            self.context_resets[cid] = self._build_day_prompt(cid, mems, summary)

    def _check_market_timer(self) -> None:
        """Auto-close market if time is up."""
        if self._is_market_expired() and self.phase == "market":
            self._start_planning_phase()

    def is_complete(self) -> bool:
        """Scenario-native endpoint: configured market sessions have closed."""
        return self._simulation_complete

    def completion_reason(self) -> str:
        return self._completion_reason

    async def _check_planning_timer(self) -> None:
        """Force-advance day if planning phase has run too long.

        Wall-clock based so it fires even when most agents are blocked
        in done-state and the action loop is dominated by no-op turns.
        """
        if self.phase != "planning" or self._planning_start_time is None:
            return
        elapsed = time.time() - self._planning_start_time
        if elapsed >= self.planning_timeout_seconds:
            self._vendors_done = set(VENDOR_IDS)
            self._customers_done = set(CUSTOMER_IDS)
            if self.current_day > 0:
                await self._run_dream_phase()
            self._advance_day()

    def _check_arrivals(self) -> None:
        """Wake customers when wall-clock has elapsed past wave thresholds.

        Each wave specifies sim_minutes_after_open (= real_seconds since
        market opened, since 1 real min = 1 sim hour = 60 sim min). We
        compute elapsed sim minutes and trigger any waves whose threshold
        has passed.
        """
        if self.phase != "market" or self._market_start_time is None:
            return
        elapsed_real_min = (time.time() - self._market_start_time) / 60.0
        elapsed_sim_min = elapsed_real_min * 60.0 / self.real_min_per_sim_hour
        for i, wave in enumerate(self._arrival_template):
            if i in self._triggered_waves:
                continue
            threshold = wave.get("sim_minutes_after_open", wave.get("step", 0))
            if elapsed_sim_min >= threshold:
                self._triggered_waves.add(i)
                for cid in wave["customers"]:
                    if cid in self.customers and cid not in self._customers_done:
                        self._arrived_customers.add(cid)
                        c = self.customers[cid]
                        still_need = c["still_need"]
                        items_str = self._format_item_names(still_need) if still_need else "browsing"
                        sim_time = self._get_simulated_time()
                        vendors = self._format_agent_list(self._vendor_order_for_customer(cid))
                        if not vendors:
                            vendors = "none currently open"
                        self._queue_context(
                            cid,
                            "Market Bell",
                            f"{self._format_date()}, {sim_time} - Kōen Market is open!\n"
                            f"Your shopping list: {items_str}\n"
                            f"Budget: ${c['remaining_budget']:.0f}\n"
                            f"Available vendors: {vendors}\n"
                            f"Market closes at {self.close_hour}:00.",
                        )

    def _vendor_order_for_customer(self, customer_id: str) -> List[str]:
        """Stable per-customer/day rotation to avoid first-listed vendor bias."""
        vendors = sorted(self._active_vendor_ids())
        if not vendors:
            return []
        offset = (sum(ord(ch) for ch in customer_id) + self.current_day) % len(vendors)
        return vendors[offset:] + vendors[:offset]

    # ── Day prompt builder (used by context reset) ──

    def _format_action_catalog(self, agent_id: str) -> str:
        """Format the action catalog the same way async_orchestrator does."""
        lines = []
        if agent_id in CUSTOMER_IDS and self.phase == "market":
            # Market-day context resets are built before staggered customer
            # arrivals, but customers need the market action contract in the
            # prompt they wake up with when their arrival marker is delivered.
            available_actions = self.CUSTOMER_MARKET_TOOLS
        else:
            available_actions = self.get_available_actions(agent_id)
        for act in available_actions:
            lines.append(f"- {act['name']}: {act.get('description', '')}")
        for act in self.get_builtin_actions(agent_id) or []:
            lines.append(f"- {act['name']}: {act.get('description', '')}")
        return (
            "Available actions:\n"
            + "\n".join(lines)
            + "\n\nStructured response format:\n"
            "Fields: think (optional private reasoning), action (optional action name), "
            "target (optional visible name or id), parameters (optional named inputs), "
            "respond (optional speech), respond_to (optional private recipient).\n"
            "- Choose at most one action from Available actions and put its name in action.\n"
            "- If an action needs a named target, put that visible name or id in target.\n"
            "- Put named inputs in parameters using exactly the parameter names shown in the action description. "
            "Use numbers as numbers, lists as lists, and dictionaries as dictionaries.\n"
            "- For ordinary speech, use respond. If speaking privately, set respond_to to the visible recipient name; "
            "if speaking publicly, leave respond_to empty.\n"
            "- For normal tool actions, leave respond empty unless the action is respond.\n"
            "Example tool action shape: {\"action\":\"action_name\",\"target\":\"visible_target_name\","
            "\"parameters\":{\"parameter_name\":\"value\"}}\n"
            "Example public speech shape: {\"action\":\"respond\",\"respond\":\"message text\"}\n"
            "Example private speech shape: {\"action\":\"respond\",\"respond_to\":\"visible_recipient_name\","
            "\"respond\":\"message text\"}"
        )

    def get_builtin_actions(self, agent_id: Optional[str] = None) -> List[Dict[str, str]]:
        if self.phase == "planning":
            if agent_id in VENDOR_IDS:
                return [
                    {
                        "name": "respond",
                        "description": (
                            "Private supplier negotiation only during preparation. "
                            "Set respond_to to Hayashi Supply. Do not use speech for "
                            "status summaries or tool-use narration."
                        ),
                    },
                    {"name": "wait", "description": "No action this step"},
                ]
            if agent_id in CUSTOMER_IDS:
                return [
                    {"name": "wait", "description": "No action this step"},
                ]
            if agent_id == SUPPLIER_ID:
                return [
                    {
                        "name": "respond",
                        "description": (
                            "Reply privately to one vendor. Set respond_to to that vendor's shop name."
                        ),
                    },
                    {"name": "wait", "description": "No action this step"},
                ]
        return [
            {
                "name": "respond",
                "description": (
                    "Speak using the respond field. Leave respond_to empty for public market speech; "
                    "set respond_to to a visible participant name for private speech."
                ),
            },
            {"name": "wait", "description": "No action this step"},
        ]

    def should_sleep_when_idle(self, agent_id: str) -> Optional[bool]:
        # During active planning/market phases, agents must keep taking turns.
        # Once a vendor/customer explicitly marks ready/done, let it sleep
        # until the next phase reset wakes it with fresh context.
        if agent_id in VENDOR_IDS and not self._is_vendor_active(agent_id):
            return True
        if agent_id in self._vendors_done or agent_id in self._customers_done:
            return True
        if (
            agent_id in CUSTOMER_IDS
            and self.phase == "market"
            and agent_id not in self._arrived_customers
        ):
            return True
        if agent_id == SUPPLIER_ID:
            return True
        if agent_id in VENDOR_IDS or agent_id in CUSTOMER_IDS:
            return False
        return None

    def is_agent_phase_complete(self, agent_id: str) -> bool:
        if agent_id == SUPPLIER_ID:
            return self.phase != "planning" or not self._supplier_waiting_vendors
        if agent_id in VENDOR_IDS and not self._is_vendor_active(agent_id):
            return True
        if (
            agent_id in CUSTOMER_IDS
            and self.phase == "market"
            and agent_id not in self._arrived_customers
        ):
            return True
        return agent_id in self._vendors_done or agent_id in self._customers_done

    def _build_identity_block(self, agent_id: str) -> str:
        """Reconstruct the identity block the orchestrator built at startup.

        Mirrors async_orchestrator._build_system_prompt's identity section.
        """
        profile = self.agent_profiles.get(agent_id)
        if profile is None:
            return ""
        lines = []
        profile_metadata = getattr(profile, "metadata", {}) or {}
        identity_template = profile_metadata.get("identity_template")
        used_identity_template = False
        if isinstance(identity_template, str) and identity_template.strip():
            lines.append(identity_template.format(
                name=profile.name,
                role=profile.role,
                agent_id=profile.agent_id,
                **profile_metadata,
            ))
            used_identity_template = True
        else:
            if getattr(profile, "name", ""):
                lines.append(f"Name: {profile.name}")
            if getattr(profile, "role", ""):
                lines.append(f"Role: {profile.role}")
        if getattr(profile, "personality", ""):
            lines.append(f"Personality: {profile.personality}")
        if getattr(profile, "background", ""):
            if used_identity_template:
                lines.append("")
            lines.append(f"Context: {profile.background}")
        goals = getattr(profile, "goals", None) or []
        if goals:
            lines.append("Goals: " + "; ".join(goals))
        rels = getattr(profile, "relationships", None) or {}
        if rels:
            lines.append(
                "Relationships: " + ", ".join(f"{k}: {v}" for k, v in rels.items())
            )
        return "\n".join(lines)

    def _build_day_prompt(
        self,
        agent_id: str,
        dream_memories: List[str],
        dream_summary: str = "",
    ) -> str:
        """Build a fresh system prompt for the start of a new day.

        Includes identity, persona, world context, current date, compressed
        dream memories, the agent's pinned plan or list artifact, current
        inventory/budget snapshot, and the action catalog. Replaces the
        accumulated context window from the prior day.

        Identity and persona are pulled from ``self.agent_profiles`` and
        ``self.agent_prompts``, populated by the orchestrator via
        ``bind_orchestrator_context``.
        """
        parts: List[str] = []

        identity_block = self._build_identity_block(agent_id)
        if identity_block:
            parts.append(identity_block)

        persona_overlay = self.agent_prompts.get(agent_id, "")
        if persona_overlay:
            parts.append(persona_overlay)

        # World context — Kōen Market is shared knowledge
        parts.append(
            "Kōen Market is an underground bazaar in Neo-Shibuya, Tokyo.\n"
            f"Today is {self._format_date()}. The market operates Monday-Friday, "
            f"{self._market_hours_text()}.\n"
            f"Market status: {'open-market time' if self.phase == 'market' else 'preparation time'}.\n"
            "The current market contains only the named vendors and customers shown by your tools. "
            "Do not assume unseen aisles, outside stalls, or imaginary vendors exist."
        )

        # Dream summary + memories — what the agent remembers from prior days
        if dream_summary:
            parts.append(f"Yesterday's summary and reflection:\n{dream_summary}")
        if dream_memories:
            mem_lines = "\n".join(f"- {m}" for m in dream_memories)
            parts.append(f"What you remember from recent days:\n{mem_lines}")

        # Pinned plan / list artifact
        if agent_id in VENDOR_IDS:
            v = self.vendors.get(agent_id, {})
            if self.phase == "planning":
                parts.append(
                    "Preparation task: review your business state, set listed prices "
                    "for the next market session, write your strategy notes, and "
                    "optionally order stock from Hayashi Supply. When finished, use "
                    "wait_for_next_day. Do not ask for operating instructions."
                )
            elif v.get("plan"):
                parts.append(f"Your plan for today:\n{v['plan']}")
            # Inventory + listed prices snapshot
            inv_lines = []
            for item_id, qty in sorted(v.get("stock", {}).items()):
                name = self.catalog.get(item_id, {}).get("name", item_id)
                listed = v.get("listed_prices", {}).get(item_id)
                price_str = f", listed ${listed:.2f}" if listed else ""
                inv_lines.append(f"  {name} ({item_id}): {qty} units{price_str}")
            if inv_lines:
                parts.append(
                    f"Current inventory (cash ${v.get('cash', 0):.2f}):\n"
                    + "\n".join(inv_lines)
                )
            parts.append(
                "Stall economics:\n"
                f"  Daily operating fee: ${self.daily_operating_fee:.2f} before each market session opens\n"
                "  If you cannot pay the entry fee, your business cannot open."
            )
            # Recent ledger summary (last 5 entries — sales or orders)
            ledger = v.get("ledger", [])
            if ledger:
                recent = ledger[-5:]
                lines = []
                for e in recent:
                    if e.get("type") == "supplier_order":
                        items_str = ", ".join(
                            f"{self.catalog.get(k, {}).get('name', k)} x{q}"
                            for k, q in e.get("items", {}).items()
                        )
                        lines.append(
                            f"  {self._format_date(self._date_for_market_day(e['day']))}: "
                            f"ORDER {items_str} for ${e['cost']:.2f} "
                            f"(arrives {self._format_order_arrival(e)})"
                        )
                    elif e.get("type") == "operating_fee":
                        lines.append(
                            f"  {self._format_date(self._date_for_market_day(e['day']))}: "
                            f"OPERATING FEE ${e['amount']:.2f} "
                            f"(cash after ${e['cash_after']:.2f})"
                        )
                    else:
                        name = self.catalog.get(e["item"], {}).get("name", e["item"])
                        lines.append(
                            f"  {self._format_date(self._date_for_market_day(e['day']))}: "
                            f"SALE {name} → {e['customer']} for "
                            f"${e['final_price']:.2f} (margin ${e['margin']:.2f})"
                        )
                parts.append("Recent ledger:\n" + "\n".join(lines))
        elif agent_id in CUSTOMER_IDS:
            c = self.customers.get(agent_id, {})
            if self.phase == "planning":
                if c.get("still_need"):
                    parts.append(
                        "Unfilled items from today's market: "
                        + self._format_item_names(c.get("still_need", []))
                    )
                if c.get("purchased"):
                    purchases = []
                    for p in c.get("purchased", []):
                        name = self.catalog.get(p["item"], {}).get("name", p["item"])
                        purchases.append(f"{name} for ${p['price']:.2f}")
                    parts.append("What you bought today:\n- " + "\n- ".join(purchases))
                parts.append(
                    "Preparation task: decide what you want to cook, eat, or buy "
                    "next, then create the next market session's shopping list with "
                    "write_list. Pick 3-6 items total, with at most 2 unusual or "
                    "specialty wants. Writing the list completes preparation. Do "
                    "not try to shop while the market is closed."
                )
            elif c.get("written_list"):
                parts.append(f"Your shopping notes for today:\n{c['written_list']}")
            elif c.get("list_context"):
                parts.append(f"Shopping context for today:\n{c['list_context']}")
            still_need = c.get("still_need", [])
            if self.phase == "market" and still_need:
                items = self._format_item_names(still_need)
                parts.append(
                    f"Still to buy: {items}\n"
                    f"Budget: ${c.get('remaining_budget', 0):.2f}"
                )

        # Action catalog
        parts.append(self._format_action_catalog(agent_id))

        return "\n\n".join(parts)

    def _format_catalog(self) -> str:
        """Format the wholesale catalog for display."""
        lines = []
        by_cat: Dict[str, list] = {}
        for item_id, item in sorted(self.catalog.items()):
            cat = item.get("category", "other")
            by_cat.setdefault(cat, []).append((item_id, item))
        for cat in sorted(by_cat.keys()):
            lines.append(f"  [{cat.upper()}]")
            for item_id, item in by_cat[cat]:
                lines.append(f"    {item['name']} ({item_id}): ${item['wholesale']}/unit wholesale")
        return "\n".join(lines)

    def _item_display_name(self, item_id: str) -> str:
        return self.catalog.get(item_id, {}).get("name", item_id.replace("_", " ").replace("*", "").title())

    def _format_item_names(self, item_ids: List[str] | Set[str]) -> str:
        return ", ".join(self._item_display_name(item_id) for item_id in item_ids)

    def _resolve_item_id(self, item: Optional[str]) -> Optional[str]:
        """Accept canonical IDs and exact display names from model parameters."""
        if not item:
            return None
        raw = str(item).strip()
        if raw in self.catalog:
            return raw
        lowered = raw.lower()
        for item_id, info in self.catalog.items():
            if lowered == str(info.get("name", "")).lower():
                return item_id
            if f"({item_id})" in lowered:
                return item_id
        return raw

    # ── Tool catalogs ──

    VENDOR_MARKET_TOOLS: List[Dict[str, Any]] = [
        {"name": "check_inventory", "description": "View your current stock, wholesale costs, and listed prices."},
        {"name": "check_customer_activity", "description": "See customers currently engaging your business, including recent negotiation dialogue and any formal offers."},
        {"name": "check_ledger", "description": "Read your full transaction ledger."},
        {"name": "accept_deal", "description": "Accept one customer's formal offer and complete one sale or bundle sale. Target: customer name. Parameters: item (optional exact item name), price (optional number)."},
        {"name": "reject_deal", "description": "Reject one customer's formal offer. Target: customer name. Parameters: item (optional exact item name)."},
    ]

    VENDOR_PLANNING_TOOLS: List[Dict[str, Any]] = [
        {"name": "check_inventory", "description": "View your current stock and wholesale costs."},
        {"name": "check_ledger", "description": "Read your full transaction ledger."},
        {"name": "set_prices", "description": "Set listed prices for the next market session. Parameters: prices (dictionary of item_id to numeric price), e.g. {\"prices\": {\"miso_paste\": 7}}."},
        {"name": "write_plan", "description": "Write your strategy notes for the next market session. Parameters: content (text). These notes remain available during that session."},
        {"name": "order_from_supplier", "description": "Order standard catalog items from Hayashi Supply at listed wholesale prices. Arrives after two calendar days; if the market is closed then, stock is available at the next open market session. Parameters: items (dictionary of item_id to whole-number quantity), e.g. {\"items\": {\"soy_sauce\": 4}}."},
        {"name": "place_supplier_order", "description": "Place an order after negotiating a supplier quote. Parameters: item (item name), quantity (whole number), unit_cost (number)."},
        {"name": "wait_for_next_day", "description": "Done planning. The next market session begins when all vendors and customers are ready."},
    ]

    SUPPLIER_TOOLS: List[Dict[str, Any]] = []

    CUSTOMER_MARKET_TOOLS: List[Dict[str, Any]] = [
        {"name": "check_market_status", "description": "See current market time, active vendors, active customers, the vendor you are currently engaging, and recent public market talk."},
        {"name": "inspect_vendor", "description": "Inspect one vendor's current goods and listed prices, and start or continue engagement with that vendor. Target: shop name, such as Lantern Pantry or Corner Provisions."},
        {"name": "make_offer", "description": "Make a formal offer after negotiation. For one item, use item (exact item name from the vendor listing) and price. For a bundle, use items (list of exact item names from the vendor listing) and price as the total bundle price. Target: shop name."},
        {"name": "check_budget", "description": "View your remaining budget, shopping list, and purchases."},
        {"name": "leave_market", "description": "Leave the market for the day when you are done shopping or decide to stop."},
    ]

    CUSTOMER_PLANNING_TOOLS: List[Dict[str, Any]] = [
        {"name": "write_list", "description": "Set your shopping list for the next market session and finish preparation. Parameters: items (list of 3-6 ordinary item names), notes or content (optional text). Use clear names for unusual or specialty wants."},
    ]

    def get_available_actions(self, agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if agent_id in VENDOR_IDS:
            if not self._is_vendor_active(agent_id):
                return [
                    {"name": "check_inventory", "description": "View your final stock, cash, and listed prices."},
                    {"name": "check_ledger", "description": "Read your full transaction ledger."},
                ]
            return self.VENDOR_MARKET_TOOLS if self.phase == "market" else self.VENDOR_PLANNING_TOOLS
        if agent_id in CUSTOMER_IDS:
            if self.phase == "planning" and self.current_day == 0:
                return []
            if self.phase == "market" and agent_id not in self._arrived_customers:
                return []
            return self.CUSTOMER_MARKET_TOOLS if self.phase == "market" else self.CUSTOMER_PLANNING_TOOLS
        if agent_id == SUPPLIER_ID:
            return self.SUPPLIER_TOOLS
        return []

    async def execute(
        self,
        action_type: str,
        target: Optional[str],
        parameters: Optional[dict],
        agent_id: str,
    ) -> Optional[ActionResult]:
        self._step_in_phase += 1
        if self._simulation_complete:
            return ActionResult(
                content=self._completion_reason or "The Kōen Market week is complete."
            )
        self._check_market_timer()
        await self._check_planning_timer()
        self._check_arrivals()

        # Block agents who are asleep until the next phase. Closed vendors
        # are also blocked so they cannot keep asking for outside direction.
        if (
            agent_id in self._vendors_done
            or agent_id in self._customers_done
            or (
                agent_id in CUSTOMER_IDS
                and self.phase == "market"
                and agent_id not in self._arrived_customers
            )
            or (agent_id in VENDOR_IDS and not self._is_vendor_active(agent_id))
        ):
            return None  # no result → agent stays asleep via sleep_when_idle

        params = parameters or {}

        builtin_action_names = {act["name"] for act in self.get_builtin_actions(agent_id)}
        builtin_action_names.add("do_nothing")
        available_actions = {act["name"] for act in self.get_available_actions(agent_id)}
        allowed_aliases = {
            "check_market": "check_market_status",
            "check_foot_traffic": "check_customer_activity",
            "inspect": "inspect_vendor",
            "inspect_vendors": "inspect_vendor",
            "visit_vendor": "inspect_vendor",
            "done_shopping": "leave_market",
        }
        canonical_action = allowed_aliases.get(action_type, action_type)
        if canonical_action not in available_actions and action_type not in builtin_action_names:
            return self._invalid_action_for_phase(agent_id, action_type, available_actions)

        def _number(value: Any, field: str) -> tuple[Optional[float], Optional[ActionResult]]:
            if value is None or value == "":
                return None, ActionResult(success=False, content=f"Invalid parameters: `{field}` is required and must be a number.")
            try:
                return float(value), None
            except (TypeError, ValueError):
                return None, ActionResult(success=False, content=f"Invalid parameters: `{field}` must be a number, got {value!r}.")

        def _integer(value: Any, field: str) -> tuple[Optional[int], Optional[ActionResult]]:
            number, error = _number(value, field)
            if error:
                return None, error
            if number is None or int(number) != number:
                return None, ActionResult(success=False, content=f"Invalid parameters: `{field}` must be a whole number, got {value!r}.")
            return int(number), None

        # ── Shared actions ──
        if action_type in {"check_market_status", "check_market"}:
            return self._check_market(agent_id)
        elif action_type == "check_inventory":
            return self._check_inventory(agent_id)
        elif action_type == "check_ledger":
            return self._check_ledger(agent_id)
        elif action_type == "wait_for_next_day":
            return await self._wait_for_next_day(agent_id)
        elif action_type in {"wait", "do_nothing"}:
            return await self._handle_wait(agent_id)

        if agent_id in VENDOR_IDS and not self._is_vendor_active(agent_id):
            return ActionResult(
                content="Your stall is closed because cash ran out. You may review inventory or ledger only."
            )

        # ── Vendor market actions ──
        elif action_type in {"check_customer_activity", "check_foot_traffic"}:
            return self._check_foot_traffic(agent_id)
        elif action_type == "make_offer":
            if agent_id in CUSTOMER_IDS:
                price, error = _number(params.get("price"), "price")
                if error:
                    return ActionResult(
                        success=False,
                        content=(
                            error.content
                            + " Example: make_offer target=Lantern Pantry "
                            "item=\"Miso Paste (500g)\" price=5."
                        ),
                    )
                return self._make_offer(
                    agent_id,
                    params.get("items") if "items" in params else params.get("item"),
                    price or 0,
                    params.get("vendor") or target,
                )
            return ActionResult(content="Customers use make_offer after negotiation; vendors can accept_deal or reject_deal.")
        elif action_type == "accept_deal" and agent_id in VENDOR_IDS:
            raw_price = params.get("price")
            if raw_price is None or raw_price == "":
                # Omitted price → fall through with 0 so _vendor_accept_deal
                # can use the pending-offer fallback when one exists.
                price = 0
            else:
                price, error = _number(raw_price, "price")
                if error:
                    return error
            return self._vendor_accept_deal(agent_id, params.get("customer") or target,
                                            params.get("item", ""), price or 0)
        elif action_type == "reject_deal":
            return self._reject_deal(agent_id, params.get("customer") or target,
                                     params.get("item", ""))

        # ── Vendor planning actions ──
        elif action_type == "set_prices":
            return self._set_prices(agent_id, params.get("prices") or {})
        elif action_type == "write_plan":
            return self._write_plan(
                agent_id,
                params.get("content") or target or params.get("__respond") or "",
            )
        elif action_type == "order_from_supplier":
            return self._order_from_supplier(agent_id, params.get("items") or {})
        elif action_type == "place_supplier_order":
            quantity, error = _integer(params.get("quantity"), "quantity")
            if error:
                return ActionResult(success=False, content=error.content + " Example: place_supplier_order item=\"Chashu Pork\" quantity=3 unit_cost=7.")
            unit_cost, error = _number(params.get("unit_cost"), "unit_cost")
            if error:
                return ActionResult(success=False, content=error.content + " Example: place_supplier_order item=\"Chashu Pork\" quantity=3 unit_cost=7.")
            return self._place_supplier_order(
                agent_id,
                params.get("item") or target or "",
                quantity or 0,
                unit_cost or 0,
            )

        # ── Customer market actions ──
        elif action_type in {"inspect_vendor", "inspect_vendors", "inspect", "visit_vendor"}:
            return self._visit_vendor(agent_id, params.get("vendor") or target)
        elif action_type == "leave_vendor":
            return self._leave_vendor(agent_id)
        elif action_type in {"check_shopping_list", "check_budget"}:
            return self._check_shopping_list(agent_id)
        elif action_type in {"done_shopping", "leave_market"}:
            return self._done_shopping(agent_id)

        # ── Customer planning actions ──
        elif action_type == "write_list":
            if self.phase != "planning":
                return ActionResult(content="Market is open. Write tomorrow's shopping list during preparation time after the market closes.")
            return await self._write_list(
                agent_id,
                params.get("items") or params.get("shopping_list") or [],
                params.get("notes") or params.get("content") or target or params.get("__respond") or "",
            )

        return None

    def _invalid_action_for_phase(
        self,
        agent_id: str,
        action_type: str,
        available_actions: Set[str],
    ) -> ActionResult:
        builtin_actions = {act["name"] for act in self.get_builtin_actions(agent_id)}
        if not builtin_actions:
            builtin_actions = {"wait"}
        actions = ", ".join(sorted(available_actions | builtin_actions))
        if self.phase == "planning" and agent_id in CUSTOMER_IDS:
            guidance = (
                "The market is closed. During preparation, create the next "
                "market session's shopping list with write_list. If you have "
                "already written the list, wait."
            )
        elif self.phase == "planning" and agent_id in VENDOR_IDS:
            guidance = (
                "The market is closed. During preparation, use check_inventory, "
                "check_ledger, set_prices, write_plan, optional supplier ordering, "
                "then wait_for_next_day. Customer-facing market actions are not available."
            )
        elif self.phase == "market" and agent_id in CUSTOMER_IDS:
            guidance = (
                "The market is open. Shop by checking market status, inspecting "
                "vendors, speaking naturally, making formal offers, checking budget, "
                "or leaving the market."
            )
        elif self.phase == "market" and agent_id in VENDOR_IDS:
            guidance = (
                "The market is open. Serve customers by checking customer activity, "
                "speaking naturally, and accepting or rejecting formal offers."
            )
        else:
            guidance = "Use one of the currently available actions."
        return ActionResult(
            success=False,
            content=(
                f"Invalid tool choice for the current phase: '{action_type}'. "
                f"{guidance} Available actions: {actions}."
            ),
        )

    # ── Vendor implementations ──

    def _check_inventory(self, agent_id: str) -> ActionResult:
        v = self.vendors.get(agent_id)
        if not v:
            return ActionResult(content="You are not a vendor.")
        sim_time = self._get_simulated_time()
        header = f"YOUR INVENTORY — {self._format_date()}"
        if sim_time:
            header += f", {sim_time}"
        lines = [header + "\n"]
        lines.append(
            f"Cash: ${v['cash']:.2f}. Market-entry fee: ${self.daily_operating_fee:.2f}. "
            f"Status: {'open' if v.get('active', True) else 'closed'}.\n"
        )
        total_value = 0
        for item_id, qty in sorted(v["stock"].items()):
            item = self.catalog.get(item_id, {})
            name = item.get("name", item_id)
            wholesale = item.get("wholesale", 0)
            total_value += wholesale * qty
            listed = v["listed_prices"].get(item_id)
            price_str = f", listed at ${listed:.2f}" if listed else ", no price set"
            lines.append(f"  {name} ({item_id}): {qty} units — cost ${wholesale}/ea{price_str}")
        lines.append(f"\nTotal wholesale value: ${total_value}")
        if v["pending_orders"]:
            lines.append("\nPending supplier orders:")
            for order in v["pending_orders"]:
                items_str = ", ".join(f"{k} x{q}" for k, q in order["items"].items())
                lines.append(
                    f"  {items_str} — arrives "
                    f"{self._format_order_arrival(order)}"
                )
        return ActionResult(content="\n".join(lines))

    def _check_ledger(self, agent_id: str) -> ActionResult:
        v = self.vendors.get(agent_id)
        if not v:
            return ActionResult(content="You are not a vendor.")
        if not v["ledger"]:
            return ActionResult(content="Ledger is empty — no transactions yet.")
        lines = ["TRANSACTION LEDGER\n"]
        total_revenue = 0.0
        total_margin = 0.0
        total_spend = 0.0
        sale_count = 0
        order_count = 0
        for e in v["ledger"]:
            entry_type = e.get("type", "sale")
            if entry_type == "supplier_order":
                items_str = ", ".join(
                    f"{self.catalog.get(k, {}).get('name', k)} x{q}"
                    for k, q in e.get("items", {}).items()
                )
                lines.append(
                    f"  {self._format_date(self._date_for_market_day(e['day']))}: "
                    f"ORDER → supplier — {items_str} "
                    f"(cost ${e['cost']:.2f}, arrives "
                    f"{self._format_order_arrival(e)})"
                )
                total_spend += e["cost"]
                order_count += 1
            elif entry_type == "operating_fee":
                lines.append(
                    f"  {self._format_date(self._date_for_market_day(e['day']))} "
                    f"{e.get('time', '')}: OPERATING FEE — "
                    f"${e['amount']:.2f} (cash after ${e['cash_after']:.2f})"
                )
                total_spend += e["amount"]
            else:
                name = self.catalog.get(e["item"], {}).get("name", e["item"])
                lines.append(
                    f"  {self._format_date(self._date_for_market_day(e['day']))} "
                    f"{e.get('time', '')}: SALE {name} → {e['customer']} — "
                    f"listed ${e['listed_price']:.2f}, sold ${e['final_price']:.2f} "
                    f"(margin ${e['margin']:.2f})"
                )
                total_revenue += e["final_price"]
                total_margin += e["margin"]
                sale_count += 1
        lines.append(
            f"\nTotal: {sale_count} sales (${total_revenue:.2f} revenue, "
            f"${total_margin:.2f} profit), {order_count} orders "
            f"(${total_spend:.2f} spend)"
        )
        return ActionResult(content="\n".join(lines))

    def _check_foot_traffic(self, agent_id: str) -> ActionResult:
        if agent_id not in VENDOR_IDS:
            return ActionResult(content="You are not a vendor.")
        if self.phase != "market":
            return ActionResult(content="Market is closed.")
        sim_time = self._get_simulated_time()
        visitors = [cid for cid, vid in self.active_visits.items() if vid == agent_id]
        if not visitors:
            return ActionResult(content=f"No customers are currently engaging your business ({sim_time}).")
        lines = [f"Customers currently engaging your business ({sim_time}):"]
        for cid in visitors:
            c = self.customers.get(cid, {})
            still_need = c.get("still_need", [])
            items_str = self._format_item_names(still_need) if still_need else "browsing"
            lines.append(f"  {self._customer_label(cid)} — looking for: {items_str}")
            chat = self._format_stall_chat(agent_id, cid)
            if chat:
                lines.append(chat)
            offer = self.formal_offers.get((agent_id, cid))
            if offer:
                offers = offer if isinstance(offer, list) else [offer]
                for pending_offer in offers:
                    offered_item = pending_offer["item"]
                    if isinstance(offered_item, list):
                        item_name = ", ".join(
                            self.catalog.get(item_id, {}).get("name", item_id)
                            for item_id in offered_item
                        )
                        lines.append(f"  Formal bundle offer: {item_name} for ${pending_offer['price']:.2f}")
                    else:
                        item_name = self.catalog.get(offered_item, {}).get("name", offered_item)
                        lines.append(f"  Formal offer: {item_name} for ${pending_offer['price']:.2f}")
        return ActionResult(content="\n".join(lines))

    def _market_participants(self) -> Set[str]:
        active = set(self._active_vendor_ids())
        active.update(cid for cid in self._arrived_customers if cid not in self._customers_done)
        return active

    def _display_name(self, agent_id: str) -> str:
        if agent_id in self.customers:
            return self.customers[agent_id].get("name", agent_id)
        if agent_id in self.vendors:
            profile = self.agent_profiles.get(agent_id)
            return getattr(profile, "name", agent_id) if profile else agent_id
        if agent_id == SUPPLIER_ID:
            profile = self.agent_profiles.get(agent_id)
            return getattr(profile, "name", agent_id) if profile else "Hayashi Supply"
        return agent_id

    def _vendor_label(self, vendor_id: str) -> str:
        name = self._display_name(vendor_id)
        return name.replace(" Stallkeeper", "")

    def _customer_label(self, customer_id: str) -> str:
        return self.customers.get(customer_id, {}).get("name", customer_id)

    def _agent_ref_hint(self, agent_id: str) -> str:
        if agent_id in VENDOR_IDS:
            return self._vendor_label(agent_id)
        return self._display_name(agent_id)

    def _format_agent_list(self, agent_ids: List[str] | Set[str]) -> str:
        return ", ".join(self._agent_ref_hint(agent_id) for agent_id in sorted(agent_ids))

    def _resolve_agent_ref(self, ref: Optional[str]) -> Optional[str]:
        """Accept stable ids and visible names in respond_to targets."""
        if not ref:
            return None
        raw = str(ref).strip()
        if raw in self._market_participants() or raw == SUPPLIER_ID:
            return raw

        def norm(value: str) -> str:
            lowered = value.lower().strip()
            lowered = lowered.replace("stallkeeper", "").replace("stall keeper", "")
            return " ".join(lowered.split())

        needle = norm(raw)
        for agent_id in sorted(VENDOR_IDS | CUSTOMER_IDS | {SUPPLIER_ID}):
            names = {agent_id, self._display_name(agent_id)}
            if agent_id in self.vendors:
                names.add(self._vendor_label(agent_id))
                names.add(f"{self._vendor_label(agent_id)} Stallkeeper")
            if needle in {norm(name) for name in names}:
                return agent_id
        return None

    def _check_market(self, agent_id: str) -> ActionResult:
        if self.phase != "market":
            return ActionResult(content="Market is closed.")
        sim_time = self._get_simulated_time()
        vendors = self._format_agent_list(self._active_vendor_ids()) or "none"
        customers = ", ".join(
            self._customer_label(cid)
            for cid in sorted(CUSTOMER_IDS)
            if cid in self._arrived_customers and cid not in self._customers_done
        ) or "none"
        lines = [
            f"Kōen Market is open ({sim_time}).",
            f"Vendors: {vendors}",
            f"Customers in market: {customers}",
        ]
        if agent_id in CUSTOMER_IDS:
            current = self.active_visits.get(agent_id)
            lines.append(f"Currently engaging: {self._vendor_label(current) if current else 'none'}")
        if self.market_chats:
            recent_public_talk = [
                entry for entry in self.market_chats
                if entry.get("day") == self.current_day
                and not self._looks_like_preparation_summary(
                    self._resolve_agent_ref(entry.get("speaker", "")) or "",
                    entry.get("message", ""),
                )
            ][-8:]
            if recent_public_talk:
                lines.append("Recent public market talk:")
            for entry in recent_public_talk:
                lines.append(f"  {entry['speaker']}: {entry['message']}")
        return ActionResult(content="\n".join(lines))

    def _chat_key(self, vendor: str, customer: str) -> tuple[str, str]:
        return (vendor, customer)

    def _append_stall_chat(self, vendor: str, customer: str, speaker: str, message: str) -> str:
        key = self._chat_key(vendor, customer)
        self.stall_chats.setdefault(key, []).append({
            "speaker": speaker,
            "message": message.strip(),
            "time": self._get_simulated_time() or "",
            "day": self.current_day,
        })
        return self._format_stall_chat(vendor, customer)

    def _format_stall_chat(self, vendor: str, customer: str, limit: int = 8) -> str:
        entries = [
            entry for entry in self.stall_chats.get(self._chat_key(vendor, customer), [])
            if entry.get("day") == self.current_day
        ][-limit:]
        if not entries:
            return ""
        lines = ["  Recent negotiation dialogue:"]
        for entry in entries:
            lines.append(f"    {entry['speaker']}: {entry['message']}")
        return "\n".join(lines)

    def _record_supplier_speech(self, agent_id: str, target: str, content: str) -> None:
        vendor = agent_id if agent_id in VENDOR_IDS else target
        speaker = self._display_name(agent_id)
        self.supplier_chats.setdefault(vendor, []).append({
            "speaker": speaker,
            "message": content.strip(),
            "time": self._format_date(),
        })
        if agent_id in VENDOR_IDS:
            self._supplier_waiting_vendors.add(agent_id)
        elif agent_id == SUPPLIER_ID:
            self._supplier_waiting_vendors.discard(target)
        marker = (
            f"Supplier negotiation: {speaker} says privately to "
            f"{self._agent_ref_hint(target)}: {content.strip()}"
        )
        self.pending_context_markers.append({"to": [agent_id, target], "content": marker})

    def _record_market_speech(
        self,
        agent_id: str,
        content: str,
        target: Optional[str],
        *,
        private: bool,
    ) -> str:
        speaker = self._display_name(agent_id)
        content = content.strip()
        if private:
            marker = f"{speaker} says privately to {self._agent_ref_hint(target or '')}: {content}"
            targets = {agent_id, target} if target else {agent_id}
        else:
            self.market_chats.append({
                "speaker": speaker,
                "message": content,
                "time": self._get_simulated_time() or "",
                "day": self.current_day,
            })
            marker = f"{speaker} says aloud in the market: {content}"
            targets = self._market_participants()

        # If the speech occurs between a vendor and a customer at a stall,
        # also keep it with that stall's negotiation history.
        vendor: Optional[str] = None
        customer: Optional[str] = None
        other = target
        if agent_id in CUSTOMER_IDS:
            vendor = other if other in VENDOR_IDS else self.active_visits.get(agent_id)
            customer = agent_id
        elif agent_id in VENDOR_IDS and other in CUSTOMER_IDS:
            vendor = agent_id
            customer = other
        if vendor and customer and self.active_visits.get(customer) == vendor:
            self._append_stall_chat(vendor, customer, speaker, content)

        if private:
            self.pending_context_markers.append({
                "to": sorted(recipient for recipient in targets if recipient),
                "content": marker,
            })
        else:
            self.pending_context_markers.append({
                "to": sorted(self._market_participants()),
                "content": marker,
            })
        return marker

    async def on_agent_response(
        self,
        agent_id: str,
        content: str,
        respond_to: Optional[str] = None,
        action_type: Optional[str] = None,
    ) -> bool | str:
        """Treat natural-language responses as audible bazaar speech."""
        if not content.strip():
            return False
        if agent_id in VENDOR_IDS and not self._is_vendor_active(agent_id):
            return "suppress"
        resolved_target = self._resolve_agent_ref(respond_to)
        action_response_is_not_speech = {
            "accept_deal",
            "check_budget",
            "check_customer_activity",
            "check_foot_traffic",
            "check_inventory",
            "check_ledger",
            "check_market_status",
            "check_market",
            "leave_market",
            "make_offer",
            "order_from_supplier",
            "place_supplier_order",
            "reject_deal",
            "set_prices",
            "inspect",
            "inspect_vendor",
            "inspect_vendors",
            "visit_vendor",
            "wait",
            "wait_for_next_day",
            "write_list",
            "write_plan",
        }
        if action_type in action_response_is_not_speech:
            return "suppress"
        if self._looks_like_preparation_summary(agent_id, content):
            return "suppress"
        # Non-empty respond_to that does not resolve → tell the agent and
        # do not route the speech anywhere. Prevents "I'll DM the corner one"
        # from silently broadcasting publicly when the name doesn't match.
        if respond_to and respond_to.strip() and resolved_target is None:
            available = self._format_agent_list(self._market_participants()) or "none"
            self.pending_context_markers.append({
                "to": agent_id,
                "content": (
                    f"[Speech routing failed] No agent named "
                    f"'{respond_to}' is in the market. Available: {available}. "
                    f"Your message was not sent: {content.strip()}"
                ),
            })
            return "suppress"
        if self.phase == "planning":
            if agent_id in VENDOR_IDS and resolved_target == SUPPLIER_ID:
                self._record_supplier_speech(agent_id, SUPPLIER_ID, content)
                return True
            if agent_id == SUPPLIER_ID and resolved_target in VENDOR_IDS:
                self._record_supplier_speech(agent_id, resolved_target, content)
                return True
            return "suppress"
        if self.phase != "market" or agent_id not in self._market_participants():
            return True
        private = bool(resolved_target)
        self._record_market_speech(agent_id, content, resolved_target, private=private)
        return True

    def _looks_like_preparation_summary(self, agent_id: str, content: str) -> bool:
        """Suppress stale planning summaries that occasionally arrive after phase changes."""
        text = " ".join(content.lower().split())
        if agent_id in CUSTOMER_IDS:
            customer_patterns = (
                "preparation complete",
                "shopping list written",
                "shopping list is ready",
                "writing shopping list",
                "ready to enter the market",
                "ready to check the market",
                "my shopping list",
            )
            return any(pattern in text for pattern in customer_patterns)
        if agent_id in VENDOR_IDS:
            vendor_patterns = (
                "awaiting any user changes",
                "choose one:",
                "do you want me to",
                "give instructions and i'll proceed",
                "keep everything as-is",
                "my recommendation:",
                "option 1:",
                "option number",
                "options i can run",
                "outside operator",
                "plan saved",
                "reply with the letter",
                "reply with the number",
                "reply with the option",
                "strategy written",
                "tell me a specific action",
                "prices set",
                "ready for the next market",
                "ready to open",
                "what would you like me to do",
            )
            if any(pattern in text for pattern in vendor_patterns):
                return True
            stale_phase_patterns = (
                "market closed",
                "market is closed",
                "prep time",
                "preparation time",
                "during prep",
            )
            if self.phase == "market" and any(pattern in text for pattern in stale_phase_patterns):
                return True
            stale_closing_patterns = (
                "closing flash",
                "final call",
                "final hour",
                "last chance",
                "last minutes",
                "open until 5pm",
                "open until 5:00",
                "before close",
            )
            if (
                self.phase == "market"
                and not self._is_final_market_hour()
                and any(pattern in text for pattern in stale_closing_patterns)
            ):
                return True
            return False
        return False

    def _is_final_market_hour(self) -> bool:
        """Whether current simulated market time is in the final open hour."""
        if self.phase != "market" or self._market_start_time is None:
            return False
        elapsed_real_min = (time.time() - self._market_start_time) / 60.0
        elapsed_sim_min = elapsed_real_min * 60.0 / self.real_min_per_sim_hour
        final_hour_start = max(0.0, (self.close_hour - self.open_hour - 1) * 60.0)
        return elapsed_sim_min >= final_hour_start

    def _make_offer(
        self,
        agent_id: str,
        item: Any,
        price: float,
        vendor_ref: Optional[str] = None,
    ) -> ActionResult:
        if agent_id not in CUSTOMER_IDS:
            return ActionResult(content="You are not a customer.")
        resolved_vendor = self._resolve_agent_ref(vendor_ref)
        if resolved_vendor in VENDOR_IDS:
            vendor = resolved_vendor
            self.active_visits[agent_id] = vendor
        elif agent_id in self.active_visits:
            vendor = self.active_visits[agent_id]
        else:
            return ActionResult(content="No vendor selected. Inspect a vendor or specify a target vendor first.")
        v = self.vendors[vendor]
        if not v.get("active", True):
            return ActionResult(content=f"{self._vendor_label(vendor)} is closed.")
        if price <= 0:
            return ActionResult(success=False, content="Invalid price. Price must be positive. Example: make_offer target=Lantern Pantry item=\"Miso Paste (500g)\" price=5, or items=[\"Miso Paste (500g)\", \"Short-grain Rice (2kg bag)\"] price=13.")
        item_list = self._coerce_item_list(item)
        if not item_list:
            return ActionResult(success=False, content="Invalid parameters: make_offer needs `item` for one item or `items` for a bundle.")
        unique_items: List[str] = []
        for item_id in item_list:
            if item_id not in unique_items:
                unique_items.append(item_id)
        invalid = [item_id for item_id in unique_items if item_id not in self.catalog]
        if invalid:
            return ActionResult(success=False, content=f"Invalid item(s) {invalid!r}. Use exact item names from the vendor listing, then call make_offer again.")
        out = [
            self.catalog[item_id]["name"]
            for item_id in unique_items
            if v["stock"].get(item_id, 0) <= 0
        ]
        if out:
            return ActionResult(content=f"{self._vendor_label(vendor)} is out of: {', '.join(out)}.")
        key = (vendor, agent_id)
        pending = self.formal_offers.get(key, [])
        if isinstance(pending, dict):
            pending = [pending]
        offered_set = set(unique_items)
        pending = [
            offer for offer in pending
            if not (set(self._coerce_item_list(offer.get("item"))) & offered_set)
        ]
        offer_item: str | List[str] = unique_items if len(unique_items) > 1 else unique_items[0]
        pending.append({"item": offer_item, "price": price})
        self.formal_offers[key] = pending
        if len(unique_items) > 1:
            names = ", ".join(self.catalog[item_id]["name"] for item_id in unique_items)
            self._append_stall_chat(vendor, agent_id, self.customers[agent_id]["name"], f"I offer ${price:.2f} total for {names}.")
            return ActionResult(content=f"Formal bundle offer made to {self._vendor_label(vendor)}: {names} for ${price:.2f} total.")
        name = self.catalog[unique_items[0]]["name"]
        self._append_stall_chat(vendor, agent_id, self.customers[agent_id]["name"], f"I offer ${price:.2f} for {name}.")
        return ActionResult(content=f"Formal offer made to {self._vendor_label(vendor)}: {name} for ${price:.2f}.")

    def _coerce_item_list(self, raw_items: Any) -> List[str]:
        if raw_items is None or raw_items == "":
            return []
        if isinstance(raw_items, list):
            candidates = raw_items
        elif isinstance(raw_items, tuple):
            candidates = list(raw_items)
        else:
            text = str(raw_items)
            candidates = re.split(r"\s*(?:,|;|\+|\band\b)\s*", text)
        resolved: List[str] = []
        for candidate in candidates:
            item_id = self._resolve_item_id(str(candidate).strip())
            if item_id:
                resolved.append(item_id)
        return resolved

    def _offer_item_list(self, offer: dict) -> List[str]:
        return self._coerce_item_list(offer.get("item"))

    def _flatten_offer_items(self, offers: List[dict]) -> List[str]:
        flattened: List[str] = []
        for offer in offers:
            for item_id in self._offer_item_list(offer):
                if item_id not in flattened:
                    flattened.append(item_id)
        return flattened

    def _execute_bundle_sale(
        self,
        vendor_id: str,
        customer_id: str,
        items: List[str],
        total_price: float,
    ) -> ActionResult:
        if not items:
            return ActionResult(success=False, content="No valid items supplied for the bundle.")
        unique_items: List[str] = []
        for item in items:
            if item not in unique_items:
                unique_items.append(item)
        invalid = [item for item in unique_items if item not in self.catalog]
        if invalid:
            return ActionResult(
                success=False,
                content=(
                    "Unknown bundle item(s): "
                    + ", ".join(str(item) for item in invalid)
                    + ". Use exact item names from the vendor listing."
                ),
            )
        vendor = self.vendors.get(vendor_id, {})
        customer = self.customers.get(customer_id, {})
        out = [
            self.catalog[item]["name"]
            for item in unique_items
            if vendor.get("stock", {}).get(item, 0) <= 0
        ]
        if out:
            return ActionResult(success=False, content="Out of stock: " + ", ".join(out) + ".")
        if total_price <= 0:
            return ActionResult(success=False, content="Bundle price must be positive.")
        if total_price > customer.get("remaining_budget", 0):
            return ActionResult(
                success=False,
                content=(
                    f"Customer can't afford ${total_price:.2f}. "
                    f"Budget: ${customer.get('remaining_budget', 0):.2f}."
                ),
            )

        listed_values = [
            float(vendor.get("listed_prices", {}).get(item, self.catalog[item]["wholesale"]))
            for item in unique_items
        ]
        total_listed = sum(listed_values)
        allocations: List[float] = []
        running = 0.0
        for idx, listed in enumerate(listed_values):
            if idx == len(listed_values) - 1:
                allocated = round(total_price - running, 2)
            elif total_listed > 0:
                allocated = round(total_price * (listed / total_listed), 2)
            else:
                allocated = round(total_price / len(unique_items), 2)
            allocations.append(allocated)
            running += allocated

        sale_results: List[str] = []
        for item, allocated_price in zip(unique_items, allocations):
            result = self._execute_sale(
                vendor_id,
                customer_id,
                item,
                allocated_price,
                initiated_by="vendor",
            )
            if not result.content.startswith("SALE:"):
                return result
            sale_results.append(result.content)

        names = ", ".join(self.catalog[item]["name"] for item in unique_items)
        return ActionResult(
            content=(
                f"BUNDLE SALE: {names} to {self._customer_label(customer_id)} "
                f"for ${total_price:.2f}. Cash: ${self.vendors[vendor_id]['cash']:.2f}.\n"
                + "\n".join(sale_results)
            )
        )

    def _vendor_accept_deal(self, agent_id: str, customer: Optional[str], item: Any, price: float) -> ActionResult:
        if agent_id not in VENDOR_IDS:
            return ActionResult(content="You are not a vendor.")
        resolved_customer = self._resolve_agent_ref(customer)
        customer = resolved_customer if resolved_customer in CUSTOMER_IDS else customer
        if not customer or customer not in CUSTOMER_IDS:
            return ActionResult(success=False, content=f"Customer '{customer}' not found. Use the visible customer name from check_customer_activity.")
        offer = self.formal_offers.get((agent_id, customer))
        offers = offer if isinstance(offer, list) else ([offer] if offer else [])
        has_dialogue = bool(self.stall_chats.get(self._chat_key(agent_id, customer)))
        if self.active_visits.get(customer) != agent_id and not offers and not has_dialogue:
            return ActionResult(success=False, content=f"{self._customer_label(customer)} is not currently engaging your business. Use check_customer_activity to see current customers and pending offers.")
        selected_offers: List[dict] = []
        if offers:
            requested_items = self._coerce_item_list(item)
            if requested_items:
                requested_set = set(requested_items)
                for pending_offer in offers:
                    offer_items = set(self._offer_item_list(pending_offer))
                    if (
                        offer_items
                        and (
                            offer_items.issubset(requested_set)
                            or requested_set.issubset(offer_items)
                            or bool(offer_items & requested_set)
                        )
                    ):
                        selected_offers.append(pending_offer)
            if not selected_offers:
                selected_offers = offers

            item = (
                selected_offers[0]["item"]
                if len(selected_offers) == 1
                else self._flatten_offer_items(selected_offers)
            )
            price = sum(float(pending_offer["price"]) for pending_offer in selected_offers)
        item_list = self._coerce_item_list(item)
        if len(item_list) > 1:
            if price <= 0:
                return ActionResult(
                    success=False,
                    content="Bundle accept_deal requires a total numeric price.",
                )
            result = self._execute_bundle_sale(agent_id, customer, item_list, price)
            if result.content.startswith("BUNDLE SALE:"):
                remaining_offers = [
                    pending_offer
                    for pending_offer in offers
                    if pending_offer not in selected_offers
                ]
                if remaining_offers:
                    self.formal_offers[(agent_id, customer)] = remaining_offers
                else:
                    self.formal_offers.pop((agent_id, customer), None)
            return result
        result = self._execute_sale(agent_id, customer, item, price, initiated_by="vendor")
        if "SALE:" in result.content:
            remaining_offers = [
                pending_offer
                for pending_offer in offers
                if pending_offer not in selected_offers
                and pending_offer.get("item") != self._resolve_item_id(item)
            ]
            if remaining_offers:
                self.formal_offers[(agent_id, customer)] = remaining_offers
            else:
                self.formal_offers.pop((agent_id, customer), None)
        return result

    def _reject_deal(self, agent_id: str, customer: Optional[str], item: str) -> ActionResult:
        if agent_id not in VENDOR_IDS:
            return ActionResult(content="You are not a vendor.")
        resolved_customer = self._resolve_agent_ref(customer)
        customer = resolved_customer if resolved_customer in CUSTOMER_IDS else customer
        if not customer or customer not in CUSTOMER_IDS:
            return ActionResult(success=False, content=f"Customer '{customer}' not found. Use the visible customer name from check_customer_activity.")
        item = self._resolve_item_id(item) or item
        offer = self.formal_offers.get((agent_id, customer))
        offers = offer if isinstance(offer, list) else ([offer] if offer else [])
        if offers and not item:
            item = offers[0]["item"]
        if item and offers:
            resolved_item = self._resolve_item_id(item)
            remaining_offers = [
                pending_offer
                for pending_offer in offers
                if pending_offer.get("item") != resolved_item
            ]
            if remaining_offers:
                self.formal_offers[(agent_id, customer)] = remaining_offers
            else:
                self.formal_offers.pop((agent_id, customer), None)
        elif offers:
            self.formal_offers.pop((agent_id, customer), None)
        item_name = self.catalog.get(item, {}).get("name", item) if item else "the item"
        self._append_stall_chat(agent_id, customer, agent_id, f"I can't accept that price for {item_name}.")
        return ActionResult(content=f"Rejected {self._customer_label(customer)}'s offer on {item_name}.")

    def _set_prices(self, agent_id: str, prices: dict) -> ActionResult:
        v = self.vendors.get(agent_id)
        if not v:
            return ActionResult(content="You are not a vendor.")
        if self.phase != "planning":
            return ActionResult(content="You can only set prices during planning.")
        if not prices:
            return ActionResult(success=False, content='Invalid parameters: `prices` must be a dictionary like {"rice": 9, "miso_paste": 6}.')
        set_items = []
        for item_id, price in prices.items():
            item_id = str(item_id)
            try:
                price = float(price)
            except (TypeError, ValueError):
                return ActionResult(success=False, content=f"Invalid price for `{item_id}`: {price!r}. Prices must be numbers.")
            if item_id not in self.catalog and item_id not in v["stock"]:
                continue
            v["listed_prices"][item_id] = price
            name = self.catalog.get(item_id, {}).get("name", item_id)
            wholesale = self.catalog.get(item_id, {}).get("wholesale", 0)
            set_items.append(f"  {name}: ${price:.2f} (cost ${wholesale}, margin ${price - wholesale:.2f})")
        return ActionResult(content="Prices set for the next market session:\n" + "\n".join(set_items))

    def _write_plan(self, agent_id: str, content: str) -> ActionResult:
        v = self.vendors.get(agent_id)
        if not v:
            return ActionResult(content="You are not a vendor.")
        content = str(content or "").strip()
        if not content:
            return ActionResult(
                success=False,
                content=(
                    "write_plan needs strategy notes in the `content` parameter. "
                    "Include tomorrow's pricing, inventory, cash reserve, supplier, and sales plan."
                ),
            )
        v["plan"] = content
        return ActionResult(content="Plan saved. It will remain available during the next market session.")

    def _order_from_supplier(self, agent_id: str, items: dict) -> ActionResult:
        v = self.vendors.get(agent_id)
        if not v:
            return ActionResult(content="You are not a vendor.")
        if self.phase != "planning":
            return ActionResult(content="You can only order during preparation time.")
        if not items:
            return ActionResult(success=False, content=f"Invalid parameters: `items` must be a dictionary like {{\"rice\": 4}}. Catalog item_ids: {', '.join(self.catalog.keys())}")

        total_cost = 0
        order_items = {}
        lines = []
        for item_id, qty in items.items():
            item_id = str(item_id)
            try:
                qty = int(qty)
            except (TypeError, ValueError):
                return ActionResult(success=False, content=f"Invalid quantity for `{item_id}`: {qty!r}. Quantities must be whole numbers.")
            if qty <= 0:
                continue
            if item_id in self.catalog:
                wholesale = self.catalog[item_id]["wholesale"]
                total_cost += wholesale * qty
                order_items[item_id] = qty
                lines.append(f"  {self.catalog[item_id]['name']} x{qty} @ ${wholesale} = ${wholesale * qty}")
            else:
                lines.append(f"  {item_id} x{qty} — unavailable; use standard catalog item_ids")

        if total_cost > v["cash"]:
            return ActionResult(content=f"Can't afford. Total: ${total_cost:.2f}, cash: ${v['cash']:.2f}.")

        if order_items:
            v["cash"] -= total_cost
            arrives_date = self._order_arrival_date()
            v["pending_orders"].append({
                "items": order_items,
                "cost": total_cost,
                "ordered_day": self.current_day,
                "ordered_date": self.current_date.isoformat(),
                "arrives_date": arrives_date.isoformat(),
            })
            # Record the supplier order in the ledger so vendors can review
            # spend alongside revenue.
            v["ledger"].append({
                "day": self.current_day,
                "time": "",
                "type": "supplier_order",
                "customer": "supplier",
                "items": order_items,
                "cost": total_cost,
                "ordered_date": self.current_date.isoformat(),
                "arrives_date": arrives_date.isoformat(),
            })
            return ActionResult(
                content=(
                    "Order placed! Arrives "
                    f"{self._format_date(arrives_date)}.\n"
                    + "\n".join(lines)
                    + f"\n\nTotal: ${total_cost:.2f}. Cash remaining: ${v['cash']:.2f}"
                )
            )
        return ActionResult(content="Supplier inquiries sent for specialty items:\n" + "\n".join(lines))

    def _place_supplier_order(self, agent_id: str, item: str, quantity: int, unit_cost: float) -> ActionResult:
        v = self.vendors.get(agent_id)
        if not v:
            return ActionResult(content="You are not a vendor.")
        if self.phase != "planning":
            return ActionResult(content="You can only place supplier orders during preparation time.")
        item = str(item).strip()
        if not item or quantity <= 0 or unit_cost <= 0:
            return ActionResult(success=False, content='Invalid parameters. Specify item, positive quantity, and positive unit_cost from your supplier negotiation. Example: place_supplier_order item="Chashu Pork" quantity=3 unit_cost=7.')
        total_cost = quantity * unit_cost
        if total_cost > v["cash"]:
            return ActionResult(content=f"Can't afford. Total: ${total_cost:.2f}, cash: ${v['cash']:.2f}.")

        item_id = self._resolve_item_id(item) or item
        if item_id not in self.catalog:
            self.catalog[item_id] = {
                "name": item.replace("_", " ").replace("*", "").title(),
                "wholesale": unit_cost,
                "category": "specialty",
            }
        v["cash"] -= total_cost
        arrives_date = self._order_arrival_date()
        v["pending_orders"].append({
            "items": {item_id: quantity},
            "cost": total_cost,
            "ordered_day": self.current_day,
            "ordered_date": self.current_date.isoformat(),
            "arrives_date": arrives_date.isoformat(),
        })
        v["ledger"].append({
            "day": self.current_day,
            "time": "",
            "type": "supplier_order",
            "customer": "supplier",
            "items": {item_id: quantity},
            "cost": total_cost,
            "ordered_date": self.current_date.isoformat(),
            "arrives_date": arrives_date.isoformat(),
            "negotiated": True,
        })
        self._record_supplier_speech(
            agent_id,
            SUPPLIER_ID,
            f"Order confirmed: {quantity} x {item_id} at ${unit_cost:.2f}/unit.",
        )
        self._supplier_waiting_vendors.discard(agent_id)
        return ActionResult(
            content=(
                f"Supplier order placed: {quantity} x {item_id} at ${unit_cost:.2f}/unit. "
                f"Total ${total_cost:.2f}. Cash remaining: ${v['cash']:.2f}. "
                f"Arrives {self._format_date(arrives_date)}."
            )
        )

    # ── Customer implementations ──

    def _visit_vendor(self, agent_id: str, vendor: Optional[str]) -> ActionResult:
        if agent_id not in CUSTOMER_IDS:
            return ActionResult(content="You are not a customer.")
        if self.phase != "market":
            return ActionResult(content="Market is closed.")
        resolved_vendor = self._resolve_agent_ref(vendor)
        vendor = resolved_vendor if resolved_vendor in VENDOR_IDS else vendor
        if not vendor or vendor not in VENDOR_IDS:
            vendors = self._format_agent_list(self._active_vendor_ids()) or "none currently open"
            return ActionResult(content=f"Available vendors: {vendors}")
        if not self._is_vendor_active(vendor):
            return ActionResult(content=f"{self._vendor_label(vendor)} is closed.")

        self.active_visits[agent_id] = vendor
        v = self.vendors[vendor]
        still_need = self.customers[agent_id]["still_need"]
        sim_time = self._get_simulated_time()

        lines = []
        for item_id, qty in sorted(v["stock"].items()):
            if qty > 0:
                name = self.catalog.get(item_id, {}).get("name", item_id)
                listed = v["listed_prices"].get(item_id)
                price_str = f"${listed:.2f}" if listed else "ask for price"
                relevant = " ← you need this!" if item_id in still_need else ""
                lines.append(f"  {name}: {qty} available — {price_str}{relevant}")

        cname = self.customers[agent_id]["name"]
        self._append_stall_chat(vendor, agent_id, cname, "Hi, I'm here to look around.")

        if lines:
            chat = self._format_stall_chat(vendor, agent_id)
            return ActionResult(
                content=f"Inspecting {self._vendor_label(vendor)} ({sim_time}).\nGoods and listed prices:\n"
                + "\n".join(lines)
                + (f"\n{chat}" if chat else "")
            )
        return ActionResult(content=f"Inspecting {self._vendor_label(vendor)}: no goods currently in stock.")

    def _leave_vendor(self, agent_id: str) -> ActionResult:
        if agent_id not in self.active_visits:
            return ActionResult(content="No vendor is currently selected.")
        old = self.active_visits.pop(agent_id)
        return ActionResult(content=f"You stopped engaging {self._vendor_label(old)}.")

    def _customer_accept_deal(self, agent_id: str, item: Optional[str], price: float) -> ActionResult:
        if agent_id not in CUSTOMER_IDS:
            return ActionResult(content="You are not a customer.")
        if agent_id not in self.active_visits:
            return ActionResult(content="No vendor selected. Inspect a vendor first.")
        vendor_id = self.active_visits[agent_id]
        return self._execute_sale(vendor_id, agent_id, self._resolve_item_id(item) or "", price, initiated_by="customer")

    def _check_shopping_list(self, agent_id: str) -> ActionResult:
        c = self.customers.get(agent_id)
        if not c:
            return ActionResult(content="You are not a customer.")
        sim_time = self._get_simulated_time()
        lines = [f"SHOPPING LIST — Budget: ${c['remaining_budget']:.2f}"]
        if sim_time:
            lines[0] += f" ({sim_time})"
        if c["still_need"]:
            lines.append("\nStill need:")
            for item_id in c["still_need"]:
                name = self.catalog.get(item_id, {}).get("name", item_id)
                lines.append(f"  [ ] {name}")
        if c["purchased"]:
            lines.append("\nBought:")
            for p in c["purchased"]:
                name = self.catalog.get(p["item"], {}).get("name", p["item"])
                lines.append(f"  [x] {name} from {p['vendor']} for ${p['price']:.2f}")
        if not c["still_need"]:
            lines.append("\nAll items purchased!")
        if self.phase == "planning" and not c.get("next_shopping_list"):
            lines.append(
                "\nPreparation is not complete. Use write_list to create the next market session's shopping list."
            )
        return ActionResult(content="\n".join(lines))

    def _done_shopping(self, agent_id: str) -> ActionResult:
        if agent_id not in CUSTOMER_IDS:
            return ActionResult(content="You are not a customer.")
        self._customers_done.add(agent_id)
        if agent_id in self.active_visits:
            self.active_visits.pop(agent_id)
        c = self.customers[agent_id]
        spent = c["budget"] - c["remaining_budget"]
        return ActionResult(
            content=f"Done shopping for {self._format_date()}. Spent ${spent:.2f}, "
            f"{len(c['still_need'])} items still needed."
        )

    def _normalize_shopping_item(self, raw_item: Any) -> Optional[str]:
        if isinstance(raw_item, dict):
            for key in ("item", "name", "item_id", "id"):
                value = raw_item.get(key)
                if value:
                    raw_item = value
                    break
        raw = str(raw_item).strip()
        if not raw:
            return None
        # Exact catalog match (id or display name) → catalog SKU.
        # No substring matching: "rice noodles" must not collapse to "rice".
        resolved = self._resolve_item_id(raw)
        if resolved in self.catalog:
            return resolved
        lowered = raw.lower()
        for item_id in self.catalog:
            if lowered == item_id.lower():
                return item_id
        # Otherwise treat as specialty want; supplier-sourcing path handles it.
        cleaned = re.sub(r"[^a-z0-9_*]+", "_", raw.lower()).strip("_")
        if not cleaned:
            return None
        if not cleaned.endswith("*"):
            cleaned += "*"
        return cleaned

    def _extract_items_from_notes(self, notes: str) -> List[str]:
        """Best-effort recovery when an agent writes list items in prose."""
        extracted: List[str] = []
        for line in notes.splitlines():
            text = line.strip()
            if not text:
                continue
            item_id_match = re.search(r"\bitem_id\b\s*[:=]\s*[\"']?([A-Za-z0-9_*]+)", text)
            if item_id_match:
                extracted.append(item_id_match.group(1))
                continue
            if not re.match(r"^([-*]|\d+[\).])\s+", text):
                continue
            text = re.sub(r"^([-*]|\d+[\).])\s+", "", text).strip()
            text = re.split(r"\s+[—–-]\s+|\s+\(|\s+\[", text, maxsplit=1)[0].strip()
            if text and not re.search(r"^(budget|notes?|items?)\b", text, flags=re.I):
                extracted.append(text)
        return extracted

    async def _write_list(self, agent_id: str, items: Any, notes: str) -> ActionResult:
        c = self.customers.get(agent_id)
        if not c:
            return ActionResult(content="You are not a customer.")
        if isinstance(items, str):
            raw_items = [part.strip() for part in re.split(r"[,;\n]", items) if part.strip()]
        elif isinstance(items, list):
            raw_items = items
        else:
            raw_items = []
        if not raw_items and notes.strip():
            raw_items = self._extract_items_from_notes(notes)
        normalized = []
        for raw_item in raw_items:
            item_id = self._normalize_shopping_item(raw_item)
            if item_id and item_id not in normalized:
                normalized.append(item_id)
        if not normalized:
            return ActionResult(
                content=(
                    "write_list needs an items list for tomorrow's shopping objective. "
                    "Use ordinary item names such as rice, matcha, noodles, soap, "
                    "or a clear name for an unusual item."
                )
            )
        constrained = []
        skipped = []
        specialty_count = 0
        for item_id in normalized:
            is_specialty = item_id not in self.catalog
            if len(constrained) >= 6:
                skipped.append(item_id)
                continue
            if is_specialty and specialty_count >= 2:
                skipped.append(item_id)
                continue
            constrained.append(item_id)
            if is_specialty:
                specialty_count += 1
        for item_id in c.get("shopping_list", []):
            if len(constrained) >= 3:
                break
            if item_id not in constrained:
                constrained.append(item_id)
        normalized = constrained
        c["next_shopping_list"] = normalized
        item_lines = []
        for item_id in normalized:
            name = self.catalog.get(item_id, {}).get("name", item_id)
            item_lines.append(f"- {name}")
        adjustment_note = ""
        if skipped:
            adjustment_note = (
                "Adjusted to fit one-day list limits: kept at most 6 items and at most "
                f"2 specialty items; skipped {', '.join(skipped)}.\n\n"
            )
        c["written_list"] = (
            adjustment_note
            + ((notes.strip() + "\n\n") if notes.strip() else "")
            + "Next market shopping list:\n"
            + "\n".join(item_lines)
        )
        self._customers_done.add(agent_id)
        all_done = (
            self._active_vendor_ids().issubset(self._vendors_done)
            and CUSTOMER_IDS.issubset(self._customers_done)
        )
        if all_done:
            if self.current_day > 0:
                await self._run_dream_phase()
            self._advance_day()
            waiting_note = f"Everyone ready. The market session for {self._format_date()} begins!"
        else:
            remaining_v = len(self._active_vendor_ids() - self._vendors_done)
            remaining_c = len(CUSTOMER_IDS - self._customers_done)
            waiting_note = (
                f"Preparation complete. Waiting for {remaining_v} vendor(s) "
                f"and {remaining_c} customer(s) to finish planning."
            )
        return ActionResult(
            content=(
                "Shopping list saved for the next market session:\n"
                + "\n".join(item_lines)
                + (f"\n\n{adjustment_note.strip()}" if adjustment_note else "")
                + f"\n\n{waiting_note}"
            )
        )

    # ── Shared sale execution ──

    def _execute_sale(self, vendor_id: str, customer_id: str, item: str, price: float, initiated_by: str) -> ActionResult:
        v = self.vendors.get(vendor_id)
        c = self.customers.get(customer_id)
        if not v or not c:
            return ActionResult(content="Invalid vendor or customer.")
        if not v.get("active", True):
            return ActionResult(content=f"{self._vendor_label(vendor_id)} is closed.")
        item = self._resolve_item_id(item) or ""
        if not item or item not in self.catalog:
            return ActionResult(content=f"Unknown item '{item}'. Use an item name from the stall listing.")
        if v["stock"].get(item, 0) <= 0:
            return ActionResult(content=f"Out of stock: {self.catalog[item]['name']}.")
        if price > c["remaining_budget"]:
            return ActionResult(content=f"Customer can't afford ${price:.2f}. Budget: ${c['remaining_budget']:.2f}.")
        if price <= 0:
            return ActionResult(content="Price must be positive.")

        item_name = self.catalog[item]["name"]
        wholesale = self.catalog[item]["wholesale"]
        margin = price - wholesale
        listed = v["listed_prices"].get(item, price)
        sim_time = self._get_simulated_time()

        # Execute
        v["stock"][item] -= 1
        v["cash"] += price
        c["remaining_budget"] -= price
        c["purchased"].append({"vendor": vendor_id, "item": item, "price": price, "day": self.current_day})
        if item in c["still_need"]:
            c["still_need"].remove(item)

        # Ledger entry (system-generated)
        v["ledger"].append({
            "day": self.current_day,
            "time": sim_time,
            "customer": customer_id,
            "item": item,
            "listed_price": listed,
            "final_price": price,
            "margin": margin,
        })

        remaining = c["still_need"]
        remaining_str = self._format_item_names(remaining) if remaining else "shopping complete!"
        sale_line = (
            f"Sale completed: {item_name} for ${price:.2f}. "
            f"Customer budget now ${c['remaining_budget']:.2f}; still need: {remaining_str}."
        )
        self._append_stall_chat(vendor_id, customer_id, "Register", sale_line)
        self.pending_context_markers.append({"to": vendor_id, "content": sale_line})
        self.pending_context_markers.append({"to": customer_id, "content": sale_line})

        if initiated_by == "vendor":
            return ActionResult(content=f"SALE: {item_name} to {self._customer_label(customer_id)} for ${price:.2f} (margin ${margin:.2f}). Cash: ${v['cash']:.2f}.")
        else:
            return ActionResult(content=f"Bought {item_name} for ${price:.2f}. Budget: ${c['remaining_budget']:.2f}. Still need: {remaining_str}")

    # ── Wait for next day (with dream) ──

    async def _wait_for_next_day(self, agent_id: str) -> ActionResult:
        if self.phase != "planning":
            return ActionResult(content="Market is still open. You can only wait for the next day during preparation time.")

        if agent_id in VENDOR_IDS:
            vendor = self.vendors.get(agent_id, {})
            if not vendor.get("listed_prices"):
                return ActionResult(content="Set your listed prices with set_prices before marking preparation complete.")
            if not vendor.get("plan"):
                return ActionResult(content="Write your strategy notes with write_plan before marking preparation complete.")
            self._vendors_done.add(agent_id)
        elif agent_id in CUSTOMER_IDS:
            customer = self.customers.get(agent_id, {})
            if not customer.get("next_shopping_list"):
                return ActionResult(content="Set tomorrow's shopping list with write_list before marking preparation complete.")
            self._customers_done.add(agent_id)

        all_done = (
            self._active_vendor_ids().issubset(self._vendors_done)
            and CUSTOMER_IDS.issubset(self._customers_done)
        )

        if all_done:
            # Dream phase — compress today's events into memories for each agent,
            # then queue context resets so each wakes up with fresh state.
            if self.current_day > 0:
                await self._run_dream_phase()
            self._advance_day()
            return ActionResult(content=f"Everyone ready. The market session for {self._format_date()} begins!")
        else:
            remaining_v = len(self._active_vendor_ids() - self._vendors_done)
            remaining_c = len(CUSTOMER_IDS - self._customers_done)
            return ActionResult(
                content=f"Waiting for {remaining_v} vendor(s) and {remaining_c} customer(s) to finish planning."
            )

    async def _handle_wait(self, agent_id: str) -> ActionResult:
        if self.phase != "planning":
            return ActionResult(content="Waiting.")
        if agent_id in CUSTOMER_IDS:
            customer = self.customers.get(agent_id, {})
            if customer.get("next_shopping_list"):
                return await self._wait_for_next_day(agent_id)
            return ActionResult(
                success=False,
                content=(
                    "Preparation is not complete. Use write_list now to create the next market session's shopping list. "
                    "Writing the list completes preparation and you will wait until the market opens."
                ),
            )
        if agent_id in VENDOR_IDS:
            vendor = self.vendors.get(agent_id, {})
            if not vendor.get("listed_prices"):
                return ActionResult(
                    success=False,
                    content="Preparation is not complete. Use set_prices before waiting for the next market day.",
                )
            if not vendor.get("plan"):
                return ActionResult(
                    success=False,
                    content="Preparation is not complete. Use write_plan with strategy notes before waiting for the next market day.",
                )
            return await self._wait_for_next_day(agent_id)
        return ActionResult(content="Waiting.")

    # ── Dream phase ──

    def _build_vendor_day_summary(self, vendor_id: str) -> str:
        """Construct a factual summary of one vendor's day for the dream prompt."""
        v = self.vendors.get(vendor_id, {})
        today_ledger = [
            e for e in v.get("ledger", [])
            if e.get("day") == self.current_day and e.get("type", "sale") == "sale"
        ]
        today_orders = [
            e for e in v.get("ledger", [])
            if e.get("day") == self.current_day and e.get("type") == "supplier_order"
        ]
        lines = [f"{self._format_date()} summary for {vendor_id}:"]
        if v.get("plan"):
            lines.append(f"Your plan today was: {v['plan']}")
        if v.get("listed_prices"):
            price_lines = []
            for item_id, price in sorted(v["listed_prices"].items()):
                name = self.catalog.get(item_id, {}).get("name", item_id)
                wholesale = self.catalog.get(item_id, {}).get("wholesale", 0)
                price_lines.append(f"  {name}: listed ${price:.2f} (cost ${wholesale})")
            lines.append("Prices you set:\n" + "\n".join(price_lines))
        fee_entries = [
            e for e in v.get("ledger", [])
            if e.get("day") == self.current_day and e.get("type") == "operating_fee"
        ]
        if fee_entries:
            fee = fee_entries[-1]
            lines.append(
                f"Daily operating fee: ${fee['amount']:.2f}; "
                f"cash after fee ${fee['cash_after']:.2f}"
            )
        if today_ledger:
            sale_lines = []
            for e in today_ledger:
                name = self.catalog.get(e["item"], {}).get("name", e["item"])
                sale_lines.append(
                    f"  Sold {name} to {e['customer']} at ${e['final_price']:.2f} "
                    f"(listed ${e['listed_price']:.2f}, margin ${e['margin']:.2f})"
                )
            revenue = sum(e["final_price"] for e in today_ledger)
            margin = sum(e["margin"] for e in today_ledger)
            lines.append(
                f"Sales ({len(today_ledger)}, revenue ${revenue:.2f}, profit ${margin:.2f}):\n"
                + "\n".join(sale_lines)
            )
        else:
            lines.append("You made no sales today.")
        if today_orders:
            order_lines = []
            for e in today_orders:
                items_str = ", ".join(
                    f"{self.catalog.get(k, {}).get('name', k)} x{q}"
                    for k, q in e.get("items", {}).items()
                )
                order_lines.append(
                    f"  Ordered {items_str} for ${e['cost']:.2f} "
                    f"(arrives {self._format_order_arrival(e)})"
                )
            lines.append("Supplier orders today:\n" + "\n".join(order_lines))
        # Inventory at close
        inv_lines = []
        for item_id, qty in sorted(v.get("stock", {}).items()):
            name = self.catalog.get(item_id, {}).get("name", item_id)
            inv_lines.append(f"  {name}: {qty} units left")
        if inv_lines:
            lines.append("Stock at close:\n" + "\n".join(inv_lines))
        lines.append(f"Cash: ${v.get('cash', 0):.2f}")
        if not v.get("active", True):
            lines.append("Stall status: closed")
        return "\n\n".join(lines)

    def _build_customer_day_summary(self, customer_id: str) -> str:
        """Construct a factual summary of one customer's day for the dream prompt."""
        c = self.customers.get(customer_id, {})
        lines = [f"{self._format_date()} summary for {customer_id}:"]
        if c.get("written_list"):
            lines.append(f"Your shopping list was: {c['written_list']}")
        purchases = [p for p in c.get("purchased", []) if p.get("day") == self.current_day]
        if purchases:
            buy_lines = []
            for p in purchases:
                name = self.catalog.get(p["item"], {}).get("name", p["item"])
                buy_lines.append(f"  Bought {name} from {p['vendor']} for ${p['price']:.2f}")
            spent = sum(p["price"] for p in purchases)
            lines.append(
                f"Purchases ({len(purchases)}, spent ${spent:.2f}):\n"
                + "\n".join(buy_lines)
            )
        else:
            lines.append("You bought nothing today.")
        still_need = c.get("still_need", [])
        if still_need:
            lines.append(f"Still on your list: {self._format_item_names(still_need)}")
        lines.append(
            f"Budget remaining: ${c.get('remaining_budget', 0):.2f} of "
            f"${c.get('budget', 0):.2f}"
        )
        return "\n\n".join(lines)

    def _render_context_window_for_dream(self, agent_id: str) -> str:
        """Render the live active context window for day-boundary memory.

        This intentionally uses ``to_prompt()`` rather than ``to_transcript()``:
        the dream should compress the context the agent currently has for the
        day, not all historical artifact transcript text across prior resets.
        """
        if agent_id in VENDOR_IDS:
            snapshot = self.vendors.get(agent_id, {}).get("_dream_context_snapshot")
        else:
            snapshot = self.customers.get(agent_id, {}).get("_dream_context_snapshot")
        if snapshot:
            factual_summary = (
                self._build_vendor_day_summary(agent_id)
                if agent_id in VENDOR_IDS
                else self._build_customer_day_summary(agent_id)
            )
            return snapshot + "\n\n=== FACTUAL CLOSE SUMMARY ===\n" + factual_summary

        ctx = self.agent_context_windows.get(agent_id)
        if ctx is not None and hasattr(ctx, "to_prompt"):
            system_prompt, user_prompt = ctx.to_prompt()
            parts = []
            if system_prompt:
                parts.append(f"=== ACTIVE SYSTEM PROMPT ===\n{system_prompt}")
            if user_prompt:
                parts.append(f"=== ACTIVE DAY CONTEXT ===\n{user_prompt}")
            if parts:
                return "\n\n".join(parts)

        if agent_id in VENDOR_IDS:
            return self._build_vendor_day_summary(agent_id)
        return self._build_customer_day_summary(agent_id)

    def render_dream_prompt_for_audit(self, agent_id: str) -> Dict[str, str]:
        """Expose the exact dream prompt shape for prompt audits and tests."""
        role_hint = "vendor" if agent_id in VENDOR_IDS else "customer"
        persona = self.agent_prompts.get(agent_id, "")
        active_context = self._render_context_window_for_dream(agent_id)
        dream_system = self._build_dream_system_prompt(role_hint, persona)
        dream_user = self._build_dream_user_prompt(active_context)
        return {"system": dream_system, "user": dream_user}

    def _build_dream_system_prompt(self, role_hint: str, persona: str = "") -> str:
        parts = [
            f"You are compressing one day at Kōen Market into memories "
            f"for a {role_hint} agent.",
            "Preserve the agent's continuity and point of view.",
        ]
        if persona.strip():
            parts.append(f"Agent persona/context:\n{persona.strip()}")
        parts.append(
            "Output exactly one daily summary/reflection note plus 3-7 memory "
            "items. The daily summary should be concise, first-person, and "
            "capture the overall arc of the day and what the agent should carry "
            "forward tomorrow. Each memory item should be concise, first-person, "
            "and capture something useful in a later market session: a price "
            "learned, an impression of a person, a strategy that worked or "
            "didn't, or a relationship update. Preserve concrete details from "
            "the agent's actual context window; do not add events, motives, "
            "relationships, or transactions that are not in the context."
        )
        return "\n\n".join(parts)

    def _build_dream_user_prompt(self, active_context: str) -> str:
        return (
            f"Agent's active context window for the day:\n{active_context}\n\n"
            f"Compress this context into one daily summary/reflection note and memories."
        )

    async def _run_dream_phase(self) -> None:
        """Compress each agent's active context window into memories.

        For each agent:
          1. Render the active context window the agent had for the day.
          2. Call LLM with a dream prompt → summary + memory items.
          3. Queue the summary and each memory item via pending_memories.
          4. Stash summary/memories on the agent's state for ``_advance_day`` to
             use when building the reset prompt.

        All dream LLM calls run concurrently via asyncio.gather to
        avoid serializing latency across 10 agents.
        """
        import asyncio
        import os
        from miniverse.llm_utils import call_llm_with_retries
        from pydantic import BaseModel, Field

        class DreamMemory(BaseModel):
            kind: str = Field(description="One of: impression, price_intel, strategy, relationship, observation")
            content: str = Field(description="The memory text — concise, first-person")

        class DreamOutput(BaseModel):
            daily_summary: str = Field(description="One concise first-person daily summary/reflection note")
            memories: List[DreamMemory] = Field(description="3-7 compressed memories from today")

        provider = os.environ.get("LLM_PROVIDER", "openai")
        model = os.environ.get("LLM_MODEL", "gpt-5-mini")

        async def _dream_for(agent_id: str) -> tuple[str, str, List[str], List[Dict[str, Any]]]:
            if agent_id in VENDOR_IDS:
                role_hint = "vendor"
            else:
                role_hint = "customer"

            persona = self.agent_prompts.get(agent_id, "")
            active_context = self._render_context_window_for_dream(agent_id)
            dream_system = self._build_dream_system_prompt(role_hint, persona)
            dream_user = self._build_dream_user_prompt(active_context)

            daily_summary = ""
            memories: List[str] = []
            mem_records: List[Dict[str, Any]] = []
            try:
                dream_output = await call_llm_with_retries(
                    system_prompt=dream_system,
                    user_prompt=dream_user,
                    llm_provider=provider,
                    llm_model=model,
                    response_model=DreamOutput,
                )
                daily_summary = dream_output.daily_summary.strip()
                if daily_summary:
                    mem_records.append({
                        "agent_id": agent_id,
                        "content": daily_summary,
                        "memory_type": "dream_summary",
                        "importance": 8,
                        "tags": ["dream", f"day:{self.current_day}", "kind:daily_summary"],
                        "metadata": {"day": self.current_day, "kind": "daily_summary"},
                    })
                for m in dream_output.memories:
                    memories.append(m.content)
                    mem_records.append({
                        "agent_id": agent_id,
                        "content": m.content,
                        "memory_type": "dream",
                        "importance": 7,
                        "tags": ["dream", f"day:{self.current_day}", f"kind:{m.kind}"],
                        "metadata": {"day": self.current_day, "kind": m.kind},
                    })
            except Exception:
                # Do not store the raw active context as memory. If the dream
                # LLM fails, a deterministic factual summary is safer than
                # re-injecting stale dialogue and old phase instructions into
                # the next day's prompt.
                fallback = (
                    self._build_vendor_day_summary(agent_id)
                    if agent_id in VENDOR_IDS
                    else self._build_customer_day_summary(agent_id)
                )
                daily_summary = fallback
                mem_records.append({
                    "agent_id": agent_id,
                    "content": fallback,
                    "memory_type": "dream_fallback",
                    "importance": 6,
                    "tags": ["dream", "fallback", f"day:{self.current_day}"],
                    "metadata": {"day": self.current_day},
                })
            return agent_id, daily_summary, memories, mem_records

        all_agents = list(VENDOR_IDS) + list(CUSTOMER_IDS)
        results = await asyncio.gather(*[_dream_for(aid) for aid in all_agents])

        for agent_id, daily_summary, memories, mem_records in results:
            self.pending_memories.extend(mem_records)
            if agent_id in VENDOR_IDS:
                self.vendors[agent_id]["_dream_summary"] = daily_summary
                self.vendors[agent_id]["_dream_memories"] = memories
                self.vendors[agent_id].pop("_dream_context_snapshot", None)
            else:
                self.customers[agent_id]["_dream_summary"] = daily_summary
                self.customers[agent_id]["_dream_memories"] = memories
                self.customers[agent_id].pop("_dream_context_snapshot", None)

    def export_artifacts(self) -> Dict[str, Any]:
        """Export quantitative market state for analysis."""
        return {
            "current_day": self.current_day,
            "phase": self.phase,
            "market_economics": {
                "simulation_days": self.simulation_days,
                "daily_operating_fee": self.daily_operating_fee,
                "planning_timeout_seconds": self.planning_timeout_seconds,
                "real_minutes_per_sim_hour": self.real_min_per_sim_hour,
                "complete": self._simulation_complete,
                "completion_reason": self._completion_reason,
            },
            "vendors": {
                vendor_id: {
                    "cash": vendor["cash"],
                    "active": vendor.get("active", True),
                    "fees_paid": vendor.get("fees_paid", 0),
                    "stock": vendor["stock"],
                    "listed_prices": vendor["listed_prices"],
                    "ledger": vendor["ledger"],
                    "plan": vendor["plan"],
                    "pending_orders": vendor["pending_orders"],
                }
                for vendor_id, vendor in self.vendors.items()
            },
            "customers": {
                customer_id: {
                    "budget": customer["budget"],
                    "remaining_budget": customer["remaining_budget"],
                    "shopping_list": customer["shopping_list"],
                    "still_need": customer["still_need"],
                    "purchased": customer["purchased"],
                    "written_list": customer["written_list"],
                }
                for customer_id, customer in self.customers.items()
            },
            "active_visits": self.active_visits,
        }
