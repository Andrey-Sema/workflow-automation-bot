# Как заполнены последние проведённые наряды (Документ.ЗаказПокупателя):
# права текущего пользователя на документ, какие поля шапки и колонки
# табличных частей заполнены, и позиции вкладок Услуги / Склад / Услуги
# прочих организаций.
#
# Только чтение. Персональные данные (ФИО, телефоны, адреса, маршруты)
# не выводятся - только отметка "заполнено", кроме полей из $white.

$white = "ВидНаряда", "Важность", "Организация", "Подразделение", "СкладГруппа", "ТипЦен",
  "ДополнительныйТипЦен", "ВалютаДокумента", "СуммаВключаетНДС", "УчитыватьНДС",
  "СтруктурнаяЕдиница", "ЧастнаяФирма", "ПринадлежностьКРайону", "УсловиеПродаж",
  "СуммаДокумента", "СуммаКОплате", "НомерДня"

$count = 3

$r = @()
try {
  Connect-1C
  $md = P1C $global:AI1C_CONN "Metadata"
  $mdDoc = Find1C (P1C $md "Documents") "ЗаказПокупателя"
  if ($null -eq $mdDoc) { throw "Документ ЗаказПокупателя не найден в конфигурации" }

  $r += "=== Права пользователя $global:AI1C_USER на Наряд (ЗаказПокупателя) ==="
  foreach ($rt in "Чтение", "Добавление", "Изменение", "Проведение", "ОтменаПроведения", "Удаление", "ИнтерактивнаяПометкаУдаления") {
    try { $v = M1C $global:AI1C_CONN "AccessRight|ПравоДоступа" @($rt, $mdDoc) } catch { $v = "?" }
    $r += "  $rt : $v"
  }

  $hdr = @()
  foreach ($a in (Items1C (P1C $mdDoc "Attributes"))) { $hdr += (P1C $a "Name") }
  $tsMeta = @()
  foreach ($ts in (Items1C (P1C $mdDoc "TabularSections"))) {
    $cols = @()
    foreach ($a in (Items1C (P1C $ts "Attributes"))) { $cols += (P1C $a "Name") }
    $tsMeta += , @((P1C $ts "Name"), $cols)
  }

  $sel = Query1C "ВЫБРАТЬ ПЕРВЫЕ $count Ссылка ИЗ Документ.ЗаказПокупателя ГДЕ Проведен И НЕ ПометкаУдаления И НЕ Отменен УПОРЯДОЧИТЬ ПО Дата УБЫВ"
  $refs = @()
  while (M1C $sel "Next|Следующий" @()) { $refs += (P1C $sel "Ссылка") }

  foreach ($ref in $refs) {
    $o = M1C $ref "GetObject|ПолучитьОбъект" @()
    $r += ""
    $r += "#### Наряд " + (P1C $o "Номер") + " от " + (T1C (P1C $o "Дата"))
    $filled = @()
    foreach ($nm in $hdr) {
      $v = SP1C $o $nm
      if (F1C $v) {
        if ($white -contains $nm) { $filled += "$nm=" + (T1C $v) } else { $filled += $nm }
      }
    }
    $r += "  Шапка заполнено: " + ($filled -join ", ")

    foreach ($t in $tsMeta) {
      $tsn = $t[0]; $cols = $t[1]
      $rows = @(Items1C (SP1C $o $tsn))
      if ($rows.Count -eq 0) { $r += "  [$tsn] пусто"; continue }
      $r += "  [$tsn] строк: " + $rows.Count
      $fc = @{}
      foreach ($row in $rows) {
        foreach ($cn in $cols) { if (F1C (SP1C $row $cn)) { $fc[$cn]++ } }
      }
      $r += "     заполнены колонки: " + (($cols | Where-Object { $fc[$_] } | ForEach-Object { "$_(" + $fc[$_] + ")" }) -join ", ")
      if ($tsn -in "Услуги", "Товары", "УслугиПрочихОрганизаций") {
        $nomField = if ($tsn -eq "УслугиПрочихОрганизаций") { "Услуга" } else { "Номенклатура" }
        foreach ($row in $rows) {
          $r += "     - " + (T1C (SP1C $row $nomField)) + " | кол " + (SP1C $row "Количество") + " | цена " + (SP1C $row "Цена") + " | сумма " + (SP1C $row "Сумма")
        }
      }
    }
  }
} catch {
  $r += Format-Error1C $_
} finally {
  $o = $null; $refs = $null; $sel = $null; $mdDoc = $null; $md = $null
  Disconnect-1C
}
Save-Result "03-naryad-sample" $r
