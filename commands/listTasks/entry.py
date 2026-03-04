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

# Dedicated listTasks icons (clipboard body + three blue report lines)
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "")

# ClickUp API configuration
CLICKUP_API_BASE = "https://api.clickup.com/api/v2"

CACHE_DIR = config.CACHE_DIR
AUTH_JSON_PATH = os.path.join(CACHE_DIR, "auth.json")
PROJECTS_JSON_PATH = os.path.join(CACHE_DIR, "projects.json")

# Priority constants
_PRIORITY_SORT_KEY = {1: 0, 2: 1, 3: 2, 4: 3}
_PRIORITY_OPTIONS = ["Urgent", "High", "Normal", "Low"]
_PRIORITY_LABEL_TO_INT = {"Urgent": 1, "High": 2, "Normal": 3, "Low": 4}
_PRIORITY_INT_TO_LABEL = {v: k for k, v in _PRIORITY_LABEL_TO_INT.items()}

local_handlers = []

# Module-level state shared between command_created and command_execute
_list_url: str = ""
_api_token: str = ""
_list_statuses: list = []  # [{"status": str, "color": str, ...}, ...] from ClickUp API
_task_originals: dict = (
    {}
)  # "{id_prefix}_{task_id}" → {"status": str, "priority": int|None, "description": str, "time_estimate_ms": int|None}


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
    global _list_url, _api_token, _list_statuses, _task_originals
    _list_url = ""
    _api_token = ""
    _list_statuses = []
    _task_originals = {}

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

    _api_token = _load_api_token()
    if not _api_token:
        ui.messageBox(
            f"ClickUp API token not found.\n\nPlease run 'Set Tokens'.",
            "Authentication Error",
        )
        args.command.isAutoExecute = True
        return

    # Fetch available statuses for the list (populates the status dropdowns)
    _list_statuses = _fetch_list_statuses(list_id, _api_token)
    futil.log(
        f"{CMD_NAME}: fetched {len(_list_statuses)} status(es) for list '{list_id}'."
    )

    # ------------------------------------------------------------------ #
    # Fetch tasks filtered by the Fusion Document URN custom field        #
    # ------------------------------------------------------------------ #
    urn_field_id = _get_urn_custom_field_id(list_id, _api_token)
    if not urn_field_id:
        ui.messageBox(
            "The 'Fusion Document URN' custom field was not found on this ClickUp list.\n\n"
            "Add a text custom field named 'Fusion Document URN' to your ClickUp list,\n"
            "then create tasks with 'Add Task' to populate it.",
            "Custom Field Missing",
        )
        args.command.isAutoExecute = True
        return

    # Fetch both task sets
    doc_tasks_raw = _fetch_tasks_for_urn(list_id, urn_field_id, doc_urn, _api_token)
    all_tasks = _fetch_all_tasks(list_id, _api_token)

    # The ClickUp API text-field filter can return partial/fuzzy matches.
    # Apply a strict client-side exact-match on the custom field value.
    def _urn_matches(task: dict) -> bool:
        for cf in task.get("custom_fields", []):
            if cf.get("id") == urn_field_id:
                return cf.get("value", "") == doc_urn
        return False

    doc_tasks = [t for t in doc_tasks_raw if _urn_matches(t)]
    futil.log(
        f"{CMD_NAME}: {len(doc_tasks_raw)} API result(s) → {len(doc_tasks)} "
        f"exact URN match(es); {len(all_tasks)} total in list."
    )

    def _priority_sort_key(task):
        raw = task.get("priority") or {}
        try:
            return _PRIORITY_SORT_KEY.get(int(raw.get("id", 99)), 99)
        except (ValueError, TypeError):
            return 99

    doc_tasks.sort(key=_priority_sort_key)
    all_tasks.sort(key=_priority_sort_key)

    # ------------------------------------------------------------------ #
    # Build dialog inputs                                                 #
    # ------------------------------------------------------------------ #
    inputs = args.command.commandInputs

    # Info bar: document name + open list link
    if _list_url:
        list_link = f'<a href="{_list_url}">🔗 Open List in ClickUp</a>'
    else:
        list_link = "(No list URL configured)"

    inputs.addTextBoxCommandInput(
        "info",
        "",
        f"<b>Document:</b> {doc_name}<br>{list_link}",
        2,
        True,
    )

    # ------------------------------------------------------------------ #
    # Table 1 — tasks linked to this document                            #
    # ------------------------------------------------------------------ #
    inputs.addTextBoxCommandInput(
        "doc_tasks_header",
        "",
        f"<b>Tasks Linked to This Document</b> ({len(doc_tasks)})",
        1,
        True,
    )
    _build_task_table(
        inputs,
        doc_tasks,
        table_id="doc_tasks_table",
        id_prefix="doc",
        status_options=_list_statuses,
        task_originals=_task_originals,
    )

    # ------------------------------------------------------------------ #
    # Table 2 — all tasks in the list                                    #
    # ------------------------------------------------------------------ #
    inputs.addTextBoxCommandInput(
        "all_tasks_header",
        "",
        f"<b>Project Tasks</b> ({len(all_tasks)})",
        1,
        True,
    )
    _build_task_table(
        inputs,
        all_tasks,
        table_id="all_tasks_table",
        id_prefix="all",
        status_options=_list_statuses,
        task_originals=_task_originals,
    )

    # Connect events
    futil.add_handler(
        args.command.execute, command_execute, local_handlers=local_handlers
    )
    futil.add_handler(
        args.command.destroy, command_destroy, local_handlers=local_handlers
    )


def _build_task_table(
    inputs: adsk.core.CommandInputs,
    tasks: list,
    table_id: str,
    id_prefix: str,
    status_options: list = None,
    task_originals: dict = None,
) -> adsk.core.TableCommandInput:
    """Add a Name | Priority | Status table to *inputs* and populate it.

    *id_prefix* is used to namespace all child input IDs so two tables on
    the same dialog never share an ID.
    *status_options* is the list of status dicts fetched from the ClickUp API.
    If provided, Status is rendered as an editable dropdown; otherwise read-only.
    *task_originals* dict is populated with "{id_prefix}_{task_id}" → original
    status string so command_execute can detect and PATCH changes.
    """
    if status_options is None:
        status_options = []

    table = inputs.addTableCommandInput(table_id, "", 3, "5:2:2")
    table.hasGrid = True
    table.minimumVisibleRows = 3
    table.maximumVisibleRows = 15

    # Header row
    for col_id, label in [
        (f"{id_prefix}_h_name", "Task Name"),
        (f"{id_prefix}_h_priority", "Priority"),
        (f"{id_prefix}_h_status", "Status"),
    ]:
        h = inputs.addStringValueInput(col_id, "", label)
        h.isReadOnly = True

    table.addCommandInput(inputs.itemById(f"{id_prefix}_h_name"), 0, 0)
    table.addCommandInput(inputs.itemById(f"{id_prefix}_h_priority"), 0, 1)
    table.addCommandInput(inputs.itemById(f"{id_prefix}_h_status"), 0, 2)

    if not tasks:
        empty = inputs.addTextBoxCommandInput(
            f"{id_prefix}_empty", "", "No tasks found.", 1, True
        )
        table.addCommandInput(empty, 1, 0, 0, 3)
        return table

    for i, task in enumerate(tasks, start=1):
        tid = task.get("id", f"unknown_{i}")
        task_name = task.get("name", "(unnamed)")
        task_url = task.get("url", "")

        priority_id = None
        raw_priority = task.get("priority")
        if raw_priority and raw_priority.get("id"):
            try:
                priority_id = int(raw_priority["id"])
            except (ValueError, TypeError):
                pass

        status_str = task.get("status", {}).get("status", "").lower()
        description_str = (task.get("description") or "").strip()
        try:
            time_est_ms = (
                int(task["time_estimate"]) if task.get("time_estimate") else None
            )
        except (ValueError, TypeError):
            time_est_ms = None
        if task_originals is not None:
            task_originals[f"{id_prefix}_{tid}"] = {
                "status": status_str,
                "priority": priority_id,
                "description": description_str,
                "time_estimate_ms": time_est_ms,
            }

        name_html = f'<a href="{task_url}">{task_name}</a>' if task_url else task_name
        name_cell = inputs.addTextBoxCommandInput(
            f"{id_prefix}_name_{i}", "", name_html, 1, True
        )

        # Priority cell — editable dropdown
        pri_label_plain = _PRIORITY_INT_TO_LABEL.get(priority_id, "Normal")
        priority_cell = inputs.addDropDownCommandInput(
            f"{id_prefix}_priority_{tid}",
            "",
            adsk.core.DropDownStyles.TextListDropDownStyle,
        )
        priority_cell.tooltip = "Priority"
        priority_cell.tooltipDescription = "Set the ClickUp task priority."
        for opt in _PRIORITY_OPTIONS:
            priority_cell.listItems.add(opt, opt == pri_label_plain)

        # Status cell — dropdown if we have API-sourced options, else read-only string
        if status_options:
            status_cell = inputs.addDropDownCommandInput(
                f"{id_prefix}_status_{tid}",
                "",
                adsk.core.DropDownStyles.TextListDropDownStyle,
            )
            status_cell.tooltip = "Status"
            status_cell.tooltipDescription = "Set the ClickUp task status."
            matched = False
            for opt in status_options:
                opt_name = opt.get("status", "")
                is_selected = opt_name.lower() == status_str
                status_cell.listItems.add(opt_name.title(), is_selected)
                if is_selected:
                    matched = True
            if not matched and status_cell.listItems.count > 0:
                status_cell.listItems.item(0).isSelected = True
        else:
            status_cell = inputs.addStringValueInput(
                f"{id_prefix}_status_{tid}", "", status_str.title() or "—"
            )
            status_cell.isReadOnly = True
            status_cell.tooltip = "Status"
            status_cell.tooltipDescription = "Status could not be fetched from ClickUp."

        table.addCommandInput(name_cell, i, 0)
        table.addCommandInput(priority_cell, i, 1)
        table.addCommandInput(status_cell, i, 2)

    return table


def _build_description_inputs(
    inputs: adsk.core.CommandInputs,
    tasks: list,
    id_prefix: str,
) -> None:
    """Add an editable description TextBox and time estimate field for each task.

    Description inputs are named "{id_prefix}_desc_{task_id}".
    Time estimate inputs are named "{id_prefix}_time_{task_id}" (value in hours).
    """
    if not tasks:
        return
    inputs.addTextBoxCommandInput(
        f"{id_prefix}_desc_header",
        "",
        "<b>Descriptions &amp; Time Estimates</b>",
        1,
        True,
    )
    for task in tasks:
        tid = task.get("id", "")
        if not tid:
            continue
        task_name = task.get("name", "(unnamed)")
        description = (task.get("description") or "").strip()
        desc_cell = inputs.addTextBoxCommandInput(
            f"{id_prefix}_desc_{tid}",
            task_name,
            description,
            4,
            False,
        )
        desc_cell.tooltip = "Description"
        desc_cell.tooltipDescription = "Edit the task description. Click OK to save."

        # Time estimate — stored in ms by ClickUp, displayed/entered in hours
        try:
            est_ms = int(task["time_estimate"]) if task.get("time_estimate") else None
        except (ValueError, TypeError):
            est_ms = None
        est_str = f"{est_ms / 3_600_000:.2f}".rstrip("0").rstrip(".") if est_ms else ""
        time_cell = inputs.addStringValueInput(
            f"{id_prefix}_time_{tid}",
            "Est. Hours",
            est_str,
        )
        time_cell.tooltip = "Time Estimate (hours)"
        time_cell.tooltipDescription = (
            "Enter the estimated time in hours (e.g. 1.5). Leave blank to clear."
        )


def command_execute(args: adsk.core.CommandEventArgs):
    """OK was clicked — PATCH any changed priority, status, or description fields to ClickUp."""
    futil.log(f"{CMD_NAME}: Execute — scanning for changed fields.")

    inputs = args.command.commandInputs
    updated = 0
    errors = 0

    for key, original in _task_originals.items():
        # key is "{id_prefix}_{task_id}"
        prefix, task_id = key.split("_", 1)

        payload: dict = {}

        # ---- Priority ----
        pri_input = inputs.itemById(f"{prefix}_priority_{task_id}")
        if pri_input and hasattr(pri_input, "selectedItem") and pri_input.selectedItem:
            new_pri_label = pri_input.selectedItem.name
            new_pri_int = _PRIORITY_LABEL_TO_INT.get(new_pri_label, 3)
            if new_pri_int != original.get("priority"):
                payload["priority"] = new_pri_int
                futil.log(
                    f"{CMD_NAME}: [{task_id}] priority changed → {new_pri_int} ({new_pri_label})"
                )

        # ---- Status ----
        status_input = inputs.itemById(f"{prefix}_status_{task_id}")
        if status_input is not None:
            if hasattr(status_input, "selectedItem") and status_input.selectedItem:
                new_status = status_input.selectedItem.name.lower()
            else:
                new_status = getattr(status_input, "value", "").lower()
            if new_status and new_status != original.get("status", ""):
                payload["status"] = new_status
                futil.log(f"{CMD_NAME}: [{task_id}] status changed → '{new_status}'")

        # ---- Description ----
        desc_input = inputs.itemById(f"{prefix}_desc_{task_id}")
        if desc_input is not None:
            new_desc = (getattr(desc_input, "formattedText", "") or "").strip()
            if new_desc != (original.get("description", "") or "").strip():
                payload["description"] = new_desc
                futil.log(f"{CMD_NAME}: [{task_id}] description changed")

        # ---- Time estimate ----
        time_input = inputs.itemById(f"{prefix}_time_{task_id}")
        if time_input is not None:
            raw_val = getattr(time_input, "value", "").strip()
            try:
                new_est_ms = int(float(raw_val) * 3_600_000) if raw_val else 0
            except ValueError:
                new_est_ms = original.get("time_estimate_ms") or 0
            orig_est_ms = original.get("time_estimate_ms") or 0
            if new_est_ms != orig_est_ms:
                payload["time_estimate"] = new_est_ms
                futil.log(
                    f"{CMD_NAME}: [{task_id}] time_estimate changed → {new_est_ms}ms"
                )

        if not payload:
            continue

        ok = _patch_task(task_id, payload, _api_token)
        if ok:
            updated += 1
        else:
            errors += 1

    if updated == 0 and errors == 0:
        futil.log(f"{CMD_NAME}: No changes — dialog closed.")
    elif errors == 0:
        ui.messageBox(f"{updated} task(s) updated successfully.", CMD_NAME)
    else:
        ui.messageBox(
            f"{updated} task(s) updated, {errors} failed.\n\nCheck the add-in log for details.",
            CMD_NAME,
        )


def command_destroy(args: adsk.core.CommandEventArgs):
    """Called when the dialog closes."""
    futil.log(f"{CMD_NAME}: Destroyed. Clearing handlers.")
    global local_handlers
    local_handlers = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_task(task_id: str, payload: dict, api_token: str) -> bool:
    """PATCH /api/v2/task/{task_id} with *payload*. Returns True on success."""
    import json as _json

    url = f"{CLICKUP_API_BASE}/task/{task_id}"
    body = _json.dumps(payload)
    futil.log(f"{CMD_NAME}: _patch_task — PATCH '{url}' body={body}")
    try:
        req = adsk.core.HttpRequest.create(url, adsk.core.HttpMethods.PutMethod)
        req.setHeader("Authorization", api_token)
        req.setHeader("Content-Type", "application/json")
        req.setHeader("Accept", "application/json")
        req.data = body
        response = req.executeSync()
        futil.log(f"{CMD_NAME}: _patch_task [{task_id}] — HTTP {response.statusCode}")
        if not (200 <= response.statusCode < 300):
            futil.log(f"{CMD_NAME}: _patch_task [{task_id}] — error: {response.data}")
            return False
        return True
    except Exception as exc:
        futil.log(f"{CMD_NAME}: _patch_task [{task_id}] — exception: {exc}")
        return False


def _fetch_list_statuses(list_id: str, api_token: str) -> list:
    """GET /api/v2/list/{list_id} and return its statuses array sorted by orderindex.

    Each item is a dict like: {"status": "in progress", "color": "#...", ...}.
    Returns an empty list on any failure.
    ClickUp docs: https://developer.clickup.com/reference/getlist
    """
    url = f"{CLICKUP_API_BASE}/list/{list_id}"
    futil.log(f"{CMD_NAME}: _fetch_list_statuses — GET '{url}'")
    try:
        req = adsk.core.HttpRequest.create(url, adsk.core.HttpMethods.GetMethod)
        req.setHeader("Authorization", api_token)
        req.setHeader("Accept", "application/json")
        response = req.executeSync()
        futil.log(f"{CMD_NAME}: _fetch_list_statuses — HTTP {response.statusCode}")
        if not (200 <= response.statusCode < 300):
            futil.log(f"{CMD_NAME}: _fetch_list_statuses — error: {response.data}")
            return []
        data = json.loads(response.data)
        statuses = data.get("statuses", [])
        statuses.sort(key=lambda s: s.get("orderindex", 0))
        return statuses
    except Exception as exc:
        futil.log(f"{CMD_NAME}: _fetch_list_statuses — exception: {exc}")
        return []


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
    return (
        data.get("projects", {}).get(project_urn, {}).get("clickup_list_id", "").strip()
    )


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
            futil.log(
                f"{CMD_NAME}: _get_urn_custom_field_id — HTTP {response.statusCode}: {response.data}"
            )
            return ""

        fields = json.loads(response.data).get("fields", [])
        matched = next((f for f in fields if f.get("name") == TARGET_NAME), None)
        if matched:
            futil.log(
                f"{CMD_NAME}: _get_urn_custom_field_id — found id='{matched.get('id')}'"
            )
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

    Returns a list of raw ClickUp task dicts (page 0, up to 100).
    API docs: https://developer.clickup.com/reference/gettasks
    """
    import urllib.parse

    cf_filter = json.dumps(
        [{"field_id": urn_field_id, "operator": "=", "value": doc_urn}]
    )
    params = urllib.parse.urlencode(
        {
            "custom_field": cf_filter,
            "page": 0,
            "include_closed": "true",
        }
    )
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


def _fetch_all_tasks(list_id: str, api_token: str) -> list:
    """Fetch all tasks from the list (no custom-field filter), including closed.

    Returns a list of raw ClickUp task dicts (page 0, up to 100).
    API docs: https://developer.clickup.com/reference/gettasks
    """
    import urllib.parse

    params = urllib.parse.urlencode({"page": 0, "include_closed": "true"})
    url = f"{CLICKUP_API_BASE}/list/{list_id}/task?{params}"
    futil.log(f"{CMD_NAME}: _fetch_all_tasks — GET '{url}'")

    try:
        req = adsk.core.HttpRequest.create(url, adsk.core.HttpMethods.GetMethod)
        req.setHeader("Authorization", api_token)
        req.setHeader("Accept", "application/json")
        response = req.executeSync()
        futil.log(f"{CMD_NAME}: _fetch_all_tasks — HTTP {response.statusCode}")
        if not (200 <= response.statusCode < 300):
            futil.log(f"{CMD_NAME}: _fetch_all_tasks — error body: {response.data}")
            return []
        return json.loads(response.data).get("tasks", [])
    except Exception as exc:
        futil.log(f"{CMD_NAME}: _fetch_all_tasks — exception: {exc}")
        return []
