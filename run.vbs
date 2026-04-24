Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "cmd /c cd /d ""c:\Users\BXJ0042\Desktop\4090"" && ""C:\Users\BXJ0042\AppData\Local\Programs\Python\Python313\python.exe"" server_minimal.py > server.log 2>&1 && pause", 1, False
Set WshShell = Nothing
