from google.adk.agents import SequentialAgent
from .layout_parser import LayoutParserAgent
from .section_planner import SectionPlannerAgent
from .state_initializer import StateInitializerAgent
from .connection_generator import ConnectionGeneratorAgent
from .orientation_agents import OrientationFinderLoopAgent
from .text_extraction_agents import TextExtractorAgent, TextDataAggregatorAgent
from .json_assembler import JsonAssemblerAgent
from .xml_transformer import XmlTransformerAgent
from .plant_sim_builder import PlantSimBuilderAgent

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
