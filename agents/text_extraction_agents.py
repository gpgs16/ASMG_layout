from google.adk.agents import Agent, BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types
from typing import AsyncGenerator
import json
import re
from .common import MODEL_PRO

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
