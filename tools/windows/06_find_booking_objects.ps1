# Поиск объектов конфигурации, связанных с бронированием и рабочим местом
# оператора: документы, справочники, регистры и обработки, в имени или
# синониме которых встречаются ключевые слова. Нужно, чтобы понять, как
# бронирование (БУТ) создаёт наряд и где хранится "адрес смерти".
# Только чтение метаданных.

$pattern = "брон|рмо|рабоч.*мест|событ|усопш|маршрут|катафалк|автотранспорт|дежур|наряд"
$kinds = [ordered]@{
  "Документ"          = "Documents"
  "Справочник"        = "Catalogs"
  "РегистрСведений"   = "InformationRegisters"
  "РегистрНакопления" = "AccumulationRegisters"
  "Обработка"         = "DataProcessors"
  "Отчет"             = "Reports"
  "ЖурналДокументов"  = "DocumentJournals"
}

$r = @()
try {
  Connect-1C
  $md = P1C $global:AI1C_CONN "Metadata"
  $r += "Объекты, где имя или синоним совпадает с: $pattern"
  foreach ($kind in $kinds.Keys) {
    foreach ($obj in (Items1C (SP1C $md $kinds[$kind]))) {
      $name = P1C $obj "Name"
      $syn = P1C $obj "Synonym"
      if ("$name $syn" -match $pattern) { $r += "  $kind.$name  ($syn)" }
    }
  }
} catch {
  $r += Format-Error1C $_
} finally {
  $md = $null
  Disconnect-1C
}
Save-Result "06-find-booking-objects" $r
