import adsk.core
import adsk.fusion
import os
import json
from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface


# TODO *** Specify the command identity information. ***
CMD_ID = f"{config.COMPANY_NAME}_{config.ADDIN_NAME}_saveClickUpURL"
CMD_NAME = "Save ClickUp URL"
CMD_Description = "Save a ClickUp URL for the current Fusion project"

# Specify that the command will be promoted to the panel.
IS_PROMOTED = False
WORKSPACE_ID = config.design_workspace
TAB_ID = config.tools_tab_id
TAB_NAME = config.my_tab_name

PANEL_ID = config.my_panel_id
PANEL_NAME = config.my_panel_name
PANEL_AFTER = config.my_panel_after

# Resource location for command icons, here we assume a sub folder in this directory named "resources".
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "")

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected.
local_handlers = []


# Executed when add-in is run.
def start():
    # Create a command Definition.
    cmd_def = ui.commandDefinitions.addButtonDefinition(
        CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER
    )

    # Define an event handler for the command created event. It will be called when the button is clicked.
    futil.add_handler(cmd_def.commandCreated, command_created)

    # ******** Add a button into the UI so the user can run the command. ********
    # Get the target workspace the button will be created in.
    workspace = ui.workspaces.itemById(WORKSPACE_ID)

    # Get or create the tab
    tab = workspace.toolbarTabs.itemById(TAB_ID)
    if not tab:
        tab = workspace.toolbarTabs.add(TAB_ID, TAB_NAME)

    # Get or create the panel
    panel = tab.toolbarPanels.itemById(PANEL_ID)
    if not panel:
        panel = tab.toolbarPanels.add(PANEL_ID, PANEL_NAME, PANEL_AFTER, False)

    # Create the button command control in the UI
    control = panel.controls.addCommand(cmd_def, "", False)

    # Specify if the command is promoted to the main toolbar.
    control.isPromoted = IS_PROMOTED


# Executed when add-in is stopped.
def stop():
    # Get the various UI elements for this command
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    if workspace:
        tab = workspace.toolbarTabs.itemById(TAB_ID)
        if tab:
            panel = tab.toolbarPanels.itemById(PANEL_ID)
            if panel:
                command_control = panel.controls.itemById(CMD_ID)
                # Delete the button command control
                if command_control:
                    command_control.deleteMe()

    # Delete the command definition
    command_definition = ui.commandDefinitions.itemById(CMD_ID)
    if command_definition:
        command_definition.deleteMe()


# Function that is called when a user clicks the corresponding button in the UI.
# This defines the contents of the command dialog and connects to the command related events.
def command_created(args: adsk.core.CommandCreatedEventArgs):
    # General logging for debug.
    futil.log(f"{CMD_NAME} Command Created Event")

    # Check if there's an active document
    doc = app.activeDocument
    if not doc:
        ui.messageBox(
            "No active document found. Please open a Fusion document first.",
            "No Document",
        )
        return

    # Check if the document is saved
    if not doc.isSaved:
        ui.messageBox(
            "The active document must be saved before configuring ClickUp URL. Please save the document first.",
            "Document Not Saved",
        )
        return

    # Get the data file and project information
    data_file = doc.dataFile
    if not data_file:
        ui.messageBox("Unable to access document data file.", "Error")
        return

    project = data_file.parentProject
    if not project:
        ui.messageBox("Unable to access parent project for this document.", "Error")
        return

    # Get project information
    project_name = project.name
    project_urn = project.id

    # Load existing ClickUp URL if available
    existing_clickup_url = ""
    try:
        with open(config.PROJECTS_JSON_PATH, "r") as f:
            projects_data = json.load(f)
            if "projects" in projects_data and project_urn in projects_data["projects"]:
                existing_clickup_url = projects_data["projects"][project_urn].get(
                    "clickup_url", ""
                )
    except (FileNotFoundError, json.JSONDecodeError):
        # File doesn't exist or is invalid, we'll create it when saving
        pass

    # Create the dialog inputs
    inputs = args.command.commandInputs

    # Project name (read-only)
    project_name_input = inputs.addTextBoxCommandInput(
        "project_name", "Project Name:", project_name, 1, True
    )

    # Project URN (read-only)
    project_urn_input = inputs.addTextBoxCommandInput(
        "project_urn", "Project URN:", project_urn, 3, True
    )

    # ClickUp URL input
    clickup_url_input = inputs.addStringValueInput(
        "clickup_url", "ClickUp URL:", existing_clickup_url
    )

    # Connect to the events
    futil.add_handler(
        args.command.execute, command_execute, local_handlers=local_handlers
    )
    futil.add_handler(
        args.command.validateInputs,
        command_validate_input,
        local_handlers=local_handlers,
    )
    futil.add_handler(
        args.command.destroy, command_destroy, local_handlers=local_handlers
    )


# This event handler is called when the user clicks the OK button in the command dialog.
def command_execute(args: adsk.core.CommandEventArgs):
    # General logging for debug.
    futil.log(f"{CMD_NAME} Command Execute Event")

    try:
        # Get the input values
        inputs = args.command.commandInputs
        project_name_input = inputs.itemById("project_name")
        project_urn_input = inputs.itemById("project_urn")
        clickup_url_input = inputs.itemById("clickup_url")

        # Access properties using getattr to avoid type checking issues
        project_name = getattr(project_name_input, "text", "")
        project_urn = getattr(project_urn_input, "text", "")
        clickup_url = getattr(clickup_url_input, "value", "").strip()

        # Validate ClickUp URL
        if not clickup_url:
            ui.messageBox("Please enter a ClickUp URL.", "Missing URL")
            return

        # Load existing projects data or create new structure
        projects_data = {"projects": {}}
        try:
            with open(config.PROJECTS_JSON_PATH, "r") as f:
                projects_data = json.load(f)
        except FileNotFoundError:
            # File doesn't exist, will be created
            futil.log("Projects cache not found, creating new one")
        except json.JSONDecodeError:
            # File is corrupted, start fresh
            futil.log("Projects cache corrupted, creating new one")

        # Ensure projects key exists
        if "projects" not in projects_data:
            projects_data["projects"] = {}

        # Check if project already exists
        project_exists = project_urn in projects_data["projects"]

        # Update or add the project
        projects_data["projects"][project_urn] = {
            "project_name": project_name,
            "clickup_url": clickup_url,
        }

        # Save the updated data
        with open(config.PROJECTS_JSON_PATH, "w") as f:
            json.dump(projects_data, f, indent=2)

        # Show success message
        action = "Updated" if project_exists else "Added"
        #ui.messageBox(f"{action} ClickUp URL for project: {project_name}", "Success")
        futil.log(f"{action} ClickUp URL for project URN: {project_urn}")

    except Exception as e:
        error_msg = f"Error saving ClickUp URL: {str(e)}"
        ui.messageBox(error_msg, "Error")
        futil.log(error_msg)


# This event handler is called when the user interacts with any of the inputs in the dialog
# which allows you to verify that all of the inputs are valid and enables the OK button.
def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    # General logging for debug.
    futil.log(f"{CMD_NAME} Validate Input Event")

    inputs = args.inputs

    # Verify that ClickUp URL is not empty
    clickup_url_input = inputs.itemById("clickup_url")
    if clickup_url_input:
        clickup_value = getattr(clickup_url_input, "value", "")
        args.areInputsValid = bool(clickup_value and clickup_value.strip())
    else:
        args.areInputsValid = False


# This event handler is called when the command terminates.
def command_destroy(args: adsk.core.CommandEventArgs):
    # General logging for debug.
    futil.log(f"{CMD_NAME} Command Destroy Event")

    global local_handlers
    local_handlers = []
