from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types
from typing import AsyncGenerator
import json
import re

class ConnectionGeneratorAgent(BaseAgent):
    """
    Code-based agent to deterministically generate the connection list from the ordered flow_sections.
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

        # If flow_sections is not already parsed, try to parse it from flow_sections_raw
        if not flow_sections:
            flow_sections_raw = ctx.session.state.get("flow_sections_raw")
            if flow_sections_raw:
                try:
                    print("--- ConnectionGeneratorAgent: Parsing 'flow_sections_raw'... ---")
                    json_match = re.search(r'\[.*\]', flow_sections_raw, re.DOTALL)
                    if json_match:
                        json_string = json_match.group(0)
                        flow_sections = json.loads(json_string)
                        print(f"--- ConnectionGeneratorAgent: Successfully parsed {len(flow_sections)} sections. ---")
                    else:
                        print("--- ConnectionGeneratorAgent: No JSON list found in 'flow_sections_raw'. ---")
                except Exception as e:
                    print(f"--- ConnectionGeneratorAgent: Failed to parse 'flow_sections_raw'. Details: {e} ---")

        if not flow_sections:
            error_msg = "ConnectionGeneratorAgent Error: 'flow_sections' not found in state and could not be parsed."
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
            # Also save flow_sections if we parsed it
            state_delta = {
                "connections": master_connection_list,
                "flow_sections": flow_sections 
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
