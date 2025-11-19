from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types
from typing import AsyncGenerator
import re
import json

class StateInitializerAgent(BaseAgent):
    """
    Agent 3: (MODIFIED) A robust, code-based agent to parse the new
    sections JSON and initialize the state for all loops.
    """
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self):
        super().__init__(
            name="StateInitializerAgent",
            description="Parses flow sections and initializes loop states.",
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        print("\n--- Running Agent: StateInitializerAgent ---")
        flow_sections_raw = ctx.session.state.get("flow_sections_raw")

        if not flow_sections_raw:
            error_msg = "StateInitializerAgent Error: 'flow_sections_raw' not found in state. Cannot proceed."
            print(f"--- {error_msg} ---")
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=error_msg)])
            )
            return

        try:
            # --- ROBUST JSON PARSING ---
            json_match = re.search(r'\[.*\]', flow_sections_raw, re.DOTALL)
            if not json_match:
                raise ValueError("No JSON list (e.g., `[...]`) found in the output.")
            
            json_string = json_match.group(0)
            flow_sections = json.loads(json_string)
            # --- END ROBUST PARSING ---

            if not isinstance(flow_sections, list) or len(flow_sections) == 0:
                raise ValueError("Parsed sections are not a valid, non-empty list.")
            
            first_section = flow_sections[0]

            # --- MODIFICATION: Use 'section' instead of 'from'/'to' ---
            if "section" not in first_section or "trace_instruction" not in first_section:
                raise ValueError("First section in JSON is missing 'section' or 'trace_instruction' key.")

            # Initialize state for ALL loops
            state_delta = {
                "flow_sections": flow_sections,
                "current_section_index": 0,
                "current_section_components": first_section.get("section"), # <-- NEW
                "current_section_trace_instruction": first_section.get("trace_instruction"),
                "master_orientation_map": {}, 
            }
            # --- END MODIFICATION ---
            
            if not state_delta["current_section_components"]:
                raise ValueError("First section's 'section' key is empty.")

            print(f"--- StateInitializerAgent: Successfully parsed {len(flow_sections)} sections. Initializing loops. ---")
            
            yield Event(
                author=self.name,
                actions=EventActions(
                    state_delta=state_delta,
                )
            )

        except Exception as e:
            error_msg = f"StateInitializerAgent Error: Failed to parse 'flow_sections_raw' JSON. Raw output was: '{flow_sections_raw}'. Details: {e}"
            print(f"--- {error_msg} ---")
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=error_msg)])
            )
            return
