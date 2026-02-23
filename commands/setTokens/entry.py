import adsk.core
import adsk.fusion
import os
import json
from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f"{config.COMPANY_NAME}_{config.ADDIN_NAME}_setClickUpTokens"
CMD_NAME = "Set ClickUp Tokens"
CMD_Description = "Set the ClickUp and TinyURL API tokens used by Power Tools"

# QAT flyout (shared across PowerTools add-ins — create only if absent).
PT_SETTINGS_ID = "PTSettings"
PT_SETTINGS_NAME = "PowerTools Settings"

ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "")

local_handlers = []


def start():
    cmd_def = ui.commandDefinitions.addButtonDefinition(
        CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER
    )
    futil.add_handler(cmd_def.commandCreated, command_created)

    qat = ui.toolbars.itemById("QAT")
    file_dropdown = adsk.core.DropDownControl.cast(
        qat.controls.itemById("FileSubMenuCommand")
    )

    pt_settings_control = file_dropdown.controls.itemById(PT_SETTINGS_ID)
    if not pt_settings_control:
        pt_settings = file_dropdown.controls.addDropDown(
            PT_SETTINGS_NAME, "", PT_SETTINGS_ID
        )
    else:
        pt_settings = adsk.core.DropDownControl.cast(pt_settings_control)

    pt_settings.controls.addCommand(cmd_def)


def stop():
    qat = ui.toolbars.itemById("QAT")
    file_dropdown = adsk.core.DropDownControl.cast(
        qat.controls.itemById("FileSubMenuCommand")
    )
    pt_settings = adsk.core.DropDownControl.cast(
        file_dropdown.controls.itemById(PT_SETTINGS_ID)
    )

    if pt_settings:
        command_control = pt_settings.controls.itemById(CMD_ID)
        if command_control:
            command_control.deleteMe()

        if pt_settings.controls.count == 0:
            pt_settings.deleteMe()

    command_definition = ui.commandDefinitions.itemById(CMD_ID)
    if command_definition:
        command_definition.deleteMe()


def command_created(args: adsk.core.CommandCreatedEventArgs):
    futil.log(f"{CMD_NAME}: Command Created.")

    # Load existing token values if auth.json exists
    existing_clickup_token = ""
    existing_tinyurl_token = ""
    try:
        with open(config.AUTH_JSON_PATH, "r", encoding="utf-8") as f:
            auth_data = json.load(f)
            existing_clickup_token = auth_data.get("clickup_api_token", "")
            existing_tinyurl_token = auth_data.get("tinyurl_api_token", "")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    inputs = args.command.commandInputs

    inputs.addStringValueInput(
        "clickup_api_token", "ClickUp API Token:", existing_clickup_token
    )
    inputs.addStringValueInput(
        "tinyurl_api_token", "TinyURL API Token:", existing_tinyurl_token
    )

    futil.add_handler(
        args.command.execute, command_execute, local_handlers=local_handlers
    )
    futil.add_handler(
        args.command.destroy, command_destroy, local_handlers=local_handlers
    )


def command_execute(args: adsk.core.CommandEventArgs):
    futil.log(f"{CMD_NAME}: Execute.")

    try:
        inputs = args.command.commandInputs

        clickup_token_input = inputs.itemById("clickup_api_token")
        tinyurl_token_input = inputs.itemById("tinyurl_api_token")

        clickup_token = getattr(clickup_token_input, "value", "").strip()
        tinyurl_token = getattr(tinyurl_token_input, "value", "").strip()

        # Load existing auth data to preserve any unrelated keys
        auth_data = {}
        try:
            with open(config.AUTH_JSON_PATH, "r", encoding="utf-8") as f:
                auth_data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        # Update only the two token keys; leave others untouched
        if clickup_token:
            auth_data["clickup_api_token"] = clickup_token
        if tinyurl_token:
            auth_data["tinyurl_api_token"] = tinyurl_token

        # Ensure cache directory exists
        os.makedirs(config.CACHE_DIR, exist_ok=True)

        with open(config.AUTH_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(auth_data, f, indent=2)

        futil.log(f"{CMD_NAME}: auth.json saved to '{config.AUTH_JSON_PATH}'.")
        ui.messageBox("API tokens saved.", CMD_NAME)

    except Exception as e:
        msg = f"Error saving tokens: {e}"
        futil.log(f"{CMD_NAME}: {msg}")
        ui.messageBox(msg, "Error")


def command_destroy(args: adsk.core.CommandEventArgs):
    futil.log(f"{CMD_NAME}: Command Destroyed.")
    global local_handlers
    local_handlers = []
