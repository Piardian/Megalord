Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\piard\Desktop\MegalordProject"
WshShell.Run "cmd.exe /c ""C:\Users\piard\Desktop\MegalordProject\OTO_BASLAT_MEGALORD_LIVE.bat""", 0, False
