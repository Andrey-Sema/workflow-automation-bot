# Как заполнены последние проведённые наряды (Документ.ЗаказПокупателя):
# права текущего пользователя на документ, какие поля шапки и колонки
# табличных частей заполнены, и позиции вкладок Услуги / Склад / Услуги
# прочих организаций.
#
# Данные читаются запросами, а не через ПолучитьОбъект(): модуль объекта
# наряда не компилируется во внешнем соединении (использует клиентский
# РежимДиалогаВопрос), а запросам модуль объекта не нужен.
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

  $sel = Query1C "ВЫБРАТЬ ПЕРВЫЕ $count Ссылка, Номер, Дата ИЗ Документ.ЗаказПокупателя ГДЕ Проведен И НЕ ПометкаУдаления И НЕ Отменен УПОРЯДОЧИТЬ ПО Дата УБЫВ"
  $docs = @()
  while (Next1C $sel) { $docs += , @((P1C $sel "Ссылка"), (P1C $sel "Номер"), (P1C $sel "Дата")) }

  $hdrQuery = "ВЫБРАТЬ " + (($hdr | ForEach-Object { "Д.$_ КАК $_" }) -join ", ") + " ИЗ Документ.ЗаказПокупателя КАК Д ГДЕ Д.Ссылка = &Ссылка"

  foreach ($doc in $docs) {
    $ref = $doc[0]
    $r += ""
    $r += "#### Наряд " + $doc[1] + " от " + (T1C $doc[2])

    $hs = Query1C $hdrQuery @{ "Ссылка" = $ref }
    $filled = @()
    if (Next1C $hs) {
      foreach ($nm in $hdr) {
        $v = SP1C $hs $nm
        if (F1C $v) {
          if ($white -contains $nm) { $filled += "$nm=" + (T1C $v) } else { $filled += $nm }
        }
      }
    }
    $r += "  Шапка заполнено: " + ($filled -join ", ")

    foreach ($t in $tsMeta) {
      $tsn = $t[0]; $cols = $t[1]
      $tq = "ВЫБРАТЬ " + (($cols | ForEach-Object { "Т.$_ КАК $_" }) -join ", ") +
        " ИЗ Документ.ЗаказПокупателя.$tsn КАК Т ГДЕ Т.Ссылка = &Ссылка УПОРЯДОЧИТЬ ПО Т.НомерСтроки"
      $ts = Query1C $tq @{ "Ссылка" = $ref }
      $rowCount = 0
      $fc = @{}
      $items = @()
      $nomField = if ($tsn -eq "УслугиПрочихОрганизаций") { "Услуга" } else { "Номенклатура" }
      while (Next1C $ts) {
        $rowCount++
        foreach ($cn in $cols) { if (F1C (SP1C $ts $cn)) { $fc[$cn]++ } }
        if ($tsn -in "Услуги", "Товары", "УслугиПрочихОрганизаций") {
          $items += "     - " + (T1C (SP1C $ts $nomField)) + " | кол " + (SP1C $ts "Количество") + " | цена " + (SP1C $ts "Цена") + " | сумма " + (SP1C $ts "Сумма")
        }
      }
      if ($rowCount -eq 0) { $r += "  [$tsn] пусто"; continue }
      $r += "  [$tsn] строк: $rowCount"
      $r += "     заполнены колонки: " + (($cols | Where-Object { $fc[$_] } | ForEach-Object { "$_(" + $fc[$_] + ")" }) -join ", ")
      $r += $items
    }
  }
} catch {
  $r += Format-Error1C $_
} finally {
  $ref = $null; $docs = $null; $sel = $null; $hs = $null; $ts = $null; $mdDoc = $null; $md = $null
  Disconnect-1C
}
Save-Result "03-naryad-sample" $r
