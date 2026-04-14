# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2022-2026 IMA LLC

from .saveURL import entry as commandDialog
from .openClickUp import entry as openClickUp
from .addtask import entry as addTask
from .setTokens import entry as setTokens
from .listTasks import entry as listTasks
from .updateTasks import entry as updateTasks

# Fusion will automatically call the start() and stop() functions.
commands = [commandDialog, openClickUp, addTask, listTasks, updateTasks, setTokens]


# Assumes you defined a "start" function in each of your modules.
# The start function will be run when the add-in is started.
def start():
    for command in commands:
        command.start()


# Assumes you defined a "stop" function in each of your modules.
# The stop function will be run when the add-in is stopped.
def stop():
    for command in commands:
        command.stop()
