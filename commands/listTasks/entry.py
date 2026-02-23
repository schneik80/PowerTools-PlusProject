import adsk.core
import adsk.fusion
import json
import os
import webbrowser
from urllib.parse import quote

from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

# Command identity information
CMD_ID = f"{config.COMPANY_NAME}_{config.ADDIN_NAME}_listTasks"
CMD_NAME = "List Tasks"
CMD_Description = "List ClickUp tasks linked to the current Fusion document"

IS_PROMOTED = True
WORKSPACE_ID = config.design_workspace
TAB_ID = config.tools_tab_id
TAB_NAME = config.my_tab_name

PANEL_ID = config.my_panel_id
PANEL_NAME = config.my_panel_name
PANEL_AFTER = config.my_panel_after

# Reuse the addtask icons until dedicated ones are created
ICON_FOLDER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "addtask", "resources", ""
)

# ClickUp API configuration
CLICKUP_API_BASE = "https://api.clickup.com/api/v2"

CACHE_DIR = config.CACHE_DIR
AUTH_JSON_PATH = os.path.join(CACHE_DIR, "auth.json")
PROJECTS_JSON_PATH = os.path.join(CACHE_DIR, "projects.json")

# Priority mapping: ClickUp integer → display label
_PRIORITY_LABEL = {1: "🔴 Urgent", 2: "🟠 High", 3: "🔵 Normal", 4: "⚪ Low"}
_PRIORITY_SORT_KEY = {1: 0, 2: 1, 3: 2, 4: 3}

local_handlers = []

# Module-level state shared between command_created and command_input_changed
_list_url: str = ""


def start():
    """Executed when add-in is run."""
    cmd_def = ui.commandDefinitions.addButtonDefinition(
        CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER
    )
    futil.add_handler(cmd_def.commandCreated, command_created)

    workspace = ui.workspaces.itemById(WORKSPACE_ID)

    tab = workspace.toolbarTabs.itemById(TAB_ID)
    if not tab:
        tab = workspace.toolbarTabs.add(TAB_ID, TAB_NAME)

    panel = tab.toolbarPanels.itemById(PANEL_ID)
    if not panel:
        panel = tab.toolbarPanels.add(PANEL_ID, PANEL_NAME, PANEL_AFTER, False)

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
                if command_control:
                    command_control.deleteMe()

    command_definition = ui.commandDefinitions.itemById(CMD_ID)
    if command_definition:
        command_definition.deleteMe()


def command_created(args: adsk.core.CommandCreatedEventArgs):
    """Builds the task-list dialog."""
    global _list_url
    _list_url = ""

    futil.log(f"{CMD_NAME}: Command Created — building task list dialog.")

    # ------------------------------------------------------------------ #
    # Pre-flight: require auth.json and projects.json                     #
    # ------------------------------------------------------------------ #
    missing = []
    if not os.path.isfile(AUTH_JSON_PATH):
        missing.append(f"  • {AUTH_JSON_PATH}")
    if not os.path.isfile(PROJECTS_JSON_PATH):
        missing.append(f"  • {PROJECTS_JSON_PATH}")

    if missing:
        ui.messageBox(
            "Required configuration files are missing:\n\n"
            + "\n".join(missing)
            + "\n\nRun 'Set Tokens' and 'Map Project' first.",
            "Setup Required",
        )
        args.command.isAutoExecute = True
        return

    # ------------------------------------------------------------------ #
    # Resolve active document → project URN → list_id + clickup_url      #
    # ------------------------------------------------------------------ #
    doc = app.activeDocument
    data_file = doc.dataFile if doc else None
    if not doc or not data_file:
        ui.messageBox(
            "Please open a saved Fusion 360 document first.",
            "No Document",
        )
        args.command.isAutoExecute = True
        return

    doc_name = doc.name
    doc_urn = data_file.id

    project = data_file.parentProject
    project_urn = project.id if project else None
    if not project_urn:
        ui.messageBox(
            "Could not determine the current Fusion 360 project.",
            "Project Not Found",
        )
        args.command.isAutoExecute = True
        return

    futil.log(f"{CMD_NAME}: project_urn='{project_urn}' doc_urn='{doc_urn}'")

    list_id = _load_list_id_for_project(project_urn)
    if not list_id:
        ui.messageBox(
            "No ClickUp list ID configured for this project.\n\n"
            "Run 'Map Project' to register the current project.",
            "List ID Not Configured",
        )
        args.command.isAutoExecute = True
        return

    _list_url = _load_clickup_url_for_project(project_urn)
    futil.log(f"{CMD_NAME}: list_id='{list_id}'  list_url='{_list_url}'")

    api_token = _load_api_token()
    if not api_token:
        ui.messageBox(
            f"ClickUp API token not found.\n\nPlease run 'Set Tokens'.",
            "Authentication Error",
        )
        args.command.isAutoExecute = True
        return

    # ------------------------------------------------------------------ #
    # Fetch tasks filtered by the Fusion Document URN custom field        #
    # ------------------------------------------------------------------ #
    urn_field_id = _get_urn_custom_field_id(list_id, api_token)
    if not urn_field_id:
        ui.messageBox(
            "The 'Fusion Document URN' custom field was not found on this ClickUp list.\n\n"
            "Add a text custom field named 'Fusion Document URN' to your ClickUp list,\n"
            "then create tasks with 'Add Task' to populate it.",
            "Custom Field Missing",
        )
        args.command.isAutoExecute = True
        return

    tasks = _fetch_tasks_for_urn(list_id, urn_field_id, doc_urn, api_token)
    futil.log(f"{CMD_NAME}: {len(tasks)} task(s) found for this document.")

    # Sort by priority ascending (Urgent=1 first, then High, Normal, Low, unset last)
    def _priority_sort_key(task):
        raw = task.get("priority") or {}
        try:
            return _PRIORITY_SORT_KEY.get(int(raw.get("id", 99)), 99)
        except (ValueError, TypeError):
            return 99

    tasks.sort(key=_priority_sort_key)

    # ------------------------------------------------------------------ #
    # Build dialog inputs                                                 #
    # ------------------------------------------------------------------ #
    inputs = args.command.commandInputs

    # Info bar: document name
    info_text = (
        f"<b>Document:</b> {doc_name}<br>"
        f"<b>Tasks linked to this document:</b> {len(tasks)}"
    )
    inputs.addTextBoxCommandInput("info", "", info_text, 2, True)

    # Open List button — rendered as a clickable HTML link
    if _list_url:
        open_btn = inputs.addTextBoxCommandInput(
            "open_list",
            "",
            f'<a href="{_list_url}">🔗 Open List in ClickUp</a>',
            1,
            True,
        )
    else:
        open_btn = inputs.addTextBoxCommandInput(
            "open_list", "", "(No list URL configured)", 1, True
        )

    # ------------------------------------------------------------------ #
    # Task table: Name | Priority | Status                                #
    # Column width ratios: 5 : 2 : 2                                     #
    # ------------------------------------------------------------------ #
    table = inputs.addTableCommandInput("tasks_table", "Tasks", 3, "5:2:2")
    table.hasGrid = True
    table.minimumVisibleRows = 3
    table.maximumVisibleRows = 20

    # Header row
    def _add_header(input_id: str, label: str) -> adsk.core.StringValueCommandInput:
        h = inputs.addStringValueInput(input_id, "", label)
        h.isReadOnly = True
        return h

    table.addCommandInput(_add_header("h_name",     "Task Name"),  0, 0)
    table.addCommandInput(_add_header("h_priority", "Priority"),   0, 1)
    table.addCommandInput(_add_header("h_status",   "Status"),     0, 2)

    if not tasks:
        empty = inputs.addTextBoxCommandInput(
            "no_tasks", "", "No tasks found for this document.", 1, True
        )
        table.addCommandInput(empty, 1, 0, 0, 3)  # span all 3 columns
    else:
        for i, task in enumerate(tasks, start=1):
            row = i  # row 0 is header

            task_name = task.get("name", "(unnamed)")
            task_url  = task.get("url", "")
            priority_id = None
            raw_priority = task.get("priority")
            if raw_priority and raw_priority.get("id"):
                try:
                    priority_id = int(raw_priority["id"])
                except (ValueError, TypeError):
                    pass
            priority_label = _PRIORITY_LABEL.get(priority_id, "—")
            status = task.get("status", {}).get("status", "—").title()

            # Name cell — clickable HTML hyperlink
            if task_url:
                name_html = f'<a href="{task_url}">{task_name}</a>'
            else:
                name_html = task_name

            name_cell = inputs.addTextBoxCommandInput(
                f"name_{row}", "", name_html, 1, True
            )

            priority_cell = inputs.addStringValueInput(
                f"priority_{row}", "", priority_label
            )
            priority_cell.isReadOnly = True

            status_cell = inputs.addStringValueInput(
                f"status_{row}", "", status
            )
            status_cell.isReadOnly = True

            table.addCommandInput(name_cell,     row, 0)
            table.addCommandInput(priority_cell, row, 1)
            table.addCommandInput(status_cell,   row, 2)

    # Connect events
    futil.add_handler(
        args.command.execute, command_execute, local_handlers=local_handlers
    )
    futil.add_handler(
        args.command.destroy, command_destroy, local_handlers=local_handlers
    )


def command_execute(args: adsk.core.CommandEventArgs):
    """OK was clicked — nothing to persist, dialog just closes."""
    futil.log(f"{CMD_NAME}: Dialog closed.")


def command_destroy(args: adsk.core.CommandEventArgs):
    """Called when the dialog closes."""
    futil.log(f"{CMD_NAME}: Destroyed. Clearing handlers.")
    global local_handlers
    local_handlers = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_api_token() -> str:
    """Read the ClickUp API token from cache/auth.json."""
    if not os.path.isfile(AUTH_JSON_PATH):
        return ""
    try:
        with open(AUTH_JSON_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return ""
    return data.get("clickup_api_token", "").strip()


def _load_list_id_for_project(project_urn: str) -> str:
    """Return the clickup_list_id for the given project URN from projects.json."""
    if not os.path.isfile(PROJECTS_JSON_PATH):
        return ""
    try:
        with open(PROJECTS_JSON_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return ""
    return data.get("projects", {}).get(project_urn, {}).get("clickup_list_id", "").strip()


def _load_clickup_url_for_project(project_urn: str) -> str:
    """Return the clickup_url for the given project URN from projects.json."""
    if not os.path.isfile(PROJECTS_JSON_PATH):
        return ""
    try:
        with open(PROJECTS_JSON_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return ""
    return data.get("projects", {}).get(project_urn, {}).get("clickup_url", "").strip()


def _get_urn_custom_field_id(list_id: str, api_token: str) -> str:
    """Return the field ID of the 'Fusion Document URN' custom field on the given list."""
    TARGET_NAME = "Fusion Document URN"
    fields_url = f"{CLICKUP_API_BASE}/list/{list_id}/field"
    futil.log(f"{CMD_NAME}: _get_urn_custom_field_id — querying list '{list_id}'")

    try:
        req = adsk.core.HttpRequest.create(fields_url, adsk.core.HttpMethods.GetMethod)
        req.setHeader("Authorization", api_token)
        req.setHeader("Accept", "application/json")
        response = req.executeSync()

        if not (200 <= response.statusCode < 300):
            futil.log(f"{CMD_NAME}: _get_urn_custom_field_id — HTTP {response.statusCode}: {response.data}")
            return ""

        fields = json.loads(response.data).get("fields", [])
        matched = next((f for f in fields if f.get("name") == TARGET_NAME), None)
        if matched:
            futil.log(f"{CMD_NAME}: _get_urn_custom_field_id — found id='{matched.get('id')}'")
            return matched.get("id", "")

        futil.log(f"{CMD_NAME}: _get_urn_custom_field_id — '{TARGET_NAME}' not found.")
        return ""

    except Exception as exc:
        futil.log(f"{CMD_NAME}: _get_urn_custom_field_id — exception: {exc}")
        return ""


def _fetch_tasks_for_urn(
    list_id: str, urn_field_id: str, doc_urn: str, api_token: str
) -> list:
    """Query GET /api/v2/list/{list_id}/task filtered by the Fusion Document URN field.

    Returns a list of raw ClickUp task dicts. Results are limited to 100 tasks
    (page 0). The caller is responsible for sorting.

    API docs: https://developer.clickup.com/reference/gettasks
    Custom field filter: ?custom_field=[{"field_id":"...","operator":"=","value":"..."}]
    """
    import urllib.parse

    cf_filter = json.dumps([{"field_id": urn_field_id, "operator": "=", "value": doc_urn}])
    params = urllib.parse.urlencode({
        "custom_field": cf_filter,
        "page": 0,
        "include_closed": "true",
    })
    url = f"{CLICKUP_API_BASE}/list/{list_id}/task?{params}"
    futil.log(f"{CMD_NAME}: _fetch_tasks_for_urn — GET '{url}'")

    try:
        req = adsk.core.HttpRequest.create(url, adsk.core.HttpMethods.GetMethod)
        req.setHeader("Authorization", api_token)
        req.setHeader("Accept", "application/json")
        response = req.executeSync()

        futil.log(f"{CMD_NAME}: _fetch_tasks_for_urn — HTTP {response.statusCode}")

        if not (200 <= response.statusCode < 300):
            futil.log(f"{CMD_NAME}: _fetch_tasks_for_urn — error body: {response.data}")
            return []

        return json.loads(response.data).get("tasks", [])

    except Exception as exc:
        futil.log(f"{CMD_NAME}: _fetch_tasks_for_urn — exception: {exc}")
        return []
