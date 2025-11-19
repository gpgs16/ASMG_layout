from google.adk.agents import Agent
from google.genai import types
from .common import MODEL_PRO

class SectionPlannerAgent(Agent):
    """
    Analyzes the layout to identify ALL 11 flow paths and their component lists.
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
