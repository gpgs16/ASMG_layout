from google.adk.agents import SequentialAgent
from .layout_parser import LayoutParserAgent
from .section_planner import SectionPlannerAgent
from .connection_generator import ConnectionGeneratorAgent
from .orientation_agents import OrientationFinderAgent
from .text_extraction_agents import TextExtractorAgent, TextDataAggregatorAgent
from .json_assembler import JsonAssemblerAgent
from .xml_transformer import XmlTransformerAgent
from .plant_sim_builder import PlantSimBuilderAgent

class OrchestratorAgent(SequentialAgent):
    """
    Main entry point and controller of the entire workflow.
    This agent orchestrates the sequence of sub-agents in the provided order to analyze layout diagrams
    """

    def __init__(self):
        super().__init__(
            name="OrchestratorAgent",
            description="A sequential agent that analyzes layout diagrams and outputs JSON.",
            sub_agents=[
                LayoutParserAgent(),
                SectionPlannerAgent(),
                ConnectionGeneratorAgent(),
                OrientationFinderAgent(),
                TextExtractorAgent(),        
                TextDataAggregatorAgent(),
                JsonAssemblerAgent(),          
                XmlTransformerAgent(),
                PlantSimBuilderAgent()
            ],
        )
