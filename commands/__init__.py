from .saveURL import entry as commandDialog
from .openClickUp import entry as openClickUp
from .addtask import entry as addTask
from .setTokens import entry as setTokens
from .listTasks import entry as listTasks

# Fusion will automatically call the start() and stop() functions.
commands = [commandDialog, openClickUp, addTask, listTasks, setTokens]


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
