@echo off
setlocal
"%~dp0python-core\python.exe" "%~dp0worker\local_api.py" %*
endlocal
