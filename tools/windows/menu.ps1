# Меню проверочных скриптов для 1С. Запускается на сервере 1С одной строкой
# в обычном (не админском) PowerShell:
#
#   irm https://raw.githubusercontent.com/Andrey-Sema/workflow-automation-bot/ccr-60f70972-blxpe5/tools/windows/menu.ps1 | iex
#
# Скрипты каждый раз скачиваются свежими из репозитория, поэтому их не
# нужно копировать через мессенджеры (те портят отступы и спецсимволы).
# Все пункты, кроме 9, только читают.

$AI1C_URL = "https://raw.githubusercontent.com/Andrey-Sema/workflow-automation-bot/ccr-60f70972-blxpe5/tools/windows"
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Get-AI1CScript($file) {
  # Случайный параметр обходит кэш raw.githubusercontent.com (5 минут),
  # чтобы после исправления скрипта сразу запускалась новая версия.
  Invoke-RestMethod "$AI1C_URL/${file}?nocache=$(Get-Random)"
}

Invoke-Expression (Get-AI1CScript "lib1c.ps1")

$AI1C_MENU = [ordered]@{
  "1" = @("Проверка окружения сервера", "01_env_check.ps1")
  "2" = @("Структура объекта 1С (реквизиты, табличные части)", "02_structure.ps1")
  "3" = @("Как заполнены последние наряды (без персональных данных)", "03_naryad_sample.ps1")
  "4" = @("Открывается ли наряд через скрытый толстый клиент (V83.Application)", "04_app_getobject.ps1")
  "5" = @("Какие услуги и товары используются в нарядах (за 90 дней)", "05_nomenclature_usage.ps1")
}

while ($true) {
  Write-Host ""
  Write-Host "===== Проверки 1С =====" -ForegroundColor Cyan
  foreach ($k in $AI1C_MENU.Keys) { Write-Host "  $k - $($AI1C_MENU[$k][0])" }
  Write-Host "  9 - Установить команду ai1c (дальше меню открывается одним словом)"
  Write-Host "  0 - Выход"
  $choice = Read-Host "Выберите пункт"
  if ($choice -eq "0" -or -not $choice) { break }
  if ($choice -eq "9") {
    $prof = $PROFILE.CurrentUserAllHosts
    if (-not (Test-Path $prof)) { New-Item -ItemType File -Path $prof -Force | Out-Null }
    if (-not (Select-String -Path $prof -Pattern "function ai1c" -Quiet)) {
      Add-Content -Path $prof -Value "function ai1c { [Net.ServicePointManager]::SecurityProtocol = 'Tls12'; Invoke-Expression (Invoke-RestMethod '$AI1C_URL/menu.ps1') }"
    }
    Write-Host "Готово. В новом окне PowerShell / ISE просто наберите: ai1c" -ForegroundColor Green
    continue
  }
  if (-not $AI1C_MENU.Contains($choice)) { Write-Host "Нет такого пункта" -ForegroundColor Yellow; continue }
  try {
    Invoke-Expression (Get-AI1CScript $AI1C_MENU[$choice][1])
  } catch {
    Format-Error1C $_ | ForEach-Object { Write-Host $_ -ForegroundColor Red }
  }
}
