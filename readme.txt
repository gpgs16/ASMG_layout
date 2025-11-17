# Auto Sim Agent System

An intelligent agent system built with Google ADK for analyzing layout diagrams and converting them into structured JSON representations.

## Overview

This system processes images of layout diagrams (containing components like sources, conveyors, machines, and diverters) and extracts:
- Component information (type, position, labels)
- Connection relationships between components
- Structured JSON output

## Architecture

### Agents

1. **OrchestratorAgent**: Main controller that coordinates the workflow
2. **LayoutParserAgent**: Core analysis engine that processes images using computer vision and LLM reasoning
3. **JSONWriterAgent**: Formats the extracted data into structured JSON

### Tools

1. **ComponentDetector**: Computer vision tool for detecting geometric shapes (boxes, arrows)
2. **TextRecognizer**: OCR tool for extracting text labels and positions

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt

2. Install Tesseract OCR (for text recognition):

# On macOS
brew install tesseract

# On Ubuntu
sudo apt-get install tesseract-ocr

# On Windows
# Download from: https://github.com/UB-Mannheim/tesseract/wiki