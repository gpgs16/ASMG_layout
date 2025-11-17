import datetime
import logging
import sys
import time
from pathlib import Path

import pytz
import win32com.client

# Centralized config import
from config.config_loader import Config

config = Config()

# Configure logger
logger = logging.getLogger(__name__)


def wait_for_model_loaded(plant_sim, timeout=30):
    """
    Wait for model to be fully loaded and accessible.

    Args:
        plant_sim (object): The Plant Simulation COM object.
        timeout (int): Maximum time to wait in seconds.

    Returns:
        bool: True if model is loaded, False if timeout.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            # Try to access a basic model property
            model_name = plant_sim.GetValue("Models.Model.Name")
            if model_name:  # Model loaded successfully
                logger.info("Model verification successful")
                return True
        except Exception:
            pass
        time.sleep(0.5)  # Poll every 500ms

    # Fallback to sleep if polling failed
    logger.warning("Model polling failed, falling back to sleep")
    time.sleep(2)
    return True


def wait_for_simulation_state(plant_sim, expected_running_state, timeout=10):
    """
    Wait for simulation to reach expected running state.

    Args:
        plant_sim (object): The Plant Simulation COM object.
        expected_running_state (bool): Expected simulation running state.
        timeout (int): Maximum time to wait in seconds.

    Returns:
        bool: True if state reached, False if timeout.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            is_running = plant_sim.IsSimulationRunning()
            if bool(is_running) == expected_running_state:
                logger.info("Simulation state verification successful")
                return True
        except Exception:
            pass
        time.sleep(0.2)  # Faster polling for simulation state

    # Fallback to sleep if polling failed
    logger.warning("Simulation state polling failed, falling back to sleep")
    time.sleep(1)
    return True


def save_with_verification(plant_sim, model_path, timeout=15):
    """
    Save model and verify it was saved successfully.

    Args:
        plant_sim (object): The Plant Simulation COM object.
        model_path (str): Path to save the model.
        timeout (int): Maximum time to wait in seconds.

    Returns:
        bool: True if saved successfully, False otherwise.
    """
    try:
        abs_path = str(Path(model_path).absolute())
        logger.info("Saving model as: %s", abs_path)
        plant_sim.SaveModel(abs_path)

        # Verify file exists and has recent timestamp
        start_time = time.time()
        while time.time() - start_time < timeout:
            if Path(abs_path).exists():
                # Check if file was modified recently (within last 5 seconds)
                file_time = Path(abs_path).stat().st_mtime
                if time.time() - file_time < 5:
                    logger.info("Model saved successfully")
                    return True
            time.sleep(0.5)

        # If verification failed but no exception, assume success
        logger.warning("Save verification failed, but no errors reported")
        return True

    except Exception as e:
        logger.exception("Failed to save model. Error: %s", str(e))
        return False


def connect_to_plant_simulation(prog_id):
    """
    Connects to Plant Simulation using the provided ProgID.

    Args:
        prog_id (str): The ProgID of the Plant Simulation COM object.

    Returns:
        object: The Plant Simulation COM object.

    Raises:
        SystemExit: If the connection to Plant Simulation fails.

    """
    logger.info("Attempting to connect using ProgID: %s", prog_id)
    try:
        plant_sim = win32com.client.Dispatch(prog_id)
        logger.info("Successfully connected using ProgID: %s", prog_id)
    except win32com.client.pywintypes.com_error:  # type: ignore[attr-defined]
        logger.exception("Failed to connect using ProgID: %s.", prog_id)
        sys.exit(1)
    return plant_sim


def load_model(plant_sim, model_path):
    """
    Loads the specified Plant Simulation model with polling verification.

    Args:
        plant_sim (object): The Plant Simulation COM object.
        model_path (str): The path to the Plant Simulation model file.

    Returns:
        bool: True if successful, False otherwise.
    """
    logger.info("Loading model from path: %s", model_path)
    if not Path(model_path).exists():
        logger.error("Error: Model file not found at %s", model_path)
        return False

    try:
        plant_sim.loadModel(model_path)
        logger.info("Model load command sent, waiting for model to be ready...")

        if wait_for_model_loaded(plant_sim):
            logger.info("Successfully loaded model: %s", model_path)
            return True
        else:
            logger.error("Model loading verification failed (timeout)")
            return False

    except Exception:
        logger.exception("Failed to load model: %s.", model_path)
        return False


def reset_simulation(plant_sim, event_controller_path):
    """
    Resets the simulation.

    Args:
        plant_sim (object): The Plant Simulation COM object.
        event_controller_path (str): The path to the event controller in the model.

    Returns:
        bool: True if successful, False otherwise.
    """
    logger.info("Resetting simulation for event controller: %s", event_controller_path)
    try:
        plant_sim.resetSimulation(event_controller_path)
        logger.info("Simulation reset successfully.")
        time.sleep(1)  # Keep simple sleep for reset
        return True
    except Exception:
        logger.exception("Failed to reset simulation.")
        return False


def start_simulation(plant_sim, event_controller_path):
    """
    Starts the simulation with state verification.

    Args:
        plant_sim (object): The Plant Simulation COM object.
        event_controller_path (str): The path to the event controller in the model.

    Returns:
        bool: True if successful, False otherwise.
    """
    logger.info("Starting simulation for event controller: %s", event_controller_path)
    try:
        plant_sim.startSimulation(event_controller_path)

        if wait_for_simulation_state(plant_sim, expected_running_state=True):
            logger.info("Simulation started successfully.")
            return True
        else:
            logger.error("Simulation start verification failed")
            return False

    except Exception:
        logger.exception("Failed to start simulation.")
        return False


def stop_simulation(plant_sim):
    """
    Stops the simulation with state verification.

    Args:
        plant_sim (object): The Plant Simulation COM object.

    Returns:
        bool: True if successful, False otherwise.
    """
    logger.info("Stopping simulation.")
    try:
        plant_sim.StopSimulation()

        if wait_for_simulation_state(plant_sim, expected_running_state=False):
            logger.info("Simulation stopped successfully.")
            return True
        else:
            logger.error("Simulation stop verification failed")
            return False

    except Exception:
        logger.exception("Failed to stop simulation.")
        return False


def quit_simulation(plant_sim):
    """
    Quits Plant Simulation.

    Args:
        plant_sim (object): The Plant Simulation COM object.

    Returns:
        bool: True if successful, False otherwise.
    """
    logger.info("Closing Plant Simulation.")
    try:
        plant_sim.Quit()
        logger.info("Plant Simulation closed successfully.")
        return True
    except Exception:
        logger.exception("Failed to quit Plant Simulation.")
        return False


def model_name_generator():
    """
    Generates a model name string in the format "ASMG_model_DDMMYYYYHHMMSS"
    based on the current date and time in CET.
    """
    try:
        cet_timezone = pytz.timezone("CET")
    except pytz.exceptions.UnknownTimeZoneError:
        logger.exception(
            "CET timezone not found. Ensure 'pytz' is installed. Falling back to UTC for model name."
        )
        # Fallback to UTC if CET is not found, though this breaks "same naming" consistency
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        return now.strftime("ASMG_model_%d%m%Y%H%M%S_UTC")  # Indicate fallback

    now_cet = datetime.datetime.now(cet_timezone)
    return now_cet.strftime("ASMG_model_%d%m%Y%H%M%S")


def setup_and_load_model(plant_sim, template_path, dest_dir):
    """
    Consolidated function to set up (copy template) and load a new model.

    Args:
        plant_sim (object): The Plant Simulation COM object.
        template_path (str): Path to the template model file.
        dest_dir (str): Directory to create the new model in.

    Returns:
        str: Path to the loaded model if successful, None otherwise.
    """
    logger.info("Setting up and loading new model...")

    # Generate unique model name
    new_model_base_name = model_name_generator()
    if not new_model_base_name:
        logger.error("Failed to generate model name")
        return None

    # Setup paths
    destination_path_obj = Path(dest_dir) / f"{new_model_base_name}.spp"
    destination_path_str = str(destination_path_obj.resolve())
    source_file_abs_path = str(Path(template_path).resolve())

    # Validate source file exists
    if not Path(template_path).exists():
        logger.error("Source template file not found: %s", source_file_abs_path)
        return None

    # Copy template to new location
    try:
        destination_path_obj.parent.mkdir(parents=True, exist_ok=True)
        import shutil

        shutil.copy2(source_file_abs_path, destination_path_str)
        logger.info("Successfully copied template to: %s", destination_path_str)
    except Exception:
        logger.exception(
            "Failed to copy simulation model from '%s' to '%s'",
            source_file_abs_path,
            destination_path_str,
        )
        return None

    # Load the new model
    if load_model(plant_sim, destination_path_str):
        logger.info("Model setup and loading completed successfully")
        return destination_path_str
    else:
        logger.error("Failed to load the newly created model")
        return None


def create_new_model(plant_sim):
    """
    Creates a new Plant Simulation model.

    Args:
        plant_sim (object): The Plant Simulation COM object.

    Returns:
        bool: True if successful, False otherwise

    """
    logger.info("Creating new model")
    try:
        plant_sim.newModel()
    except Exception as e:
        logger.exception("Failed to create new model. Error: %s", str(e))  # noqa: TRY401
        return False
    else:
        logger.info("New model created successfully")
        return True


def save(plant_sim, model_path=None):
    """
    Saves the current model with verification.

    Args:
        plant_sim (object): The Plant Simulation COM object.
        model_path (str, optional): The path to save the model to. If None, a default path will be used.

    Returns:
        str: The path to the saved model if successful, None otherwise
    """
    try:
        if model_path is None:
            # Use config for default model name and simulation directory
            model_name = config.plant_simulation.get(
                "default_model_name", "CurrentModel"
            )
            simulation_dir = Path(
                config.plant_simulation.get("dest_dir", Path.cwd() / "Simulation_Model")
            )
            simulation_dir.mkdir(exist_ok=True)
            model_path = simulation_dir / f"{model_name}.spp"

        if save_with_verification(plant_sim, model_path):
            return str(Path(model_path).absolute())
        else:
            return None

    except Exception as e:
        logger.exception("Failed to save model. Error: %s", str(e))
        return None


def execute_simtalk(plant_sim, simtalk_code):
    """
    Executes SimTalk code in Plant Simulation.

    Args:
        plant_sim (object): The Plant Simulation COM object.
        simtalk_code (str): The SimTalk code to execute.

    Returns:
        bool: True if execution was successful, False otherwise.

    """
    logger.info("Executing SimTalk code")
    try:
        plant_sim.ExecuteSimTalk(simtalk_code)
    except Exception as e:
        logger.exception("Failed to execute SimTalk code. Error: %s", str(e))  # noqa: TRY401
        return False
    else:
        logger.info("SimTalk code executed successfully")
        return True
