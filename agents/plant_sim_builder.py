from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types
from typing import AsyncGenerator
import os
from pathlib import Path
from datetime import datetime
from .common import config
from ..src import plant_sim_controller

class PlantSimBuilderAgent(BaseAgent):
    """
    Agent that takes CMSD XML data and orchestrates Plant Simulation 
    to build the visual model.
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

        # 2. Save XML and Update active_xml_path.txt
        try:
            # Ensure output directory exists
            xml_output_dir = Path(config.cmsd_xml["output_dir"])
            xml_output_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            prefix = config.cmsd_xml["file_prefix"]
            ext = config.cmsd_xml["file_extension"]
            xml_filename = f"{prefix}{timestamp}{ext}"
            xml_file_path = xml_output_dir / xml_filename

            with open(xml_file_path, "w", encoding="utf-8") as f:
                f.write(xml_content)
            
            print(f"--- PlantSimBuilderAgent: Saved XML to {xml_file_path} ---")

            # Update active_xml_path.txt where interpreter.py expects it
            # The interpreter looks for it in the auto_sim directory (project_root)
            # We need to find the project root. Assuming this file is in agents/plant_sim_builder.py
            # and project root is ../..
            
            # Assuming auto_sim_plant_sim is the root where active_xml_path.txt resides.
            # agents/plant_sim_builder.py -> parent -> agents -> parent -> auto_sim_plant_sim
            project_root = Path(__file__).parent.parent
            active_path_file = project_root / "active_xml_path.txt"
            
            with open(active_path_file, "w", encoding="utf-8") as f:
                f.write(str(xml_file_path.resolve()))
            
            print(f"--- PlantSimBuilderAgent: Updated {active_path_file} with path: {xml_file_path.resolve()} ---")

        except Exception as e:
            error_msg = f"PlantSimBuilderAgent Error: Failed to save XML files. Details: {e}"
            yield Event(author=self.name, content=types.Content(parts=[types.Part(text=error_msg)]))
            return

        # 3. Setup Plant Simulation (Connect & Load Template)
        try:
            if os.getenv("ASMG_DRY_RUN", "0") == "1":
                msg = "ASMG_DRY_RUN enabled: Skipping Plant Simulation execution."
                yield Event(author=self.name, content=types.Content(parts=[types.Part(text=msg)]))
                return

            prog_id = config.plant_simulation["prog_id"]
            template_path = config.plant_simulation["template_path"]
            dest_dir = config.plant_simulation["dest_dir"]
            
            print(f"--- PlantSimBuilderAgent: Connecting to Plant Sim ({prog_id})... ---")
            plant_sim = plant_sim_controller.connect_to_plant_simulation(prog_id)
            
            if not plant_sim:
                raise Exception("Failed to connect to Plant Simulation.")
            
            plant_sim.setVisible(True)
            plant_sim.setTrustModels(True)

            # Load the template (copying it to destination first)
            model_path = plant_sim_controller.setup_and_load_model(
                plant_sim, 
                str(template_path), 
                str(dest_dir)
            )
            
            if not model_path:
                raise Exception("Failed to load simulation model template.")

            print(f"--- PlantSimBuilderAgent: Successfully loaded model: {model_path} ---")

            # Execute interpreter.py INSIDE Plant Simulation using SimTalk
            # This matches the implementation in main.py
            print("--- PlantSimBuilderAgent: Executing SimTalk commands to run interpreter inside Plant Sim... ---")
            
            # Verify paths exist before executing
            python_dll_path = config.simtalk["python_dll_path"]
            interpreter_path = config.simtalk["interpreter_path"]
            
            print(f"--- PlantSimBuilderAgent: Python DLL Path: {python_dll_path} ---")
            print(f"--- PlantSimBuilderAgent: Interpreter Path: {interpreter_path} ---")
            
            if not Path(python_dll_path).exists():
                raise Exception(f"Python DLL not found at: {python_dll_path}")
            
            if not Path(interpreter_path).exists():
                raise Exception(f"Interpreter script not found at: {interpreter_path}")
            
            # STEP 1: Try setting Python DLL path first
            print("--- PlantSimBuilderAgent: Step 1 - Setting Python DLL path... ---")
            simtalk_set_dll = f'setPythonDLLPath("{python_dll_path}");'
            print(f"SimTalk code: {simtalk_set_dll}")
            
            if not plant_sim_controller.execute_simtalk(plant_sim, simtalk_set_dll):
                error_msg = "Failed to set Python DLL path. Check if the DLL is accessible and Plant Simulation supports this Python version."
                print(f"--- {error_msg} ---")
                print("--- PlantSimBuilderAgent: Keeping Plant Simulation open for debugging. ---")
                yield Event(
                    author=self.name,
                    content=types.Content(parts=[types.Part(text=error_msg)])
                )
                return
            
            print("--- PlantSimBuilderAgent: Successfully set Python DLL path. ---")
            
            # STEP 2: Try executing the Python file
            print("--- PlantSimBuilderAgent: Step 2 - Executing Python interpreter file... ---")
            simtalk_exec_file = f'executePythonFile("{interpreter_path}");'
            print(f"SimTalk code: {simtalk_exec_file}")
            
            if not plant_sim_controller.execute_simtalk(plant_sim, simtalk_exec_file):
                error_msg = "Failed to execute interpreter.py. Check Plant Simulation console for Python errors."
                print(f"--- {error_msg} ---")
                print("--- PlantSimBuilderAgent: Keeping Plant Simulation open for debugging. Please check the console. ---")
                # Don't close Plant Sim on error so user can see the error message
                yield Event(
                    author=self.name,
                    content=types.Content(parts=[types.Part(text=error_msg)])
                )
                return
            
            print("--- PlantSimBuilderAgent: SimTalk commands executed successfully. ---")

            # Save the model after setup
            if not plant_sim_controller.save(plant_sim, model_path):
                raise Exception("Failed to save model after setup.")

            print(f"--- PlantSimBuilderAgent: Model saved to {model_path} ---")

            success_msg = f"Plant Simulation Model successfully generated at: {model_path}"
            yield Event(
                author=self.name,
                content=types.Content(parts=[types.Part(text=success_msg)])
            )

        except Exception as e:
            import traceback
            traceback.print_exc()
            error_msg = f"PlantSimBuilderAgent Critical Error: {e}"
            print(f"--- {error_msg} ---")
            print("--- PlantSimBuilderAgent: Keeping Plant Simulation open for debugging. Please check the console. ---")
            yield Event(author=self.name, content=types.Content(parts=[types.Part(text=error_msg)]))
