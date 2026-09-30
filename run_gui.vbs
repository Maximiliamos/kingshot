Option Explicit

Dim shell, files, root, pythonw
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
pythonw = "C:\warbot_wsa\tugarin-venv\Scripts\pythonw.exe"

If Not files.FileExists(pythonw) Then
    pythonw = "pythonw.exe"
End If

shell.CurrentDirectory = root
shell.Run """" & pythonw & """ """ & root & "\gui.py""", 0, False
