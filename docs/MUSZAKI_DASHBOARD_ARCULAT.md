# Műszaki dashboard: világos munkafelület

A felületi átdolgozás a meglévő, magyar nyelvű MQTT-szimulátor kezelőfelületét rendezi át. A sötét felső navigáció alatt egy helyen található a futásvezérlés és a négy meglévő eredménymutató. A térkép mellett külön járművizsgáló oszlop mutatja a kiválasztott autót és a nyolccsatornás virtuális közelségképet. A sebességidősor külön kártyát kapott; a részletes érzékelési adatok, a szintetikus elölnézet, a fékezési magyarázat és a hibainjektálás natív, billentyűzettel is kezelhető lenyitható elemekben maradtak elérhetők.

A következő futás szerkesztője külön navigációs ponton található. A terv összefoglalója nem az éppen futó rendszer kapacitása: a tényleges futás parkolóinak számát továbbra is az élő MQTT-állapotból számolt mutató jelzi. A navigáció a böngésző szokásos fragmentumait használja, a kijelölt pont `aria-current` jelzést kap. Telefonon külön görgethető a felső navigáció; a teljes oldal nem csúszik oldalirányban. A kapcsolófeliratok és a véletlenmag mezője ezen a méreten is megmaradnak.

## Adatok és modellhatár

A térkép, a radarnézet, a grafikonok és a történeti nézet továbbra is a ténylegesen fogadott mintákból készülnek. A grafikai koncepció illusztratív mutatói és járművei nem kerültek a program adatforrásába. A közelségkép sötét műszerként megmaradt, hogy a távolságjelzések elkülönüljenek a világos térképtől; a színküszöbök, adatfrissesség és kijelölési logika változatlanok. Az elölnézet szintetikus geometriai megjelenítés, nem kamerafelvétel.

A módosítás nem változtatja a vezérlőmodellt, a mérési konfigurációkat, az MQTT-adatsémát vagy az archívum szerződését. A 20 mért forrás és konfiguráció SHA-256 lenyomata változatlan. Az eredeti 428 DOM-azonosító megmaradt, nincs duplikált azonosító. A korábbi mérési és beadási nyugták történeti bizonyítékok maradnak.

## Vizuális elemek és licencek

A betűkészlet az [Inter 4.1](https://github.com/rsms/inter/releases/tag/v4.1), SIL Open Font License 1.1 alatt. A felületi ikonok a [Tabler Icons 3.31.0](https://github.com/tabler/tabler-icons/tree/v3.31.0) MIT licencű készletéből származnak. A helyi források, licencek, pontos letöltési címek és lenyomatok a `docs/vendor/console` mappában találhatók. A betű a CSS-ben, az ikonok a HTML-ben vagy CSS-maszkként szerepelnek, így a dashboard megjelenítése nem igényel külső CDN-t. A meglévő SVG-alapú adatábrázolás továbbra is natív kódból készül.

## Ellenőrzés

`python -m experiments.verify_console_redesign`: 313 meglévő felületi ellenőrzés, valamint 2486 korábbi MQTT-keret offline feldolgozása két ablakban. Ez új felületi regressziós nyugta, nem új statisztikai vagy brokeres kampány. Kimenet: `results/ui/console-redesign-check.json` és `results/ui/console-redesign-regressions.json`.

A külön böngészős ellenőrzés a helyi, valódi MQTT-adatúttal futó alkalmazáson történik: szünet/folytatás, járműváltás, érzékelési részletek, beállítások, archivált út és pontos fékezési keret kiválasztása. A képek és a böngészős nyugta a `results/ui/console-redesign` mappában, a vizuális összevetés a `design-qa.md` fájlban található. A meglévő beadási dokumentum forrásreferenciája továbbra is az `f625cfe5e85cf5a8a28943704e7e025b89b7b0a4` commit.
