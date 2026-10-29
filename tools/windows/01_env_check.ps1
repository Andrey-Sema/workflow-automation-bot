# Проверка окружения сервера: права Windows, установленная 1С, COM,
# доступные программы и выход в интернет. Только чтение.

$r = @()
try {
  [Net.ServicePointManager]::SecurityProtocol = 'Tls12'
  $r += "=== Система ==="
  $r += "Пользователь: $env:USERDOMAIN\$env:USERNAME   Компьютер: $env:COMPUTERNAME"
  $r += (Get-CimInstance Win32_OperatingSystem | ForEach-Object { "$($_.Caption) $($_.Version)" })
  $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
  $r += "Локальный админ: " + $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  $r += "PowerShell: $($PSVersionTable.PSVersion)   ExecutionPolicy: $(Get-ExecutionPolicy)"
  $r += (Get-PSDrive C | ForEach-Object { "Свободно на C: {0:N1} ГБ" -f ($_.Free / 1GB) })

  $r += "=== 1С ==="
  $r += Get-ChildItem "C:\Program Files\1cv8", "C:\Program Files (x86)\1cv8" -Directory -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }
  $r += Get-Process 1cv8* -ErrorAction SilentlyContinue | Where-Object { $_.Path } | ForEach-Object { "Процесс: $($_.Path)" }
  $r += Get-Service *1C* -ErrorAction SilentlyContinue | ForEach-Object { "Служба: $($_.DisplayName) [$($_.Status)]" }
  $r += "COMConnector зарегистрирован: " + (Test-Path "Registry::HKEY_CLASSES_ROOT\V83.COMConnector")
  $r += "V83.Application зарегистрирован: " + (Test-Path "Registry::HKEY_CLASSES_ROOT\V83.Application")

  $r += "=== Программы ==="
  foreach ($n in "python", "py", "git", "node", "docker", "winget") {
    $cmd = Get-Command $n -ErrorAction SilentlyContinue
    $r += "$n : " + $(if ($cmd) { $cmd.Source } else { "нет" })
  }

  $r += "=== Запуск своих программ из своей папки ==="
  $t = "$env:TEMP\ai1c_test.exe"
  Copy-Item "$env:WINDIR\System32\whoami.exe" $t -Force
  try { & $t | Out-Null; $r += "Запуск из TEMP: OK" } catch { $r += "Запуск из TEMP: ЗАБЛОКИРОВАН ($($_.Exception.Message))" }
  Remove-Item $t -ErrorAction SilentlyContinue

  $r += "=== Сеть ==="
  foreach ($h in "api.telegram.org", "generativelanguage.googleapis.com", "api.anthropic.com", "github.com", "pypi.org") {
    $ok = Test-NetConnection $h -Port 443 -InformationLevel Quiet -WarningAction SilentlyContinue
    $r += "$h :443 -> $ok"
  }
} catch {
  $r += Format-Error1C $_
}
Save-Result "01-env" $r
