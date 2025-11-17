"""
Mapping Engine for ASMG interpreter.

Transforms XML data to Plant Simulation parameters using configuration mappings.
Handles unit conversions, property transformations, and validation.
"""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

from interpreter.data_models import CMSDData, Property, Resource


class MappingError(Exception):
    """Custom exception for mapping errors."""

    pass


class UnitConverter:
    """Handles unit conversions between different measurement systems."""

    def __init__(self, conversion_config: Dict):
        self.conversions = conversion_config

    def convert(self, value: float, from_unit: str, conversion_type: str) -> float:
        """Convert value from one unit to another."""
        if conversion_type not in self.conversions:
            return value  # No conversion available

        type_config = self.conversions[conversion_type]
        base_unit = type_config.get("base_unit", "")
        conversions = type_config.get("conversions", {})

        if from_unit == base_unit or from_unit not in conversions:
            return value  # No conversion needed or unknown unit

        # Convert to base unit
        multiplier = conversions[from_unit]
        return value * multiplier


class PropertyValidator:
    """Validates property values against configured rules."""

    def __init__(self, validation_config: Dict):
        self.validation_config = validation_config

    def validate_property(
        self, prop_name: str, value: Any, data_type: str
    ) -> Tuple[bool, str]:
        """Validate a property value."""
        # Check data type
        if not self._validate_data_type(value, data_type):
            return (
                False,
                f"Invalid data type for {prop_name}. Expected {data_type}, got {type(value).__name__}",
            )

        # Check range if numeric
        if data_type in ["float", "int", "positive_float", "positive_int"]:
            numeric_value = (
                float(value) if isinstance(value, (int, float, str)) else None
            )
            if numeric_value is not None:
                ranges = self.validation_config.get("ranges", {})
                if prop_name in ranges:
                    min_val, max_val = ranges[prop_name]
                    if not (min_val <= numeric_value <= max_val):
                        return (
                            False,
                            f"{prop_name} value {numeric_value} outside valid range [{min_val}, {max_val}]",
                        )

        return True, ""

    def _validate_data_type(self, value: Any, data_type: str) -> bool:
        """Validate that value matches expected data type."""
        if data_type == "string":
            return isinstance(value, str)
        elif data_type == "int":
            try:
                int(str(value))
                return True
            except (ValueError, TypeError):
                return False
        elif data_type == "float":
            try:
                float(str(value))
                return True
            except (ValueError, TypeError):
                return False
        elif data_type == "positive_int":
            try:
                val = int(str(value))
                return val >= 0
            except (ValueError, TypeError):
                return False
        elif data_type == "positive_float":
            try:
                val = float(str(value))
                return val >= 0.0
            except (ValueError, TypeError):
                return False

        return True  # Unknown type, assume valid


class NameSanitizer:
    """Sanitizes object names for Plant Simulation compatibility."""

    def __init__(self, naming_config: Dict):
        self.config = naming_config

    def sanitize_name(self, name: str) -> str:
        """Clean name for Plant Simulation compatibility."""
        if not name:
            return "unnamed"

        # Apply case handling
        case_handling = self.config.get("case_handling", "preserve")
        if case_handling == "upper":
            name = name.upper()
        elif case_handling == "lower":
            name = name.lower()

        # Replace invalid characters
        invalid_chars = self.config.get("invalid_chars", [])
        replacement_char = self.config.get("replacement_char", "_")

        for char in invalid_chars:
            name = name.replace(char, replacement_char)

        # Ensure max length
        max_length = self.config.get("max_length", 32)
        if len(name) > max_length:
            name = name[:max_length]

        # Ensure name doesn't start with number or underscore
        if name and name[0].isdigit():
            name = "obj_" + name

        return name


class PlantSimMapping:
    """Represents mapping information for a Plant Simulation object."""

    def __init__(self, resource: Resource, mapping_config: Dict):
        self.resource = resource
        self.config = mapping_config
        self.template = mapping_config.get("template", "")
        self.properties = {}
        self.errors = []
        self.warnings = []

    def add_property(self, ps_property: str, value: Any, data_type: str = "string"):
        """Add a property mapping."""
        self.properties[ps_property] = {"value": value, "data_type": data_type}

    def add_error(self, message: str):
        """Add an error message."""
        self.errors.append(message)

    def add_warning(self, message: str):
        """Add a warning message."""
        self.warnings.append(message)


class MappingEngine:
    """Main engine for transforming XML data to Plant Simulation mappings."""

    def __init__(self, config_path: Optional[Path] = None):
        """Initialize mapping engine with configuration."""
        if config_path is None:
            # Get project root relative to this file
            script_dir = Path(__file__).resolve().parent
            project_root = script_dir.parent
            config_path = project_root / "config" / "plantsim_mapping.yaml"

        self.config_path = config_path
        self.config = self._load_config()
        self.unit_converter = UnitConverter(self.config.get("unit_conversions", {}))
        self.validator = PropertyValidator(self.config.get("property_validation", {}))
        self.name_sanitizer = NameSanitizer(
            self.config.get("plantsim_settings", {}).get("naming", {})
        )

        # Material unit tracking
        self.material_units = {}
        self.next_mu_letter = ord("A")

    def _load_config(self) -> Dict:
        """Load Plant Simulation mapping configuration."""
        try:
            with self.config_path.open("r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            raise MappingError(f"Failed to load mapping config: {e}")

    def map_cmsd_data(self, cmsd_data: CMSDData) -> Dict[str, PlantSimMapping]:
        """Map CMSD data to Plant Simulation objects."""
        mappings = {}

        # Process layout objects that have associated resources
        for lo_id, layout_obj in cmsd_data.layout_objects.items():
            resource = cmsd_data.get_resource(layout_obj.associated_resource_id)
            if not resource:
                continue

            # Get placement information
            placement = cmsd_data.get_placement(lo_id)

            # Create mapping for this object
            mapping = self._create_object_mapping(
                resource, layout_obj, placement, cmsd_data
            )
            if mapping:
                mappings[resource.identifier] = mapping

        return mappings

    def _create_object_mapping(
        self, resource: Resource, layout_obj, placement, cmsd_data: CMSDData
    ) -> Optional[PlantSimMapping]:
        """Create Plant Simulation mapping for a single object."""
        # Get resource type mapping
        resource_type = resource.resource_type.lower()

        # Handle aliases (e.g., 'sink' -> 'drain', 'machine' -> 'station')
        resource_mappings = self.config.get("resource_mappings", {})
        mapping_config = resource_mappings.get(resource_type)

        if not mapping_config:
            # Check for aliases
            for res_type, config in resource_mappings.items():
                if config.get("alias") == resource_type:
                    mapping_config = config
                    break

        if not mapping_config:
            print(
                f"Warning: No mapping configuration for resource type '{resource_type}'"
            )
            return None

        # Create mapping object
        mapping = PlantSimMapping(resource, mapping_config)

        # Set basic properties
        self._map_basic_properties(mapping, placement, layout_obj)

        # Map resource-specific properties
        self._map_resource_properties(mapping, cmsd_data)

        # Handle special cases
        self._handle_special_properties(mapping, cmsd_data)

        return mapping

    def _map_basic_properties(self, mapping: PlantSimMapping, placement, layout_obj):
        """Map basic properties like position and rotation."""
        if not placement:
            mapping.add_warning("No placement information found")
            return

        # Use raw XML coordinates (coordinate adjuster disabled)
        position = placement.position

        # Log coordinates being used
        print(
            f"{mapping.resource.name}: Using XML coordinates ({position.x:.3f}, {position.y:.3f}) "
            f"[Type: {mapping.resource.resource_type}]"
        )

        # Set position directly from XML
        mapping.add_property(
            "Coordinate3D", [position.x, position.y, position.z], "list"
        )

        # Rotation
        if placement.rotation:
            rotation = placement.rotation
            # Always use 4-element array format as per Plant Simulation documentation
            # [angle, axis_x, axis_y, axis_z]
            rotation_value = [
                rotation.angle,
                rotation.axis_x,
                rotation.axis_y,
                rotation.axis_z,
            ]
            mapping.add_property("_3D.Rotation", rotation_value, "list")
            print(
                f"Mapped rotation for {mapping.resource.name}: {rotation_value} [angle, x, y, z]"
            )
        else:
            print(f"No rotation data found for {mapping.resource.name}")

        # Object name (sanitized)
        sanitized_name = self.name_sanitizer.sanitize_name(mapping.resource.name)
        mapping.add_property("name", sanitized_name, "string")

    def _map_resource_properties(self, mapping: PlantSimMapping, cmsd_data: CMSDData):
        """Map resource-specific properties based on configuration."""
        properties_config = mapping.config.get("properties", {})
        required_props = mapping.config.get("required_properties", [])
        default_props = mapping.config.get("default_properties", {})

        # Process each configured property
        for xml_prop_name, prop_config in properties_config.items():
            self._map_single_property(mapping, xml_prop_name, prop_config, cmsd_data)

        # Check required properties
        for req_prop in required_props:
            if req_prop not in [p.lower() for p in mapping.resource.properties.keys()]:
                # Try to use default value
                if req_prop in default_props:
                    default_value = default_props[req_prop]
                    ps_prop = properties_config.get(req_prop, {}).get(
                        "plantsim_property", req_prop
                    )
                    mapping.add_property(ps_prop, default_value, "float")
                    mapping.add_warning(
                        f"Using default value for required property '{req_prop}': {default_value}"
                    )
                else:
                    mapping.add_error(
                        f"Required property '{req_prop}' not found and no default available"
                    )

    def _map_single_property(
        self,
        mapping: PlantSimMapping,
        xml_prop_name: str,
        prop_config: Dict,
        cmsd_data: CMSDData,
    ):
        """Map a single property from XML to Plant Simulation."""
        # Get the property value from the resource
        xml_property = mapping.resource.get_property(xml_prop_name)
        if not xml_property:
            return

        ps_property = prop_config.get("plantsim_property", xml_prop_name)
        data_type = prop_config.get("data_type", "string")
        unit_conversion = prop_config.get("unit_conversion")
        special_handler = prop_config.get("special_handler")

        # Handle special handlers
        if special_handler:
            self._handle_special_property(
                mapping, xml_property, special_handler, cmsd_data
            )
            return

        # Convert value
        value = self._convert_property_value(xml_property, data_type, unit_conversion)

        # Validate value
        is_valid, error_msg = self.validator.validate_property(
            xml_prop_name, value, data_type
        )
        if not is_valid:
            mapping.add_error(error_msg)
            return

        # Add to mapping
        mapping.add_property(ps_property, value, data_type)

    def _convert_property_value(
        self, xml_property: Property, data_type: str, unit_conversion: Optional[str]
    ) -> Any:
        """Convert property value to appropriate type and units."""
        value = xml_property.value

        # Handle unit conversion
        if unit_conversion and xml_property.unit:
            try:
                numeric_value = float(value)
                converted_value = self.unit_converter.convert(
                    numeric_value, xml_property.unit, unit_conversion
                )
                value = str(converted_value)
            except (ValueError, TypeError):
                pass  # Keep original value if conversion fails

        # Convert to target data type
        if data_type == "int":
            try:
                return int(float(value))
            except (ValueError, TypeError):
                return 0
        elif data_type in ["float", "positive_float"]:
            try:
                return float(value)
            except (ValueError, TypeError):
                return 0.0
        elif data_type == "string":
            return str(value)

        return value

    def _handle_special_properties(self, mapping: PlantSimMapping, cmsd_data: CMSDData):
        """Handle special property mappings that require custom logic."""
        # This method can be extended for complex property mappings
        pass

    def _handle_special_property(
        self,
        mapping: PlantSimMapping,
        xml_property: Property,
        handler_name: str,
        cmsd_data: CMSDData,
    ):
        """Handle special property with custom logic."""
        if handler_name == "assign_material_unit":
            self._handle_material_unit_assignment(mapping, xml_property)
        else:
            mapping.add_warning(f"Unknown special handler: {handler_name}")

    def _handle_material_unit_assignment(
        self, mapping: PlantSimMapping, xml_property: Property
    ):
        """Handle assignment of Material Unit objects to sources."""
        product_type = xml_property.value

        # Check if we already have a MU for this product type
        if product_type in self.material_units:
            mu_name = self.material_units[product_type]
        else:
            # Create new MU name
            mu_name = f"Part{chr(self.next_mu_letter)}"
            self.material_units[product_type] = mu_name
            self.next_mu_letter += 1

        # Add to mapping - this will need special handling in the Plant Simulation interface
        mapping.add_property("Path", mu_name, "material_unit")
        mapping.add_property(
            "_material_unit_info",
            {"product_type": product_type, "mu_name": mu_name},
            "special",
        )

    def get_material_units(self) -> Dict[str, str]:
        """Get mapping of product types to material unit names."""
        return self.material_units.copy()


def create_mapping_engine(config_path: Optional[Path] = None) -> MappingEngine:
    """Factory function to create mapping engine."""
    return MappingEngine(config_path)
