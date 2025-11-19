from google.adk.agents import Agent, BaseAgent, LoopAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types
from typing import AsyncGenerator
import json
import re
from .common import MODEL_PRO

class OrientationLoopInitializerAgent(BaseAgent):
    """Initializes loop state once flow sections are available."""
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self):
        super().__init__(
            name="OrientationLoopInitializerAgent",
            description="Sets up the loop context for orientation finding.",
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        print("\n--- Running Agent: OrientationLoopInitializerAgent ---")
        flow_sections = ctx.session.state.get("flow_sections")

        if not flow_sections:
            error_msg = "OrientationLoopInitializerAgent Error: 'flow_sections' not found in state. Cannot proceed."
            print(f"--- {error_msg} ---")
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=error_msg)])
            )
            return

        try:
            if not isinstance(flow_sections, list) or len(flow_sections) == 0:
                raise ValueError("flow_sections is not a valid, non-empty list.")

            first_section = flow_sections[0]

            if "section" not in first_section or "trace_instruction" not in first_section:
                raise ValueError("First section in JSON is missing 'section' or 'trace_instruction' key.")

            state_delta = {
                "current_section_index": 0,
                "current_section_components": first_section.get("section"),
                "current_section_trace_instruction": first_section.get("trace_instruction"),
                "master_orientation_map": {},
            }

            if not state_delta["current_section_components"]:
                raise ValueError("First section's 'section' key is empty.")

            print(f"--- OrientationLoopInitializerAgent: Initializing loop for {len(flow_sections)} sections. ---")

            yield Event(
                author=self.name,
                actions=EventActions(
                    state_delta=state_delta,
                )
            )

        except Exception as e:
            error_msg = f"OrientationLoopInitializerAgent Error: Failed to initialize loop. Details: {e}"
            print(f"--- {error_msg} ---")
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=error_msg)])
            )
            return

class SectionOrientationFinderAgent(Agent):
    """
    LLM agent that finds orientations *only* for 'C' and 'M' components in one section list.
    """
    def __init__(self):
        super().__init__(
            name="SectionOrientationFinderAgent",
            model=MODEL_PRO,
            generate_content_config=types.GenerateContentConfig(temperature=0),
            description="Determines 'C' and 'M' component orientations for a single section.",
            instruction="""
            You are a component orientation specialist. You will analyze the provided image.
            Your task is to determine the primary orientation (angle in degrees) for *only* the components that are listed below **AND** whose IDs start with 'C' or 'M'.

            **Component List for this Section:**
            `{current_section_components}`

            **General Flow Direction Guide:** This is a **CRITICAL HINT**. The flow for this section is:
            `{current_section_trace_instruction}`

            **!!CRITICAL RULES!!:**
            1.  **ONLY ANALYZE THIS SECTION:** Your task is *only* for the components in the list above. Do **NOT** analyze or output orientations for any components *not* in this list.
            2.  **ARROWS ARE TRUTH:** The orientation is defined by the **small, pointed arrows** on the lines connecting *along the main path* of the component.
            3.  **USE THE GUIDE:** Use the `General Flow Direction Guide` as a **very strong hint**. If the guide says "Right-to-Left", the horizontal conveyors in that section should almost certainly be 180°.
            4.  **JSON ONLY:** Respond **ONLY** with a single, valid JSON object. Do not add *any* other text, explanation, or conversational phrases.
            5.  **NO SUMMARIES:** Do **NOT** output text like "All sections have been processed". Your only output is the JSON object for the *current* section.

            **Your 3-Step Reasoning Process for each 'C' or 'M' component in the list:**
            1.  **Identify Main Path:** Look at the component. Identify its main, linear path. Ignore perpendicular side connections.
            2.  **Observe Arrow Direction:** Observe the **small, pointed arrows** *on this main path*.
            3.  **Assign Angle:** Map this *observed direction* to the correct angle based on an anti-clockwise rotation from 0°.
                - **0°**: Flow is **Left-to-Right**.
                - **90°**: Flow is **Bottom-to-Top**.
                - **180°**: Flow is **Right-to-Left**.
                - **270°**: Flow is **Top-to-Bottom**.

            Respond **only** with a single JSON object. If no 'C' or 'M' components are in this section's list, return an empty object `{}`.

            **Example Output:** `{{"C1": 0, "M1": 0, "C2": 90}}`
            """,
            output_key="section_orientations_raw",
        )

class OrientationAggregatorAgent(BaseAgent):
    """
    Custom code-based agent to merge orientation dictionaries into the master map.
    """
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self):
        super().__init__(
            name="OrientationAggregatorAgent",
            description="Aggregates orientation maps from each section.",
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        print("\n--- Running Agent: OrientationAggregatorAgent ---")
        section_orientations_raw = ctx.session.state.get("section_orientations_raw")
        master_orientation_map = ctx.session.state.get("master_orientation_map", {})

        if not section_orientations_raw:
            print("--- OrientationAggregatorAgent: 'section_orientations_raw' not found. Skipping aggregation. ---")
            yield Event(author=self.name)
            return

        json_string = None
        try:
            # First, try to parse the raw string directly
            section_orientations = json.loads(section_orientations_raw)
            json_string = section_orientations_raw
            print("--- OrientationAggregatorAgent: Parsed raw JSON successfully. ---")

        except json.JSONDecodeError:
            print(f"--- OrientationAggregatorAgent: Raw JSON parsing failed. Attempting correction... ---")
            # If it fails, try to extract the JSON block
            json_match = re.search(r'\{.*\}', section_orientations_raw, re.DOTALL)
            if json_match:
                json_string = json_match.group(0)
                try:
                    # Second, try to parse the *extracted* string
                    section_orientations = json.loads(json_string)
                    print("--- OrientationAggregatorAgent: Parsed *corrected* JSON successfully. ---")
                except json.JSONDecodeError as e:
                    # If even the extracted string fails, then error out
                    error_msg = f"OrientationAggregatorAgent Error: Failed to parse even *corrected* JSON. Raw: '{section_orientations_raw}'. Corrected: '{json_string}'. Details: {e}"
                    print(f"--- {error_msg} ---")
                    yield Event(author=self.name, content=types.Content(parts=[types.Part(text=error_msg)]))
                    return
            else:
                # If no JSON block was found at all
                error_msg = f"OrientationAggregatorAgent Error: Initial parse failed and no JSON object `{{...}}` found in raw output. Raw: '{section_orientations_raw}'."
                print(f"--- {error_msg} ---")
                yield Event(author=self.name, content=types.Content(parts=[types.Part(text=error_msg)]))
                return

        # If we successfully parsed (either first or second try)
        try:
            if not isinstance(section_orientations, dict):
                raise ValueError("Parsed orientations are not a dictionary.")

            # Merge the new section data into the master map
            master_orientation_map.update(section_orientations)
            
            state_delta = {
                "master_orientation_map": master_orientation_map,
                "section_orientations_raw": None, # Clear the raw key
            }
            
            print(f"--- OrientationAggregatorAgent: Merged {len(section_orientations)} orientations. Total now: {len(master_orientation_map)} ---")
            
            yield Event(
                author=self.name,
                actions=EventActions(state_delta=state_delta)
            )

        except Exception as e:
            error_msg = f"OrientationAggregatorAgent Error: Failed during aggregation logic (after parsing). Parsed data: '{section_orientations}'. Details: {e}"
            print(f"--- {error_msg} ---")
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=error_msg)])
            )
            return

class OrientationLoopControllerAgent(BaseAgent):
    """
    Custom code-based agent that controls the orientation-finding loop and sets default 0 for non-C/M components.
    """
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self):
        super().__init__(
            name="OrientationLoopControllerAgent",
            description="Controls the orientation-finding loop and sets defaults.",
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        print("\n--- Running Agent: OrientationLoopControllerAgent ---")
        current_index = ctx.session.state.get("current_section_index", 0)
        flow_sections = ctx.session.state.get("flow_sections", [])
        
        new_index = current_index + 1
        
        if new_index < len(flow_sections):
            # --- Loop Continues ---
            next_section = flow_sections[new_index]

            if "section" not in next_section or "trace_instruction" not in next_section:
                 error_msg = f"OrientationLoopControllerAgent Error: Section {new_index} in JSON is missing 'section' or 'trace_instruction' key."
                 print(f"--- {error_msg} ---")
                 yield Event(author=self.name, content=types.Content(parts=[types.Part(text=error_msg)]))
                 return

            state_delta = {
                "current_section_index": new_index,
                "current_section_components": next_section.get("section"),
                "current_section_trace_instruction": next_section.get("trace_instruction"),
            }

            print(f"--- OrientationLoopControllerAgent: Proceeding to section {new_index} ---")
            yield Event(
                author=self.name,
                actions=EventActions(state_delta=state_delta)
            )
        else:
            # --- Loop Ends ---
            print("--- OrientationLoopControllerAgent: All sections processed. Aggregating final orientations... ---")
            master_map = ctx.session.state.get("master_orientation_map", {})
            all_component_ids = ctx.session.state.get("component_ids", [])
            
            if not all_component_ids:
                 error_msg = f"OrientationLoopControllerAgent Error: 'component_ids' list not found in state. Cannot set default orientations."
                 print(f"--- {error_msg} ---")
                 yield Event(author=self.name, content=types.Content(parts=[types.Part(text=error_msg)]))
                 return

            final_orientations = {}
            for comp_id in all_component_ids:
                # Get the orientation from the map if it exists, otherwise default to 0
                final_orientations[comp_id] = master_map.get(comp_id, 0)

            state_delta = {
                # Set the final 'orientations' key for the next agent
                "orientations": final_orientations
            }
            
            print(f"--- OrientationLoopControllerAgent: Finalized {len(final_orientations)} orientations (with defaults). Escalating to end loop. ---")
            yield Event(
                author=self.name,
                actions=EventActions(
                    state_delta=state_delta,
                    escalate=True # Signal to LoopAgent to stop
                )
            )

class OrientationFinderLoop(LoopAgent):
    """
    The LoopAgent that orchestrates finding orientations for all sections.
    """
    def __init__(self):
        super().__init__(
            name="OrientationFinderLoop",
            sub_agents=[
                SectionOrientationFinderAgent(),
                OrientationAggregatorAgent(),
                OrientationLoopControllerAgent()
            ],
            max_iterations=20 # Safeguard against infinite loops
        )

class OrientationFinderAgent(SequentialAgent):
    """
    Sequential agent that first initializes the loop state, then runs the orientation finding loop.
    """
    def __init__(self):
        super().__init__(
            name="OrientationFinderAgent",
            description="Initializes and runs the orientation finding process.",
            sub_agents=[
                OrientationLoopInitializerAgent(),
                OrientationFinderLoop()
            ]
        )
