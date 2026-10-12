# Flottaüzemeltetés és nyugtázható incidensek · V16

A 0.16.0 változat az élő diagnosztikai adatokat üzemeltetési incidensekké alakítja. A kezelő külön láthatja, hogy egy megfigyelt probléma fennáll-e, illetve foglalkozott-e már vele valaki. Egy epizódhoz a nyitó és a legutóbbi mentett forgalmi mintából elérhető fogadott jármű- és eseményadatok is hozzárendelhetők.

## 1. Piaci indok és rendszerhatár

A Siemens SIMOVE nyilvános termékleírása központi flotta- és járműdiagnosztikát, rendszerjelzéseket, állapotadatokat és hibaelhárítást segítő megfigyelést tárgyal [1]. Ez a prototípus számára olyan megvalósítható követelményt jelent, hogy a kezelő az ismétlődő nyers figyelmeztetések helyett azonosítható, előzménnyel és megfigyelési bizonyítékkal rendelkező hibajelzést kapjon.

Az OPC UA Alarms and Conditions leírásában az aktív állapot és a kezelői nyugtázás külön állapot; a nyugtázás nem jelenti automatikusan a kiváltó feltétel megszűnését [2], [3]. A V16 ezt az általános üzemeltetési elvet saját helyi API-val valósítja meg. Nem implementál OPC UA szervert vagy az Alarms and Conditions teljes szolgáltatáskészletét, és nem állít VDA 5050 megfelelést.

A rendszerhatár a laptopon futó szimulátor, MQTT-közvetítő, koordinátor, naplózó feliratkozó, SQLite-adatbázis és HTTP-műszerfal. A jelzések a fogadott szimulációs és gazdagépi diagnosztikai adatokat értelmezik. A funkció nem fizikai jármű, érzékelő vagy gyári biztonsági rendszer minősítése.

## 2. Állapotok és megfigyelési idő

Az incidens azonosítója egy futáson belül egy epizódot jelöl. Ugyanannak a hibának a későbbi visszatérése új epizódot hoz létre, ezért az új probléma nem örökli a korábbi nyugtázást. Az `active` és az `acknowledged` két független érték: egy kezelt, de fennálló probléma aktív marad; egy már helyreállt probléma előzményként és nyugtázatlan tételként is megmaradhat.

A felismerés a ténylegesen mentett forgalmi megfigyelésekhez kötődik, ezért a HTTP-oldal ismételt lekérése nem számít új hibamintának. A kommunikáció és a feldolgozási lemaradás értékelése monoton gazdagépi fogadási korokat használ. A mentett járműállapot és az érzékelőcsomag saját modellideje változatlanul megmarad; a külön órák értékeit a rendszer nem vonja ki egymásból.

Az ismeretlen vagy feldolgozatlan állapot nem bizonyít helyreállást. Ilyenkor a felismerési sorozat megszakad, a már aktív epizód pedig megmarad. A parkoló jármű és a frissen megfigyelt, szüneteltetett szimuláció külön mérnöki értelmezést igényel: az állás önmagában nem hiba.

## 3. Felismerési szabályok és API

| Szabály | Megfigyelt feltétel | Kezelés |
| --- | --- | --- |
| `communication_unavailable` | Öt gazdagépmásodperces indulási türelem után nem él a brokerkapcsolat, aktív a kommunikációs hibainjektálás, vagy a callbackben fogadott utolsó szimulátorállapot kora meghaladja a 2 s-ot. | Két hibás megfigyelés nyitja meg; két ismert, egészséges megfigyelés zárja le. |
| `processing_lag` | A callbackállapot legfeljebb 0,75 s-os és a kapcsolat ismerten működik, miközben az utoljára feldolgozott állapot kora meghaladja a 2 s-ot. | A kommunikációs kieséstől külön epizód; két hibás, illetve két egészséges megfigyelés. |
| `sensor_unavailable` | Frissen feldolgozott, nem parkoló autó ténylegesen mintavételezett szenzorcsomagja elavult, vagy aktív érzékelőhibát jelez. | Autónként külön epizód; a régi feldolgozott állapot és az eltűnő jármű ismeretlen marad. A frissen megfigyelt parkoló autó nem vált ki új hibát. |
| `worker_stopped` | A megfigyelt háttérfolyamat leállási kóddal rendelkezik. | Egy megfigyelés után nyílik meg, az adott futásban fennmarad; a kezelői nyugtázás nem indítja újra. |

A felismerés legalább 1 s távolságú mentett gazdagépi megfigyeléseket használ. A 2,5 s-nál nagyobb megfigyelési rés, óra- vagy szakaszváltás megszakítja a korábbi megerősítési sorozatot. Ez megfigyelésszámra vonatkozó szabály, nem garantált észlelési késleltetés.

A `GET /api/operations` kötelező, explicit `run_id` alapján olvassa a saját futásában tárolt katalógust. A `status` értéke `all`, `active` vagy `unacknowledged`; a `limit` legfeljebb 100. A `before` kizáró, csökkenő incidensazonosító-kurzor, az `until_id` felső epizódazonosító. Az utóbbi az epizódok tagságát rögzíti; élő futásban a már kiválasztott epizód aktív és nyugtázási állapota később változhat. A végpont nem állít teljes életciklus-pillanatképet egy régi lapozási határról. A fő összesítő darabszámai a teljes kiválasztott futásra vonatkoznak (`counts_scope=entire_selected_run`); az `until_id` határán belüli tagság darabszámát külön `prefix_count` adja meg.

A `POST /api/operations/acknowledge` a `run_id`, `id`, `expected_revision` és opcionális, legfeljebb 500 karakteres `note` mezőket fogadja. Csak az aktuális futás incidense nyugtázható. A megváltozott életciklusrevízió vagy az időközben archivált futás HTTP 409 választ kap; az eredeti nyugtázási revízióval megismételt kérés nem hoz létre második auditbejegyzést. A nyugtázás helyi üzemeltetési adatot módosít, és külön szemantikus eseményt ment; MQTT-mozgásparancsot nem küld.

Az `opening` az első hibás megfigyelés bizonyítékát, az `opened_utc` a megerősítést adó megfigyelés idejét jelöli. A `latest` a későbbi ismert megfigyeléssel frissül. Mindkét bizonyíték mentett `transport_sample_id`, `telemetry_until_id`, `events_until_id`, `sim_time` és `utc` mezőket tartalmaz. A jelzés saját életciklus-eseménye a mintamentés után jön létre, ezért nem része ugyanennek a mintának a korábban feldolgozott eseményhatárának. Ez nem elveszett audit: a saját incidens-esemény a teljes szemantikus naplóban érhető el.

A SQLite `operations_incidents` tábla a teljes epizódobjektumot és az indexelhető életciklusadatokat tartósan tárolja. A teljes futás ZIP-exportja külön `incidents.json` állományt is tartalmaz. Korábbi, incidens-tábla nélküli adatbázis esetén az elérhetőség hamis; az archívum hiánya nem bizonyít hibamentes régi működést.

## 4. Követelmény–megvalósítás–bizonyíték

| Követelmény | Megvalósítás | Bizonyíték és korlát |
| --- | --- | --- |
| A kezelő egy azonosítható problémaepizódot kapjon az ismétlődő figyelmeztetések helyett. | Megfigyelésekből képzett, futáshoz kötött incidensek, aktív és nyugtázási állapottal. | Valódi MQTT alatt beavatkozással kiváltott érzékelőhiba, helyreállás és új epizód. |
| A nyugtázás ne jelentse a hiba megszüntetését. | Külön kezelői nyugtázás, módosítási revízió és szemantikus auditbejegyzés. | HTTP-nyugtázás aktív érzékelőhiba alatt, az aktív állapot és a tényleges megállási állapot külön ellenőrzésével. |
| A megfigyelő lemaradása különüljön el az adatforrás kommunikációs kiesésétől. | Friss callback és régi feldolgozott állapot külön szabályt vált ki. | A megfigyelő rövid, kizárólag kísérleti szünete a programzáron kívül; az MQTT-fogadás közben működik. Külön kommunikációs hibainjektálás. |
| A hiba idején rendelkezésre álló bizonyíték ne bővüljön későbbi adatokkal. | Mentett diagnosztikai mintára és az akkor feldolgozott rekordhatárokra mutató hivatkozás. | API-válasz és független, csak olvasható SQLite-prefix összevetése, majd újabb adatok érkezése után változatlansági ellenőrzés. |
| Reset és újranyitás után az előzmény maradjon a saját futásában. | Explicit futásazonosító, tartós incidens-adatbázis és archív olvasás. | Valódi HTTP-reset, új `Application`, külön csak olvasható `Journal` és közvetlen SQL-újranyitás. |

## 5. Reprodukálható rendszerkampány

A projekt könyvtárából:

```text
.venv\Scripts\python.exe -m experiments.verify_operations_v16
```

A kampány saját ideiglenes helyi MQTT-portot és HTTP-portot használ, így nem vezérli a futó bemutatót. A jelentés a `results/operations-check-v16.json` fájlba, a hordozható válaszok és SQL-reference adatok a `results/operations-proof-v16` könyvtárba kerülnek. Sikeres státusz csak a végrehajtott ellenőrzések és a kampány elején/végén számított, változatlan forráslenyomat után adható.

A vizsgálat véges helyi forgatókönyvre vonatkozik. Nem mér maximális flottakapacitást, és nem bizonyít valós gyári rendelkezésre állást vagy fizikai biztonsági megfelelést. Az incidensfelismerés mintavételezett: a két megfigyelés között kezdődő és végződő rövid problémát nem feltétlenül észleli.

## 6. Végrehajtott ellenőrzések

A 2026. október 11-i, 0.16.0 forráson végrehajtott Python-tesztkészlet 354 tesztet, a JavaScript-ellenőrzés 198 esetet teljesített, hiba és kihagyás nélkül. A külön, tényleges MQTT/HTTP-incidenskampány tíz ellenőrzési pontja sikeres. Az érzékelő- és útarchívum külön rendszertesztje nyolc ellenőrzési pontban 2486 fogadott keretet, 2426 különböző eredeti érzékelőmintát és 60 elavult hibakori keretet ellenőrzött; két jármű beparkolt, nulla modellezett ütközéssel.

A rendszerkampány az aktív hiba alatti nyugtázást, az ismétlés egyszeri auditját, a hibás revízió és futásazonosító elutasítását, a két egészséges megfigyeléssel igazolt helyreállást, az új hibaepizódot, a tényleges feldolgozási lemaradást és kommunikációs kiesést is megvizsgálta. Az API-válaszokat külön SQLite-olvasás ellenőrizte. A HTTP-n letöltött ZIP CRC-je helyes volt; az `incidents.json` teljes életcikluslistája és az egyetlen nyugtázási auditbejegyzés megegyezett a független SQL-referenciával. A reset és az adatbázis újranyitása után az eredeti futás bizonyítékai megmaradtak.

A jelentések: `results/unit-summary.json`, `results/ui/perception-renderer-check-v16.json`, `results/operations-check-v16.json` és `results/PERCEPTION_MQTT_RENDSZERTESZT.json`. A sikeres futtatások az ellenőrzés elején és végén egyező forráslenyomatot rögzítettek. A böngészőből megfigyelt interakciók és a képernyőkép külön a `results/ui/perception-ui-check-v16.json` jelentésben szerepelnek; az egyes ellenőrzések pontos hatókörét az összesített `results/ELLENORZES.json` tartalmazza.

## 7. Források

[1] Siemens, „SIMOVE,” termékoldal. [Online]. Elérhető: https://www.siemens.com/en-us/products/simove/ . Hozzáférés: 2026. október 11.

[2] OPC Foundation, „OPC 10000-9: Alarms and Conditions — 4.3 AcknowledgeableConditions.” [Online]. Elérhető: https://reference.opcfoundation.org/specs/OPC-10000-9/4.3 . Hozzáférés: 2026. október 11.

[3] OPC Foundation, „OPC 10000-9: Alarms and Conditions — 5.7.3 Acknowledge.” [Online]. Elérhető: https://reference.opcfoundation.org/specs/OPC-10000-9/5.7.3 . Hozzáférés: 2026. október 11.
