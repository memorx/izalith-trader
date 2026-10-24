' Abre Izalith desde el escritorio: si el bot no está corriendo lo arranca (sin ventana) y
' después abre el panel en el navegador. Si ya está corriendo, sólo abre el panel.
' Lo usa el ícono "Izalith Trader" del escritorio.
Option Explicit
Dim sh, fso, carpeta, i
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
carpeta = fso.GetParentFolderName(WScript.ScriptFullName)

Function Escucha(puerto)
    Escucha = (sh.Run("powershell -NoProfile -WindowStyle Hidden -Command ""if (Get-NetTCPConnection -LocalPort " _
        & puerto & " -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }""", 0, True) = 0)
End Function

' 47651 = el candado del bot (sólo una copia a la vez); 8765 = el panel
If Not Escucha(47651) Then
    sh.Run """" & carpeta & "\arrancar.bat""", 0, False
    For i = 1 To 60
        WScript.Sleep 2000
        If Escucha(8765) Then Exit For
    Next
End If
sh.Run "http://127.0.0.1:8765"
