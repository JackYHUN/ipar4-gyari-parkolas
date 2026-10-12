# V04: közlekedési térkép és visszakereshető autóutak

## A korábbi figyelmeztetés jelentése

A „nyers MQTT-naplókeret betelt” üzenet a 0.3-as változat tárhelyvédelmi szabályát jelezte. Egy programindítás 100 MiB nyers üzenet után nem mentett további nagy állapotképeket és telemetriakereteket. Az MQTT-adatút ettől működhetett, és a szemantikus eseménynapló tovább íródott. A részletes SQLite-telemetria azonban külön, globális 200 000 mintás ablakot használt, így egy korábban kiadott autó első mintái hosszú futásban elveszhettek.

A 0.4-es változat szétválasztja a nyers diagnosztikát, a részletes rövid távú telemetriát és az autóút tartós bizonyítékát. A nyers diagnosztika körkörös fájlkeretet használ: új üzenetek kerülnek bele, a legrégebbi nyers részek helyét újak veszik át. A körkörös keret mérete futásonként alapértelmezetten 100 MiB; a részek célmérete 25 MiB. A `logging.detail_limit_mib` és `logging.raw_segment_mib` konfigurálja őket. A megőrzési számlálók jelzik a már eltávolított nyers fájlokat; ez nem MQTT-kapcsolati hiba.

## Tartós, fogadott útvonalminták

A futás SQLite-adatbázisának `trip_samples` táblája megőrzi az összes feldolgozott járműtelemetria tömörített mezőkészletét: fogadási UTC idő, szimulációs idő, X/Y pozíció, sebesség, irány, állapot, parkoló, várakozási ok, útvonal-előrehaladás, megtett távolság és járműmintasorszám. A `trip_cars` index a már kiadott autók keresését is lehetővé teszi. A 200 000 mintás részletes ablak cserélődése ezt a külön útarchívumot nem törli. Az esemény-, döntés- és kezelői rekordok továbbra is a teljes futásra megmaradnak.

Ez lemezen növekvő adatarchívum: a nyers fájlgyűrű korlátja nem a teljes `results` könyvtár méretkorlátja. Új futás új adatbázist kap; a korábbi futások bizonyítékai megmaradnak. Az archivált előzmények olvasása nem módosítja a régi adatbázist. Az autóazonosító csak a futásazonosítóval együtt egyértelmű, mert új futásban ismét létrejöhet `CAR001`.

A mintavételi periódus alapértéke 0,1 szimulációs másodperc. A fogadás nem veszteségmentes mérési ígéret: a telemetria MQTT QoS 0 üzenet. A sorszámhiány és a vártnál hosszabb szimulációs mintaköz külön jelzés. Az érzékelőkiesés sorszámugrás nélkül is okozhat időbeli lyukat. Az előzménynézet ezeket nem köti össze feltételezett mozgással.

A 0.3-as vagy régebbi futásból csak a ténylegesen megmaradt minták olvashatók. Ha a régi megőrzési ablak már törölt adatot, az nem rekonstruálható; a felület és az export jelzi az archívum részlegességét. A korábbi mintákból hiányzó várakozási ok vagy távolság sem helyettesíthető feltételezett mérési adattal.

## Felületi használat

A közlekedési térkép a releváns gyártási kijáratra, útvonalra, zebrákra és parkolóterületre fókuszál. A távoli motor-, energia-, prés- és egyéb gyárelemek a közlekedési nézetben nem foglalnak helyet. A fizikai modell koordinátái és az útvonalak változatlanok; a nagyobb megjelenés a megfelelő területre illesztett nézetből következik.

A terület megnevezése a térképen kívül, állandó fejlécben is szerepel. A térképfeliratok képernyőmérethez igazodnak, a jobb szélen külön megjelenítési ráhagyás akadályozza meg a címkék levágását. A sűrű parkolósorok azonosítói ütközésvizsgálattal ritkulnak; kijelölés, rámutatás vagy billentyűzetfókusz esetén az adott parkoló azonosítója megjelenik.

A teljes út előzménye külön kártyában és külön térképen látható. A kezelő kiválasztja a futást és az autót, majd betölti a fogadott mintasort. Az időcsúszka és a visszajátszás egy-egy ténylegesen rögzített mintát választ ki; a hozzá tartozó UTC idő, szimulációs idő, koordináta, sebesség, állapot és várakozási ok együtt jelenik meg. A kapcsolódó események külön lapozhatók. A korábbi futás térképe annak mentett konfigurációjából származik, az élő szimuláció frissítése nem írja felül.

Az autóhoz tartozó CSV és JSON letöltés a teljes tárolt mintasort és eseménysorokat tartalmazza, függetlenül a felület lapméretétől. A CSV sorainak `record_type` mezője megkülönbözteti a telemetriát és az eseményt. Az export egy rögzített adatprefixet olvas; az export indítása után érkező új minták egy későbbi exportban szerepelnek.

A böngésző egy autó útjából legfeljebb 200 000 mintát tölt be. Ennél hosszabb út esetén a betöltött kezdeti szakaszt és a korlátot külön jelzi; a teljes CSV/JSON exportot ez nem korlátozza. A térképi vonal rajzolásához ritkított pontsor használható, az időcsúszka azonban minden betöltött mintát elér.

## Követelmény–megvalósítás–bizonyíték

| Követelmény | Megvalósítás | Verifikáció |
| --- | --- | --- |
| A kiadott autó első mintája is visszakereshető legyen. | Külön, teljes fogadott futásra megőrzött tömör útarchívum; futás és autó szerinti index. | A részletes telemetriaablaknál több minta, újranyitás, a már nem aktív autó HTTP-lekérdezése. |
| Két futás azonos nevű autói ne keveredjenek. | Kötelező futáskontextus; külön adatbázis és mentett konfiguráció. | Reset és programújraindítás után a korábbi autó mintaszáma és futásazonosítója változatlan. |
| Hiányzó adatból ne keletkezzen bizonyítatlan út. | Sorszám- és időhézag jelzése, részleges régi archívum jelzése, elkülönített élő és történeti térkép. | Kihagyott sorszám, egymást követő sorszámok közötti hosszú időköz, régi törölt minták tesztesete. |
| Az export ne csak az utolsó grafikonablakot tartalmazza. | Rögzített felső azonosítóig, kötegekben kiolvasott CSV/JSON stream. | A több lapból kiolvasott mintaszám egyezik a teljes CSV/JSON mintaszámával. |
| A nyers keret elérése után is érkezzen új diagnosztikai üzenet. | Futásonként körkörös nyers fájlmegőrzés; régi részek szabályozott cseréje. | Kis kerettel kikényszerített rotáció és a legújabb üzenet visszaolvasása. |

## Ellenőrzés és modellhatár

Az egységtesztek tényleges eredményét a `python verify_units.py` a `results/unit-summary.json` és `results/unit-tests.txt` fájlokba menti. Az új, valódi MQTT-útvonalteszt a `python -m tests.history_mqtt_smoke` paranccsal fut: saját helyi brokert, szimulátort, koordinátort és HTTP-kiszolgálót használ; az összegzése a `results/history-smoke-*/summary.json` fájlba kerül.

A 2026. október 5-i ellenőrzésben 110 egységteszt sikeres volt. A valódi MQTT-rendszertesztben két autó beparkolt, majd kiadásra került, modellezett ütközés nélkül; a CAR001 2424 mintája a teljes CSV/JSON exportban, reset után és programújraindítás után is megmaradt. Az 1 MiB-ra szűkített nyers keretben a rotáció ténylegesen bekövetkezett, és nem akadályozta az útarchívum mentését. A böngészőben az archivált futás kiválasztása, időcsúszkája, visszajátszása, JSON-letöltése és 390 px széles mobilnézete is ellenőrzésre került.

Az autó útja a modellbe belépéstől a parkolásig, majd a parkolási tartózkodásig követhető. A gyártósori pufferben még nincs mért járműpozíció. A kiadás jelenleg absztrakt eltávolítás, így a `departed` eseményhez nem tartozik ténylegesen szimulált kihajtási út. A visszajátszás ezt a hiányzó fizikai szakaszt sem pótolja. A győri csarnokelrendezés forrása és a közlekedési geometria oktatási feltételezései továbbra is a [V03 dokumentumban](TERKEP_ES_FORGATOKONYV_V03.md) és a [forrásleírásban](GYOR_FORRASOK.md) szerepelnek.
