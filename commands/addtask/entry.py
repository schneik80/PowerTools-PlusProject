import adsk.core
import adsk.fusion
import http.client
import json
import os
import tempfile
import time
from datetime import datetime
from urllib.parse import quote

from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

# Command identity information
CMD_ID = f"{config.COMPANY_NAME}_{config.ADDIN_NAME}_addTask"
CMD_NAME = "Add ClickUp Task"
CMD_Description = "Create a new ClickUp task with a name, description, and due date"

# Specify that the command will be promoted to the panel
IS_PROMOTED = True
WORKSPACE_ID = config.design_workspace
TAB_ID = config.tools_tab_id
TAB_NAME = config.my_tab_name

PANEL_ID = config.clickup_panel_id
PANEL_NAME = config.clickup_panel_name
PANEL_AFTER = config.clickup_panel_after

# Resource location for command icons
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "")

# ClickUp API configuration
# The API token is read from cache/auth.json.
# The list ID is read per-project from cache/projects.json ("clickup_list_id" key),
# keyed by the Fusion 360 project URN — the same lookup used by openClickUp and saveURL.
CLICKUP_API_BASE = "https://api.clickup.com/api/v2"

# Path to auth credentials in the shared cache folder
CACHE_DIR = config.CACHE_DIR
AUTH_JSON_PATH = os.path.join(CACHE_DIR, "auth.json")
PROJECTS_JSON_PATH = os.path.join(CACHE_DIR, "projects.json")

# Local list of event handlers used to maintain a reference so
# they are not released and garbage collected.
local_handlers = []

# Module-level cache of list members fetched during command_created so they are
# available by index when command_execute resolves the selected dropdown item.
_list_members: list = []  # [{"id": int, "username": str, "email": str}, ...]

# Module-level list of pre-calculated quick-date (label, value) tuples built in command_created.
_quick_date_options: list = []


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
                if command_control:
                    command_control.deleteMe()

    # Delete the command definition
    command_definition = ui.commandDefinitions.itemById(CMD_ID)
    if command_definition:
        command_definition.deleteMe()


def command_created(args: adsk.core.CommandCreatedEventArgs):
    """Called when the command button is clicked — builds the dialog."""
    futil.log(f"{CMD_NAME}: Command Created — building dialog inputs.")

    # ------------------------------------------------------------------ #
    # Pre-flight: confirm required cache files are present               #
    # ------------------------------------------------------------------ #
    missing = []
    if not os.path.isfile(AUTH_JSON_PATH):
        missing.append(f"  • {AUTH_JSON_PATH}")
    if not os.path.isfile(PROJECTS_JSON_PATH):
        missing.append(f"  • {PROJECTS_JSON_PATH}")

    if missing:
        missing_list = "\n".join(missing)
        futil.log(f"{CMD_NAME}: Aborting — missing cache file(s):\n{missing_list}")
        ui.messageBox(
            "Required configuration files are missing:\n\n"
            f"{missing_list}\n\n"
            "To fix:\n"
            "  1. Run 'Set Tokens' to save your ClickUp and TinyURL API tokens.\n"
            "  2. Run 'Map Project' (or open a ClickUp link) to register the "
            "current project.",
            "Setup Required",
        )
        # Skip the dialog — set auto-execute so the command terminates immediately
        # without opening the input form.
        args.command.isAutoExecute = True
        return

    # ------------------------------------------------------------------ #
    # Pre-fetch list members for the assignee dropdown (best-effort)      #
    # ------------------------------------------------------------------ #
    global _list_members
    _list_members = []
    try:
        _api_token_early = _load_api_token()
        _doc_early = app.activeDocument
        _data_file_early = _doc_early.dataFile if _doc_early else None
        _proj_early = _data_file_early.parentProject if _data_file_early else None
        _proj_urn_early = _proj_early.id if _proj_early else None
        if _api_token_early and _proj_urn_early:
            _list_id_early = _load_list_id_for_project(_proj_urn_early)
            if _list_id_early:
                _list_members = _fetch_list_members(_list_id_early, _api_token_early)
                futil.log(
                    f"{CMD_NAME}: command_created — fetched {len(_list_members)} member(s)."
                )
    except Exception as _exc:
        futil.log(
            f"{CMD_NAME}: command_created — member prefetch failed (non-fatal): {_exc}"
        )

    inputs = args.command.commandInputs

    # Task name — default to active document name + " Task"
    active_doc = app.activeDocument
    doc_name = active_doc.name if active_doc else ""
    default_task_name = f"{doc_name} Task" if doc_name else ""
    name_input = inputs.addStringValueInput(
        "task_name", "Task Name:", default_task_name
    )
    name_input.tooltip = "Task Name"
    name_input.tooltipDescription = "The title of the new ClickUp task."

    # Description — multi-line editable text box
    desc_input = inputs.addTextBoxCommandInput(
        "task_description", "Description:", "", 4, False
    )
    desc_input.tooltip = "Description"
    desc_input.tooltipDescription = "Optional task description. Supports Markdown formatting (bold, italic, bullet lists, etc.)."

    # Due date — single-line string input in YYYY-MM-DD format
    today = datetime.now().strftime("%Y-%m-%d")
    due_input = inputs.addStringValueInput(
        "task_due_date", "Due Date:", today
    )
    due_input.tooltip = "Due Date"
    due_input.tooltipDescription = "Enter the task due date in YYYY-MM-DD format, or select a Quick Date preset to fill it automatically."

    # Quick Date — pre-calculated combo box (no icons)
    global _quick_date_options
    _quick_date_options = futil.compute_quick_dates()

    quick_date_input = inputs.addDropDownCommandInput(
        "quick_date", "Quick Date:", adsk.core.DropDownStyles.TextListDropDownStyle
    )
    quick_date_input.tooltip = "Quick Date"
    quick_date_input.tooltipDescription = (
        "Select a preset date to automatically fill in the Due Date field.\n"
        "Dates falling on a weekend are advanced to the following Monday."
    )
    for label, _ in _quick_date_options:
        quick_date_input.listItems.add(label, False)

    # Priority — drop-down (ClickUp values: 1=Urgent, 2=High, 3=Normal, 4=Low)
    priority_input = inputs.addDropDownCommandInput(
        "task_priority", "Priority:", adsk.core.DropDownStyles.TextListDropDownStyle
    )
    priority_input.tooltip = "Priority"
    priority_input.tooltipDescription = (
        "Set the task priority in ClickUp:\n"
        "  • Urgent — must be done immediately\n"
        "  • High — important, tackle soon\n"
        "  • Normal — standard priority (default)\n"
        "  • Low — nice to have"
    )
    priority_input.listItems.add("Normal", True)  # default selected
    priority_input.listItems.add("Low", False)
    priority_input.listItems.add("High", False)
    priority_input.listItems.add("Urgent", False)

    # Link Document — checkbox to attach an Open on Desktop link as a custom field
    link_input = inputs.addBoolValueInput(
        "link_document", "Link Document to Task", True, "", True
    )
    link_input.tooltip = "Link Document to Task"
    link_input.tooltipDescription = (
        "When checked, a shortened Open-on-Desktop link for the active Fusion 360 document "
        "is attached to the task via the 'Fusion Design' custom field in ClickUp. "
        "Requires a TinyURL API token configured in Set Tokens."
    )

    # Assign To — drop-down populated from the ClickUp list members API
    assignee_input = inputs.addDropDownCommandInput(
        "task_assignee", "Assign To:", adsk.core.DropDownStyles.TextListDropDownStyle
    )
    assignee_input.tooltip = "Assign To"
    assignee_input.tooltipDescription = (
        "Optionally assign the new task to a list member.\n"
        "Select '— Unassigned —' to leave the task unassigned.\n"
        "(Populated from the ClickUp list members.)"
    )
    assignee_input.listItems.add("— Unassigned —", True)
    for _member in _list_members:
        assignee_input.listItems.add(_member["username"], False)

    # Private Task — checkbox; only enabled when an assignee is selected
    # (ClickUp private tasks require at least one assignee to be meaningful)
    private_input = inputs.addBoolValueInput(
        "task_private", "Private Task", True, "", False
    )
    private_input.tooltip = "Private Task"
    private_input.tooltipDescription = (
        "When checked, the task is marked as private in ClickUp. "
        "Private tasks are only visible to the task creator and assignees. "
        "Select an assignee above to enable this option."
    )
    private_input.isEnabled = False  # disabled until an assignee is chosen

    # Connect to command events
    futil.add_handler(
        args.command.execute, command_execute, local_handlers=local_handlers
    )
    futil.add_handler(
        args.command.validateInputs,
        command_validate_input,
        local_handlers=local_handlers,
    )
    futil.add_handler(
        args.command.inputChanged,
        command_input_changed,
        local_handlers=local_handlers,
    )
    futil.add_handler(
        args.command.destroy, command_destroy, local_handlers=local_handlers
    )


def command_execute(args: adsk.core.CommandEventArgs):
    """Called when the user clicks OK in the dialog."""
    futil.log(f"{CMD_NAME}: Execute event started.")

    try:
        # ------------------------------------------------------------------ #
        # 1. Collect dialog inputs                                            #
        # ------------------------------------------------------------------ #
        inputs = args.command.commandInputs

        name_input = inputs.itemById("task_name")
        desc_input = inputs.itemById("task_description")
        date_input = inputs.itemById("task_due_date")
        priority_input = inputs.itemById("task_priority")
        link_doc_input = inputs.itemById("link_document")
        private_input = inputs.itemById("task_private")
        assignee_input = inputs.itemById("task_assignee")

        task_name = getattr(name_input, "value", "").strip()
        task_description = getattr(desc_input, "text", "").strip()
        due_date_str = getattr(date_input, "value", "").strip()
        priority_label = (
            priority_input.selectedItem.name
            if priority_input and priority_input.selectedItem
            else "Normal"
        )
        link_document = getattr(link_doc_input, "value", False)
        task_private = getattr(private_input, "value", False)

        # Resolve assignee: look up selected username in cached _list_members
        assignee_id = 0  # 0 = unassigned
        if assignee_input and assignee_input.selectedItem:
            selected_name = assignee_input.selectedItem.name
            for _m in _list_members:
                if _m["username"] == selected_name:
                    assignee_id = _m["id"]
                    break

        # Map label → ClickUp priority integer
        _PRIORITY_MAP = {"Low": 4, "Normal": 3, "High": 2, "Urgent": 1}
        priority_value = _PRIORITY_MAP.get(priority_label, 3)

        futil.log(
            f"{CMD_NAME}: Inputs collected — name='{task_name}', due='{due_date_str}', priority='{priority_label}'({priority_value}), link_document={link_document}, private={task_private}, assignee_id={assignee_id}"
        )

        # ------------------------------------------------------------------ #
        # 1b. Build Open-on-Desktop URL and shorten via TinyURL (if checked) #
        # Done early so the result is ready before payload construction.     #
        # ------------------------------------------------------------------ #
        short_url = None
        if link_document:
            futil.log(
                f"{CMD_NAME}: [TinyURL] link_document=True — building Open-on-Desktop URL."
            )
            active_doc = app.activeDocument
            futil.log(
                f"{CMD_NAME}: [TinyURL] active_doc='{getattr(active_doc, 'name', None)}' "
                f"isSaved={getattr(active_doc, 'isSaved', None)} "
                f"dataFile={getattr(active_doc, 'dataFile', None)}",
            )
            if active_doc and active_doc.isSaved and active_doc.dataFile:
                fusion_url = _build_open_on_desktop_url(active_doc)
                futil.log(f"{CMD_NAME}: [TinyURL] fusion_url='{fusion_url}'")

                tinyurl_token = _load_tinyurl_token()
                if tinyurl_token:
                    futil.log(
                        f"{CMD_NAME}: [TinyURL] token loaded (len={len(tinyurl_token)}), calling _shorten_url..."
                    )
                    short_url = _shorten_url(fusion_url, tinyurl_token)
                    futil.log(
                        f"{CMD_NAME}: [TinyURL] _shorten_url returned: '{short_url}'"
                    )
                else:
                    futil.log(
                        f"{CMD_NAME}: [TinyURL] WARNING — tinyurl_api_token missing in auth.json. Skipping."
                    )
            else:
                futil.log(
                    f"{CMD_NAME}: [TinyURL] WARNING — document unsaved or no dataFile. Skipping."
                )
        else:
            futil.log(f"{CMD_NAME}: link_document=False — skipping document link.")

        # ------------------------------------------------------------------ #
        # 2. Load API token from cache/auth.json                             #
        # ------------------------------------------------------------------ #
        futil.log(f"{CMD_NAME}: Loading API token from '{AUTH_JSON_PATH}'")

        api_token = _load_api_token()
        if not api_token:
            futil.log(f"{CMD_NAME}: ERROR — API token not found in '{AUTH_JSON_PATH}'")
            ui.messageBox(
                f"ClickUp API token not found.\n\n"
                f"Please add your token to:\n{AUTH_JSON_PATH}\n\n"
                f"Expected format:\n"
                f'{{\n    "clickup_api_token": "pk_YOUR_TOKEN_HERE"\n}}',
                "Authentication Error",
            )
            return

        futil.log(
            f"{CMD_NAME}: API token loaded successfully (length={len(api_token)})."
        )

        # ------------------------------------------------------------------ #
        # 2b. Resolve list ID from projects.json using the active project    #
        # ------------------------------------------------------------------ #
        doc = app.activeDocument
        data_file = doc.dataFile if doc else None
        project = data_file.parentProject if data_file else None
        project_urn = project.id if project else None

        if not project_urn:
            futil.log(f"{CMD_NAME}: ERROR — could not determine current project URN.")
            ui.messageBox(
                "Could not determine the current Fusion 360 project.\n\n"
                "Please make sure a saved document is open.",
                "Project Not Found",
            )
            return

        futil.log(f"{CMD_NAME}: Active project URN = '{project_urn}'")

        list_id = _load_list_id_for_project(project_urn)
        if not list_id:
            futil.log(
                f"{CMD_NAME}: ERROR — clickup_list_id not set for project '{project_urn}'"
            )
            ui.messageBox(
                f"No ClickUp list ID configured for this project.\n\n"
                f"To fix:\n"
                f"1. Open ClickUp and navigate into a List (not a Folder).\n"
                f"2. Copy the number after /li/ in the URL.\n"
                f'3. Add "clickup_list_id" to this project\'s entry in:\n'
                f"   {config.PROJECTS_JSON_PATH}",
                "List ID Not Configured",
            )
            return

        futil.log(f"{CMD_NAME}: Using list ID '{list_id}'.")

        # ------------------------------------------------------------------ #
        # 3. Build the ClickUp Create Task payload                           #
        # ClickUp API: POST /api/v2/list/{list_id}/task                      #
        # Docs: https://developer.clickup.com/reference/createtask          #
        # ------------------------------------------------------------------ #
        payload: dict = {
            "name": task_name,
            "priority": priority_value,
            "is_private": task_private,
        }

        # Add assignee if one was selected (ClickUp create task takes an array of IDs)
        if assignee_id:
            payload["assignees"] = [assignee_id]
            futil.log(f"{CMD_NAME}: Assigning task to user ID {assignee_id}.")

        # Use markdown_content if a description was provided (overrides plain description)
        if task_description:
            payload["markdown_content"] = task_description

        # Convert due date to Unix timestamp in milliseconds
        if due_date_str:
            due_ms = _date_to_unix_ms(due_date_str)
            if due_ms is not None:
                payload["due_date"] = due_ms
                payload["due_date_time"] = " " in due_date_str  # True when HH:MM present (Later)
                futil.log(f"{CMD_NAME}: Due date '{due_date_str}' → {due_ms} ms.")
            else:
                futil.log(
                    f"{CMD_NAME}: WARNING — Could not parse due date '{due_date_str}'. Skipping."
                )

        futil.log(f"{CMD_NAME}: Payload prepared — {list(payload.keys())}")

        # ------------------------------------------------------------------ #
        # 3b. Inject document custom fields when "Link Document" is enabled  #
        # ------------------------------------------------------------------ #
        custom_fields_list = []

        if short_url:
            futil.log(
                f"{CMD_NAME}: [TinyURL] Attaching short_url='{short_url}' to ClickUp custom field."
            )
            url_field_id = _get_url_custom_field_id(list_id, api_token)
            if url_field_id:
                custom_fields_list.append({"id": url_field_id, "value": short_url})
                futil.log(
                    f"{CMD_NAME}: [TinyURL] URL custom field queued — field_id='{url_field_id}'."
                )
            else:
                futil.log(
                    f"{CMD_NAME}: [TinyURL] WARNING — 'Fusion Design' URL field not found on list '{list_id}'. "
                    f"Document link will not be attached.",
                )
        elif link_document:
            futil.log(
                f"{CMD_NAME}: [TinyURL] WARNING — shortening failed or was skipped. "
                f"No URL custom field added to payload.",
            )

        # Look up the 'Fusion Document URN' field ID now — value is written after task creation
        # using the dedicated /task/{task_id}/field/{field_id} endpoint for reliability.
        urn_field_id = None
        doc_urn = None
        if link_document and data_file:
            doc_urn = data_file.id
            futil.log(f"{CMD_NAME}: [URN] Document URN resolved: '{doc_urn}'")
            urn_field_id = _get_urn_custom_field_id(list_id, api_token)
            if urn_field_id:
                futil.log(
                    f"{CMD_NAME}: [URN] 'Fusion Document URN' field found — id='{urn_field_id}'. Will write after task creation."
                )
            else:
                futil.log(
                    f"{CMD_NAME}: [URN] WARNING — 'Fusion Document URN' field not found on list '{list_id}'. "
                    f"Document URN will not be attached.",
                )

        if custom_fields_list:
            payload["custom_fields"] = custom_fields_list
            futil.log(
                f"{CMD_NAME}: payload['custom_fields'] set with {len(custom_fields_list)} field(s)."
            )

        # ------------------------------------------------------------------ #
        # 4. POST to ClickUp API using adsk.core.HttpRequest (Fusion native) #
        # Ref: adsk.core.HttpRequest.create / executeSync                    #
        # ------------------------------------------------------------------ #
        url = f"{CLICKUP_API_BASE}/list/{list_id}/task"
        body_str = json.dumps(payload)

        futil.log(f"{CMD_NAME}: Creating HttpRequest — POST '{url}'")

        http_req = adsk.core.HttpRequest.create(url, adsk.core.HttpMethods.PostMethod)
        http_req.setHeader("Authorization", api_token)
        http_req.setHeader("Content-Type", "application/json")
        http_req.setHeader("Accept", "application/json")
        http_req.data = body_str

        futil.log(f"{CMD_NAME}: Executing request synchronously...")
        response = http_req.executeSync()

        status_code = response.statusCode
        futil.log(f"{CMD_NAME}: Response status — {status_code}")

        # ------------------------------------------------------------------ #
        # 5. Handle response                                                  #
        # ------------------------------------------------------------------ #
        if 200 <= status_code < 300:
            task = json.loads(response.data)
            task_id = task.get("id", "")
            task_url = task.get("url", "—")
            task_status = task.get("status", {}).get("status", "—")

            futil.log(f"{CMD_NAME}: Task created successfully.")
            futil.log(f"{CMD_NAME}:   Task ID  = {task_id}")
            futil.log(f"{CMD_NAME}:   Task URL = {task_url}")
            futil.log(f"{CMD_NAME}:   Status   = {task_status}")

            # ------------------------------------------------------------------ #
            # 5b. Write Fusion Document URN to custom field via dedicated API    #
            # POST /api/v2/task/{task_id}/field/{field_id}                       #
            # This is more reliable than inline custom_fields on task creation   #
            # for text-type fields.                                              #
            # ------------------------------------------------------------------ #
            if urn_field_id and doc_urn and task_id:
                futil.log(
                    f"{CMD_NAME}: [URN] Setting 'Fusion Document URN' on task '{task_id}'."
                )
                urn_ok = _set_task_custom_field(
                    task_id, urn_field_id, doc_urn, api_token
                )
                if urn_ok:
                    futil.log(
                        f"{CMD_NAME}: [URN] 'Fusion Document URN' written successfully."
                    )
                else:
                    futil.log(
                        f"{CMD_NAME}: [URN] WARNING — failed to write 'Fusion Document URN' field."
                    )

            # ------------------------------------------------------------------ #
            # 5c. Attach document thumbnail when Link Document is enabled        #
            # DataFile.thumbnail → DataObjectFuture → 256×256 PNG attachment       #
            # ------------------------------------------------------------------ #
            if link_document and task_id and data_file:
                futil.log(f"{CMD_NAME}: [Thumbnail] Attaching document thumbnail to task '{task_id}'.")
                _attach_thumbnail_to_task(task_id, data_file, api_token)

            ui.messageBox(
                f"Task <b>{task_name}</b> created.<br>"
                f"Priority: {priority_label}<br><br>"
                f'<a href="{task_url}">{task_url}</a>',
                "ClickUp Task Created",
            )

        else:
            error_body = response.data
            futil.log(f"{CMD_NAME}: ERROR — API returned {status_code}: {error_body}")
            ui.messageBox(
                f"Failed to create task.\n\n" f"HTTP {status_code}\n\n" f"{error_body}",
                "ClickUp API Error",
            )

    except Exception as e:
        futil.log(f"{CMD_NAME}: EXCEPTION in command_execute — {e}")
        ui.messageBox(f"An unexpected error occurred:\n\n{e}", "Error")


def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    """Called on every input change to enable/disable the OK button."""
    inputs = args.inputs

    name_input = inputs.itemById("task_name")
    date_input = inputs.itemById("task_due_date")

    task_name = getattr(name_input, "value", "").strip()
    due_date_str = getattr(date_input, "value", "").strip()

    # Name must not be empty
    if not task_name:
        args.areInputsValid = False
        return

    # Date must be valid if provided
    if due_date_str and _date_to_unix_ms(due_date_str) is None:
        args.areInputsValid = False
        return

    args.areInputsValid = True


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    """Handles input changes: date shortcuts fill the due-date field;
    the assignee dropdown enables/disables the private-task checkbox.
    """
    changed = args.input

    # ---- Assignee → toggle private checkbox ----
    if changed.id == "task_assignee":
        private_input = args.inputs.itemById("task_private")
        if private_input is not None:
            selected = getattr(changed, "selectedItem", None)
            is_assigned = selected is not None and selected.name != "— Unassigned —"
            private_input.isEnabled = is_assigned
            if not is_assigned:
                private_input.value = False
        return

    if changed.id == "quick_date":
        selected = getattr(changed, "selectedItem", None)
        if selected is not None:
            idx = selected.index
            if 0 <= idx < len(_quick_date_options):
                date_input = args.inputs.itemById("task_due_date")
                if date_input:
                    date_input.value = _quick_date_options[idx][1]
        return


def command_destroy(args: adsk.core.CommandEventArgs):
    """Called when the command dialog closes — clears event handler references."""
    futil.log(f"{CMD_NAME}: Command destroyed. Clearing local handlers.")
    global local_handlers
    local_handlers = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fetch_list_members(list_id: str, api_token: str) -> list:
    """GET /api/v2/list/{list_id}/member and return a sorted list of user dicts.

    Each returned item has: {"id": int, "username": str, "email": str}.
    Returns an empty list on any failure.
    ClickUp docs: https://developer.clickup.com/reference/getlistmembers
    """
    url = f"{CLICKUP_API_BASE}/list/{list_id}/member"
    futil.log(f"{CMD_NAME}: _fetch_list_members — GET '{url}'")
    try:
        req = adsk.core.HttpRequest.create(url, adsk.core.HttpMethods.GetMethod)
        req.setHeader("Authorization", api_token)
        req.setHeader("Accept", "application/json")
        response = req.executeSync()
        futil.log(f"{CMD_NAME}: _fetch_list_members — HTTP {response.statusCode}")
        if not (200 <= response.statusCode < 300):
            futil.log(f"{CMD_NAME}: _fetch_list_members — error: {response.data}")
            return []
        data = json.loads(response.data)
        members = []
        for item in data.get("members", []):
            user = item.get("user", item)
            uid = user.get("id")
            if not uid:
                continue
            members.append(
                {
                    "id": int(uid),
                    "username": (user.get("username") or user.get("email") or str(uid)),
                    "email": user.get("email", ""),
                }
            )
        members.sort(key=lambda m: m["username"].lower())
        return members
    except Exception as exc:
        futil.log(f"{CMD_NAME}: _fetch_list_members — exception: {exc}")
        return []


def _load_api_token() -> str:
    """Read the ClickUp API token from cache/auth.json.

    Expected file layout::

        {
            "clickup_api_token": "pk_YOUR_TOKEN_HERE"
        }

    Returns an empty string if the file is missing, malformed, or the key is absent.
    """
    futil.log(f"{CMD_NAME}: _load_api_token — reading '{AUTH_JSON_PATH}'")

    if not os.path.isfile(AUTH_JSON_PATH):
        futil.log(
            f"{CMD_NAME}: _load_api_token — auth.json not found at '{AUTH_JSON_PATH}'"
        )
        return ""

    try:
        with open(AUTH_JSON_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        futil.log(f"{CMD_NAME}: _load_api_token — JSON parse error: {exc}")
        return ""
    except OSError as exc:
        futil.log(f"{CMD_NAME}: _load_api_token — file read error: {exc}")
        return ""

    token = data.get("clickup_api_token", "").strip()
    if not token:
        futil.log(
            f"{CMD_NAME}: _load_api_token — 'clickup_api_token' key is missing or empty."
        )
    return token


def _load_list_id_for_project(project_urn: str) -> str:
    """Read the ClickUp list ID for the given Fusion project URN from projects.json.

    Expected file layout::

        {
            "projects": {
                "<project_urn>": {
                    "clickup_list_id": "901112345678"
                }
            }
        }

    The list ID is the number after /li/ in a ClickUp list URL.
    Returns an empty string if the file/key is missing, malformed, or the project is not found.
    """
    futil.log(f"{CMD_NAME}: _load_list_id_for_project — URN='{project_urn}'")

    if not os.path.isfile(config.PROJECTS_JSON_PATH):
        futil.log(f"{CMD_NAME}: _load_list_id_for_project — projects.json not found.")
        return ""

    try:
        with open(config.PROJECTS_JSON_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        futil.log(f"{CMD_NAME}: _load_list_id_for_project — JSON parse error: {exc}")
        return ""
    except OSError as exc:
        futil.log(f"{CMD_NAME}: _load_list_id_for_project — file read error: {exc}")
        return ""

    project_entry = data.get("projects", {}).get(project_urn, {})
    list_id = project_entry.get("clickup_list_id", "").strip()
    if not list_id:
        futil.log(
            f"{CMD_NAME}: _load_list_id_for_project — 'clickup_list_id' missing or empty for URN '{project_urn}'."
        )
    return list_id


def _date_to_unix_ms(date_str: str):
    """Convert a YYYY-MM-DD or YYYY-MM-DD HH:MM string to Unix timestamp in ms.
    Returns None if the string cannot be parsed."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    return None


def _build_open_on_desktop_url(doc) -> str:
    """Build a fusion360:// Open-on-Desktop deep-link for the given document.

    URL format::

        fusion360://lineageUrn=<encoded_id>&hubUrl=<encoded_hub>&documentName=<encoded_name>

    The hubUrl is derived from the Autodesk Galileo web URL, with the last
    three characters stripped and uppercased (matches what the Share add-in does).
    """
    data_file = doc.dataFile
    lineage_urn = quote(data_file.id)

    galileo_url = data_file.parentProject.parentHub.fusionWebURL
    # Strip trailing locale suffix (last 3 chars, e.g. "/en") and uppercase
    hub_stripped = galileo_url.replace(" ", "").rstrip(galileo_url[-3:]).upper()
    hub_encoded = quote(hub_stripped)

    doc_name_encoded = quote(doc.name)

    return (
        f"fusion360://lineageUrn={lineage_urn}"
        f"&hubUrl={hub_encoded}"
        f"&documentName={doc_name_encoded}"
    )


def _get_url_custom_field_id(list_id: str, api_token: str) -> str:
    """Query the ClickUp list for its custom fields and return the ID of the
    field named ``'Fusion Design'`` with type ``'url'``.

    Returns an empty string when the field is not found or if the request fails.

    API reference: GET /api/v2/list/{list_id}/field
    """
    futil.log(f"{CMD_NAME}: _get_url_custom_field_id — querying list '{list_id}'")

    TARGET_NAME = "Fusion Design"
    TARGET_TYPE = "url"

    fields_url = f"{CLICKUP_API_BASE}/list/{list_id}/field"

    try:
        req = adsk.core.HttpRequest.create(fields_url, adsk.core.HttpMethods.GetMethod)
        req.setHeader("Authorization", api_token)
        req.setHeader("Accept", "application/json")

        response = req.executeSync()
        status = response.statusCode
        futil.log(f"{CMD_NAME}: _get_url_custom_field_id — status {status}")

        if not (200 <= status < 300):
            futil.log(
                f"{CMD_NAME}: _get_url_custom_field_id — non-2xx response: {response.data}",
            )
            return ""

        fields_data = json.loads(response.data)
        all_fields = fields_data.get("fields", [])

        # Log all field types to help diagnose mismatches
        for f in all_fields:
            futil.log(f"{CMD_NAME}:   field '{f.get('name')}' type='{f.get('type')}'")

        # Find the field named "Fusion Design" with type "url"
        matched = next(
            (
                f
                for f in all_fields
                if f.get("name") == TARGET_NAME and f.get("type") == TARGET_TYPE
            ),
            None,
        )

        if matched:
            field_id = matched.get("id", "")
            futil.log(
                f"{CMD_NAME}: _get_url_custom_field_id — found '{TARGET_NAME}' (url) id='{field_id}'",
            )
            return field_id

        futil.log(
            f"{CMD_NAME}: _get_url_custom_field_id — '{TARGET_NAME}' (url) field not found on "
            f"list '{list_id}'. Add a URL custom field named '{TARGET_NAME}' in ClickUp.",
        )
        return ""

    except Exception as exc:
        futil.log(f"{CMD_NAME}: _get_url_custom_field_id — exception: {exc}")
        return ""


def _attach_thumbnail_to_task(task_id: str, data_file, api_token: str) -> None:
    """Download the DataFile thumbnail and upload it to the ClickUp task as an attachment.

    Uses DataFile.thumbnail to start an async download (returns a DataObjectFuture),
    polls until the 256×256 PNG is ready, saves it to a temp file, then POSTs it
    to the ClickUp attachment endpoint as multipart/form-data.

    Uses http.client.HTTPSConnection (instead of adsk.core.HttpRequest) so the
    body is sent as raw bytes — adsk.core.HttpRequest.data is a string and would
    re-encode the binary PNG as UTF-8, corrupting byte values above 0x7F.

    All failures are logged but do not raise — thumbnail attachment is best-effort.

    ClickUp API: POST /api/v2/task/{task_id}/attachment
    """
    futil.log(f"{CMD_NAME}: [Thumbnail] Starting thumbnail fetch for task '{task_id}'.")
    tmp_path = None
    try:
        # 1. Start async thumbnail download
        future = data_file.thumbnail
        if future is None:
            futil.log(f"{CMD_NAME}: [Thumbnail] data_file.thumbnail returned None — skipping.")
            return

        # 2. Poll until the download leaves the running state (max 10 s)
        MAX_WAIT = 10.0
        POLL_INTERVAL = 0.1
        start_time = time.time()
        while time.time() - start_time < MAX_WAIT:
            try:
                if future.state != adsk.core.FutureStates.RunningFutureState:
                    break
            except AttributeError:
                # FutureStates enum path differs — treat as done
                break
            time.sleep(POLL_INTERVAL)

        futil.log(f"{CMD_NAME}: [Thumbnail] Wait ended after {time.time() - start_time:.1f}s (state={future.state}).")

        data_obj = future.dataObject
        if data_obj is None:
            futil.log(
                f"{CMD_NAME}: [Thumbnail] dataObject is None — "
                "no thumbnail available for this document."
            )
            return

        # 3. Save PNG to a temp file
        tmp_path = os.path.join(
            tempfile.gettempdir(), f"fpp_thumb_{task_id}.png"
        )
        if not data_obj.saveToFile(tmp_path):
            futil.log(f"{CMD_NAME}: [Thumbnail] saveToFile returned False — skipping.")
            return

        futil.log(f"{CMD_NAME}: [Thumbnail] Thumbnail saved to '{tmp_path}' ({os.path.getsize(tmp_path)} bytes).")

        # 4. Read the PNG bytes
        with open(tmp_path, "rb") as fh:
            image_bytes = fh.read()

        # 5. Build multipart/form-data body as bytes (preserves all 256 byte values)
        boundary = b"----FusionAddInBoundary3c1f9e"
        safe_name = (data_file.name.replace(" ", "_") + "_thumbnail.png").encode("utf-8")

        body = (
            b"--" + boundary + b"\r\n"
            b'Content-Disposition: form-data; name="attachment"; filename="' + safe_name + b'"\r\n'
            b"Content-Type: image/png\r\n"
            b"\r\n"
            + image_bytes
            + b"\r\n--" + boundary + b"--\r\n"
        )

        content_type = f"multipart/form-data; boundary={boundary.decode('ascii')}"

        # 6. POST via http.client so the body is sent as raw bytes
        #    (adsk.core.HttpRequest.data is a string and would corrupt binary data)
        conn = http.client.HTTPSConnection("api.clickup.com", timeout=30)
        conn.request(
            "POST",
            f"/api/v2/task/{task_id}/attachment",
            body=body,
            headers={
                "Authorization": api_token,
                "Content-Type": content_type,
                "Accept": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        resp = conn.getresponse()
        status = resp.status
        resp_body = resp.read().decode("utf-8", errors="replace")
        conn.close()

        futil.log(f"{CMD_NAME}: [Thumbnail] Attachment upload — HTTP {status}")
        if 200 <= status < 300:
            futil.log(f"{CMD_NAME}: [Thumbnail] Thumbnail attached successfully.")
        else:
            futil.log(f"{CMD_NAME}: [Thumbnail] Upload failed: {resp_body}")

    except Exception as exc:
        futil.log(f"{CMD_NAME}: [Thumbnail] Exception — {exc}")
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def _set_task_custom_field(
    task_id: str, field_id: str, value: str, api_token: str
) -> bool:
    """Set a custom field value on an existing task via the dedicated ClickUp endpoint.

    Using this endpoint (rather than inline ``custom_fields`` on task creation) is
    the most reliable approach for all field types, including ``short_text`` and
    ``text``.

    API reference: POST /api/v2/task/{task_id}/field/{field_id}
    Body: ``{"value": "<value>"}``

    Returns ``True`` on success, ``False`` otherwise.
    """
    futil.log(
        f"{CMD_NAME}: _set_task_custom_field — task='{task_id}' field='{field_id}' "
        f"value='{value[:80]}{'...' if len(value) > 80 else ''}'"
    )

    url = f"{CLICKUP_API_BASE}/task/{task_id}/field/{field_id}"
    body = json.dumps({"value": value})

    try:
        req = adsk.core.HttpRequest.create(url, adsk.core.HttpMethods.PostMethod)
        req.setHeader("Authorization", api_token)
        req.setHeader("Content-Type", "application/json")
        req.setHeader("Accept", "application/json")
        req.data = body

        response = req.executeSync()
        status = response.statusCode
        futil.log(f"{CMD_NAME}: _set_task_custom_field — status {status}")

        if 200 <= status < 300:
            return True

        futil.log(f"{CMD_NAME}: _set_task_custom_field — FAILED: {response.data}")
        return False

    except Exception as exc:
        futil.log(f"{CMD_NAME}: _set_task_custom_field — exception: {exc}")
        return False


def _get_urn_custom_field_id(list_id: str, api_token: str) -> str:
    """Query the ClickUp list for its custom fields and return the ID of the
    field named ``'Fusion Document URN'``.

    Matches any field type so the ClickUp field can be ``short_text``, ``text``, etc.
    Returns an empty string when the field is not found or if the request fails.

    API reference: GET /api/v2/list/{list_id}/field
    """
    futil.log(f"{CMD_NAME}: _get_urn_custom_field_id — querying list '{list_id}'")

    TARGET_NAME = "Fusion Document URN"

    fields_url = f"{CLICKUP_API_BASE}/list/{list_id}/field"

    try:
        req = adsk.core.HttpRequest.create(fields_url, adsk.core.HttpMethods.GetMethod)
        req.setHeader("Authorization", api_token)
        req.setHeader("Accept", "application/json")

        response = req.executeSync()
        status = response.statusCode
        futil.log(f"{CMD_NAME}: _get_urn_custom_field_id — status {status}")

        if not (200 <= status < 300):
            futil.log(
                f"{CMD_NAME}: _get_urn_custom_field_id — non-2xx response: {response.data}",
            )
            return ""

        fields_data = json.loads(response.data)
        all_fields = fields_data.get("fields", [])

        matched = next(
            (f for f in all_fields if f.get("name") == TARGET_NAME),
            None,
        )

        if matched:
            field_id = matched.get("id", "")
            futil.log(
                f"{CMD_NAME}: _get_urn_custom_field_id — found '{TARGET_NAME}' "
                f"(type='{matched.get('type')}') id='{field_id}'",
            )
            return field_id

        futil.log(
            f"{CMD_NAME}: _get_urn_custom_field_id — '{TARGET_NAME}' field not found on "
            f"list '{list_id}'. Add a text custom field named '{TARGET_NAME}' in ClickUp.",
        )
        return ""

    except Exception as exc:
        futil.log(f"{CMD_NAME}: _get_urn_custom_field_id — exception: {exc}")
        return ""


def _load_tinyurl_token() -> str:
    """Read the TinyURL API token from cache/auth.json.

    Expected key: ``"tinyurl_api_token"``.
    Returns an empty string if the file is missing, malformed, or the key is absent.
    """
    futil.log(f"{CMD_NAME}: _load_tinyurl_token — reading '{AUTH_JSON_PATH}'")

    if not os.path.isfile(AUTH_JSON_PATH):
        futil.log(f"{CMD_NAME}: _load_tinyurl_token — auth.json not found.")
        return ""

    try:
        with open(AUTH_JSON_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        futil.log(f"{CMD_NAME}: _load_tinyurl_token — read error: {exc}")
        return ""

    token = data.get("tinyurl_api_token", "").strip()
    if not token:
        futil.log(
            f"{CMD_NAME}: _load_tinyurl_token — 'tinyurl_api_token' missing or empty."
        )
    return token


def _shorten_url(long_url: str, tinyurl_token: str) -> str:
    """Shorten ``long_url`` using the TinyURL API and return the short URL.

    API reference: POST https://api.tinyurl.com/create
    Authentication: Bearer token in the Authorization header.
    Request body: ``{"url": "<long_url>", "domain": "tinyurl.com"}``
    Response: ``data.tiny_url`` contains the shortened URL.

    Returns ``None`` if shortening fails (network error, invalid URL, non-2xx
    response, or missing ``tiny_url`` in the response body). The caller is
    responsible for deciding whether to skip the field in that case.
    """
    futil.log(f"{CMD_NAME}: _shorten_url — shortening via TinyURL API")
    futil.log(f"{CMD_NAME}: _shorten_url — long_url='{long_url}'")
    futil.log(
        f"{CMD_NAME}: _shorten_url — token prefix='{tinyurl_token[:8]}...' (len={len(tinyurl_token)})"
    )

    TINYURL_API_BASE = "https://api.tinyurl.com"
    endpoint = f"{TINYURL_API_BASE}/create"
    body = json.dumps({"url": long_url, "domain": "tinyurl.com"})

    futil.log(f"{CMD_NAME}: _shorten_url — endpoint='{endpoint}'")
    futil.log(f"{CMD_NAME}: _shorten_url — request body={body}")

    try:
        req = adsk.core.HttpRequest.create(endpoint, adsk.core.HttpMethods.PostMethod)
        req.setHeader("Authorization", f"Bearer {tinyurl_token}")
        req.setHeader("Content-Type", "application/json")
        req.setHeader("Accept", "application/json")
        req.data = body

        futil.log(f"{CMD_NAME}: _shorten_url — executing request...")
        response = req.executeSync()
        status = response.statusCode
        raw_response = response.data
        futil.log(f"{CMD_NAME}: _shorten_url — status={status}")
        futil.log(f"{CMD_NAME}: _shorten_url — raw response={raw_response}")

        if 200 <= status < 300:
            resp_data = json.loads(raw_response)
            short_url = resp_data.get("data", {}).get("tiny_url", "")
            if short_url:
                futil.log(
                    f"{CMD_NAME}: _shorten_url — SUCCESS: short_url='{short_url}'"
                )
                return short_url
            futil.log(
                f"{CMD_NAME}: _shorten_url — 'tiny_url' key missing in response data. Returning None."
            )
        else:
            futil.log(
                f"{CMD_NAME}: _shorten_url — FAILED: non-2xx status={status} body={raw_response}. Returning None."
            )

    except Exception as exc:
        futil.log(f"{CMD_NAME}: _shorten_url — EXCEPTION: {exc}. Returning None.")

    return None
