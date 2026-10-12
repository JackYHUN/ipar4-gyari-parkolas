# V17 – incidensriport és a kiválasztott futás exportja

A műszerfal Üzemfelügyelet paneljének **Incidensriport** része a kiválasztott futás mentett hibáit összesíti. A „Riport rögzítése / frissítése” gomb külön lekérést indít; a teljes archívum összegzése nem ismétlődik a rendszeres állapotlekérésekkel. A riport szabályonkénti darabszámokat, lezárt epizódok időtartamát, nyugtázási időt és visszatérő epizódok számát mutatja.

## Piaci indok és a megvalósított követelmény

Az OPC Foundation Alarms & Conditions leírása az összesített riasztási mutatókat a kezelhető felügyelet eszközeként tárgyalja [1]. Az AlarmMetricsType többek között riasztásszámot, aktív és nyugtázatlan állapothoz kapcsolódó mutatókat, illetve ismétlődésre vonatkozó mutatót határoz meg [2]. A prototípus ez alapján saját, szűkített riportot ad a már meglévő MQTT-felügyelethez. Az itt használt időtartamok és futáshatárok saját definíciók; az adatút továbbra is MQTT, és a funkció nem OPC UA implementáció.

| Követelmény | Megvalósítás | Ellenőrizhető bizonyíték |
| --- | --- | --- |
| A teljes kijelölt archívum jelenjen meg az összesítésben. | Az `until_id` felső epizódhatárig minden epizód feldolgozódik, állapotszűrő és oldalméret nélkül. | 245 mentett epizód, 233-as határ, független elvárt darabszámok; valódi MQTT-kampány SQL-összevetése. |
| A riport és az export egyetlen konzisztens állapotot olvasson. | Külön, csak olvasható SQLite-pillanatkép marad nyitva a lekérés végéig. | Életciklus-módosítás és futásváltás közben folytatott export; a későbbi módosítás nem keveredik bele. |
| Az időtartam legyen értelmezhető. | Kizárólag időzónás, nem negatív, véges UTC-különbségek fogadhatók el. | Hiányzó, időzóna nélküli és visszalépő időbélyegek kizárási tesztjei. |
| Hosszú futásnál se épüljön teljes Python-lista az exportból. | Legfeljebb 100 soros kötegek, öt szabálycsoport és három állandó méretű statisztikai gyűjtő; az ismétlődés SQL-csoportosítással számolódik. | A 100 sort meghaladó export pontos, hiánytalan JSON- és CSV-ellenőrzése. Ez memóriakorlát, nem mért áteresztőképesség. |
| A kezelő lássa, melyik futást vizsgálja. | „Aktuális futás” és „Korábbi futás” címkék, az észlelt futásváltáskor azonnali átjelöléssel. | Késleltetett válasz, visszaállítás és kézzel kijelölt archívum felületi regressziói. |

## Adat és idő

A számítás forrása a `operations_incidents` tábla tartósan mentett epizódja. Az epizód az első hibás megfigyelést (`opening.utc`), a megerősített nyitást (`opened_utc`), a helyreállás miatti lezárást (`resolved_utc`) és a kezelői nyugtázást (`acknowledged_utc`) külön rögzíti. Ezek a megfigyelő gép UTC-időbélyegei. A járműmozgás `sim_time` modellidejét a riport nem használja időtartamként.

| Mutató | Definíció és beválasztás |
| --- | --- |
| Összes / aktív / lezárt / nyugtázott / nyugtázatlan | Az epizódok mentett életciklusállapota a lekérés pillanatképében. A nyugtázás és az aktív állapot független. |
| Megerősítési idő | `opened_utc − opening.utc`; minden epizód jogosult. Az első hibás megfigyelés és a megerősített nyitás közti könyvelési idő. |
| Lezárt epizód időtartama | `resolved_utc − opened_utc`; kizárólag a lezárt epizódokból. Nyitott epizódhoz nem keletkezik „mostanáig” becslés. |
| Nyugtázási idő | `acknowledged_utc − opened_utc`; kizárólag a nyugtázott epizódokból. |
| Ismétlődő epizódok | Szabály és érintett szerinti csoportonként az elsőn túli epizódok száma, majd ezek összege. Ugyanannak az epizódnak a frissítése nem ismétlődés. |

Az időtartamokhoz darabszám, kizárt jogosult minták száma, minimum, maximum és számtani átlag tartozik, másodpercben. A nem jogosult nyitott vagy nem nyugtázott epizód nem „hibás mintaként” számolódik. Nulla érvényes minta esetén az átlag, minimum és maximum `null`, a felületen „—”. Az ismert nulla másodperc megmarad. Az ismeretlen szabályok egy „Egyéb incidens” sorba kerülnek.

Az időbélyegek a gazdagép órájától és a megfigyelés ütemétől függenek. Nem igazolják a fizikai gyári állásidőt, a javítás tényleges idejét vagy a rendelkezésre állást. A csak lezárt epizódok átlaga nem az összes hiba átlagos javítási ideje; a nyitva maradt hibák ebben nem szerepelnek. A szimulátor szünete alatt a gazdagép UTC-ideje tovább telik.

## Pillanatkép és letöltés

`GET /api/operations/report?run_id=<futás>&until_id=<határ>` kifejezett futásazonosítót igényel. Az opcionális határ nélkül a lekéréskor mentett legnagyobb epizódazonosító lesz a határ. A `scope` értéke `episode_prefix_current_lifecycle_snapshot`: az epizódok köre rögzített, az életciklusállapot a lekérés pillanatképéből származik. Egy korábbi epizód felső határa önmagában nem állítja vissza annak régebbi aktív vagy nyugtázási állapotát.

A felületen a rögzített riport nem változik az incidenslista szűrésétől vagy későbbi állapotfrissítésektől. Új futás választása érvényteleníti; a későn visszaérkező válasz nem tölthető be másik futás helyére. A CSV- és JSON-link ugyanazt a futást és epizódhatárt adja át, de a **letöltés külön, friss életciklus-pillanatképet készít**. A `captured_at_utc` minden válaszban rögzíti a lekérés idejét. Két külön lekérés időközben lezárt vagy nyugtázott epizódja ezért eltérhet.

`GET /api/operations/export?run_id=<futás>&until_id=<határ>&format=csv|json` a teljes kijelölt epizódkört streameli, csökkenő epizódazonosítóval. Az export nem az incidenslista egyetlen oldalát és nem csak annak aktuális szűrőjét tölti le. A JSON tartalmazza a vele azonos pillanatképből számolt riportot és az eredeti mentett epizódobjektumokat. A korábbi futás önállóan exportálható; a meglévő futás-ZIP `incidents.json` szerződése megmaradt.

A CSV UTF-8 BOM-mal készül; mért időtartamokat és a nyitó, illetve legutóbbi bizonyíték forgalmi, telemetria- és eseményhatárait külön oszlopokban adja át. A táblázatkezelőben képletként értelmezhető `=`, `+`, `-`, `@` kezdetű, illetve kezdő szóközt vagy vezérlőjelet tartalmazó cella elé aposztróf kerül. Az `X-CSV-Formula-Escape` HTTP-fejléc és a JSON `csv_formula_escape` mező rögzíti ezt a konvenciót. A JSON nem módosítja az eredeti szöveget; bájtpontos szöveges feldolgozáshoz ez használható.

Hiányzó, hibás vagy nem megőrzött futásazonosító, jövőbeli epizódhatár és nem támogatott formátum 400 hibaválaszt ad a stream megkezdése előtt, a meglévő archívumvégpontokkal egyezően. Régi, incidensarchívum nélküli futásnál a riport `available=false`, a mutatók ismeretlenek; nem jelenik meg kitalált nulla. Ilyen futás letöltése szintén 400 választ ad. A csak olvasható lekérés nem hoz létre új táblát a régi adatbázisban.

## Reprodukálás és bizonyíték

```powershell
.\.venv\Scripts\python.exe verify_units.py
.\.venv\Scripts\python.exe -m tests.perception_mqtt_smoke
.\.venv\Scripts\python.exe -m experiments.verify_frontend
.\.venv\Scripts\python.exe -m experiments.verify_operations_v17
```

A riport egységtesztjei: `tests/test_operations_report_v17.py`; a felületi regressziók: `tests/frontend_operations_report_v17.cjs`. Az MQTT-kampány HTTP-parancsokkal okoz és helyreállít érzékelő- és kommunikációhibát, valamint kísérleti, időben korlátozott megfigyelői visszatartással feldolgozási lemaradást. Nem illeszt mesterséges incidenssort az adatbázisba. Az összesítés saját SQL-lekérdezésből és külön számításból képzett referenciával, az export eredeti SQL-epizódokkal kerül összevetésre. A korábbi tíz ellenőrzési pont mellett a teljes és szűkített riport, a CSV/JSON, a hibás kérések és az archivált futás riportja négy új pontban szerepel.

A 2026. október 11-i aktuális forráson 367 Python-teszt és 217 JavaScript-ellenőrzés sikeres. A külön tényleges MQTT/HTTP-kampány mind a 14 ellenőrzési pontot teljesítette: hat epizód, egy nyugtázás és két ismétlődés egyezett a külön SQL-referenciával. Az érzékelő-/útarchívum-próba nyolc pontja 2485 fogadott keretet, 2425 eredeti érzékelőmintát és 60 elavult hibakori keretet ellenőrzött; két autó beparkolt referenciaütközés nélkül. Ezek véges funkcionális ellenőrzések, nem tartós terhelhetőségi vagy gyári megbízhatósági mérések.

Az aktuális eredmények a `results/unit-summary.json`, `results/ui/perception-renderer-check-v17.json`, `results/operations-check-v17.json`, `results/ui/perception-ui-check-v17.json` és `results/PERCEPTION_MQTT_RENDSZERTESZT.json` fájlokba kerülnek. A `results/operations-proof-v17` mappa a független referenciát és a kapott exportokat őrzi. A rögzítő csak sikeres, a vizsgált forráslenyomatokkal egyező jelentéseket fogad el. A V16 bizonyítékai eredeti dátummal és verzióval megmaradnak; az új riport nem jelent új korábbi teljesítménymérést.

A tényleges asztali böngészős vizsgálat 11 ellenőrzési pontban igazolta a kifejezett riportkészítést, a kampányreferenciával egyező mutatókat és szabályeloszlást, az exportlinkek futás- és epizódhatárát, a listaszűrőtől független rögzítést, a futásváltás miatti érvénytelenítést, az ismert nulla és ismeretlen időtartam megkülönböztetését, a valódi visszaállítás utáni címkéket és a 663 px-es töréspontot. A normál nézethez visszaállított ablakban nincs vízszintes túlcsordulás, a konzolban nincs figyelmeztetés vagy hiba. A natív böngészős fájlletöltés ebben a próbában nem szerepel; a HTTP-export tényleges bájtjait a külön MQTT-kampány ellenőrizte. Képernyőkép: `results/ui/incidensriport_v17.jpg`, a keskeny nézet: `results/ui/incidensriport_keskeny_v17.jpg`.

## Irodalom

[1] OPC Foundation, „OPC Unified Architecture – Part 9: Alarms & Conditions, 9.1 Overview,” verzió 1.05.06, OPC Foundation. [Online]. Elérhető: https://reference.opcfoundation.org/specs/OPC-10000-9/9.1 . Hozzáférés: 2026. október 11.

[2] OPC Foundation, „OPC Unified Architecture – Part 9: Alarms & Conditions, 9.2 AlarmMetricsType,” verzió 1.05.06, OPC Foundation. [Online]. Elérhető: https://reference.opcfoundation.org/specs/OPC-10000-9/9.2 . Hozzáférés: 2026. október 11.
