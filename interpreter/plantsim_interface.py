"""
Plant Simulation Interface for ASMG interpreter.

Handles all Plant Simulation API calls, object creation, property setting, and connections.
Provides clean abstraction layer for Plant Simulation operations.
"""

from typing import Any, Dict, List, Optional, Tuple

# Plant Simulation import - only available in Plant Simulation environment
try:
    import PlantSimulation  # type: ignore[attr-defined]

    PLANT_SIM_AVAILABLE = True
except ImportError:
    # For testing outside Plant Simulation
    PLANT_SIM_AVAILABLE = False
    PlantSimulation = None

from interpreter.data_models import CMSDData
from interpreter.mapping_engine import PlantSimMapping


class PlantSimulationError(Exception):
    """Custom exception for Plant Simulation interface errors."""

    pass


class COMPlantSimObject:
    """
    Wrapper for Plant Simulation objects via COM/win32com.
    Translates Python actions into SimTalk/COM commands.
    """

    def __init__(self, com_object, path: str):
        # Use direct dict assignment to avoid triggering __setattr__
        self.__dict__["_com"] = com_object
        self.__dict__["path"] = path
        self.__dict__["_name"] = path.split(".")[-1]

    def derive(self, parent, name: str):
        """
        Derive a new object from this template.
        SimTalk: template.derive(Location, "Name")
        """
        # Construct the SimTalk command
        # Note: derive returns the new object, but via COM we just execute the command
        # and instantiate a new wrapper for the expected result path.
        cmd = f'{self.path}.derive({parent.path}, "{name}")'
        try:
            self._com.ExecuteSimTalk(cmd)
            full_new_path = f"{parent.path}.{name}"
            return COMPlantSimObject(self._com, full_new_path)
        except Exception as e:
            print(f"COM Error calling derive on {self.path}: {e}")
            raise

    def connect(self, from_obj, to_obj):
        """
        Connect two objects.
        SimTalk: Connector.connect(From, To)
        """
        cmd = f'{self.path}.connect({from_obj.path}, {to_obj.path})'
        try:
            self._com.ExecuteSimTalk(cmd)
        except Exception as e:
            print(f"COM Error calling connect: {e}")
            raise

    def __getattr__(self, name: str):
        """
        Enable chained access (e.g., obj._3D.Rotation).
        Returns a new wrapper for the nested path.
        """
        return COMPlantSimObject(self._com, f"{self.path}.{name}")

    def __setattr__(self, name: str, value: Any):
        """
        Set a property value via COM.
        """
        # Check if we are setting an internal attribute defined in __init__
        if name in self.__dict__:
            super().__setattr__(name, value)
            return

        # Otherwise, set the value in Plant Sim
        full_path = f"{self.path}.{name}"
        try:
            self._com.SetValue(full_path, value)
        except Exception as e:
            print(f"COM Error setting {full_path} = {value}: {e}")
            raise


class MockPlantSimObject:
    """Mock Plant Simulation object for testing outside Plant Simulation."""

    def __init__(self, path: str):
        self.path = path
        self.properties = {}
        self._name = path.split(".")[-1]

    def derive(self, parent, name: str):
        """Mock derive method."""
        mock_obj = MockPlantSimObject(f"{parent.path}.{name}")
        mock_obj._name = name
        return mock_obj

    def connect(self, from_obj, to_obj):
        """Mock connect method."""
        print(f"Mock connection: {from_obj._name} -> {to_obj._name}")

    def __setattr__(self, name: str, value: Any):
        if name.startswith("_") or name in ["path", "properties"]:
            super().__setattr__(name, value)
        else:
            self.properties[name] = value
            print(f"Mock: Set {self._name}.{name} = {value}")

    def __getattr__(self, name: str):
        if name in self.properties:
            return self.properties[name]
        # Return a mock object for chained attribute access
        return MockPlantSimObject(f"{self.path}.{name}")


class PlantSimInterface:
    """Interface for Plant Simulation API operations."""

    def __init__(self, config: Dict, com_object: Any = None):
        """
        Initialize Plant Simulation interface.
        
        Args:
            config: Configuration dictionary.
            com_object: Optional active Plant Simulation COM object (win32com).
        """
        self.config = config
        self.plantsim_settings = config.get("plantsim_settings", {})
        self.error_handling = config.get("error_handling", {})
        self.plant_sim_com = com_object

        # Initialize Plant Simulation objects
        self._init_plantsim_objects()

        # Track created objects and connections
        self.created_objects = {}  # resource_id -> plant_sim_object
        self.created_connections = []
        self.material_units = {}  # product_type -> mu_object

        # Statistics
        self.stats = {
            "objects_created": 0,
            "connections_created": 0,
            "errors": 0,
            "warnings": 0,
        }

    def _init_plantsim_objects(self):
        """Initialize Plant Simulation framework objects."""
        model_frame_path = self.plantsim_settings.get("model_frame", ".Models.Model")
        connector_path = self.plantsim_settings.get("connector", ".MaterialFlow.Connector")

        if self.plant_sim_com:
            # Use COM Wrappers
            self.model_frame = COMPlantSimObject(self.plant_sim_com, model_frame_path)
            self.connector = COMPlantSimObject(self.plant_sim_com, connector_path)
            print("PlantSimInterface: Using Active COM Connection.")

        elif PLANT_SIM_AVAILABLE and PlantSimulation is not None:
            # Use Internal Python Module
            try:
                self.model_frame = PlantSimulation.Object(model_frame_path)
                self.connector = PlantSimulation.Object(connector_path)
            except Exception as e:
                raise PlantSimulationError(
                    f"Failed to initialize Plant Simulation objects: {e}"
                )
        else:
            # Mock objects for testing
            self.model_frame = MockPlantSimObject(model_frame_path)
            self.connector = MockPlantSimObject(connector_path)
            print("PlantSimInterface: Using MOCK Objects (Dry Run).")

    def create_objects(self, mappings: Dict[str, PlantSimMapping]) -> Dict[str, Any]:
        """Create all Plant Simulation objects from mappings."""
        print(f"Creating {len(mappings)} Plant Simulation objects...")

        created_objects = {}

        for resource_id, mapping in mappings.items():
            try:
                obj = self._create_single_object(mapping)
                if obj:
                    created_objects[resource_id] = obj
                    self.created_objects[resource_id] = obj
                    self.stats["objects_created"] += 1
                    print(
                        f"Created object: {mapping.resource.name} ({mapping.resource.resource_type})"
                    )

            except Exception as e:
                error_msg = f"Failed to create object {mapping.resource.name}: {e}"
                self._handle_error("creation", error_msg, mapping)

        return created_objects

    def _create_single_object(self, mapping: PlantSimMapping) -> Optional[Any]:
        """Create a single Plant Simulation object."""
        # Get template
        template_name = mapping.template
        if not template_name:
            mapping.add_error("No template specified")
            return None

        # Get template object
        template_path = self.plantsim_settings.get("templates", {}).get(template_name)
        if not template_path:
            template_path = f"{self.plantsim_settings.get('user_objects', '.UserObjects')}.{template_name}"

        try:
            if self.plant_sim_com:
                template = COMPlantSimObject(self.plant_sim_com, template_path)
            elif PLANT_SIM_AVAILABLE and PlantSimulation is not None:
                template = PlantSimulation.Object(template_path)
            else:
                template = MockPlantSimObject(template_path)
        except Exception as e:
            mapping.add_error(f"Template '{template_name}' not found: {e}")
            return None

        # Generate object name
        obj_name = self._get_object_name(mapping)

        # Create object
        try:
            obj = template.derive(self.model_frame, obj_name)

            # Set properties
            self._set_object_properties(obj, mapping)

            return obj

        except Exception as e:
            mapping.add_error(f"Failed to create object from template: {e}")
            return None

    def _get_object_name(self, mapping: PlantSimMapping) -> str:
        """Generate object name from mapping."""
        # Try to get sanitized name from properties
        for prop_name, prop_data in mapping.properties.items():
            if prop_name.lower() == "name":
                return prop_data["value"]

        # Fallback to resource name (sanitized)
        from interpreter.mapping_engine import NameSanitizer

        naming_config = self.plantsim_settings.get("naming", {})
        sanitizer = NameSanitizer(naming_config)
        return sanitizer.sanitize_name(mapping.resource.name)

    def _set_object_properties(self, obj: Any, mapping: PlantSimMapping):
        """Set properties on Plant Simulation object."""
        for prop_name, prop_data in mapping.properties.items():
            if prop_name.lower() == "name":
                continue  # Name is set during object creation

            try:
                self._set_single_property(obj, prop_name, prop_data, mapping)
            except Exception as e:
                error_msg = f"Failed to set property {prop_name}: {e}"
                self._handle_error("property", error_msg, mapping)

    def _set_single_property(
        self, obj: Any, prop_name: str, prop_data: Dict, mapping: PlantSimMapping
    ):
        """Set a single property on Plant Simulation object."""
        value = prop_data["value"]
        data_type = prop_data["data_type"]

        # Handle special property types
        if data_type == "material_unit":
            self._handle_material_unit_property(obj, prop_name, value, mapping)
            return
        elif data_type == "special":
            # Skip special internal properties
            return
        elif data_type == "list":
            # Special handling for _3D.Rotation
            if prop_name == "_3D.Rotation":
                try:
                    # Direct assignment
                    obj._3D.Rotation = value  # [angle, axis_x, axis_y, axis_z] format
                except Exception as e:
                    print(
                        f"Info: Could not set rotation for {mapping.resource.name}: {e}"
                    )
                return
            # Handle other coordinate arrays, etc.
            elif isinstance(value, list):
                setattr(obj, prop_name, value)
            else:
                mapping.add_warning(f"Expected list for {prop_name}, got {type(value)}")
            return

        # Handle nested property access (e.g., other nested properties)
        if "." in prop_name:
            self._set_nested_property(obj, prop_name, value)
        else:
            setattr(obj, prop_name, value)

    def _set_nested_property(self, obj: Any, prop_path: str, value: Any):
        """Set nested property using dot notation."""
        parts = prop_path.split(".")
        current = obj

        # Navigate to the parent object
        for part in parts[:-1]:
            current = getattr(current, part)

        # Set the final property
        try:
            setattr(current, parts[-1], value)
        except Exception as e:
            print(f"Failed to set {prop_path} = {value}: {e}")
            raise

    def _handle_material_unit_property(
        self, obj: Any, prop_name: str, mu_name: str, mapping: PlantSimMapping
    ):
        """Handle Material Unit assignment."""
        try:
            # Get or create Material Unit object
            mu_obj = self._get_or_create_material_unit(mu_name, mapping)

            if mu_obj:
                setattr(obj, prop_name, mu_obj)
                print(f"Assigned Material Unit '{mu_name}' to {mapping.resource.name}")
            else:
                mapping.add_error(f"Failed to create Material Unit '{mu_name}'")

        except Exception as e:
            mapping.add_error(f"Error assigning Material Unit '{mu_name}': {e}")

    def _get_or_create_material_unit(
        self, mu_name: str, mapping: PlantSimMapping
    ) -> Optional[Any]:
        """Get existing or create new Material Unit object."""
        # Check if already created
        if mu_name in self.material_units:
            return self.material_units[mu_name]

        # Create new Material Unit
        try:
            mu_config = self.config.get("material_units", {})
            template_path = mu_config.get("template_path", ".UserObjects.PartA")
            
            # Determine user objects path
            user_objs_path = self.plantsim_settings.get("user_objects", ".UserObjects")

            if self.plant_sim_com:
                 # COM Wrapper
                mu_template = COMPlantSimObject(self.plant_sim_com, template_path)
                # derive needs the parent *object* (wrapper)
                parent_obj = COMPlantSimObject(self.plant_sim_com, user_objs_path)
                mu_obj = mu_template.derive(parent_obj, mu_name)

            elif PLANT_SIM_AVAILABLE and PlantSimulation is not None:
                mu_template = PlantSimulation.Object(template_path)
                mu_obj = mu_template.derive(
                    PlantSimulation.Object(user_objs_path),
                    mu_name,
                )
            else:
                mu_template = MockPlantSimObject(template_path)
                mu_obj = mu_template.derive(MockPlantSimObject(user_objs_path), mu_name)

            self.material_units[mu_name] = mu_obj
            print(f"Created Material Unit: {mu_name}")
            return mu_obj

        except Exception as e:
            print(f"Error creating Material Unit '{mu_name}': {e}")
            return None

    def create_connections(self, cmsd_data: CMSDData) -> List[Tuple[str, str]]:
        """Create connections between objects."""
        print(f"Creating {len(cmsd_data.connections)} connections...")

        created_connections = []

        for connection in cmsd_data.connections:
            try:
                success = self._create_single_connection(
                    connection.from_resource_id, connection.to_resource_id
                )
                if success:
                    created_connections.append(
                        (connection.from_resource_id, connection.to_resource_id)
                    )
                    self.stats["connections_created"] += 1
                    print(
                        f"Created connection: {connection.from_resource_id} -> {connection.to_resource_id}"
                    )

            except Exception as e:
                error_msg = f"Failed to create connection {connection.from_resource_id} -> {connection.to_resource_id}: {e}"
                self._handle_error("connection", error_msg)

        self.created_connections = created_connections
        return created_connections

    def _create_single_connection(
        self, from_resource_id: str, to_resource_id: str
    ) -> bool:
        """Create a single connection between two objects."""
        from_obj = self.created_objects.get(from_resource_id)
        to_obj = self.created_objects.get(to_resource_id)

        if not from_obj:
            print(
                f"Warning: Source object '{from_resource_id}' not found for connection"
            )
            return False

        if not to_obj:
            print(f"Warning: Target object '{to_resource_id}' not found for connection")
            return False

        try:
            self.connector.connect(from_obj, to_obj)
            return True
        except Exception as e:
            print(f"Error creating connection: {e}")
            return False

    def _handle_error(
        self, error_type: str, message: str, mapping: Optional[PlantSimMapping] = None
    ):
        """Handle errors according to configuration."""
        self.stats["errors"] += 1

        if mapping:
            mapping.add_error(message)

        error_config = self.error_handling.get(
            f"on_{error_type}_error", "warn_and_continue"
        )

        if error_config == "error_and_stop":
            raise PlantSimulationError(message)
        elif error_config == "warn_and_continue":
            print(f"ERROR: {message}")
        # "ignore" does nothing

    def get_statistics(self) -> Dict[str, int]:
        """Get creation statistics."""
        return self.stats.copy()

    def validate_created_objects(self) -> Dict[str, List[str]]:
        """Validate all created objects and return issues."""
        issues = {"errors": [], "warnings": []}

        # Check for orphaned objects (no connections)
        connected_objects = set()
        for from_id, to_id in self.created_connections:
            connected_objects.add(from_id)
            connected_objects.add(to_id)

        for resource_id in self.created_objects:
            if resource_id not in connected_objects:
                issues["warnings"].append(f"Object '{resource_id}' has no connections")

        return issues


def create_plantsim_interface(config: Dict, com_object: Any = None) -> PlantSimInterface:
    """Factory function to create Plant Simulation interface."""
    return PlantSimInterface(config, com_object)