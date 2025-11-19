from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types
from typing import AsyncGenerator
from ..tools import ComponentDetector
from .common import logger

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
            print(f"--- {error_message} ---")
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
