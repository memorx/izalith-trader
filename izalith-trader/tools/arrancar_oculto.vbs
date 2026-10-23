' Corre arrancar.bat sin abrir ninguna ventana (0 = oculta). Lo usa la tarea programada.
Dim carpeta
carpeta = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run """" & carpeta & "\arrancar.bat""", 0, False
