"""
Modular ASMG Interpreter - New Architecture

Clean orchestration layer that coordinates XML parsing, data mapping,
and Plant Simulation object creation using the modular components.
"""

import sys
from pathlib import Path

# Add project root to Python path for config access in Plant Simulation environment
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent
sys.path.insert(0, str(project_root))

from config.config_loader import Config  # noqa: E402
from interpreter.data_models import DataValidator  # noqa: E402
from interpreter.mapping_engine import create_mapping_engine  # noqa: E402
from interpreter.plantsim_interface import create_plantsim_interface  # noqa: E402
from interpreter.xml_parser import create_parser  # noqa: E402


class InterpreterError(Exception):
    """Custom exception for interpreter errors."""

    pass


class ASMGInterpreter:
    """Main interpreter class that orchestrates the entire process."""

    def __init__(self):
        """Initialize the interpreter with configuration."""
        print("Initializing ASMG Interpreter (New Architecture)...")

        # Load base configuration
        self.config = Config()

        # Initialize modules
        self.xml_parser = create_parser()
        self.mapping_engine = create_mapping_engine()
        self.plantsim_interface = create_plantsim_interface(self.mapping_engine.config)

        # Statistics
        self.stats = {
            "xml_parsing_time": 0.0,
            "mapping_time": 0.0,
            "creation_time": 0.0,
            "total_time": 0.0,
        }

    def process_xml_file(self, xml_file_path: Path) -> bool:
        """
        Process XML file and create Plant Simulation model.

        Returns True if successful, False otherwise.
        """
        import time

        start_time = time.time()

        try:
            print(f"Processing XML file: {xml_file_path}")

            # Step 1: Parse XML
            print("\n" + "=" * 60)
            print("STEP 1: Parsing XML")
            print("=" * 60)
            parse_start = time.time()

            cmsd_data = self.xml_parser.parse_file(xml_file_path)

            parse_time = time.time() - parse_start
            self.stats["xml_parsing_time"] = parse_time
            print(f"XML parsing completed in {parse_time:.2f} seconds")

            # Step 2: Validate data
            print("\n" + "=" * 60)
            print("STEP 2: Validating Data")
            print("=" * 60)

            validation_result = DataValidator.validate(cmsd_data)
            self._print_validation_results(validation_result)

            if not validation_result.is_valid:
                print("CRITICAL: Data validation failed. Cannot proceed.")
                return False

            # Step 3: Create mappings
            print("\n" + "=" * 60)
            print("STEP 3: Creating Mappings")
            print("=" * 60)
            mapping_start = time.time()

            mappings = self.mapping_engine.map_cmsd_data(cmsd_data)

            mapping_time = time.time() - mapping_start
            self.stats["mapping_time"] = mapping_time
            print(f"Mapping completed in {mapping_time:.2f} seconds")
            print(f"Created {len(mappings)} object mappings")

            # Step 4: Create Plant Simulation objects
            print("\n" + "=" * 60)
            print("STEP 4: Creating Plant Simulation Objects")
            print("=" * 60)
            creation_start = time.time()

            created_objects = self.plantsim_interface.create_objects(mappings)

            creation_time = time.time() - creation_start
            self.stats["creation_time"] = creation_time
            print(f"Object creation completed in {creation_time:.2f} seconds")

            # Step 5: Create connections
            print("\n" + "=" * 60)
            print("STEP 5: Creating Connections")
            print("=" * 60)

            created_connections = self.plantsim_interface.create_connections(cmsd_data)

            # Step 6: Final validation and summary
            print("\n" + "=" * 60)
            print("STEP 6: Final Validation and Summary")
            print("=" * 60)

            self._print_final_summary(cmsd_data, created_objects, created_connections)

            total_time = time.time() - start_time
            self.stats["total_time"] = total_time
            print(f"\nTotal processing time: {total_time:.2f} seconds")

            return True

        except Exception as e:
            print(f"CRITICAL ERROR during processing: {e}")
            import traceback

            traceback.print_exc()
            return False

    def _print_validation_results(self, validation_result):
        """Print validation results."""
        if validation_result.is_valid:
            print("✓ Data validation passed")
        else:
            print("✗ Data validation failed")

        if validation_result.errors:
            print(f"\nErrors ({len(validation_result.errors)}):")
            for error in validation_result.errors:
                print(f"  ✗ {error}")

        if validation_result.warnings:
            print(f"\nWarnings ({len(validation_result.warnings)}):")
            for warning in validation_result.warnings:
                print(f"  ⚠ {warning}")

    def _print_final_summary(self, cmsd_data, created_objects, created_connections):
        """Print final processing summary."""
        print("Processing Summary:")
        print(f"  Resources in XML: {len(cmsd_data.resources)}")
        print(f"  Layout objects in XML: {len(cmsd_data.layout_objects)}")
        print(f"  Connections in XML: {len(cmsd_data.connections)}")
        print(f"  Objects created: {len(created_objects)}")
        print(f"  Connections created: {len(created_connections)}")

        # Material Units summary
        mu_mapping = self.mapping_engine.get_material_units()
        if mu_mapping:
            print(f"  Material Units created: {len(mu_mapping)}")
            for product_type, mu_name in mu_mapping.items():
                print(f"    {product_type} -> {mu_name}")

        # Interface statistics
        interface_stats = self.plantsim_interface.get_statistics()
        print(f"  Total errors: {interface_stats['errors']}")
        print(f"  Total warnings: {interface_stats['warnings']}")

        # Validation issues
        issues = self.plantsim_interface.validate_created_objects()
        if issues["errors"]:
            print(f"\nValidation Errors ({len(issues['errors'])}):")
            for error in issues["errors"]:
                print(f"  ✗ {error}")

        if issues["warnings"]:
            print(f"\nValidation Warnings ({len(issues['warnings'])}):")
            for warning in issues["warnings"]:
                print(f"  ⚠ {warning}")

        if not issues["errors"] and not issues["warnings"]:
            print("\n✓ All validations passed!")


def main():
    """Main entry point for the interpreter."""
    print("ASMG Interpreter")
    print("=" * 60)

    # Determine the project root and path to active_xml_path.txt
    active_xml_path_file = project_root / "active_xml_path.txt"

    print(f"Reading XML file path from: {active_xml_path_file}")

    # Read XML file path
    if not active_xml_path_file.is_file():
        print(
            f"CRITICAL ERROR: Configuration file 'active_xml_path.txt' not found at {active_xml_path_file}"
        )
        sys.exit(1)

    try:
        with active_xml_path_file.open(encoding="utf-8") as f:
            xml_file_path_str = f.read().strip()

        if not xml_file_path_str:
            print("CRITICAL ERROR: Configuration file 'active_xml_path.txt' is empty")
            sys.exit(1)

        xml_file = Path(xml_file_path_str)

        # If path is relative, make it relative to project root
        if not xml_file.is_absolute():
            xml_file = project_root / xml_file

        if not xml_file.is_file():
            print(f"CRITICAL ERROR: XML file does not exist: {xml_file}")
            sys.exit(1)

        print(f"Target XML file: {xml_file}")

    except Exception as e:
        print(f"CRITICAL ERROR reading configuration: {e}")
        sys.exit(1)

    # Initialize and run interpreter
    try:
        interpreter = ASMGInterpreter()
        success = interpreter.process_xml_file(xml_file)

        if success:
            print("\n" + "=" * 60)
            print("PLANT SIMULATION MODEL CREATION COMPLETED SUCCESSFULLY!")
            print("=" * 60)
        else:
            print("\n" + "=" * 60)
            print("PLANT SIMULATION MODEL CREATION FAILED!")
            print("=" * 60)
            sys.exit(1)

    except KeyboardInterrupt:
        print("\nProcess interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nUNEXPECTED ERROR: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
