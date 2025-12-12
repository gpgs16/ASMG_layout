# Automatic Simulation Model Generation (ASMG)

An intelligent agent system built with Google ADK that analyzes factory layout diagrams and automatically generates Plant Simulation models. The system uses computer vision and LLM reasoning to detect components, extract relationships, and produce executable simulation models.

## Table of Contents
- [Overview](#overview)
- [System Architecture](#system-architecture)
- [Folder Structure](#folder-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the System](#running-the-system)
- [How It Works](#how-it-works)
- [Troubleshooting](#troubleshooting)

---

## License

This project is licensed under the MIT License. See the LICENSE file for details.

---

## Overview

This system processes images of factory layout diagrams and:
1. Detects components (sources, conveyors, machines, diverters, sinks) using computer vision
2. Extracts text labels, dimensions, and properties using OCR
3. Determines component orientations and connections using multi-agent LLM reasoning
4. Generates CMSD XML format files
5. Automatically builds Plant Simulation models with proper layout and connections

### Supported Components
- **L** - Source (production sources)
- **C** - Conveyor (transport lines)
- **M** - Machine/Station (processing stations)
- **D** - Diverter/Turntable (routing points)
- **U** - Sink/Drain (production endpoints)

---

## System Architecture

### Agent Pipeline
The system uses a sequential multi-agent architecture, orchestrated by the **OrchestratorAgent**:

1. **LayoutParserAgent** - Detects components using computer vision (ComponentDetector tool)
2. **SectionPlannerAgent** - Identifies all flow paths through the layout
3. **ConnectionGeneratorAgent** - Generates connection relationships from flow paths
4. **OrientationFinderAgent** - Determines component orientations. This is a composite agent containing:
    - **OrientationLoopInitializerAgent** - Sets up the loop context
    - **OrientationFinderLoop** - Iterates through sections to find orientations using:
        - **SectionOrientationFinderAgent** - Finds orientations for components in a specific section
        - **OrientationAggregatorAgent** - Collects results
        - **OrientationLoopControllerAgent** - Manages loop iteration
5. **TextExtractorAgent** - Extracts textual properties (speeds, times, dimensions)
6. **TextDataAggregatorAgent** - Aggregates and validates extracted text data
7. **JsonAssemblerAgent** - Assembles all data into structured JSON format
8. **XmlTransformerAgent** - Transforms JSON to CMSD XML format
9. **PlantSimBuilderAgent** - Executes Plant Simulation to build the visual model

### Key Tools
- **ComponentDetector** - OpenCV-based contour detection + EasyOCR for component identification

---

## Folder Structure

```
auto_sim/
├── .env                          # Environment variables (NOT in repo - must create)
├── active_xml_path.txt          # Path to current XML file (auto-generated)
├── agent.py                     # Re-exports agents for backward compatibility
├── tools.py                     # Computer vision and OCR tools
├── requirements.txt             # Python dependencies
├── __init__.py                  # Package initialization
│
├── agents/                      # Modularized agent definitions
│   ├── __init__.py
│   ├── common.py                # Shared constants and configs
│   ├── orchestrator.py          # Main OrchestratorAgent
│   ├── layout_parser.py
│   ├── section_planner.py
│   ├── connection_generator.py
│   ├── orientation_agents.py    # Orientation finding logic (Loop & Sub-agents)
│   ├── text_extraction_agents.py
│   ├── json_assembler.py
│   ├── xml_transformer.py
│   └── plant_sim_builder.py
│
├── config/
│   ├── config.yaml              # Main configuration file (MUST UPDATE PATHS)
│   ├── config_loader.py         # Configuration loading logic
│   ├── plantsim_mapping.yaml    # Plant Simulation object mappings
│   └── xml_mapping.yaml         # CMSD XML mappings
│
├── data/
│   ├── CMSD_XML_Output/         # Generated XML files (timestamped)
│   ├── Simulation_Model_Template/ # Template model (.spp file)
│   └── Simulation_Model_Output/ # Generated models (timestamped)
│
├── interpreter/
│   ├── interpreter.py           # Main XML-to-PlantSim interpreter
│   ├── xml_parser.py            # CMSD XML parser
│   ├── data_models.py           # Data structures and validation
│   ├── mapping_engine.py        # Maps XML to Plant Simulation objects
│   └── plantsim_interface.py    # COM interface to Plant Simulation
│
└── src/
    └── plant_sim_controller.py  # Plant Simulation COM automation
```

---

## Prerequisites

### Required Software
1. **Python 3.12** (recommended) or Python 3.10+
2. **Siemens Plant Simulation** (with COM automation support)
3. **Google Cloud SDK** (if using Vertex AI) - [Install gcloud CLI](https://cloud.google.com/sdk/docs/install)
4. **Git** (for cloning the repository)

### Windows-Specific Requirements
- Plant Simulation must be installed and COM automation enabled
- Python must have `pywin32` package for COM communication

---

## Installation

### 1. Clone the Repository
```bash
git clone https://github.com/gpgs16/Automatic_simulation_model_generation_from_layout_image.git
cd Automatic_simulation_model_generation_from_layout_image
```

### 2. Create Python Environment
Using conda (recommended):
```bash
conda create -n auto_sim python=3.12
conda activate auto_sim
```

Using venv:
```bash
python -m venv auto_sim_env
# On Windows:
auto_sim_env\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

Required packages include:
- `google-adk` - Google Agent Development Kit
- `opencv-python` - Computer vision
- `easyocr` - Optical character recognition
- `Pillow` - Image processing
- `numpy` - Numerical operations
- `python-dotenv` - Environment variable management
- `pywin32` - Windows COM automation
- `pytz` - Timezone handling
- `defusedxml` - Secure XML parsing

---

## Configuration

### 1. Create .env File

**IMPORTANT**: The `.env` file is NOT included in the repository and must be created manually.

Create a file named `.env` in the `auto_sim/` directory:

```bash
# .env file location: auto_sim/.env
```

#### Option A: Using Google AI Studio (API Key)

For direct API access with API key:

```dotenv
# Use AI Studio with API Key
GOOGLE_GENAI_USE_VERTEXAI=FALSE
GOOGLE_API_KEY=your_actual_api_key_here
```

To get an API key:
1. Go to [Google AI Studio](https://aistudio.google.com/api-keys)
2. Create a new API key
3. Copy and paste it into the `.env` file

#### Option B: Using Vertex AI (GCP Project)

For Vertex AI with Google Cloud Project:

```dotenv
# Use Vertex AI
GOOGLE_GENAI_USE_VERTEXAI=TRUE
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
```

**Prerequisites for Vertex AI**:
1. Install Google Cloud CLI: [Installation Guide](https://cloud.google.com/sdk/docs/install)
2. Authenticate with your Google Cloud account:
   ```bash
   gcloud auth application-default login
   ```
3. Set your project:
   ```bash
   gcloud config set project your-gcp-project-id
   ```

### 2. Update config.yaml

Edit `config/config.yaml` and update all paths to match your system:

```yaml
plant_simulation:
  prog_id: "Tecnomatix.PlantSimulation.RemoteControl"
  template_path: "C:/YOUR_PATH/auto_sim/data/Simulation_Model_Template/ASMG_model_template.spp"
  dest_dir: "C:/YOUR_PATH/auto_sim/data/Simulation_Model_Output"
  object_templates:
    source: "Source"
    drain: "Drain"
    station: "Station"
    conveyor: "Conveyor"
    buffer: "Buffer"
    turntable: "TurnTable"
  object_paths:
    model_frame: ".Models.Model"
    connector: ".MaterialFlow.Connector"
    user_object: ".UserObjects.{template_name}"

cmsd_xml:
  output_dir: "C:/YOUR_PATH/auto_sim/data/CMSD_XML_Output"
  file_prefix: "cmsd_xml_"
  file_extension: ".xml"

simtalk:
  python_dll_path: "C:/YOUR_PATH/anaconda3/envs/auto_sim/python312.dll"
  interpreter_path: "C:/YOUR_PATH/auto_sim/interpreter/interpreter.py"

logging:
  level: "INFO"
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
```

**Key paths to update**:
- `template_path` - Absolute path to the Plant Simulation template model
- `dest_dir` - Where generated models will be saved
- `output_dir` - Where XML files will be saved
- `python_dll_path` - Path to your Python DLL (must match your conda/venv environment)
- `interpreter_path` - Absolute path to `interpreter/interpreter.py`

**To find your Python DLL path**:
```bash
# On Windows with conda:
where python
# Look for python312.dll in the same directory or parent\DLLs\

# Or run in Python:
python -c "import sys; print(sys.executable)"
```

---

## Running the System

### Method 1: Using ADK Web UI (Recommended)

The web interface provides an interactive environment with visual feedback:

```bash
# Navigate to the parent directory of auto_sim
cd C:/YOUR_PATH/adk_agents

# Start the web server
adk web auto_sim
```

This will:
- Start a web server
- Open your browser automatically
- Provide an interactive chat interface

**To use**:
1. Upload a factory layout image
2. The agent will process it automatically through all stages
3. View results, intermediate outputs, and generated files in the UI

**Troubleshooting Web UI**:
- Ensure you're running the command from the **parent** folder of `auto_sim`
- Check that `__init__.py` defines `root_agent = agent.OrchestratorAgent()`

### Method 2: Using ADK Run (Terminal)

For programmatic execution without UI:

```bash
# Navigate to the auto_sim directory
cd C:/Users/gpgs16/Documents/adk_agents/auto_sim

# Run the agent
adk run .
```

Or from parent directory:
```bash
adk run auto_sim
```

### Method 3: Python Script Integration

You can also import and use the agent programmatically:

```python
from auto_sim.agent import OrchestratorAgent
from google.genai import types

# Initialize agent
agent = OrchestratorAgent()

# Load image
with open("layout.png", "rb") as f:
    image_data = f.read()

# Create user content
user_content = types.Content(
    parts=[types.Part.from_bytes(data=image_data, mime_type="image/png")]
)

# Process (async)
# [Implementation details depend on your use case]
```

---

## How It Works

### Step-by-Step Workflow

#### 1. Image Upload
User provides a factory layout diagram image (PNG, JPG)

#### 2. Component Detection
- **ComponentDetector** tool uses OpenCV to detect rectangular contours
- Applies adaptive thresholding and morphological operations
- Filters by size to identify valid components
- Uses EasyOCR to read component labels (L1, C1, M1, etc.)

#### 3. Flow Path Analysis
- **SectionPlannerAgent** analyzes arrow directions in the image
- Identifies main conveyor paths and workstation loops
- Determines material flow sequences

#### 4. Connection Generation
- **ConnectionGeneratorAgent** creates connection list from ordered flow paths
- Establishes "from-to" relationships between components

#### 5. Orientation Determination
- **OrientationFinderAgent** orchestrates the process
- **OrientationLoopInitializerAgent** prepares the loop
- **SectionOrientationFinderAgent** processes each section iteratively
- Uses visual arrow cues to determine orientation:
  - 0° = Left-to-Right
  - 90° = Bottom-to-Top
  - 180° = Right-to-Left
  - 270° = Top-to-Bottom

#### 6. Property Extraction
- **TextExtractorAgent** scans for textual properties
- Extracts: speeds, processing times, dimensions
- Associates properties with specific components

#### 7. JSON Assembly
- **JsonAssemblerAgent** combines all data
- Calculates coordinates and transformations
- Produces structured JSON with components and connections

#### 8. XML Transformation
- **XmlTransformerAgent** converts JSON to CMSD XML standard
- Includes: Resources, LayoutObjects, Placements, Connections

#### 9. Model Building
- **PlantSimBuilderAgent** saves XML to `data/CMSD_XML_Output/`
- Updates `active_xml_path.txt` with new XML location
- Connects to Plant Simulation via COM
- Executes `interpreter.py` inside Plant Simulation using SimTalk
- Interpreter parses XML and creates visual model objects

#### 10. Output
- Generated XML file in `data/CMSD_XML_Output/`
- Generated Plant Simulation model in `data/Simulation_Model_Output/`
- Model contains all components with proper positions, connections, and properties

---

## Troubleshooting

### Environment Issues

**Problem**: `GOOGLE_API_KEY` or `GOOGLE_CLOUD_PROJECT` not found
- **Solution**: Ensure `.env` file exists and contains the correct variables
- Check that `.env` is in the `auto_sim/` directory (not in subdirectories)

**Problem**: `gcloud` command not found (when using Vertex AI)
- **Solution**: Install Google Cloud SDK and ensure it's in your PATH
- Run `gcloud auth application-default login` after installation

### Plant Simulation Issues

**Problem**: "Failed to connect to Plant Simulation"
- **Solution**: 
  - Ensure Plant Simulation is installed
  - Check that COM automation is enabled in Plant Simulation
  - Verify the `prog_id` in `config.yaml` matches your installation

**Problem**: "Python DLL not found"
- **Solution**:
  - Find your Python DLL: `python -c "import sys; print(sys.executable)"`
  - Update `python_dll_path` in `config.yaml`
  - Ensure the DLL matches your active Python environment

**Problem**: "Failed to execute interpreter.py"
- **Solution**:
  - Check Plant Simulation console for Python errors
  - Verify `interpreter_path` in `config.yaml` is correct (absolute path)

---

## Additional Resources

- [Google ADK Documentation](https://google.github.io/adk-docs/)

---

## Funding Acknowledgement

This research is funded by the Federal Ministry of Research, Technology and Space of Germany (BMFTR) under Grant 02J24A150.

---

## License

This project is licensed under the MIT License. See the LICENSE file for details.