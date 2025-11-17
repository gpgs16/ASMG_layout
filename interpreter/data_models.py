"""
Data models for ASMG interpreter.

Defines schema-independent Python classes to represent CMSD XML data.
These models serve as an intermediate representation between XML parsing
and Plant Simulation object creation.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Property:
    """Represents a property with name, value, and optional unit."""

    name: str
    value: str
    unit: Optional[str] = None

    def get_numeric_value(self) -> Optional[float]:
        """Convert value to float if possible."""
        try:
            return float(self.value)
        except (ValueError, TypeError):
            return None

    def get_int_value(self) -> Optional[int]:
        """Convert value to int if possible."""
        try:
            return int(self.value)
        except (ValueError, TypeError):
            return None


@dataclass
class Position:
    """Represents 3D position coordinates."""

    x: float
    y: float
    z: float = 0.0


@dataclass
class Rotation:
    """Represents rotation with angle and axis."""

    angle: float
    axis_x: float = 0.0
    axis_y: float = 0.0
    axis_z: float = 1.0  # Default Z-axis rotation


@dataclass
class Boundary:
    """Represents object dimensions."""

    width: float
    depth: float
    height: float = 1.0
    unit: str = "meter"


@dataclass
class Resource:
    """Represents a CMSD resource (machine, conveyor, etc.)."""

    identifier: str
    resource_type: str
    name: str
    description: str = ""
    current_status: str = "idle"
    resource_class_identifier: Optional[str] = None
    properties: Dict[str, Property] = field(default_factory=dict)
    connections: List[str] = field(default_factory=list)  # List of target resource IDs

    def get_property_value(self, property_name: str) -> Optional[str]:
        """Get property value by name (case-insensitive)."""
        for name, prop in self.properties.items():
            if name.lower() == property_name.lower():
                return prop.value
        return None

    def get_property(self, property_name: str) -> Optional[Property]:
        """Get property object by name (case-insensitive)."""
        for name, prop in self.properties.items():
            if name.lower() == property_name.lower():
                return prop
        return None


@dataclass
class Connection:
    """Represents a connection between two resources."""

    identifier: str
    from_resource_id: str
    to_resource_id: str
    description: str = ""


@dataclass
class LayoutObject:
    """Represents a layout object with physical properties."""

    identifier: str
    associated_resource_id: str
    boundary: Optional[Boundary] = None


@dataclass
class Placement:
    """Represents the placement of a layout object in 3D space."""

    layout_element_id: str
    position: Position
    rotation: Optional[Rotation] = None


@dataclass
class Layout:
    """Represents the overall factory layout."""

    identifier: str
    description: str
    boundary: Optional[Boundary] = None
    placements: Dict[str, Placement] = field(
        default_factory=dict
    )  # Key: layout_element_id


@dataclass
class PartType:
    """Represents a part/product type."""

    identifier: str
    name: str
    description: str = ""
    weight: Optional[float] = None
    dimensions: Optional[Boundary] = None


@dataclass
class CMSDData:
    """Container for all CMSD data."""

    document_identifier: str
    description: str
    version: str
    creation_time: str

    # Unit defaults
    time_unit: str = "second"
    length_unit: str = "meter"
    weight_unit: str = "kilogram"

    # Core data
    resources: Dict[str, Resource] = field(default_factory=dict)
    connections: List[Connection] = field(default_factory=list)
    layout_objects: Dict[str, LayoutObject] = field(default_factory=dict)
    layout: Optional[Layout] = None
    part_types: Dict[str, PartType] = field(default_factory=dict)

    def get_resource(self, resource_id: str) -> Optional[Resource]:
        """Get resource by identifier."""
        return self.resources.get(resource_id)

    def get_layout_object(self, layout_object_id: str) -> Optional[LayoutObject]:
        """Get layout object by identifier."""
        return self.layout_objects.get(layout_object_id)

    def get_placement(self, layout_element_id: str) -> Optional[Placement]:
        """Get placement by layout element ID."""
        if self.layout:
            return self.layout.placements.get(layout_element_id)
        return None

    def get_resource_connections(self, resource_id: str) -> List[str]:
        """Get all outgoing connections for a resource."""
        resource = self.get_resource(resource_id)
        return resource.connections if resource else []


@dataclass
class ValidationResult:
    """Result of data validation."""

    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_error(self, message: str):
        """Add validation error."""
        self.errors.append(message)
        self.is_valid = False

    def add_warning(self, message: str):
        """Add validation warning."""
        self.warnings.append(message)


class DataValidator:
    """Validates CMSD data for completeness and consistency."""

    @staticmethod
    def validate(data: CMSDData) -> ValidationResult:
        """Validate CMSD data."""
        result = ValidationResult(is_valid=True)

        # Check required fields
        if not data.document_identifier:
            result.add_error("Missing document identifier")

        if not data.resources:
            result.add_error("No resources defined")

        # Validate resource-layout object associations
        for layout_obj_id, layout_obj in data.layout_objects.items():
            if layout_obj.associated_resource_id not in data.resources:
                result.add_error(
                    f"LayoutObject '{layout_obj_id}' references unknown resource "
                    f"'{layout_obj.associated_resource_id}'"
                )

        # Validate placements
        if data.layout:
            for placement_id, placement in data.layout.placements.items():
                if placement.layout_element_id not in data.layout_objects:
                    result.add_error(
                        f"Placement references unknown layout object '{placement.layout_element_id}'"
                    )

        # Validate connections
        for connection in data.connections:
            if connection.from_resource_id not in data.resources:
                result.add_error(
                    f"Connection '{connection.identifier}' references unknown source resource "
                    f"'{connection.from_resource_id}'"
                )

            if connection.to_resource_id not in data.resources:
                result.add_error(
                    f"Connection '{connection.identifier}' references unknown target resource "
                    f"'{connection.to_resource_id}'"
                )

        # Check for orphaned resources (no layout object)
        resources_with_layout = {
            lo.associated_resource_id for lo in data.layout_objects.values()
        }
        for resource_id in data.resources:
            if resource_id not in resources_with_layout:
                result.add_warning(
                    f"Resource '{resource_id}' has no associated layout object"
                )

        return result
