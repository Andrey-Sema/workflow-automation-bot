# Общие функции для работы с 1С через COM (V83.COMConnector).
# Загружается из menu.ps1, отдельно не запускается.
#
# PowerShell не умеет читать свойства объектов 1С через точку ($obj.Имя
# возвращает пустоту), поэтому все обращения идут через InvokeMember.
# Методы пробуются по очереди по списку имён "English|Русское": часть
# методов 1С отдаёт через COM только под русским именем.

$AI1C_SERVER = "WIN-GE0QRLOR8KA"
$AI1C_BASE_NAME = "UT_2026"
$AI1C_DEFAULT_USER = "Семушкин А."

function P1C($o, $n) {
  [System.__ComObject].InvokeMember($n, [Reflection.BindingFlags]::GetProperty, $null, $o, $null)
}

function SP1C($o, $n) {
  try { return (P1C $o $n) } catch { return $null }
}

# Часть методов 1С (например, у коллекций метаданных) не находится при
# вызове с одним флагом InvokeMethod, поэтому пробуем ещё и комбинацию
# InvokeMethod|GetProperty - так же, как вызывает VBScript.
function M1C($o, $n, $a) {
  $last = $null
  $flagSets = @(
    [Reflection.BindingFlags]::InvokeMethod,
    ([Reflection.BindingFlags]::InvokeMethod -bor [Reflection.BindingFlags]::GetProperty)
  )
  foreach ($nm in ($n -split '\|')) {
    foreach ($flags in $flagSets) {
      try {
        return [System.__ComObject].InvokeMember($nm, $flags, $null, $o, $a)
      } catch {
        $last = $_
      }
    }
  }
  throw $last
}

# Элемент коллекции метаданных по имени (Метаданные.Документы.<Имя>).
# Сначала как свойство коллекции, если не вышло - перебором.
function Find1C($coll, $name) {
  $item = SP1C $coll $name
  if ($null -ne $item) { return $item }
  foreach ($x in (Items1C $coll)) {
    if ((SP1C $x "Name") -eq $name) { return $x }
  }
  return $null
}

function T1C($v) {
  try { return (M1C $global:AI1C_CONN "String|Строка" @(,$v)) } catch { return "?" }
}

function F1C($v) {
  try {
    return [bool](M1C $global:AI1C_CONN "ValueIsFilled|ЗначениеЗаполнено" @(,$v))
  } catch {
    return ($null -ne $v -and "$v" -ne "")
  }
}

function Items1C($coll) {
  $list = @()
  try { foreach ($x in $coll) { $list += $x } } catch {}
  if ($list.Count -gt 0) { return $list }
  try { $cnt = M1C $coll "Count|Количество" @() } catch { $cnt = 0 }
  for ($q = 0; $q -lt $cnt; $q++) { $list += (M1C $coll "Get|Получить" @(,$q)) }
  return $list
}

function Query1C($text) {
  $q = M1C $global:AI1C_CONN "NewObject|НовыйОбъект" @("Запрос", $text)
  return (M1C (M1C $q "Execute|Выполнить" @()) "Select|Выбрать" @())
}

# Спрашивает только пароль (в отдельном окне, символы не отображаются):
# пользователь фиксирован, чтобы пароль случайно не ввели в поле имени,
# где он виден на экране.
function Connect-1C {
  $u = $AI1C_DEFAULT_USER
  $sp = Read-Host "Пароль 1С для пользователя $u (если без пароля - просто Enter)" -AsSecureString
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sp)
  try {
    $p = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  }
  Write-Host "Подключаюсь к 1С как $u ..."
  $com = New-Object -ComObject V83.COMConnector
  $global:AI1C_CONN = $com.Connect('Srvr="' + $AI1C_SERVER + '";Ref="' + $AI1C_BASE_NAME + '";Usr="' + $u + '";Pwd="' + $p + '";')
  $global:AI1C_USER = $u
  Write-Host "Подключено."
}

function Disconnect-1C {
  $global:AI1C_CONN = $null
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
}

function Format-Error1C($err) {
  $lines = @("ОШИБКА: $($err.Exception.Message)")
  if ($err.Exception.InnerException) { $lines += "ПОДРОБНО: $($err.Exception.InnerException.Message)" }
  if ($err.InvocationInfo -and $err.InvocationInfo.Line) {
    $lines += "СТРОКА: $($err.InvocationInfo.ScriptLineNumber): $($err.InvocationInfo.Line.Trim())"
  }
  return $lines
}

# Выводит результат в консоль и копирует его в буфер обмена. На диск
# ничего не сохраняется.
function Save-Result($name, $lines) {
  $lines | Out-String | Set-Clipboard
  Write-Host ""
  Write-Host "===== РЕЗУЛЬТАТ ($name) =====" -ForegroundColor Cyan
  $lines | ForEach-Object { Write-Host $_ }
  Write-Host "===== КОНЕЦ =====" -ForegroundColor Cyan
  Write-Host ">>> Результат уже в буфере обмена - вставьте в чат (Ctrl+V)." -ForegroundColor Green
}
