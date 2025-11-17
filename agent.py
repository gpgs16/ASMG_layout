from google.adk.agents import Agent, BaseAgent, SequentialAgent, LoopAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types
import json
import re  # Import regex for parsing
from typing import List, Dict, Any, AsyncGenerator, Tuple, Optional
from .tools import ComponentDetector
import asyncio
import xml.etree.ElementTree as ET
import xml.dom.minidom  # For pretty-printing the final XML

import logging
import os
import time
from pathlib import Path
from datetime import datetime

from config.config_loader import Config
from src import plant_sim_controller

config = Config()
logger = logging.getLogger(__name__)

# --- Model Constants ---
MODEL_PRO = "gemini-2.5-pro"
MODEL_FLASH = "gemini-2.5-flash"
# ---

class LayoutParserAgent(BaseAgent):
    """
    Agent 1: Wraps the ComponentDetector tool to parse the initial image.
    (RESTORED) This is the original agent from your file, which saves
    'components' (semantic_id -> box) and 'component_ids' (list of semantic_ids)
    to the state.
    """

    # Declare the tool as a class attribute for Pydantic to recognize it.
    component_detector: ComponentDetector

    # Allow arbitrary types for Pydantic v2 compatibility with custom attributes.
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self):
        # Instantiate the tool before calling super()
        component_detector = ComponentDetector()
        super().__init__(
            name="LayoutParserAgent",
            description="Parses the initial image using ComponentDetector tool.",
            component_detector=component_detector,
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        """
        Execution Logic:
        1. Validate and Get original_image_bytes from the initial user message.
        2. Call ComponentDetector tool and handle tool errors.
        3. Save the numbered image as an artifact using the artifact_service.
        4. Transform box data to the required schema, casting to int.
        5. Yield a single Event containing the image (for UI) and all data (for next agents).
        """
        print("\n--- Running Agent: LayoutParserAgent ---")

        # 1. ROBUST INPUT VALIDATION
        original_image_bytes = None
        try:
            original_image_bytes = ctx.user_content.parts[0].inline_data.data
            if not original_image_bytes:
                raise AttributeError("Image data is empty.")
        except (AttributeError, IndexError, TypeError):
            error_msg = "LayoutParserAgent Error: No image provided. Please upload an image to start the layout analysis."
            print(f"--- {error_msg} ---")
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=error_msg)])
            )
            return

        # 2. CALL COMPONENTDETECTOR TOOL
        print("LayoutParserAgent: Image data found, calling ComponentDetector tool...")
        tool_result = await self.component_detector.run_async(
            image_data=original_image_bytes
        )

        # 3. HANDLE TOOL ERROR
        if "error" in tool_result:
            error_message = f"LayoutParserAgent Error: The ComponentDetector tool failed. Details: {tool_result['error']}"
            print(f"--- {error_message} ---")
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=error_message)])
            )
            return

        # 4. PREPARE IMAGE PART
        if "numbered_image_bytes" not in tool_result:
            error_message = "LayoutParserAgent Error: ComponentDetector tool ran but did not return 'numbered_image_bytes'."
            print(f"--- {error_msg} ---")
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=error_message)])
            )
            return

        numbered_image_part = types.Part.from_bytes(
            data=tool_result["numbered_image_bytes"], 
            mime_type="image/png"
        )
        
        # 5. TRANSFORM BOX DATA (WITH INT CASTING AND COORDINATE TRANSFORMATION)
        box_data_from_tool = tool_result.get("box_data", {})
        box_data_schema = {}
        component_count = 0
        component_ids = []

        # --- MODIFICATION: Added try...except block for robustness and coordinate transformation ---
        try:
            # First pass: collect all y-coordinates to find the most negative value
            all_y_coords = []
            for contour_id, bbox in box_data_from_tool.items():
                y1 = int(bbox["y"])
                w = int(bbox["width"])
                # In OpenCV, y increases downward, so we negate it. 'h' is now 'w'.
                inverted_y1 = -y1
                inverted_y2 = -(y1 + w)
                all_y_coords.extend([inverted_y1, inverted_y2])
            
            # Find the most negative y value
            min_y = min(all_y_coords) if all_y_coords else 0
            
            # Second pass: transform all coordinates
            for contour_id, bbox in box_data_from_tool.items():
                x1 = int(bbox["x"])
                y1 = int(bbox["y"])
                l = int(bbox["length"])
                w = int(bbox["width"])
                
                # Invert y-coordinate (multiply by -1)
                inverted_y1 = -y1
                
                # Shift to positive plane by subtracting min_y (which is negative)
                transformed_y = inverted_y1 - min_y

                box_data_schema[str(contour_id)] = {
                    "x": x1, 
                    "y": transformed_y, 
                    "length": l, 
                    "width": w
                }

        except Exception as e:
            # This block catches errors during data transformation
            error_message = f"LayoutParserAgent Error: Failed to transform bounding box data. Details: {e}"
            print(f"--- {error_message} ---")
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=error_message)])
            )
            return
        # --- END OF MODIFICATION ---

        # --- NEW: Create semantic 'components' dictionary ---
        component_types = tool_result.get("component_types", {})

        # --- DEBUGGING: Check for duplicate component types from OCR ---
        print("LayoutParserAgent: Checking for duplicate component types from OCR...")
        seen_types = {}
        duplicates_found = False
        for contour_id, component_type in component_types.items():
            if component_type in seen_types:
                if not duplicates_found:
                    print("LayoutParserAgent: WARNING - Duplicate component types detected! This will cause components to be overwritten.")
                    duplicates_found = True
                print(f"  - Duplicate Type: '{component_type}'. Contour ID '{contour_id}' will overwrite Contour ID '{seen_types[component_type]}'.")
            else:
                seen_types[component_type] = contour_id
        if not duplicates_found:
            print("LayoutParserAgent: No duplicate component types found.")
        # --- END OF DEBUGGING ---

        components_data = {}
        for contour_id, component_type in component_types.items():
            if contour_id in box_data_schema:
                # Map component_type (e.g., "L") to its box data
                components_data[component_type] = box_data_schema[contour_id]
        # --- END OF NEW ---

        # --- RECOMMENDED MODIFICATION ---
        # Make component_ids semantic (e.g., ['L1', 'C1', 'D1', ...])
        component_ids = list(components_data.keys()) 
        component_count = len(component_ids)
        print(f"LayoutParserAgent: Detected {component_count} semantic components. IDs: {component_ids}")
        # --- END OF MODIFICATION ---

        # 6. PREPARE STATE DELTA
        state_delta_for_next_agent = {
            "component_ids": component_ids, # (e.g., ['L1', 'C1', ...])
            "components": components_data,  # Map: Semantic ID -> {box}
        }
        
        # 7. YIELD THE SUCCESS EVENT
        print("--- LayoutParserAgent: Yielding Event with image and state_delta ---")
        yield Event(
            author=self.name,
            content=types.Content(parts=[numbered_image_part]), # This shows the image in the UI
            actions=EventActions(
                state_delta=state_delta_for_next_agent,
            ) # This passes data to the next agent
        )


class SectionPlannerAgent(Agent):
    """
    Agent 2: (MODIFIED) Analyzes the layout to identify ALL 11 flow paths
    and their component lists.
    """

    def __init__(self):
        super().__init__(
            name="SectionPlannerAgent",
            model=MODEL_PRO, # Use a powerful model for this complex task
            generate_content_config=types.GenerateContentConfig(temperature=0),
            description="Analyzes the layout image to identify all distinct flow paths.",
            instruction="""
            You are a precision layout analyzer. Your goal is to identify all distinct, continuous material flow paths shown in the image.
            The overall flow **STARTS at L1** and generally **ENDS at U1**.
            Your **ONLY** source of truth for flow direction is the **small, pointed ARROWS** on the lines. You must follow them meticulously.

            You must identify two types of paths:
            1.  **Main Conveyor Paths:** These are the primary horizontal and vertical lines. A main path should be traced as a single, continuous section as long as it maintains a consistent direction (e.g., a long Left-to-Right path). **Do NOT split a main conveyor path just because a workstation loop branches off from it.** A new main path section should only be defined when the primary flow makes a 90-degree turn onto a different main conveyor (e.g., a horizontal conveyor feeding into a vertical one).
            2.  **Workstation Loops:** Smaller loops that branch off from a main conveyor path (often at a 'D' component) and then rejoin it later. These loops often involve turns, which you **SHOULD** follow to trace the full loop.

            **CRITICAL:** Pay close attention to the arrows on the top and bottom conveyors. Their flow directions may be opposite.

            For each path you identify, list all components *in order of flow* as a single, comma-separated string (e.g., "C1, M1, C2").
            
            Also, provide a brief 'trace_instruction' describing that path.
            - For **Main Conveyor Paths**, use simple directions (e.g., "Trace main flow Left-to-Right").
            - For **Workstation Loops**, be highly descriptive of the path's shape (e.g., "Trace workstation loop Bottom-to-Top-to-Right-to-Bottom" or "Trace workstation loop Top-to-Bottom-to-Left-to-Top").

            Respond **only** with a single, valid JSON list of objects. Do not add any conversational text or markdown.

            **Output Format Example (DO NOT use components from the user's image):**
            `[{"section": "A, B, C", "trace_instruction": "Trace main flow Left-to-Right"}, {"section": "C, D, E, F", "trace_instruction": "Trace workstation loop Top-to-Bottom-to-Left-to-Top"}, {"section": "G, H, I, J", "trace_instruction": "Trace workstation loop Bottom-to-Top-to-Right-to-Bottom"}...]`
            """,
            output_key="flow_sections_raw", # Output will be a JSON string
        )

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

class ConnectionGeneratorAgent(BaseAgent):
    """
    Agent 4: (NEW) Code-based agent to deterministically generate
    the connection list from the ordered flow_sections.
    Replaces ConnectionFinderLoopAgent.
    """
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self):
        super().__init__(
            name="ConnectionGeneratorAgent",
            description="Generates connections from ordered section lists.",
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        print("\n--- Running Agent: ConnectionGeneratorAgent ---")
        flow_sections = ctx.session.state.get("flow_sections")

        if not flow_sections:
            error_msg = "ConnectionGeneratorAgent Error: 'flow_sections' not found in state."
            print(f"--- {error_msg} ---")
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=error_msg)])
            )
            return

        master_connection_list = []
        total_connections = 0

        try:
            for section_obj in flow_sections:
                section_str = section_obj.get("section")
                if not section_str:
                    print(f"--- ConnectionGeneratorAgent: Warning - Section object missing 'section' key. Skipping. ---")
                    continue
                
                # Split the comma-separated string into a list of components
                components = [comp.strip() for comp in section_str.split(',') if comp.strip()]

                if len(components) < 2:
                    # Not enough components to form a connection
                    continue

                # Create connections from the ordered list
                for i in range(len(components) - 1):
                    connection = {
                        "from": components[i],
                        "to": components[i+1]
                    }
                    master_connection_list.append(connection)
                    total_connections += 1

            # Save the final list to state for the JsonAssemblerAgent
            state_delta = {
                "connections": master_connection_list
            }

            print(f"--- ConnectionGeneratorAgent: Successfully generated {total_connections} connections from {len(flow_sections)} sections. ---")

            yield Event(
                author=self.name,
                actions=EventActions(
                    state_delta=state_delta,
                )
            )

        except Exception as e:
            error_msg = f"ConnectionGeneratorAgent Error: Failed during connection generation. Details: {e}"
            print(f"--- {error_msg} ---")
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=error_msg)])
            )
            return

# --- NEW: AGENTS FOR ORIENTATION LOOP ---

class SectionOrientationFinderAgent(Agent):
    """
    Agent 5a: (MODIFIED) LLM agent that finds orientations *only* for
    'C' and 'M' components in one section list, with a stronger prompt.
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
    Agent 5b: (NEW) Custom code-based agent to merge orientation
    dictionaries into the master map.
    Now with integrated robust JSON extraction on failure.
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
    Agent 5c: (MODIFIED) Custom code-based agent that controls the
    orientation-finding loop and sets default 0 for non-C/M components.
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

            # --- MODIFICATION: Use 'section' key ---
            if "section" not in next_section or "trace_instruction" not in next_section:
                 error_msg = f"OrientationLoopControllerAgent Error: Section {new_index} in JSON is missing 'section' or 'trace_instruction' key."
                 print(f"--- {error_msg} ---")
                 yield Event(author=self.name, content=types.Content(parts=[types.Part(text=error_msg)]))
                 return

            state_delta = {
                "current_section_index": new_index,
                "current_section_components": next_section.get("section"), # <-- NEW
                "current_section_trace_instruction": next_section.get("trace_instruction"),
            }
            # --- END MODIFICATION ---

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

            # --- MODIFICATION: Set final state ---
            state_delta = {
                # Set the final 'orientations' key for the next agent
                "orientations": final_orientations
            }
            # --- END MODIFICATION ---
            
            print(f"--- OrientationLoopControllerAgent: Finalized {len(final_orientations)} orientations (with defaults). Escalating to end loop. ---")
            yield Event(
                author=self.name,
                actions=EventActions(
                    state_delta=state_delta,
                    escalate=True # Signal to LoopAgent to stop
                )
            )

class OrientationFinderLoopAgent(LoopAgent):
    """
    Agent 5: (NEW) The LoopAgent that orchestrates finding orientations
    for all sections.
    """
    def __init__(self):
        super().__init__(
            name="OrientationFinderLoopAgent",
            sub_agents=[
                SectionOrientationFinderAgent(),
                OrientationAggregatorAgent(),
                OrientationLoopControllerAgent()
            ],
            max_iterations=20 # Safeguard against infinite loops
        )

class TextExtractorAgent(Agent):
    """
    Agent 6: (NEW) LLM-based agent to extract all textual data (general
    and component-specific) from the image.
    """

    def __init__(self):
        super().__init__(
            name="TextExtractorAgent",
            model=MODEL_PRO, # Use a powerful model for VQA
            generate_content_config=types.GenerateContentConfig(temperature=0),
            description="Extracts textual data (speeds, times, dimensions) from the layout.",
            instruction="""You are a meticulous data extraction specialist. Your task is to scan the provided layout image and extract two types of textual information.

            1.  **General Properties:** Look for any layout-wide data. The most important one is "Conveyor speed".
            2.  **Component Properties:** For *each* component ID in the list `{component_ids}`, search the image for any nearby text that defines its properties. Specifically look for:
                - "Interval"
                - "Proc time"
                - "MTTR" (Note: This may not be present for all components)
                - "MTBF" (Note: This may not be present for all components)
                - **Dimensions:** Look for double-sided arrows near a component. A horizontal arrow indicates 'length', and a vertical arrow indicates 'width'. Extract the value associated with that arrow.

            **CRITICAL RULES:**
            1.  **ONLY EXTRACT VISIBLE TEXT:** Only report data that is *visibly written* in the image. Do not invent or infer data.
            2.  **PRESERVE KEYS:** Use the exact text for the property key (e.g., "Proc time", "Conveyor speed", "length", "width").
            3.  **JSON ONLY:** Respond **ONLY** with a single, valid JSON object in the format below. Do not add *any* other text, explanation, or conversational phrases.

            **Output Format:**
            `{{"general_properties": {{"Key": "Value", ...}}, "component_properties": {{"ComponentID_1": {{"Key": "Value", ...}}, "ComponentID_2": {{"Key": "Value", ...}}, ...}}}}`

            **Example Output (using hypothetical data):**
            `{{"general_properties": {{"Conveyor speed": "1.0 m/s"}}, "component_properties": {{"D1": {{"width": "0.25m"}}, "M1": {{"Proc time": "8 sec", "MTTR": "1440 sec", "MTBF": "43200 sec"}}, "C1": {{"length": "2.5m"}}}}}`

            If no data of a certain type is found, return an empty object for that key (e.g., `"component_properties": {}`).

            """,
            output_key="extracted_text_data_raw", # Output a JSON string
        )

class TextDataAggregatorAgent(BaseAgent):
    """
    Agent 7: (NEW) Custom code-based agent to parse and aggregate
    the extracted textual data.
    """
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self):
        super().__init__(
            name="TextDataAggregatorAgent",
            description="Parses and aggregates extracted text data.",
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        print("\n--- Running Agent: TextDataAggregatorAgent ---")
        extracted_text_data_raw = ctx.session.state.get("extracted_text_data_raw")

        if not extracted_text_data_raw:
            print("--- TextDataAggregatorAgent: 'extracted_text_data_raw' not found. Skipping. ---")
            # Yield an event with an empty dict to allow assembly to continue
            yield Event(
                author=self.name,
                actions=EventActions(
                    state_delta={"extracted_text_data": {}}
                )
            )
            return
        
        parsed_data = {}
        try:
            # Try to parse the raw string directly
            parsed_data = json.loads(extracted_text_data_raw)
            print("--- TextDataAggregatorAgent: Parsed raw JSON successfully. ---")

        except json.JSONDecodeError:
            print(f"--- TextDataAggregatorAgent: Raw JSON parsing failed. Attempting correction... ---")
            # If it fails, try to extract the JSON block
            json_match = re.search(r'\{.*\}', extracted_text_data_raw, re.DOTALL)
            if json_match:
                json_string = json_match.group(0)
                try:
                    parsed_data = json.loads(json_string)
                    print("--- TextDataAggregatorAgent: Parsed *corrected* JSON successfully. ---")
                except json.JSONDecodeError as e:
                    error_msg = f"TextDataAggregatorAgent Error: Failed to parse even *corrected* JSON. Raw: '{extracted_text_data_raw}'. Corrected: '{json_string}'. Details: {e}"
                    print(f"--- {error_msg} ---")
                    yield Event(author=self.name, content=types.Content(parts=[types.Part(text=error_msg)]))
                    return
            else:
                error_msg = f"TextDataAggregatorAgent Error: Initial parse failed and no JSON object `{{...}}` found in raw output. Raw: '{extracted_text_data_raw}'."
                print(f"--- {error_msg} ---")
                yield Event(author=self.name, content=types.Content(parts=[types.Part(text=error_msg)]))
                return

        # Validate structure
        if not isinstance(parsed_data.get("general_properties"), dict):
            parsed_data["general_properties"] = {}
        if not isinstance(parsed_data.get("component_properties"), dict):
            parsed_data["component_properties"] = {}
            
        state_delta = {
            "extracted_text_data": parsed_data, # Save the clean dict
            "extracted_text_data_raw": None,    # Clear the raw key
        }
        
        print(f"--- TextDataAggregatorAgent: Successfully parsed and saved text data. ---")
        
        yield Event(
            author=self.name,
            actions=EventActions(state_delta=state_delta)
        )

class JsonAssemblerAgent(BaseAgent):
    """
    Agent 8: (MODIFIED) Assembles all intermediate data into the final JSON format.
    ...
    """
    
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self):
        super().__init__(
            name="JsonAssemblerAgent",
            description="Assembles all intermediate data into the final JSON format.",
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        
        print("\n--- Running Agent: JsonAssemblerAgent ---")
        
        # 1. Retrieve all necessary data from state
        components_data = ctx.session.state.get("components")
        connections = ctx.session.state.get("connections")
        orientations = ctx.session.state.get("orientations")
        flow_sections = ctx.session.state.get("flow_sections")
        
        # --- NEW: Retrieve extracted text data ---
        extracted_text_data = ctx.session.state.get("extracted_text_data", {})
        component_properties = extracted_text_data.get("component_properties", {})
        general_properties = extracted_text_data.get("general_properties", {})
        # --- END NEW ---

        # --- NEW: Extract conveyor speed and prepare for component-level assignment ---
        conveyor_speed = None
        for key, value in general_properties.items():
            if "speed" in key.lower():
                conveyor_speed = value
                break
        
        if conveyor_speed is None:
            conveyor_speed = "0.2 m/s"

        # 2. Robust validation
        try:
            if not components_data or not isinstance(components_data, dict):
                raise ValueError("'components' data is missing or not a dictionary.")
            if not connections or not isinstance(connections, list):
                raise ValueError("'connections' data is missing or not a list.")
            if not orientations or not isinstance(orientations, dict):
                raise ValueError("'orientations' data is missing or not a dictionary.")
            if not flow_sections or not isinstance(flow_sections, list):
                raise ValueError("'flow_sections' data is missing or not a list.")
            
        except Exception as e:
            error_msg = f"JsonAssemblerAgent Error: Failed to retrieve or parse prerequisite data. Details: {e}"
            print(f"--- {error_msg} ---")
            yield Event(author=self.name, content=types.Content(parts=[types.Part(text=error_msg)]))
            return

        # 3. *** Build the ordered list of component IDs ***
        print("--- JsonAssemblerAgent: Building ordered component list from flow_sections... ---")
        ordered_component_ids = []
        seen_component_ids = set()
        
        is_first_section = True
        for section_obj in flow_sections:
            section_str = section_obj.get("section")
            if not section_str:
                print(f"--- JsonAssemblerAgent: Warning - Skipping section with no 'section' string: {section_obj} ---")
                continue
                
            component_ids_in_section = [comp.strip() for comp in section_str.split(',') if comp.strip()]
            
            if not component_ids_in_section:
                continue

            if is_first_section:
                for comp_id in component_ids_in_section:
                    if comp_id not in seen_component_ids:
                        ordered_component_ids.append(comp_id)
                        seen_component_ids.add(comp_id)
                is_first_section = False
            else:
                anchor_component = component_ids_in_section[0]
                try:
                    insert_index = ordered_component_ids.index(anchor_component)
                except ValueError:
                    print(f"--- JsonAssemblerAgent: Warning - Anchor component '{anchor_component}' not found in ordered list. Skipping section. ---")
                    continue
                
                for comp_id in component_ids_in_section[1:]:
                    if comp_id not in seen_component_ids:
                        insert_index += 1
                        ordered_component_ids.insert(insert_index, comp_id)
                        seen_component_ids.add(comp_id)
        
        print(f"--- JsonAssemblerAgent: Generated ordered list of {len(ordered_component_ids)} unique components. ---")

        # --- NEW: Calculate pixels_per_meter ratio ---
        pixels_per_meter = 320.0  # Default value if no reference is found
        reference_found = False
        try:
            for comp_id, props in component_properties.items():
                if comp_id in components_data:
                    pixel_box = components_data[comp_id]
                    for dim_key, dim_value_str in props.items():
                        # Check for 'length' or 'width' in the extracted properties
                        if dim_key.lower() in ["length", "width"]:
                            # Extract numeric value from string like "0.8 m"
                            match = re.search(r'(\d+(\.\d+)?)', str(dim_value_str))
                            if match:
                                meter_value = float(match.group(1))
                                pixel_value = float(pixel_box.get(dim_key.lower()))

                                if meter_value > 0 and pixel_value > 0:
                                    pixels_per_meter = pixel_value / meter_value
                                    print(f"--- JsonAssemblerAgent: Calculated pixels_per_meter = {pixels_per_meter:.2f} "
                                          f"(from {comp_id}'s {dim_key}: {pixel_value}px / {meter_value}m) ---")
                                    reference_found = True
                                    break # Use the first valid reference found
                if reference_found:
                    break
            
            if not reference_found:
                print(f"--- JsonAssemblerAgent: Warning - No length/width reference found in component_properties. "
                      f"Using default ratio of {pixels_per_meter} pixels/meter. ---")

        except Exception as e:
            print(f"--- JsonAssemblerAgent: Warning - Failed to calculate pixels_per_meter ratio. "
                  f"Using default of {pixels_per_meter}. Details: {e} ---")
        # --- END NEW ---


        # 4. Perform the deterministic transformation *using the ordered list*
        print("--- JsonAssemblerAgent: Assembling final JSON in order... ---")
        final_components = {}
        try:
            for semantic_id in ordered_component_ids:
                
                if semantic_id not in components_data:
                    print(f"--- JsonAssemblerAgent: Warning - Component ID '{semantic_id}' from flow_sections not found in 'components' data. Skipping.")
                    continue
                
                box = components_data[semantic_id].copy() # Use a copy to avoid modifying original data
                orientation = orientations.get(semantic_id, 0)

                # --- NEW: Adjust origin and swap dimensions based on orientation BEFORE conversion ---
                x_orig = int(box["x"])
                y_orig = int(box["y"])
                l_px = int(box["length"])
                w_px = int(box["width"])

                if orientation == 0:
                    x_new = x_orig
                    y_new = y_orig - (w_px / 2)
                elif orientation == 90:
                    x_new = x_orig + (l_px / 2)
                    y_new = y_orig - w_px
                    l_px, w_px = w_px, l_px # Swap length and width
                elif orientation == 180:
                    x_new = x_orig + l_px
                    y_new = y_orig - (w_px / 2)
                elif orientation == 270:
                    x_new = x_orig + (l_px / 2)
                    y_new = y_orig
                    l_px, w_px = w_px, l_px # Swap length and width
                else: # Default to 0 degree behavior
                    x_new = x_orig
                    y_new = y_orig - (w_px / 2)

                # --- NEW: Apply additional adjustments for L, M, U components ---
                if semantic_id.startswith(('L', 'M', 'U')):
                    if orientation == 0:
                        x_new += l_px / 2
                    elif orientation == 90:
                        # This adjustment happens before the swap of l_px and w_px
                        y_new += int(box["width"]) / 2
                    elif orientation == 180:
                        x_new -= l_px / 2
                    elif orientation == 270:
                        # This adjustment happens before the swap of l_px and w_px
                        y_new -= int(box["width"]) / 2
                
                # --- MODIFICATION: Convert origin coordinates to a list of meters ---
                origin_list = [
                    round(x_new / pixels_per_meter, 4),
                    round(y_new / pixels_per_meter, 4)
                ]
                # --- END NEW ---
                
                # --- MODIFICATION: Restructure bounding_box and convert dimensions ---
                length_m = round(l_px / pixels_per_meter, 4)
                width_m = round(w_px / pixels_per_meter, 4)
                # --- END MODIFICATION ---

                # --- NEW: Get component-specific text data ---
                extra_data = component_properties.get(semantic_id, {})
                # --- END NEW ---
                
                final_components[semantic_id] = {
                    "origin": origin_list,
                    "orientation": orientation
                }

                # --- NEW: Add speed to C and D components ---
                if conveyor_speed and (semantic_id.startswith('C') or semantic_id.startswith('D')):
                    final_components[semantic_id]["speed"] = conveyor_speed
                # --- END NEW ---

                # --- NEW: Merge the extra data ---
                final_components[semantic_id].update(extra_data)
                # --- END NEW ---

                # --- MODIFICATION: Add dimensions only for C and D components ---
                if semantic_id.startswith('C') or semantic_id.startswith('D'):
                    final_components[semantic_id]["length"] = length_m
                    final_components[semantic_id]["width"] = width_m
            
            # --- MODIFICATION: Reverted to use original connections list ---
            final_layout = {
                "components": final_components,
                "connections": connections
            }
            
            # 5. Save final JSON string to state
            final_layout_json_string = json.dumps(final_layout, indent=2)
            state_delta = {
                "final_layout": final_layout_json_string
            }
            
            print("--- JsonAssemblerAgent: Assembly complete. Yielding final state. ---")
            
            # 6. Yield final event with the result in state
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=final_layout_json_string)]),
                actions=EventActions(state_delta=state_delta)
            )

        except Exception as e:
            error_msg = f"JsonAssemblerAgent: Failed during final JSON assembly. Details: {e}"
            print(f"--- {error_msg} ---")
            yield Event(author=self.name, content=types.Content(parts=[types.Part(text=error_msg)]))
            return

# --- (MODIFIED) Component Type Mapping ---
# Maps your JSON prefixes to CMSD ResourceType and ResourceClass
# per your colleague's standard
COMPONENT_TYPE_MAP = {
    "L": ("source", "RC_Source"),
    "C": ("conveyor", "RC_Conveyor"),
    "M": ("machine", "RC_Station"),
    "D": ("turntable", "RC_Turntable"), # <<< CHANGED 'station' TO 'turntable'
    "U": ("sink", "RC_Drain"),
}     

class XmlTransformerAgent(BaseAgent):
    """
    Agent 9: (UPDATED) Deterministically transforms the final JSON layout
    into the CMSD XML format required by Plant Simulation.
    
    Updates:
    - Component mappings (M, D, U) updated per user specification.
    - "side" property from connections is removed.
    - Rotation axis is '1' (anti-clockwise).
    - A static <PartType> section is now added.
    - ***MODIFIED:***
      - `_build_resource` now correctly adds <Length> and <Width>
        properties to conveyor/turntable resources.
      - `_build_layout_object` now uses the correct default dimensions
        (1x2m for Source/Drain, 2x2m for Station) and sets Height to 1.0m
        for all objects per user request.
    """
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self):
        super().__init__(
            name="XmlTransformerAgent",
            description="Transforms final layout JSON into CMSD XML format.",
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        
        print("\n--- Running Agent: XmlTransformerAgent ---")
        
        # 1. Get the JSON string from state
        final_json_str = ctx.session.state.get("final_layout")
        if not final_json_str:
            error_msg = "XmlTransformerAgent Error: 'final_layout' JSON not found in state."
            print(f"--- {error_msg} ---")
            yield Event(author=self.name, content=types.Content(parts=[types.Part(text=error_msg)]))
            return

        try:
            # 2. Parse the JSON
            layout_data = json.loads(final_json_str)
            components = layout_data.get("components", {})
            connections = layout_data.get("connections", [])

            # 3. Build the XML Structure
            cmsd_doc = ET.Element("CMSDDocument", xmlns="urn:cmsd:main")
            
            # --- Build Static Sections ---
            self._build_header(cmsd_doc)
            data_section = ET.SubElement(cmsd_doc, "DataSection")
            self._build_resource_classes(data_section)
            self._build_part_types(data_section) # <-- (NEW) Add PartType section
            
            layout = ET.SubElement(data_section, "Layout")
            ET.SubElement(layout, "Identifier").text = "FactoryLayout_Main"
            ET.SubElement(layout, "Description").text = "Main factory layout generated from ADK"

            # --- Build Dynamic Sections (Resources, LayoutObjects, Placements) ---
            print("--- XmlTransformerAgent: Starting component loop... ---")
            for comp_id, props in components.items():
                # A. Create <Resource> element
                self._build_resource(data_section, comp_id, props, connections)
                
                # B. Create <LayoutObject> element
                self._build_layout_object(data_section, comp_id, props)
                
                # C. Create <Placement> element (inside the <Layout> tag)
                self._build_placement(layout, comp_id, props)
            
            print(f"--- XmlTransformerAgent: Processed {len(components)} components. ---")

            # 4. Serialize to XML String & Pretty-Print
            xml_string = self._pretty_print_xml(cmsd_doc)

            # --- 5. Save XML to a local file ---
            file_location = "./cmsd_output.xml"
            try:
                with open(file_location, "w", encoding="utf-8") as f:
                    f.write(xml_string)
                print(f"--- XmlTransformerAgent: Successfully saved XML to {file_location} ---")
            except Exception as e:
                print(f"--- XmlTransformerAgent: WARNING - Failed to save XML file. Details: {e} ---")

            # 6. Yield Final Event
            print("--- XmlTransformerAgent: XML Transformation complete. ---")
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=xml_string)]),
                actions=EventActions(
                    state_delta={"final_xml_layout": xml_string} # Save to state
                )
            )

        except Exception as e:
            error_msg = f"XmlTransformerAgent Error: Failed to transform JSON to XML. Details: {e}"
            print(f"--- {error_msg} ---")
            yield Event(author=self.name, content=types.Content(parts=[types.Part(text=error_msg)]))
            return

    # --- XML Building Helper Functions ---

    def _build_header(self, parent: ET.Element):
        """Builds the static <HeaderSection>"""
        header = ET.SubElement(parent, "HeaderSection")
        ET.SubElement(header, "DocumentIdentifier").text = "Generated_FactoryLayout_001"
        ET.SubElement(header, "Description").text = "Factory layout generated by ADK XmlTransformerAgent"
        ET.SubElement(header, "Version").text = "1.0"
        ET.SubElement(header, "CreationTime").text = "2025-01-01T12:00:00Z" # Placeholder
        unit_defaults = ET.SubElement(header, "UnitDefaults")
        ET.SubElement(unit_defaults, "TimeUnit").text = "second"
        ET.SubElement(unit_defaults, "LengthUnit").text = "meter"
        ET.SubElement(unit_defaults, "WeightUnit").text = "kilogram"

    def _build_resource_classes(self, parent: ET.Element):
        """(MODIFIED) Builds the static <ResourceClass> definitions per user spec."""
        classes = [
            ("RC_Source", "source", "Production Source Class"),
            ("RC_Conveyor", "conveyor", "Conveyor Class"),
            ("RC_Station", "machine", "Processing Station Class"), # For 'M' components
            ("RC_Turntable", "turntable", "Turntable Class"), # <<< CHANGED 'station' TO 'turntable'
            ("RC_Drain", "sink", "Production Drain Class"),     # For 'U' components
        ]
        for id, type, desc in classes:
            rc = ET.SubElement(parent, "ResourceClass")
            ET.SubElement(rc, "Identifier").text = id
            ET.SubElement(rc, "ResourceType").text = type
            ET.SubElement(rc, "Name").text = desc

    def _build_part_types(self, parent: ET.Element):
        """(NEW) Builds a static <PartType> definition as requested."""
        part_type = ET.SubElement(parent, "PartType")
        ET.SubElement(part_type, "Identifier").text = "DefaultPart"
        ET.SubElement(part_type, "Name").text = "Default Part"
        prop = ET.SubElement(part_type, "Property")
        ET.SubElement(prop, "Name").text = "PartClass"
        ET.SubElement(prop, "Value").text = "General"

    def _get_resource_type_and_class(self, comp_id: str) -> Tuple[str, str]:
        """(MODIFIED) Maps a component ID prefix to its CMSD ResourceType and ResourceClass."""
        prefix = comp_id[0].upper() # Get first letter
        if prefix not in COMPONENT_TYPE_MAP:
            # Stricter error checking
            raise ValueError(f"Unknown component prefix: '{prefix}' for component ID '{comp_id}'. No mapping found.")
        return COMPONENT_TYPE_MAP[prefix]

    def _parse_property(self, key: str, value: str) -> Tuple[str, str, Optional[str]]:
        """Parses value and unit from strings like '8 sec' or '0.2 m/s'."""
        match = re.match(r'^\s*([\d\.]+)\s*([\w/°]+)', str(value))
        if match:
            val_str, unit_str = match.groups()
            
            if unit_str.lower() in ["s", "sec", "second"]:
                unit_str = "second"
            elif unit_str.lower() in ["m/s", "meter/second"]:
                unit_str = "meter/second"
                
            return val_str, unit_str, key
        
        return str(value), None, key
    
    def _build_resource(self, parent: ET.Element, comp_id: str, props: Dict[str, Any], all_connections: List[Dict[str, Any]]):
        """
        (MODIFIED) Builds a single <Resource> element.
        - Now explicitly adds <Length> and <Width> properties if they exist
          (i.e., for conveyors/turntables), as requested.
        """
        resource = ET.SubElement(parent, "Resource")
        ET.SubElement(resource, "Identifier").text = comp_id
        ET.SubElement(resource, "Name").text = comp_id
        
        res_type, res_class = self._get_resource_type_and_class(comp_id)
        ET.SubElement(resource, "ResourceType").text = res_type
        rc_elem = ET.SubElement(resource, "ResourceClass")
        ET.SubElement(rc_elem, "ResourceClassIdentifier").text = res_class

        # Add other properties from JSON
        for key, value in props.items():
            if key in ["origin", "orientation"]:
                continue
            
            # --- NEW: Handle Length/Width explicitly for Resource properties ---
            # This fixes Problem A
            if key == "length":
                prop_elem = ET.SubElement(resource, "Property")
                ET.SubElement(prop_elem, "Name").text = "Length"
                ET.SubElement(prop_elem, "Unit").text = "meter"
                ET.SubElement(prop_elem, "Value").text = str(value)
                continue # Skip the generic parser
            
            if key == "width":
                prop_elem = ET.SubElement(resource, "Property")
                ET.SubElement(prop_elem, "Name").text = "Width"
                ET.SubElement(prop_elem, "Unit").text = "meter"
                ET.SubElement(prop_elem, "Value").text = str(value)
                continue # Skip the generic parser
            # --- END NEW ---

            # Generic parser for other properties (speed, Proc time, etc.)
            val_str, unit_str, name_str = self._parse_property(key, value)
            
            prop_elem = ET.SubElement(resource, "Property")
            ET.SubElement(prop_elem, "Name").text = name_str
            if unit_str:
                ET.SubElement(prop_elem, "Unit").text = unit_str
            ET.SubElement(prop_elem, "Value").text = val_str

        # Find and add outgoing connections
        outgoing_connections = [c for c in all_connections if c.get("from") == comp_id]
        if outgoing_connections:
            group_def = ET.SubElement(resource, "GroupDefinition")
            ET.SubElement(group_def, "Identifier").text = f"GD_{comp_id}_Output"
            
            for conn in outgoing_connections:
                to_comp = conn.get("to")
                if not to_comp:
                    continue
                    
                conn_elem = ET.SubElement(group_def, "Connection")
                ET.SubElement(conn_elem, "ConnectionIdentifier").text = f"Conn_{comp_id}_to_{to_comp}"
                target_res = ET.SubElement(conn_elem, "TargetResource")
                ET.SubElement(target_res, "ResourceIdentifier").text = to_comp
                
                # --- "side" property logic has been removed ---

    def _build_layout_object(self, parent: ET.Element, comp_id: str, props: Dict[str, Any]):
        """
        (MODIFIED) Builds a single <LayoutObject> element.
        - Now applies default dimensions (1x2, 2x2) per user request.
        - Sets Height to 1.0 for all objects per user request.
        """
        lo = ET.SubElement(parent, "LayoutObject")
        ET.SubElement(lo, "Identifier").text = f"LO_{comp_id}"
        
        assoc_res = ET.SubElement(lo, "AssociatedResource")
        ET.SubElement(assoc_res, "ResourceIdentifier").text = comp_id
        
        prefix = comp_id[0].upper()
        
        # --- NEW LOGIC FOR DIMENSIONS (Fixes Problem B & C) ---
        width_val = "1.0"  # Default
        depth_val = "1.0"  # Default
        height_val = "1.0" # User request: 1m for EVERY object
        
        if prefix == 'C' or prefix == 'D':
            # Conveyors/Turntables use their calculated dimensions
            # The XML standard uses 'Width' for length and 'Depth' for width
            width_val = str(props.get("length", 1.0))
            depth_val = str(props.get("width", 1.0))
            # Height remains "1.0" per user's "every object" rule
        elif prefix == 'L': # Source
            width_val = "1.0"
            depth_val = "2.0"
        elif prefix == 'U': # Drain
            width_val = "1.0"
            depth_val = "2.0"
        elif prefix == 'M': # Station
            width_val = "2.0"
            depth_val = "2.0"
        # else:
            # Fallback for any other type (e.g., Buffer if added)
            # will use the defaults (1.0, 1.0, 1.0)
        # --- END NEW LOGIC ---

        boundary = ET.SubElement(lo, "Boundary")
        ET.SubElement(boundary, "Width").text = width_val
        ET.SubElement(boundary, "Depth").text = depth_val
        ET.SubElement(boundary, "Height").text = height_val
        ET.SubElement(boundary, "Unit").text = "meter"

    def _map_orientation(self, angle_deg: int) -> Tuple[str, str, str, str]:
        """(MODIFIED) Maps a simple degree to the CMSD rotation tuple."""
        # Per your clarification: [Angle, 0, 0, 1] for anti-clockwise
        return (str(angle_deg), "0", "0", "1")

    def _build_placement(self, parent_layout: ET.Element, comp_id: str, props: Dict[str, Any]):
        """Builds a single <Placement> element inside the main <Layout>."""
        placement = ET.SubElement(parent_layout, "Placement")
        ET.SubElement(placement, "LayoutElementIdentifier").text = f"LO_{comp_id}"
        
        loc = ET.SubElement(placement, "Location")
        ET.SubElement(loc, "X").text = str(props.get("origin", [0,0])[0])
        ET.SubElement(loc, "Y").text = str(props.get("origin", [0,0])[1])
        ET.SubElement(loc, "Z").text = "0.0" 
        
        rot_tuple = self._map_orientation(props.get("orientation", 0))
        rotation = ET.SubElement(placement, "Rotation")
        ET.SubElement(rotation, "Angle").text = rot_tuple[0]
        ET.SubElement(rotation, "X").text = rot_tuple[1]
        ET.SubElement(rotation, "Y").text = rot_tuple[2]
        ET.SubElement(rotation, "Z").text = rot_tuple[3]

    def _pretty_print_xml(self, element: ET.Element) -> str:
        """Returns a pretty-printed XML string from an ElementTree element."""
        rough_string = ET.tostring(element, 'utf-8')
        reparsed = xml.dom.minidom.parseString(rough_string)
        return reparsed.toprettyxml(indent="    ", encoding="UTF-8").decode("utf-8")
    
class PlantSimBuilderAgent(BaseAgent):
    """
    Agent that takes CMSD XML data and orchestrates Plant Simulation 
    to build the visual model using the existing COM/SimTalk workflow.
    """
    
    def __init__(self):
        super().__init__(
            name="PlantSimBuilderAgent",
            description="Orchestrates Plant Simulation to build a model from CMSD XML.",
        )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        
        print("\n--- Running Agent: PlantSimBuilderAgent ---")
        
        # 1. Retrieve XML Content from State
        xml_content = ctx.session.state.get("final_xml_layout")
        
        if not xml_content:
            error_msg = "PlantSimBuilderAgent Error: 'final_xml_layout' not found in state."
            print(f"--- {error_msg} ---")
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=error_msg)])
            )
            return

        # 2. Save XML to File (Mirroring main.py logic)
        try:
            # Ensure output directory exists
            xml_output_dir = Path(config.cmsd_xml["output_dir"])
            xml_output_dir.mkdir(parents=True, exist_ok=True)

            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            prefix = config.cmsd_xml["file_prefix"]
            ext = config.cmsd_xml["file_extension"]
            xml_filename = f"{prefix}{timestamp}{ext}"
            xml_file_path = xml_output_dir / xml_filename

            # Write XML file
            with open(xml_file_path, "w", encoding="utf-8") as f:
                f.write(xml_content)
            
            print(f"--- PlantSimBuilderAgent: Saved XML to {xml_file_path} ---")

            # 3. Update active_xml_path.txt (Critical for interpreter.py)
            # interpreter.py reads this specific file to know what to process
            active_path_file = Path("active_xml_path.txt")
            with open(active_path_file, "w", encoding="utf-8") as f:
                f.write(str(xml_file_path.resolve()))
            
            print(f"--- PlantSimBuilderAgent: Updated {active_path_file} ---")

        except Exception as e:
            error_msg = f"PlantSimBuilderAgent Error: Failed to save XML files. Details: {e}"
            yield Event(author=self.name, content=types.Content(parts=[types.Part(text=error_msg)]))
            return

        # 4. Orchestrate Plant Simulation
        # This logic mirrors the main() function in your colleague's script
        try:
            if os.getenv("ASMG_DRY_RUN", "0") == "1":
                msg = "ASMG_DRY_RUN enabled: Skipping Plant Simulation connection."
                print(f"--- {msg} ---")
                yield Event(author=self.name, content=types.Content(parts=[types.Part(text=msg)]))
                return

            prog_id = config.plant_simulation["prog_id"]
            template_path = config.plant_simulation["template_path"]
            dest_dir = config.plant_simulation["dest_dir"]
            
            # A. Connect
            print(f"--- PlantSimBuilderAgent: Connecting to Plant Sim ({prog_id})... ---")
            plant_sim = plant_sim_controller.connect_to_plant_simulation(prog_id)
            
            plant_sim.setVisible(True)
            plant_sim.setTrustModels(True)

            # B. Setup and Load Template
            # Note: setup_and_load_model handles copying the template to a new file
            model_path = plant_sim_controller.setup_and_load_model(
                plant_sim, 
                str(template_path), 
                str(dest_dir)
            )
            
            if not model_path:
                raise Exception("Failed to load simulation model template.")

            # C. Execute SimTalk to trigger Python Interpreter
            # This matches the logic in run_simulation() from main.py
            print("--- PlantSimBuilderAgent: Triggering Interpreter via SimTalk... ---")
            
            # Construct SimTalk code exactly as main.py does
            simtalk_code = f'''
                setPythonDLLPath("{config.simtalk["python_dll_path"]}");
                executePythonFile("{config.simtalk["interpreter_path"]}")'''
            
            success = plant_sim_controller.execute_simtalk(plant_sim, simtalk_code)

            if not success:
                raise Exception("SimTalk execution failed.")

            # D. Wait and Save
            # Note: Since executePythonFile might be synchronous in PlantSim depending on config,
            # we assume it blocks until interpreter.py is done.
            
            # Save the result
            plant_sim_controller.save(plant_sim, model_path)
            print(f"--- PlantSimBuilderAgent: Model saved to {model_path} ---")

            # Cleanup
            # Uncomment if you want to close Plant Sim automatically after generation
            # plant_sim_controller.quit_simulation(plant_sim)

            success_msg = f"Plant Simulation Model successfully generated at: {model_path}"
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=success_msg)])
            )

        except Exception as e:
            import traceback
            traceback.print_exc()
            error_msg = f"PlantSimBuilderAgent Critical Error: {e}"
            yield Event(author=self.name, content=types.Content(parts=[types.Part(text=error_msg)]))

class OrchestratorAgent(SequentialAgent):
    """
    Main entry point and controller of the entire workflow.
    (MODIFIED) This agent now orchestrates the new, two-loop flow.
    """

    def __init__(self):
        super().__init__(
            name="OrchestratorAgent",
            description="A sequential agent that analyzes layout diagrams and outputs JSON.",
            sub_agents=[
                LayoutParserAgent(),
                SectionPlannerAgent(),
                StateInitializerAgent(),       # Sets up state for the loop
                ConnectionGeneratorAgent(),
                OrientationFinderLoopAgent(),  # loop
                TextExtractorAgent(),        # <-- NEW
                TextDataAggregatorAgent(),
                JsonAssemblerAgent(),          # Final assembly
                XmlTransformerAgent(),
                PlantSimBuilderAgent()
            ],
        )