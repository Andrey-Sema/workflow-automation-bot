# Структура любого объекта 1С: реквизиты и табличные части с типами.
# Только чтение метаданных, данные не читаются.

$kinds = @{
  "Документ"         = "Documents"
  "Справочник"       = "Catalogs"
  "РегистрСведений"  = "InformationRegisters"
  "РегистрНакопления" = "AccumulationRegisters"
  "Перечисление"     = "Enums"
}

$full = Read-Host "Полное имя объекта [Enter = Документ.ЗаказПокупателя]"
if (-not $full) { $full = "Документ.ЗаказПокупателя" }
$kind, $objName = $full -split '\.', 2

$r = @()
try {
  if (-not $kinds.ContainsKey($kind)) { throw "Неизвестный вид объекта '$kind'. Допустимо: $($kinds.Keys -join ', ')" }
  Connect-1C
  $md = P1C $global:AI1C_CONN "Metadata"
  $obj = M1C (P1C $md $kinds[$kind]) "Find|Найти" @(,$objName)
  if ($null -eq $obj) { throw "Объект $full не найден в конфигурации" }

  $r += "##### $full  ($(P1C $obj 'Synonym'))"
  foreach ($section in "Dimensions", "Resources", "Attributes") {
    foreach ($a in (Items1C (SP1C $obj $section))) {
      $r += "  $section  " + (P1C $a "Name") + " (" + (P1C $a "Synonym") + ") : " + (T1C (P1C $a "Type"))
    }
  }
  if ($kind -eq "Перечисление") {
    foreach ($v in (Items1C (P1C $obj "EnumValues"))) { $r += "  Значение  " + (P1C $v "Name") + " (" + (P1C $v "Synonym") + ")" }
  }
  foreach ($ts in (Items1C (SP1C $obj "TabularSections"))) {
    $r += "  --- Табличная часть " + (P1C $ts "Name") + " (" + (P1C $ts "Synonym") + ")"
    foreach ($a in (Items1C (P1C $ts "Attributes"))) {
      $r += "      " + (P1C $a "Name") + " (" + (P1C $a "Synonym") + ") : " + (T1C (P1C $a "Type"))
    }
  }
} catch {
  $r += Format-Error1C $_
} finally {
  $obj = $null; $md = $null
  Disconnect-1C
}
Save-Result "02-structure" $r
