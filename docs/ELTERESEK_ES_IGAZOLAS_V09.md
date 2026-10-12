# V0.9 – bejárható útarchívum és értelmezhető érzékelési bizonyíték

A fejlesztés a hosszú fogadott autóutak visszakeresését és a mentett érzékelési adatok értelmezését javítja. A rendszerhatár továbbra is a laptopon futó modell, a tényleges MQTT-közvetítő, a koordinátor, a fogadási napló és a böngészős megjelenítő. A közelségmodell és a szintetikus elölnézet oktatási geometriai megfigyelés; a felület új összegzései nem változtatják a mozgásvezérlést.

## Feltárt eltérések és javításuk

| Eltérés | Megvalósítás | Következmény |
|---|---|---|
| A teljesút-nézet legfeljebb az első 20 000 fogadott keretet töltötte be, és eldobta a folytatási kurzort. Az ennél későbbi eseményre ugrás a betöltött tartomány végére került. | Legfeljebb 2000 keretes, két irányban lapozható ablak, első/utolsó mintára és szimulációs időre keresés; külön célrekord-azonosító. | A hosszú archívum későbbi részei is visszajátszhatók, a régi csomagok egyszerre történő betöltése nélkül. |
| Egy adott időt vagy eseményt megelőző minta hiánya összekeveredhetett a betöltetlen minták hiányával. | A szerver a megőrzött archívumban keresi a legutóbbi, a kérés idejénél nem későbbi mintát; a legkorábbi mintára visszaesést külön jelzi. | A felület nem állít korábbi pozíciót, ha ilyen fogadott adat nincs. |
| A fogadott diagnosztikai keretek száma nem azonos az eredeti érzékelések számával. Egy hibakori csomag sokszor ismétlődhet. | Külön keretszámlálók és egyedi `(járműazonosító, eredeti mintaidő)` szerinti megfigyelésszámlálók. | A gyorsítótárazott másolat nem válik új mérési bizonyítékká. |
| A névleges periódus önmagában nem mutatja az archívumban megfigyelt mintaközöket. | Az egyedi, friss eredeti mintaidők közti minimum-, átlag- és maximumkülönbség külön jelenik meg. | Elkülönül a tervezett mintavétel és az adott ablakban megfigyelt szimulációs mintaköz. |
| A csatornatáblázat, a radar és a térképi nyaláb között nehéz volt egyetlen találatot követni. | Kijelölhető csatornák és azonos csatorna kiemelése a nézetekben; a kiválasztás a passzív SVG-állóképen is látható. | A távolság és az akadályazonosító egy konkrét mentett csatornához kapcsolható. |
| A közeli jelzések száma könnyen értelmezhető lett volna veszélyes helyzetek számaként. | Az összegzés a konfigurált geometriai színsávot tartalmazó egyedi friss mintákat számolja, a jelentést külön megadja. | A jelzési statisztika nem minősített biztonsági mutató. |

## Ablak, sorrend és rögzített futásszakasz

Egy ablak legfeljebb 2000 fogadott járműkeret teljes `sensors` és `control` mezőjét tartalmazza. A korábbi és következő lap az első, illetve utolsó rekordazonosító kizárásával folytatja a lekérdezést. A szerver a teljes fogadott rekordhalmazon számolja a látható ablak kezdő és záró sorszámát; a különböző autók közbeeső rekordjai nem növelik a kijelölt autó sorszámát.

A lapok és a sorszámok a fogadási rekordazonosítók növekvő sorrendjét követik. Az érzékelőhiba alatti megfigyelői diagnosztika is fogadott járműkeret; a sorszám ezért nem azonos a telemetria MQTT-sorszámával, és ismételt érzékelőcsomagokat is tartalmazhat. Az eredeti érzékelő-mintaidő, a járműkeret szimulációs ideje, a vezérlési döntés ideje és az UTC-fogadás külön mező marad.

Az első lekérdezés rögzíti az `until_id` felső rekordhatárt. A lapozás és az idő szerinti keresés ezt továbbviszi, így a közben érkező új adatok nem mozdítják el a megnyitott archívumszakasz végét vagy összegzését. Az új minták betöltése tudatos frissítés. A teljes CSV/JSON-export a saját indításakor rögzített teljes prefixet adja át, ezért egy később elindított export több keretet tartalmazhat, mint egy korábban megnyitott ablak.

A térkép a betöltött ablak fogadott pozícióiból rajzolt útszakaszt mutatja. Az idő- vagy sorszámrésekben a vonal megszakad; a hiányzó minták helye nincs interpolálva. A teljesút-export továbbra is minden megőrzött rekordot tartalmaz. A régi archívumok ismert, már törölt prefixe és hiányzó érzékelőcsomagjai nem állíthatók helyre.

## Idő szerinti keresés és eseményre ugrás

Adott `t` szimulációs időhöz a szerver a legnagyobb olyan megőrzött mintaidőt választja, amely legfeljebb `t`. Azonos időbélyegnél a később fogadott rekord a cél. A válasz `target_id` mezője azonosítja a tényleges célkeretet; a böngésző nem kerekített időfelirat alapján keresi vissza azt. A későbbi keretek ugyanabban az ablakban megjelenhetnek, de a kijelölt minta a keresés tényleges előzménye marad.

Ha az esemény minden mentett minta elé esik, a szerver `earliest_fallback` feloldást jelez. Ekkor a legkorábbi elérhető mintát lehet megmutatni, de az nem bizonyítja az esemény helyét. Időbélyeggel rendelkező keret nélküli, csak szemantikus eseményeket tartalmazó autónál nincs kitalált pozíció vagy célkeret.

| API | Paraméterek és eredmény |
|---|---|
| `GET /api/car/history/page` | `run_id`, `car_id`, `direction=start/end/after/before/time`, szükség szerint `cursor` vagy `sim_time`, `limit` legfeljebb 2000, rögzített `until_id`. |
| `window` | Kezdő/záró globális autósorszám, teljes keretszám, ablakkeret, első/utolsó rekordazonosító, előző/következő kurzor; `order=receipt_id_ascending`. |
| `target_id`, `seek` | A kijelölendő mentett rekord és az időkeresés kért/feloldott ideje, valamint `predecessor`, `earliest_fallback` vagy `no_timed_sample` feloldás. |
| Futáskörnyezet | A saját archivált térkép, konfiguráció és metaadat; a régi futás nem az élő térképet kapja. |

A negatív vagy nem véges idő, hibás vagy másik autóhoz tartozó kurzor, egymásnak ellentmondó keresési paraméterek és érvénytelen azonosítók elutasítást kapnak. Az új adatbázisok idő szerinti indexet is tárolnak; a régi adatbázisok csak olvasható megnyitása nem migrálja a bizonyítékot. A keresés a rekordazonosítót és időt SQL-ben oldja fel; az érzékelőcsomagokat csak a kiválasztott ablakhoz bontja ki.

## Érzékelési minőség és közelségi összegzés

Az elemzés hatóköre az aktuálisan betöltött ablak. Az ablakváltás módosíthatja a számlálókat és minimumot; az értékek nem a teljes futás mutatói. A számlálókhoz a hiányzó, első mintavétel előtti, hibás, friss és elavult csomagok külön értelmezése tartozik. A feldolgozás ellenőrzi a sémát, a forrást, az eredeti mintaidőt, a hatótávot és küszöböket, valamint a véges, tartományon belüli, jelzési állapottal összhangban levő csatornaadatokat.

Egy adott eredeti minta sok fogadott keretben is szerepelhet. Az egyedi megfigyelést a járműazonosító és érzékelő-mintaidő együtt azonosítja; ugyanazon minta friss fogadása előnyt kap a későbbi elavult másolattal szemben. A 0 s mintaidő érvényes. Régi, közelségadat nélküli csomagból nem készül új találat.

A közeli, figyelmeztető és távolabbi találati számlálók az adott sávot tartalmazó egyedi friss minták számát adják. Egy mintában több csatorna és több sáv is lehet, így a számlálók átfedhetnek. A „nincs visszatérés” nem zöld találat és nem igazol szabad területet. A legkisebb érvényes felületi távolság megőrzi a cél és csatorna azonosítóját, a fogadott rekordot és az eredeti mintaidőt; az elavult forrás külön jelzett marad.

Ezek a távolságok geometriai sugármetszésből származnak. A nulla távolság érintkezést is jelenthet; ütközést a külön testátfedési referenciaellenőrzés igazol. A közeli minták száma nem „majdnem ütközés”, nem veszélyességi eseményszám és nem kockázati mérőszám. Az ismételt szűk parkolási helyzet lehet rendeltetésszerű manőver is.

## Névleges és megfigyelt időzítés

A konfigurált periódus a mintavétel tervezett üteme. A megfigyelt mintaköz az ablakban található, járművenként rendezett, különböző eredeti friss érzékelő-mintaidők különbsége. A minimum, átlag és maximum ezekből számítható; kettőnél kevesebb egyedi friss időpont mellett nincs ilyen statisztika. Az elavult másolat nem ad új időközt.

A megfigyelt mintaköz szimulációs másodpercben értendő. Eltérhet a névleges periódustól a modell lépésköze, hibainjektálás, a beérkező keretek hiánya vagy az ablak határa miatt. Ez nem MQTT-fogadási frekvencia, alkalmazási kézbesítési késleltetés vagy teljes érzékelés–beavatkozás reakcióidő. E fogalmak külön órára és mérési pontra támaszkodnak.

## Csatornakijelölés és export

A felhasználó egy mentett csatornát választhat ki a csatornaválasztóval vagy táblázatból. A kiemelés a radar megfelelő szektorát, a térképi szerelési pontot és nyalábot, valamint a találati pontot segít azonosítani. A kijelölés kezelői állapot; nem módosítja az eredeti `sensors` csomagot, távolságot vagy jelzési állapotot.

Az SVG-állókép a kijelölés vizuális kiemelését passzív geometriával őrzi meg. Nem tartalmaz futtatható szkriptet vagy külső erőforrásra mutató elemet. A szenzoradat az eredeti mentett megfigyelés marad; az állókép továbbra is sematikus rajz, nem kamerafelvétel. Régi, nem létező vagy elavult csatorna nem lesz friss akadályérzékelés a kijelöléstől.

## Követelmény–megvalósítás–bizonyíték érvelés

| Követelmény | Megvalósítás | Ellenőrzés |
|---|---|---|
| Hosszú archívum végére lehessen ugrani korlátozott böngészőmemóriával. | Legfeljebb 2000 teljes érzékelőcsomagot tartalmazó ablak, SQL-alapú rekordkeresés és kétirányú lapozási azonosítók. | 20 025 fogadott érzékelőkeretes teszt, közbeékelt másik autó rekordjaival; időkeresés 20 000 után, korlátozott kicsomagolás. |
| Egy eseményhez a valódi legutóbbi előzmény legyen kiválasztva. | Pontos időkeresés és célazonosító; a korábbi adat hiánya külön feloldás. | Duplikált és nem monoton időbélyegek, időkiesés, túl korai/túl késői keresés, üres archívum. |
| Lapozás közben a futásszakasz ne kövesse az új élő adatokat. | Minden keresés azonos felső rekordhatárral történik. | Új keret hozzáadása utáni változatlan ablakvég és sorszámok; érzékelőhiba alatti mentett csomag pontos megőrzése. |
| A statisztika a megfigyelések számát és hatókörét pontosan írja le. | Deduplikált eredeti mintaidők, ablakhatókör, hibás csomagok és bizonytalan állapotok külön kezelése. | Elemzési tesztek másolatokra, régi/hibás adatra, konfigurált küszöbökre, mintaidőkre és bemeneti adatok változatlanságára. |
| A csatornakijelölés és export ugyanahhoz a megfigyeléshez kapcsolódjon. | Közös kiválasztott csatorna, passzív SVG-kiemelés. | Renderer- és frontendtesztek; végső böngészős és letöltési ellenőrzés. |

## Ellenőrzés és bizonyítékok állapota

A fejlesztés közben a backend új kilenc navigációs tesztje sikeresen lefutott, majd a kapcsolódó útarchívum-, észlelésarchívum- és adatkonzisztencia-tesztekkel együtt 36 célzott teszt teljesült. Ez **2026. október 7-i részellenőrzés**, nem a végső, összeépített V09 teljes teszteredménye.

Az összeépített változat **176 Python-tesztje és 50 JavaScript-esete sikeres** (2026. október 7.). A JavaScript-esetek megoszlása: 16 renderer, 15 tiszta elemzés, 7 felületi regresszió, 12 archívumnavigáció és kijelölés. A külön MQTT/HTTP-rendszerteszt 2486 keretet, 2426 eredeti érzékelőmintát és 60 hibakori keretet ellenőrzött; az új HTTP-ablakok, a pontos hibakori időkeresés, a teljes CSV/JSON és a ZIP egyezett. Két autó beparkolt, referenciaütközés nélkül. A böngészőben az ablakváltás, az utolsó minta, az ablakon kívüli eseményre ugrás, a 3,073 s kereséshez tartozó 3,05 s mentett keret, a legkorábbi előzmény hiánya és a kiválasztott szenzortalálat működött. A 390 px-es mobilnézetben a dokumentum szélessége nem lépte túl a rendelkezésre álló 375 px-et. A csatornagombok újrarajzoláskor megtartják azonosságukat és billentyűs fókuszukat. Bizonyíték: `results/unit-summary.json`, `results/ui/perception-renderer-check-v09.json`, `results/ui/perception-ui-check-v09.json`.

A beépített böngésző natív mintaletöltése elakadt: a felfüggesztett letöltési válasz már nem volt elérhető. Ez **nem igazolt** V09 böngészős letöltés; a lap újratöltése helyreállította a felületet. A szerveroldali teljes archívumexport és a kijelölt csatornák passzív SVG-rajzolási szerződése külön sikeres. A korábbi V07 natív letöltési bizonyíték nem minősíti automatikusan ezt az új próbát.

A ténylegesen kirajzolt, kijelölt SVG-geometriából külön HTTP-próba készült (`results/ui/selected-frame-http-check-v09.json`). A helyi végpont a 20 118 bájtos SVG-t és 10 433 bájtos JSON-t 200-as válasszal, csatolmányfejléccel adta vissza. A `rear_center` kijelölés, a teljes eredeti érzékelőcsomag és vezérlési döntés megmaradt. Ez a DOM-geometria és a szerver közti ellenőrzés, a beépített böngésző natív letöltési útját nem igazolja.

```powershell
.\.venv\Scripts\python.exe verify_units.py
node tests/perception_analysis_smoke.cjs
node tests/perception_visuals_smoke.cjs
node tests/frontend_regressions.cjs
node tests/frontend_navigation_v09.cjs
.\.venv\Scripts\python.exe -m experiments.verify_frontend
```

A 2026. október 7-i V08 két autós valódi MQTT/HTTP-vizsgálata és négy, egyenként harmincautós determinisztikus elrendezéspróbája külön, korábbi kampány. A régebbi 30 páros véletlenmagos stratégia-összehasonlítás a korábbi, 140 × 90 m-es alapmodellre vonatkozik. Egyik korábbi eredmény sem minősíti automatikusan a V09 új felületét vagy egy valódi győri autógyár működését.

## Fennmaradó határok

Az ablak memóriakorlátja nem korlátozza a teljes megőrzött futás lemezhelyigényét. A nyers MQTT körkörös kerete és a teljes fogadott archívum megőrzése külön szabály. A véges szenzorsugarak, a téglalaptestek, az ideális vonallánc-követés és a fizikai kamerafelvétel hiánya továbbra is modellhatár. A mérési minőség összegzése bizonyítékot segít visszakeresni; nem pótolja a fizikai érzékelő kalibrációját vagy a biztonsági igazolást.
