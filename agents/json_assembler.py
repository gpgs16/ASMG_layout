from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types
from typing import AsyncGenerator
import json
import re

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
