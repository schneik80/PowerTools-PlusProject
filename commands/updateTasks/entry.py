import adsk.core
import adsk.fusion
import json
import os
from datetime import datetime
from urllib.parse import urlencode

from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

# Command identity information
CMD_ID = f"{config.COMPANY_NAME}_{config.ADDIN_NAME}_updateTasks"
CMD_NAME = "Update Tasks"
CMD_Description = "View and update ClickUp tasks linked to the active Fusion document"

IS_PROMOTED = True
WORKSPACE_ID = config.design_workspace
TAB_ID = config.tools_tab_id
TAB_NAME = config.my_tab_name

PANEL_ID = config.my_panel_id
PANEL_NAME = config.my_panel_name
PANEL_AFTER = config.my_panel_after

ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "")

# ClickUp API configuration
CLICKUP_API_BASE = "https://api.clickup.com/api/v2"

CACHE_DIR = config.CACHE_DIR
AUTH_JSON_PATH = os.path.join(CACHE_DIR, "auth.json")
PROJECTS_JSON_PATH = os.path.join(CACHE_DIR, "projects.json")

# ClickUp priority: display label → API integer
_PRIORITY_OPTIONS = ["Normal", "Low", "High", "Urgent"]
_PRIORITY_LABEL_TO_INT = {"Urgent": 1, "High": 2, "Normal": 3, "Low": 4}
_PRIORITY_INT_TO_LABEL = {v: k for k, v in _PRIORITY_LABEL_TO_INT.items()}
_PRIORITY_LABEL_DISPLAY = {1: "🔴 Urgent", 2: "🟠 High", 3: "🔵 Normal", 4: "⚪ Low"}

local_handlers = []

# Module-level: original task data keyed by task_id, set in command_created,
# consumed in command_execute to detect changed fields.
_task_originals: dict = (
    {}
)  # task_id → {"name": str, "due_ms": int|None, "priority": int|None, "status": str|None, "description": str, "time_estimate_ms": int|None}
_api_token: str = ""
_list_url: str = ""
_list_statuses: list = []  # [{"status": str, "color": str}, ...]
_list_members: list = []  # [{"id": int, "username": str, "email": str}, ...]
_selected_task_id: str = ""  # task ID of the currently selected table row
_pending_edits: dict = {}  # task_id → {desc, time_hours, assignee_name, is_private}


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
    """Builds the update-tasks dialog."""
    global _task_originals, _api_token, _list_url, _list_statuses, _list_members, _selected_task_id, _pending_edits
    _task_originals = {}
    _api_token = ""
    _list_url = ""
    _list_statuses = []
    _list_members = []
    _selected_task_id = ""
    _pending_edits = {}

    futil.log(f"{CMD_NAME}: Command Created — building update tasks dialog.")

    # ------------------------------------------------------------------ #
    # Pre-flight checks                                                   #
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

    futil.log(f"{CMD_NAME}: project_urn='{project_urn}'  doc_urn='{doc_urn}'")

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

    _api_token = _load_api_token()
    if not _api_token:
        ui.messageBox(
            "ClickUp API token not found.\n\nPlease run 'Set Tokens'.",
            "Authentication Error",
        )
        args.command.isAutoExecute = True
        return

    # Fetch available statuses for the list (used to populate the status dropdown)
    _list_statuses = _fetch_list_statuses(list_id, _api_token)
    futil.log(
        f"{CMD_NAME}: fetched {len(_list_statuses)} status(es) for list '{list_id}'."
    )

    # Fetch list members (used to populate the assignee dropdown)
    _list_members = _fetch_list_members(list_id, _api_token)
    futil.log(
        f"{CMD_NAME}: fetched {len(_list_members)} member(s) for list '{list_id}'."
    )

    # ------------------------------------------------------------------ #
    # Fetch tasks linked to this document                                 #
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

    raw_tasks = _fetch_tasks_for_urn(list_id, urn_field_id, doc_urn, _api_token)

    # Client-side exact-match on the URN custom field value
    def _urn_matches(task: dict) -> bool:
        for cf in task.get("custom_fields", []):
            if cf.get("id") == urn_field_id:
                return cf.get("value", "") == doc_urn
        return False

    doc_tasks = [t for t in raw_tasks if _urn_matches(t)]
    futil.log(
        f"{CMD_NAME}: {len(raw_tasks)} API result(s) → {len(doc_tasks)} exact match(es)."
    )

    # Sort by priority
    def _pri_sort(task):
        raw = task.get("priority") or {}
        try:
            return {1: 0, 2: 1, 3: 2, 4: 3}.get(int(raw.get("id", 99)), 99)
        except (ValueError, TypeError):
            return 99

    doc_tasks.sort(key=_pri_sort)

    # Store originals for later change detection
    for task in doc_tasks:
        tid = task.get("id", "")
        due_ms = task.get("due_date")
        try:
            due_ms = int(due_ms) if due_ms else None
        except (ValueError, TypeError):
            due_ms = None

        pri_raw = task.get("priority") or {}
        try:
            pri_int = int(pri_raw.get("id", 0)) or None
        except (ValueError, TypeError):
            pri_int = None

        status_str = task.get("status", {}).get("status", "").lower()

        try:
            time_est_ms = (
                int(task["time_estimate"]) if task.get("time_estimate") else None
            )
        except (ValueError, TypeError):
            time_est_ms = None

        raw_assignees = task.get("assignees", [])
        assignee_ids = [int(a["id"]) for a in raw_assignees if a.get("id")]

        _task_originals[tid] = {
            "name": task.get("name", ""),
            "due_ms": due_ms,
            "priority": pri_int,
            "status": status_str,
            "description": (task.get("description") or "").strip(),
            "time_estimate_ms": time_est_ms,
            "is_private": bool(task.get("is_private", False)),
            "assignee_ids": assignee_ids,
        }

    # ------------------------------------------------------------------ #
    # Build dialog                                                        #
    # ------------------------------------------------------------------ #
    inputs = args.command.commandInputs

    # Info bar
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

    inputs.addTextBoxCommandInput(
        "doc_tasks_header",
        "",
        f"<b>Tasks Linked to This Document</b> ({len(doc_tasks)})",
        1,
        True,
    )

    _build_editable_task_table(inputs, doc_tasks, _list_statuses)

    # ------------------------------------------------------------------ #
    # Shared detail controls (populated when a table row is selected)     #
    # ------------------------------------------------------------------ #
    inputs.addTextBoxCommandInput(
        "detail_header",
        "",
        "Select a row above to view and edit task details.",
        1,
        True,
    )

    desc_ctrl = inputs.addTextBoxCommandInput(
        "detail_desc", "Description", "", 6, False
    )
    desc_ctrl.isEnabled = False

    time_ctrl = inputs.addStringValueInput("detail_time", "Est. Hours", "")
    time_ctrl.isEnabled = False
    time_ctrl.tooltip = "Time Estimate (hours)"
    time_ctrl.tooltipDescription = (
        "Enter the estimated time in hours (e.g. 1.5). Leave blank to clear."
    )

    assignee_ctrl = inputs.addDropDownCommandInput(
        "detail_assignee",
        "Assignee",
        adsk.core.DropDownStyles.TextListDropDownStyle,
    )
    assignee_ctrl.isEnabled = False
    assignee_ctrl.tooltip = "Assignee"
    assignee_ctrl.tooltipDescription = (
        "Assign or reassign this task to a list member. "
        "Select '— Unassigned —' to remove all assignees."
    )
    assignee_ctrl.listItems.add("— Unassigned —", True)
    for member in _list_members:
        assignee_ctrl.listItems.add(member["username"], False)

    private_ctrl = inputs.addBoolValueInput(
        "detail_private", "Private Task", True, "", False
    )
    private_ctrl.isEnabled = False
    private_ctrl.tooltip = "Private Task"
    private_ctrl.tooltipDescription = (
        "When checked, this task is only visible to its creator and assignees. "
        "Select an assignee above to enable this option."
    )

    apply_btn = inputs.addBoolValueInput(
        "btn_apply_edits", "Apply Edits to Selected Row", True, "", False
    )
    apply_btn.isEnabled = False
    apply_btn.tooltip = "Apply Edits"
    apply_btn.tooltipDescription = "Save the detail-panel edits for the selected task and return the panel to its empty state."

    # ------------------------------------------------------------------ #
    # Connect events                                                      #
    # ------------------------------------------------------------------ #
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


def _build_editable_task_table(
    inputs: adsk.core.CommandInputs,
    tasks: list,
    status_options: list = None,
) -> None:
    """Add an editable table — Select | Task Name | Due Date | Priority | Status — to *inputs*.

    A checkbox in the first column lets the user select a row; selection populates the
    shared detail controls (description, time estimate, assignee, private) below the table.
    Name, Due Date, Priority, and Status remain directly editable in the table.
    *status_options* is a list of dicts from the ClickUp API: [{"status": str, ...}, ...].
    If empty or None, the status cell falls back to a read-only string.
    """
    if status_options is None:
        status_options = []
    table = inputs.addTableCommandInput("tasks_table", "", 5, "1:5:3:2:2")
    table.hasGrid = True
    table.minimumVisibleRows = 3
    table.maximumVisibleRows = 15
    table.tooltip = "Check a row to edit its details below. Edit Name, Due, Priority, and Status directly in the table."

    # Header row
    for col_id, label in [
        ("h_sel", ""),
        ("h_name", "Task Name"),
        ("h_due", "Due Date"),
        ("h_priority", "Priority"),
        ("h_status", "Status"),
    ]:
        cell = inputs.addStringValueInput(col_id, "", label)
        cell.isReadOnly = True

    table.addCommandInput(inputs.itemById("h_sel"), 0, 0)
    table.addCommandInput(inputs.itemById("h_name"), 0, 1)
    table.addCommandInput(inputs.itemById("h_due"), 0, 2)
    table.addCommandInput(inputs.itemById("h_priority"), 0, 3)
    table.addCommandInput(inputs.itemById("h_status"), 0, 4)

    if not tasks:
        empty = inputs.addTextBoxCommandInput(
            "tasks_empty", "", "No tasks linked to this document.", 1, True
        )
        table.addCommandInput(empty, 1, 0, 0, 5)
        return

    for i, task in enumerate(tasks, start=1):
        tid = task.get("id", f"unknown_{i}")
        task_name = task.get("name", "")
        task_url = task.get("url", "")

        # Due date: ms timestamp → YYYY-MM-DD
        due_ms = task.get("due_date")
        try:
            due_str = (
                datetime.fromtimestamp(int(due_ms) / 1000).strftime("%Y-%m-%d")
                if due_ms
                else ""
            )
        except (ValueError, TypeError):
            due_str = ""

        # Priority
        pri_raw = task.get("priority") or {}
        try:
            pri_int = int(pri_raw.get("id", 0)) or None
        except (ValueError, TypeError):
            pri_int = None
        pri_label = _PRIORITY_INT_TO_LABEL.get(pri_int, "Normal")

        # Status — current value (lowercase to match ClickUp API)
        status_str = task.get("status", {}).get("status", "").lower()

        # Select checkbox — col 0
        sel_cell = inputs.addBoolValueInput(f"sel_{tid}", "", True, "", False)
        sel_cell.tooltip = task_name
        sel_cell.tooltipDescription = (
            "Check to edit the details (description, time estimate, assignee) for this task."
            + (f'<br><a href="{task_url}">Open in ClickUp</a>' if task_url else "")
        )

        # Name cell — editable text with link in tooltip — col 1
        name_cell = inputs.addStringValueInput(f"name_{tid}", "", task_name)
        name_cell.tooltip = "Task Name"
        name_cell.tooltipDescription = f"Edit the task name.<br>" + (
            f'<a href="{task_url}">Open in ClickUp</a>' if task_url else ""
        )

        # Due date cell — editable text — col 2
        due_cell = inputs.addStringValueInput(f"due_{tid}", "", due_str)
        due_cell.tooltip = "Due Date"
        due_cell.tooltipDescription = "Enter a date in <b>YYYY-MM-DD</b> format, or leave blank to clear the due date."

        # Priority cell — drop-down — col 3
        pri_cell = inputs.addDropDownCommandInput(
            f"priority_{tid}",
            "",
            adsk.core.DropDownStyles.TextListDropDownStyle,
        )
        pri_cell.tooltip = "Priority"
        pri_cell.tooltipDescription = "Set the ClickUp task priority."
        for opt in _PRIORITY_OPTIONS:
            pri_cell.listItems.add(opt, opt == pri_label)

        # Status cell — dropdown if we have API-sourced options, else read-only — col 4
        if status_options:
            status_cell = inputs.addDropDownCommandInput(
                f"status_{tid}",
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
            # If nothing matched, force-select the first item
            if not matched and status_cell.listItems.count > 0:
                status_cell.listItems.item(0).isSelected = True
        else:
            status_cell = inputs.addStringValueInput(
                f"status_{tid}", "", status_str.title() or "—"
            )
            status_cell.isReadOnly = True
            status_cell.tooltip = "Status"
            status_cell.tooltipDescription = "Status could not be fetched from ClickUp."

        table.addCommandInput(sel_cell, i, 0)
        table.addCommandInput(name_cell, i, 1)
        table.addCommandInput(due_cell, i, 2)
        table.addCommandInput(pri_cell, i, 3)
        table.addCommandInput(status_cell, i, 4)


def command_execute(args: adsk.core.CommandEventArgs):
    """Called when the user clicks OK — PATCHes any changed tasks."""
    futil.log(f"{CMD_NAME}: Execute — scanning for changed fields.")

    inputs = args.command.commandInputs
    updated = 0
    errors = 0

    # Auto-apply any unsaved detail-panel edits for the currently selected row
    if _selected_task_id:
        _store_pending_edits(inputs, _selected_task_id)

    for task_id, original in _task_originals.items():

        # ---- Read current dialog values ----
        name_input = inputs.itemById(f"name_{task_id}")
        due_input = inputs.itemById(f"due_{task_id}")
        pri_input = inputs.itemById(f"priority_{task_id}")
        status_input = inputs.itemById(f"status_{task_id}")

        new_name = getattr(name_input, "value", original["name"]).strip()

        new_due_str = getattr(due_input, "value", "").strip()
        new_due_ms = _date_to_unix_ms(new_due_str) if new_due_str else None

        new_pri_label = (
            pri_input.selectedItem.name
            if pri_input and pri_input.selectedItem
            else "Normal"
        )
        new_pri_int = _PRIORITY_LABEL_TO_INT.get(new_pri_label, 3)

        # Status — read from dropdown (or string if fallback)
        if (
            status_input
            and hasattr(status_input, "selectedItem")
            and status_input.selectedItem
        ):
            new_status = status_input.selectedItem.name.lower()
        elif status_input and hasattr(status_input, "value"):
            new_status = getattr(status_input, "value", "").lower()
        else:
            new_status = original.get("status", "")

        # ---- Detect changes ----
        payload: dict = {}

        if new_name and new_name != original["name"]:
            payload["name"] = new_name
            futil.log(f"{CMD_NAME}: [{task_id}] name changed → '{new_name}'")

        if new_due_ms != original["due_ms"]:
            if new_due_ms is not None:
                payload["due_date"] = new_due_ms
                payload["due_date_time"] = False
                futil.log(f"{CMD_NAME}: [{task_id}] due_date changed → {new_due_ms}ms")
            else:
                # Clearing the due date: pass null
                payload["due_date"] = None
                futil.log(f"{CMD_NAME}: [{task_id}] due_date cleared")

        if new_pri_int != original["priority"]:
            payload["priority"] = new_pri_int
            futil.log(
                f"{CMD_NAME}: [{task_id}] priority changed → {new_pri_int} ({new_pri_label})"
            )

        if new_status and new_status != original.get("status", ""):
            payload["status"] = new_status
            futil.log(f"{CMD_NAME}: [{task_id}] status changed → '{new_status}'")

        # ---- Description (from pending edits) ----
        if task_id in _pending_edits:
            new_desc = (_pending_edits[task_id].get("desc", "") or "").strip()
            if new_desc != (original.get("description", "") or "").strip():
                payload["description"] = new_desc
                futil.log(f"{CMD_NAME}: [{task_id}] description changed")

        # ---- Time estimate (from pending edits) ----
        if task_id in _pending_edits:
            raw_val = _pending_edits[task_id].get("time_hours", "").strip()
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

        # ---- Private (from pending edits) ----
        if task_id in _pending_edits:
            new_private = bool(_pending_edits[task_id].get("is_private", False))
            if new_private != original.get("is_private", False):
                payload["is_private"] = new_private
                futil.log(f"{CMD_NAME}: [{task_id}] is_private changed → {new_private}")

        # ---- Assignee (from pending edits) ----
        if task_id in _pending_edits:
            selected_name = _pending_edits[task_id].get(
                "assignee_name", "— Unassigned —"
            )
            new_assignee_id = 0
            for member in _list_members:
                if member["username"] == selected_name:
                    new_assignee_id = member["id"]
                    break
            orig_assignee_ids = original.get("assignee_ids", [])
            orig_first_id = orig_assignee_ids[0] if orig_assignee_ids else 0
            if new_assignee_id != orig_first_id:
                add_ids = [new_assignee_id] if new_assignee_id else []
                rem_ids = [orig_first_id] if orig_first_id else []
                payload["assignees"] = {"add": add_ids, "rem": rem_ids}
                futil.log(
                    f"{CMD_NAME}: [{task_id}] assignees changed → "
                    f"add={add_ids} rem={rem_ids}"
                )

        if not payload:
            futil.log(f"{CMD_NAME}: [{task_id}] no changes — skipping.")
            continue

        # ---- PATCH ClickUp task ----
        ok = _patch_task(task_id, payload, _api_token)
        if ok:
            updated += 1
        else:
            errors += 1

    # ---- Summary feedback ----
    if updated == 0 and errors == 0:
        ui.messageBox("No changes were made.", CMD_NAME)
    elif errors == 0:
        ui.messageBox(
            f"{updated} task(s) updated successfully.",
            CMD_NAME,
        )
    else:
        ui.messageBox(
            f"{updated} task(s) updated, {errors} failed.\n\n"
            "Check the Fusion add-in log for details.",
            CMD_NAME,
        )


def command_validate_input(args: adsk.core.ValidateInputsEventArgs):
    """Validate due-date fields — must be blank or a valid YYYY-MM-DD."""
    inputs = args.inputs

    for task_id in _task_originals:
        due_input = inputs.itemById(f"due_{task_id}")
        if due_input is None:
            continue
        val = getattr(due_input, "value", "").strip()
        if val and _date_to_unix_ms(val) is None:
            args.areInputsValid = False
            return

    args.areInputsValid = True


def command_destroy(args: adsk.core.CommandEventArgs):
    """Called when the dialog closes — clears handler references."""
    futil.log(f"{CMD_NAME}: Destroyed. Clearing handlers.")
    global local_handlers
    local_handlers = []


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    """Handles table row selection, the Apply button, and the detail-panel assignee toggle."""
    global _selected_task_id, _pending_edits
    changed = args.input
    inputs = args.inputs

    # ---- Row selection via sel_{tid} checkboxes ----
    if changed.id.startswith("sel_"):
        tid = changed.id[4:]
        if getattr(changed, "value", False):
            # New row selected — update tracking first, then deselect any other row
            _selected_task_id = tid
            for other_tid in _task_originals:
                if other_tid != tid:
                    other_sel = inputs.itemById(f"sel_{other_tid}")
                    if other_sel and getattr(other_sel, "value", False):
                        other_sel.value = False
            _populate_detail_controls(inputs, tid)
        else:
            # Row deselected
            if _selected_task_id == tid:
                _selected_task_id = ""
                _clear_detail_controls(inputs)
        return

    # ---- Detail-panel assignee change — toggle private checkbox ----
    if changed.id == "detail_assignee":
        private_ctrl = inputs.itemById("detail_private")
        if private_ctrl:
            selected = getattr(changed, "selectedItem", None)
            is_assigned = selected is not None and selected.name != "— Unassigned —"
            private_ctrl.isEnabled = is_assigned
            if not is_assigned:
                private_ctrl.value = False
        return

    # ---- Apply button ----
    if changed.id == "btn_apply_edits" and getattr(changed, "value", False):
        changed.value = False  # Reset button immediately
        tid_to_apply = _selected_task_id
        _selected_task_id = ""  # Clear before triggering sel_ deselect event
        if tid_to_apply:
            _store_pending_edits(inputs, tid_to_apply)
            sel_input = inputs.itemById(f"sel_{tid_to_apply}")
            if sel_input:
                sel_input.value = False
            _clear_detail_controls(inputs)
            header = inputs.itemById("detail_header")
            if header:
                header.formattedText = (
                    "Edits applied. Select another row to continue editing."
                )
        return


# ---------------------------------------------------------------------------
# Detail-panel helpers
# ---------------------------------------------------------------------------


def _ms_to_hours_str(ms) -> str:
    """Convert milliseconds to a hours string like '1.5'. Returns '' if falsy."""
    if not ms:
        return ""
    return f"{ms / 3_600_000:.2f}".rstrip("0").rstrip(".")


def _get_member_name(assignee_ids: list) -> str:
    """Return the username of the first assignee ID, or '— Unassigned —'."""
    if not assignee_ids:
        return "— Unassigned —"
    first_id = assignee_ids[0]
    for member in _list_members:
        if member["id"] == first_id:
            return member["username"]
    return "— Unassigned —"


def _populate_detail_controls(inputs: adsk.core.CommandInputs, tid: str) -> None:
    """Fill the shared detail controls with data for the given task ID.

    Uses _pending_edits if available, otherwise falls back to _task_originals.
    Enables all detail controls.
    """
    if tid in _pending_edits:
        data = _pending_edits[tid]
        desc = data.get("desc", "")
        time_hours = data.get("time_hours", "")
        assignee_name = data.get("assignee_name", "— Unassigned —")
        is_private = data.get("is_private", False)
    else:
        orig = _task_originals.get(tid, {})
        desc = (orig.get("description") or "").strip()
        time_hours = _ms_to_hours_str(orig.get("time_estimate_ms"))
        assignee_name = _get_member_name(orig.get("assignee_ids", []))
        is_private = bool(orig.get("is_private", False))

    task_name = _task_originals.get(tid, {}).get("name", tid)
    header = inputs.itemById("detail_header")
    if header:
        header.formattedText = f"<b>Editing:</b> {task_name}"

    desc_ctrl = inputs.itemById("detail_desc")
    if desc_ctrl:
        desc_ctrl.formattedText = desc
        desc_ctrl.isEnabled = True

    time_ctrl = inputs.itemById("detail_time")
    if time_ctrl:
        time_ctrl.value = time_hours
        time_ctrl.isEnabled = True

    assignee_ctrl = inputs.itemById("detail_assignee")
    if assignee_ctrl:
        assignee_ctrl.isEnabled = True
        matched = False
        for i in range(assignee_ctrl.listItems.count):
            item = assignee_ctrl.listItems.item(i)
            is_match = item.name == assignee_name
            item.isSelected = is_match
            if is_match:
                matched = True
        if not matched and assignee_ctrl.listItems.count > 0:
            assignee_ctrl.listItems.item(0).isSelected = True

    private_ctrl = inputs.itemById("detail_private")
    if private_ctrl:
        is_assigned = assignee_name != "— Unassigned —"
        private_ctrl.isEnabled = is_assigned
        private_ctrl.value = is_private if is_assigned else False

    apply_btn = inputs.itemById("btn_apply_edits")
    if apply_btn:
        apply_btn.isEnabled = True


def _clear_detail_controls(inputs: adsk.core.CommandInputs) -> None:
    """Reset the shared detail controls to their empty, disabled state."""
    header = inputs.itemById("detail_header")
    if header:
        header.formattedText = "Select a row above to view and edit task details."

    desc_ctrl = inputs.itemById("detail_desc")
    if desc_ctrl:
        desc_ctrl.formattedText = ""
        desc_ctrl.isEnabled = False

    time_ctrl = inputs.itemById("detail_time")
    if time_ctrl:
        time_ctrl.value = ""
        time_ctrl.isEnabled = False

    assignee_ctrl = inputs.itemById("detail_assignee")
    if assignee_ctrl:
        assignee_ctrl.isEnabled = False
        if assignee_ctrl.listItems.count > 0:
            assignee_ctrl.listItems.item(0).isSelected = True

    private_ctrl = inputs.itemById("detail_private")
    if private_ctrl:
        private_ctrl.value = False
        private_ctrl.isEnabled = False

    apply_btn = inputs.itemById("btn_apply_edits")
    if apply_btn:
        apply_btn.value = False
        apply_btn.isEnabled = False


def _store_pending_edits(inputs: adsk.core.CommandInputs, tid: str) -> None:
    """Read the shared detail controls and store their values in _pending_edits[tid]."""
    desc_ctrl = inputs.itemById("detail_desc")
    time_ctrl = inputs.itemById("detail_time")
    assignee_ctrl = inputs.itemById("detail_assignee")
    private_ctrl = inputs.itemById("detail_private")

    desc = (getattr(desc_ctrl, "formattedText", "") or "").strip() if desc_ctrl else ""
    time_hours = getattr(time_ctrl, "value", "").strip() if time_ctrl else ""
    assignee_name = (
        assignee_ctrl.selectedItem.name
        if assignee_ctrl and assignee_ctrl.selectedItem
        else "— Unassigned —"
    )
    is_private = bool(getattr(private_ctrl, "value", False)) if private_ctrl else False

    _pending_edits[tid] = {
        "desc": desc,
        "time_hours": time_hours,
        "assignee_name": assignee_name,
        "is_private": is_private,
    }
    futil.log(f"{CMD_NAME}: Stored pending edits for task '{tid}'.")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def _patch_task(task_id: str, payload: dict, api_token: str) -> bool:
    """PATCH /api/v2/task/{task_id} with *payload*.

    Returns True on success (2xx), False otherwise.
    ClickUp docs: https://developer.clickup.com/reference/updatetask
    """
    url = f"{CLICKUP_API_BASE}/task/{task_id}"
    body = json.dumps(payload)
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
            futil.log(
                f"{CMD_NAME}: _patch_task [{task_id}] — error body: {response.data}"
            )
            return False
        return True
    except Exception as exc:
        futil.log(f"{CMD_NAME}: _patch_task [{task_id}] — exception: {exc}")
        return False


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
            return matched.get("id", "")

        futil.log(f"{CMD_NAME}: _get_urn_custom_field_id — '{TARGET_NAME}' not found.")
        return ""

    except Exception as exc:
        futil.log(f"{CMD_NAME}: _get_urn_custom_field_id — exception: {exc}")
        return ""


def _fetch_tasks_for_urn(
    list_id: str, urn_field_id: str, doc_urn: str, api_token: str
) -> list:
    """GET /api/v2/list/{list_id}/task filtered by the Fusion Document URN field."""
    cf_filter = json.dumps(
        [{"field_id": urn_field_id, "operator": "=", "value": doc_urn}]
    )
    params = urlencode(
        {
            "custom_field": cf_filter,
            "page": 0,
            "include_closed": "true",
        }
    )
    url = f"{CLICKUP_API_BASE}/list/{list_id}/task?{params}"
    futil.log(f"{CMD_NAME}: _fetch_tasks_for_urn — GET")

    try:
        req = adsk.core.HttpRequest.create(url, adsk.core.HttpMethods.GetMethod)
        req.setHeader("Authorization", api_token)
        req.setHeader("Accept", "application/json")
        response = req.executeSync()
        futil.log(f"{CMD_NAME}: _fetch_tasks_for_urn — HTTP {response.statusCode}")
        if not (200 <= response.statusCode < 300):
            futil.log(f"{CMD_NAME}: _fetch_tasks_for_urn — error: {response.data}")
            return []
        return json.loads(response.data).get("tasks", [])
    except Exception as exc:
        futil.log(f"{CMD_NAME}: _fetch_tasks_for_urn — exception: {exc}")
        return []


def _fetch_list_statuses(list_id: str, api_token: str) -> list:
    """GET /api/v2/list/{list_id} and return its statuses array.

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
        # Sort by orderindex so the dropdown matches the ClickUp order
        statuses.sort(key=lambda s: s.get("orderindex", 0))
        return statuses
    except Exception as exc:
        futil.log(f"{CMD_NAME}: _fetch_list_statuses — exception: {exc}")
        return []


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
            # API may return flat dicts or dicts nested under "user"
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


def _date_to_unix_ms(date_str: str):
    """Convert a YYYY-MM-DD string to Unix ms. Returns None if unparseable."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None
