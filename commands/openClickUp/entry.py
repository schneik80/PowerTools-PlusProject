import adsk.core
import adsk.fusion
import os
import json
import webbrowser
from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

# Command identity information
CMD_ID = f"{config.COMPANY_NAME}_{config.ADDIN_NAME}_openClickUp"
CMD_NAME = "Open ClickUp"
CMD_Description = (
    "Open the ClickUp project associated with the current Fusion 360 document"
)

# Specify that the command will be promoted to the panel
IS_PROMOTED = True
WORKSPACE_ID = config.design_workspace
TAB_ID = config.tools_tab_id
TAB_NAME = config.my_tab_name

PANEL_ID = config.my_panel_id
PANEL_NAME = config.my_panel_name
PANEL_AFTER = config.my_panel_after

# Resource location for command icons
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "")

# Local list of event handlers
local_handlers = []


def start():
    """Executed when add-in is run."""
    # Create a command Definition
    cmd_def = ui.commandDefinitions.addButtonDefinition(
        CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER
    )

    # Define an event handler for the command created event
    futil.add_handler(cmd_def.commandCreated, command_created)

    # Add a button into the UI
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
    control.isPromoted = IS_PROMOTED


def stop():
    """Executed when add-in is stopped."""
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


def command_created(args: adsk.core.CommandCreatedEventArgs):
    """Called when the command is created."""
    futil.log(f"{CMD_NAME} Command Created Event")

    # This command executes immediately without a dialog
    futil.add_handler(
        args.command.execute, command_execute, local_handlers=local_handlers
    )
    futil.add_handler(
        args.command.destroy, command_destroy, local_handlers=local_handlers
    )


def command_execute(args: adsk.core.CommandEventArgs):
    """Main execution function for the Open ClickUp command."""
    futil.log(f"{CMD_NAME} Command Execute Event")

    try:
        # Get the active document
        doc = app.activeDocument
        if not doc:
            ui.messageBox(
                "No active document found. Please open a Fusion 360 document first.",
                "No Document",
            )
            return

        # Get the data file (this contains the project information)
        data_file = doc.dataFile
        if not data_file:
            ui.messageBox("Unable to access document data file.", "Error")
            return

        # Get the project that contains this document
        project = data_file.parentProject
        if not project:
            ui.messageBox("Unable to access parent project for this document.", "Error")
            return

        # Get the project URN
        project_urn = project.id
        futil.log(f"Found project URN: {project_urn}")

        # Load the projects JSON file
        try:
            with open(config.PROJECTS_JSON_PATH, "r") as f:
                projects_data = json.load(f)
        except FileNotFoundError:
            ui.messageBox(
                f"Projects configuration file not found at: {config.PROJECTS_JSON_PATH}",
                "Configuration Error",
            )
            return
        except json.JSONDecodeError:
            ui.messageBox(
                "Invalid JSON format in projects configuration file.",
                "Configuration Error",
            )
            return

        # Look up the project URN in the JSON data
        if "projects" not in projects_data:
            ui.messageBox(
                'Invalid projects configuration: missing "projects" key.',
                "Configuration Error",
            )
            return

        if project_urn in projects_data["projects"]:
            project_info = projects_data["projects"][project_urn]
            clickup_url = project_info.get("clickup_url")
            project_name = project_info.get("project_name", "Unknown Project")

            if clickup_url:
                # Open the ClickUp URL in the default web browser
                webbrowser.open(clickup_url)
                futil.log(f"Opened ClickUp URL: {clickup_url}")
                ui.messageBox(f"Opened ClickUp project: {project_name}", "Success")
            else:
                ui.messageBox(
                    f"No ClickUp URL configured for project: {project_name}",
                    "Configuration Missing",
                )
        else:
            ui.messageBox(
                f"Project URN not found in configuration: {project_urn}\\n\\nPlease add this project to the projects.json file.",
                "Project Not Configured",
            )

    except Exception as e:
        futil.log(f"Error in {CMD_NAME}: {str(e)}")
        ui.messageBox(f"An error occurred: {str(e)}", "Error")


def command_destroy(args: adsk.core.CommandEventArgs):
    """Called when the command terminates."""
    futil.log(f"{CMD_NAME} Command Destroy Event")
    global local_handlers
    local_handlers = []
