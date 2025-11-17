"""
XML Parser for ASMG interpreter.

Handles parsing of CMSD XML files with the standard structure used in cmsd_xml_03092025175658.xml.
Supports the current XML format with separate Layout section and LayoutObject definitions.
"""

from pathlib import Path
from typing import Dict, List, Optional

import yaml
from defusedxml import ElementTree

from interpreter.data_models import (
    Boundary,
    CMSDData,
    Connection,
    Layout,
    LayoutObject,
    PartType,
    Placement,
    Position,
    Property,
    Resource,
    Rotation,
)


class XMLParsingError(Exception):
    """Custom exception for XML parsing errors."""

    pass


class XMLParser:
    """Parses CMSD XML files into structured data objects."""

    def __init__(self, config_path: Optional[Path] = None):
        """Initialize parser with configuration."""
        if config_path is None:
            # Get project root relative to this file
            script_dir = Path(__file__).resolve().parent
            project_root = script_dir.parent
            config_path = project_root / "config" / "xml_mapping.yaml"

        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        """Load XML mapping configuration."""
        try:
            with self.config_path.open("r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            raise XMLParsingError(f"Failed to load XML mapping config: {e}")

    def parse_file(self, xml_file_path: Path) -> CMSDData:
        """Parse XML file and return structured data."""
        try:
            with xml_file_path.open("r", encoding="utf-8") as f:
                xml_content = f.read()

            root = ElementTree.fromstring(xml_content)
            return self.parse_xml(root)

        except Exception as e:
            raise XMLParsingError(f"Failed to parse XML file {xml_file_path}: {e}")

    def parse_xml(self, root) -> CMSDData:
        """Parse XML root element and return structured data."""
        # Use cmsd_v1 schema directly (standard CMSD format)
        schema_config = self.config["schemas"]["cmsd_v1"]

        print("Using CMSD standard schema")

        # Parse header information
        header_data = self._parse_header(root, schema_config)

        # Create main data container
        cmsd_data = CMSDData(
            document_identifier=header_data.get("document_identifier", ""),
            description=header_data.get("description", ""),
            version=header_data.get("version", ""),
            creation_time=header_data.get("creation_time", ""),
            time_unit=header_data.get("time_unit", "second"),
            length_unit=header_data.get("length_unit", "meter"),
            weight_unit=header_data.get("weight_unit", "kilogram"),
        )

        # Parse resources
        resources = self._parse_resources(root, schema_config)
        cmsd_data.resources = {r.identifier: r for r in resources}

        # Parse connections
        connections = self._parse_connections(root, schema_config)
        cmsd_data.connections = connections

        # Parse layout objects
        layout_objects = self._parse_layout_objects(root, schema_config)
        cmsd_data.layout_objects = {lo.identifier: lo for lo in layout_objects}

        # Parse layout and placements
        layout = self._parse_layout(root, schema_config)
        cmsd_data.layout = layout

        # Parse part types
        part_types = self._parse_part_types(root, schema_config)
        cmsd_data.part_types = {pt.identifier: pt for pt in part_types}

        # Update resource connections from parsed connection data
        self._update_resource_connections(cmsd_data)

        return cmsd_data

    def _parse_header(self, root, schema_config: Dict) -> Dict[str, str]:
        """Parse header section."""
        header_config = schema_config.get("header", {})
        header_data = {}

        for field_name, xpath in header_config.items():
            value = self._get_text_by_xpath(root, xpath)
            if value:
                header_data[field_name] = value

        return header_data

    def _parse_resources(self, root, schema_config: Dict) -> List[Resource]:
        """Parse resources section."""
        resources_config = schema_config.get("resources", {})
        resources = []

        resource_elements = root.findall(
            resources_config.get("xpath", ".//{*}Resource")
        )

        for res_elem in resource_elements:
            resource = self._parse_single_resource(res_elem, resources_config)
            if resource:
                resources.append(resource)

        return resources

    def _parse_single_resource(
        self, res_elem, resources_config: Dict
    ) -> Optional[Resource]:
        """Parse a single resource element."""
        fields_config = resources_config.get("fields", {})

        # Extract basic fields
        identifier = self._get_text_by_xpath(
            res_elem, fields_config.get("identifier", "")
        )
        if not identifier:
            print("Warning: Skipping resource with no identifier")
            return None

        resource_type = self._get_text_by_xpath(
            res_elem, fields_config.get("resource_type", "")
        )
        name = self._get_text_by_xpath(res_elem, fields_config.get("name", ""))
        description = self._get_text_by_xpath(
            res_elem, fields_config.get("description", "")
        )
        current_status = self._get_text_by_xpath(
            res_elem, fields_config.get("current_status", "")
        )
        resource_class_id = self._get_text_by_xpath(
            res_elem, fields_config.get("resource_class_identifier", "")
        )

        # Parse properties
        properties = self._parse_properties(
            res_elem, resources_config.get("properties", {})
        )

        # Create resource object
        resource = Resource(
            identifier=identifier,
            resource_type=resource_type,
            name=name,
            description=description,
            current_status=current_status,
            resource_class_identifier=resource_class_id,
            properties=properties,
        )

        return resource

    def _parse_properties(
        self, parent_elem, properties_config: Dict
    ) -> Dict[str, Property]:
        """Parse properties of a resource."""
        properties = {}

        prop_xpath = properties_config.get("xpath", "")
        if not prop_xpath:
            return properties

        prop_elements = parent_elem.findall(prop_xpath)
        fields_config = properties_config.get("fields", {})

        for prop_elem in prop_elements:
            name = self._get_text_by_xpath(prop_elem, fields_config.get("name", ""))
            value = self._get_text_by_xpath(prop_elem, fields_config.get("value", ""))
            unit = self._get_text_by_xpath(prop_elem, fields_config.get("unit", ""))

            if name and value:
                properties[name] = Property(name=name, value=value, unit=unit)

        return properties

    def _parse_connections(self, root, schema_config: Dict) -> List[Connection]:
        """Parse connections from resources."""
        connections = []
        resources_config = schema_config.get("resources", {})
        connections_config = resources_config.get("connections", {})

        if not connections_config:
            return connections

        # Find all resources to get their connections
        resource_elements = root.findall(
            resources_config.get("xpath", ".//{*}Resource")
        )

        for res_elem in resource_elements:
            from_resource_id = self._get_text_by_xpath(res_elem, "{*}Identifier")
            if not from_resource_id:
                continue

            # Find connections for this resource
            conn_elements = res_elem.findall(connections_config.get("xpath", ""))
            fields_config = connections_config.get("fields", {})

            for conn_elem in conn_elements:
                identifier = self._get_text_by_xpath(
                    conn_elem, fields_config.get("identifier", "")
                )
                to_resource_id = self._get_text_by_xpath(
                    conn_elem, fields_config.get("to_resource_id", "")
                )

                if to_resource_id:
                    connection = Connection(
                        identifier=identifier
                        or f"conn_{from_resource_id}_to_{to_resource_id}",
                        from_resource_id=from_resource_id,
                        to_resource_id=to_resource_id,
                    )
                    connections.append(connection)

        return connections

    def _parse_layout_objects(self, root, schema_config: Dict) -> List[LayoutObject]:
        """Parse layout objects."""
        layout_objects = []
        lo_config = schema_config.get("layout_objects", {})

        if not lo_config:
            return layout_objects

        lo_elements = root.findall(lo_config.get("xpath", ".//{*}LayoutObject"))
        fields_config = lo_config.get("fields", {})

        for lo_elem in lo_elements:
            identifier = self._get_text_by_xpath(
                lo_elem, fields_config.get("identifier", "")
            )
            associated_resource_id = self._get_text_by_xpath(
                lo_elem, fields_config.get("associated_resource_id", "")
            )

            if not identifier or not associated_resource_id:
                print(
                    "Warning: Skipping layout object with missing identifier or resource reference"
                )
                continue

            # Parse boundary if available
            boundary = self._parse_boundary(lo_elem, lo_config.get("boundary", {}))

            layout_object = LayoutObject(
                identifier=identifier,
                associated_resource_id=associated_resource_id,
                boundary=boundary,
            )

            layout_objects.append(layout_object)

        return layout_objects

    def _parse_layout(self, root, schema_config: Dict) -> Optional[Layout]:
        """Parse layout section."""
        layout_config = schema_config.get("layout", {})

        if not layout_config:
            return None

        layout_elem = root.find(layout_config.get("xpath", ".//{*}Layout"))
        if layout_elem is None:
            return None

        fields_config = layout_config.get("fields", {})
        identifier = self._get_text_by_xpath(
            layout_elem, fields_config.get("identifier", "")
        )
        description = self._get_text_by_xpath(
            layout_elem, fields_config.get("description", "")
        )

        # Parse layout boundary
        boundary = self._parse_boundary(layout_elem, layout_config.get("boundary", {}))

        # Parse placements
        placements = self._parse_placements(
            layout_elem, layout_config.get("placements", {})
        )

        layout = Layout(
            identifier=identifier or "main_layout",
            description=description,
            boundary=boundary,
            placements=placements,
        )

        return layout

    def _parse_placements(
        self, parent_elem, placements_config: Dict
    ) -> Dict[str, Placement]:
        """Parse placement elements."""
        placements = {}

        if not placements_config:
            return placements

        placement_elements = parent_elem.findall(placements_config.get("xpath", ""))
        fields_config = placements_config.get("fields", {})

        for place_elem in placement_elements:
            layout_element_id = self._get_text_by_xpath(
                place_elem, fields_config.get("layout_element_id", "")
            )

            if not layout_element_id:
                continue

            # Parse position
            pos_x = self._get_float_by_xpath(
                place_elem, fields_config.get("position_x", "")
            )
            pos_y = self._get_float_by_xpath(
                place_elem, fields_config.get("position_y", "")
            )
            pos_z = self._get_float_by_xpath(
                place_elem, fields_config.get("position_z", "")
            )

            if pos_x is None or pos_y is None:
                print(
                    f"Warning: Skipping placement {layout_element_id} with invalid position"
                )
                continue

            position = Position(x=pos_x, y=pos_y, z=pos_z or 0.0)

            # Parse rotation (optional)
            rotation = None
            rot_angle = self._get_float_by_xpath(
                place_elem, fields_config.get("rotation_angle", "")
            )
            if rot_angle is not None:
                rot_axis_x = (
                    self._get_float_by_xpath(
                        place_elem, fields_config.get("rotation_axis_x", "")
                    )
                    or 0.0
                )
                rot_axis_y = (
                    self._get_float_by_xpath(
                        place_elem, fields_config.get("rotation_axis_y", "")
                    )
                    or 0.0
                )
                rot_axis_z = (
                    self._get_float_by_xpath(
                        place_elem, fields_config.get("rotation_axis_z", "")
                    )
                    or 1.0
                )

                rotation = Rotation(
                    angle=rot_angle,
                    axis_x=rot_axis_x,
                    axis_y=rot_axis_y,
                    axis_z=rot_axis_z,
                )

            placement = Placement(
                layout_element_id=layout_element_id,
                position=position,
                rotation=rotation,
            )

            placements[layout_element_id] = placement

        return placements

    def _parse_boundary(self, parent_elem, boundary_config: Dict) -> Optional[Boundary]:
        """Parse boundary/dimensions."""
        if not boundary_config:
            return None

        width = self._get_float_by_xpath(parent_elem, boundary_config.get("width", ""))
        depth = self._get_float_by_xpath(parent_elem, boundary_config.get("depth", ""))
        height = self._get_float_by_xpath(
            parent_elem, boundary_config.get("height", "")
        )
        unit = self._get_text_by_xpath(parent_elem, boundary_config.get("unit", ""))

        if width is not None and depth is not None:
            return Boundary(
                width=width, depth=depth, height=height or 1.0, unit=unit or "meter"
            )

        return None

    def _parse_part_types(self, root, schema_config: Dict) -> List[PartType]:
        """Parse part types."""
        part_types = []
        pt_config = schema_config.get("part_types", {})

        if not pt_config:
            return part_types

        pt_elements = root.findall(pt_config.get("xpath", ".//{*}PartType"))
        fields_config = pt_config.get("fields", {})

        for pt_elem in pt_elements:
            identifier = self._get_text_by_xpath(
                pt_elem, fields_config.get("identifier", "")
            )
            name = self._get_text_by_xpath(pt_elem, fields_config.get("name", ""))
            description = self._get_text_by_xpath(
                pt_elem, fields_config.get("description", "")
            )
            weight = self._get_float_by_xpath(pt_elem, fields_config.get("weight", ""))

            # Parse dimensions
            dimensions = None
            width = self._get_float_by_xpath(pt_elem, fields_config.get("width", ""))
            depth = self._get_float_by_xpath(pt_elem, fields_config.get("depth", ""))
            height = self._get_float_by_xpath(pt_elem, fields_config.get("height", ""))

            if width is not None and depth is not None:
                dimensions = Boundary(width=width, depth=depth, height=height or 1.0)

            if identifier:
                part_type = PartType(
                    identifier=identifier,
                    name=name or identifier,
                    description=description,
                    weight=weight,
                    dimensions=dimensions,
                )
                part_types.append(part_type)

        return part_types

    def _update_resource_connections(self, cmsd_data: CMSDData):
        """Update resource objects with their connection lists."""
        for connection in cmsd_data.connections:
            resource = cmsd_data.get_resource(connection.from_resource_id)
            if resource:
                resource.connections.append(connection.to_resource_id)

    def _get_text_by_xpath(self, elem, xpath: str) -> str:
        """Get text content using xpath with namespace handling."""
        if not xpath or elem is None:
            return ""

        # Handle relative vs absolute xpath
        if not xpath.startswith(".//"):
            xpath = f"./{xpath}"

        child = elem.find(xpath)
        return child.text if child is not None and child.text else ""

    def _get_float_by_xpath(self, elem, xpath: str) -> Optional[float]:
        """Get float value using xpath."""
        text = self._get_text_by_xpath(elem, xpath)
        if text:
            try:
                return float(text)
            except ValueError:
                pass
        return None


def create_parser(config_path: Optional[Path] = None) -> XMLParser:
    """Factory function to create XML parser."""
    return XMLParser(config_path)
