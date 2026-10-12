# Mért MQTT-üzenetforgalom és fogadási sor · V13, történeti leírás

A 0.13.0 változat rövid időablakban méri a műszerfal MQTT-fogadását és a megfigyelő sorfeldolgozását. Az új diagnosztika azt teszi vizsgálhatóvá, hogy az adott futásban több üzenet érkezik-e, mint amennyit a megfigyelő a sorból kivesz, és hogyan változik közben a sorhossz. Ez a jelenlegi futás megfigyelt forgalma; a rendszer maximális vagy tartósan garantált kapacitása ebből nem következik.

A rendszerhatár megmaradt: helyi TCP/MQTT-közvetítő, külön szimulátor- és koordinátorfolyamat, naplózó megfigyelő és HTTP-műszerfal. A járműmozgás, az érzékelőmodell és a napló formátuma nem módosult. A korábbi callback-, sorvárakozási és kijelzettállapot-kor továbbra is külön adat. A V13 mérési előzménye a memóriában él; a teljes fogadott autóút és érzékelőarchívum ettől külön, változatlan szolgáltatás.

## 1. A mérési adatok jelentése

A szerver az új adatokat az `/api/state` válasz `mqtt.capacity` objektumában adja át. A mező elnevezése egy diagnosztikai csoportot jelöl, nem minősített kapacitásértéket. A sebességek mértékegysége üzenet/s; az API `_hz` utótagja nem érzékelő-mintavételi frekvenciát jelent.

| Mező | Mért mennyiség | Megkülönböztetés |
| --- | --- | --- |
| `arrival_rate_hz` | Az érvényes, aktuális futású callback-fogadások számlálókülönbsége osztva a tényleges időablakkal. | Minden feliratkozott témát számol; nem csak autótelemetriát. |
| `dequeue_rate_hz` | A megfigyelő által a fogadási sorból kivett üzenetek számlálókülönbsége ugyanabban az időablakban. | Nem a járművek végrehajtási sebessége, a közvetítő teljes forgalma vagy a CPU kihasználtsága. |
| `window_s` | A két számlálási pont között ténylegesen eltelt pozitív gazdagépidő másodpercben. | A nevező nem a névleges frissítési periódus és nem modellidő. |
| `queue_messages` | A fogadási sor `qsize()` értéke a diagnosztikai pont felvételekor. | Aktív callback mellett közelítő; eltérhet a korábbi köteg végén mért sortól. |
| `queue_growth_hz` | Két megfigyelt sorhossz különbsége osztva a tényleges időablakkal. | Előjeles: a negatív érték apadást, a pozitív növekedést jelent. |
| `sample_age_s` | A legutóbbi diagnosztikai pont kora a fogadó gép monoton óráján. | A forgalmi megfigyelés kora; nem az autó érzékelőadata vagy a kijelzett járműállapot kora. |
| `history` | Legfeljebb 120 diagnosztikai pont a jelenlegi mérési alaphoz tartozó relatív idővel és számlálókkal. | Korlátozott memóriabeli előzmény, nem teljes futásnapló. |

A számítás egyszerű számlálókülönbséget használ:

```text
érkezési ütem = (fogadott_új − fogadott_régi) / tényleges_ablak_s
sorból kivétel üteme = (kivett_új − kivett_régi) / tényleges_ablak_s
sorváltozás = (sorhossz_új − sorhossz_régi) / tényleges_ablak_s
```

A sorhossz és a számlálók nem egyetlen atomi műveletben keletkeznek. A callback közben is dolgozhat, induláskor pedig külön feldolgozási út is kivehet üzenetet. Emiatt a mért sorváltozást nem kell pontosan az érkezési és kivételi ütem különbségével azonosítani. Rövid időablakban mindhárom adatot a saját mintavételi körülményeivel kell értelmezni.

## 2. Mintavétel, órák és ismeretlen értékek

Két diagnosztikai pont között legalább egy másodperc telik el; a tényleges mintaköz hosszabb is lehet. A sebességhez a monitor legfeljebb öt másodperces, ténylegesen megfigyelt időablakot választ; az első pontnál még nincs osztásra alkalmas pár, ezért az ütem ismeretlen. A 120 pontos korlát nem jelent pontosan két percnyi hiánytalan megfigyelést: a pontok közötti idő függ a szerver és a HTTP-lekérések ütemezésétől.

A megfigyelői köteg vége és a HTTP-állapotlekérés is mintát kérhet ugyanattól a monitortól. Az egy másodperces kapu megakadályozza a túl sűrű kettős mintavételt. A HTTP-oldali mintavétel akkor is követheti a callback-forgalmat, amikor a kísérlet időben korlátozottan visszatartja a sor feldolgozását. Így a beérkezés és a sorból kivétel eltérése a lemaradás alatt is megfigyelhető.

Az időalap a fogadó gép monoton `perf_counter_ns` órája. A 2× vagy 8× szimulációs sebesség befolyásolhatja az üzenettermelést, de nem szorozza meg a nevezőként használt valós másodpercet. A `sim_time` továbbra is a járművek és az érzékelők modellideje, az UTC-időbélyeg pedig a jelentés dátuma és a napló emberi visszakeresési ideje. A monitor előzményeinek `elapsed_s` mezője a jelenlegi mérési alap kezdőpontjához viszonyított monoton idő.

A kezdőpont, hiányzó számláló, hibás óra vagy túl hosszú mintaköz nem adhat kitalált nullás ütemet. A `null` ismeretlen mennyiséget jelent, a ténylegesen mért nulla érvényes megfigyelés marad. Az érvényes negatív sorváltozás is megmarad. Két másodpercnél nagyobb szomszédos mintarést a sebességablak nem hidal át. Új futás, visszalépő monoton óra vagy csökkenő fogadási/kivételi számláló törli a mérési alapot és az előzményt; hiányzó számláló megszakítja az abból számítható ablakot.

A `status` jelzi az értelmezhetőséget: `collecting` esetén még gyűlik az első érvényes időablak, `observed` esetén van mért ablak, `unavailable` esetén nincs megfelelő mérési adat. A 2,5 s-nál régebbi diagnosztikai pont `stale` állapotot kap. Egy megmaradó korábbi érték ebben az állapotban korábbi megfigyelésként értelmezhető.

## 3. Kapcsolat a korábbi diagnosztikával

A V12 sorvárakozási ideje az egyes üzenetek callback-fogadása és feldolgozáskezdete közötti idő; p95 értéke legfeljebb 1000 érvényes üzenetből készül. A V13 üzenet/s adata ezzel szemben számlálókülönbségből származó forgalmi ütem. A kettő együtt segít a lemaradás értelmezésében: növekvő sor mellett a várakozás is növekedhet, de egyik adat sem helyettesíti a másikat.

A V13 diagnosztikai sorhossz, a V12 kapcsolatállapot-váltáskor mért sorhossz és a műszerfal utolsó köteg végén mért sora külön mintavételi időponthoz tartozik. Mindegyikhez megmarad a megfelelő definíció. A callback-frissesség és a kijelzett, már feldolgozott állapot kora szintén külön adat; a friss callback-forgalom önmagában nem teszi frissé a járműállapotot.

A korlátozott szünet és 1× lassítás feltételei, a szerver által készített helyreállítási környezet, illetve a publikálási és alkalmazási nyugták elkülönítése megmaradt. A forgalmi panel nem ad új vezérlési jogosultságot. Egy nulla sorhosszú, már befejeződött futás nem bizonyít aktív forgalom melletti tartós feldolgozási kapacitást.

## 4. Követelmény–megvalósítás–bizonyíték

| Követelmény | Megvalósítás | Bizonyíték és korlát |
| --- | --- | --- |
| Az üzenetforgalmi ütem mért időből származzon. | Számlálókülönbség osztva pozitív, legfeljebb öt másodperces monoton időablakkal. | Célzott monitorregresszió és a tényleges V13 kampány; nem érzékelőfrekvencia vagy maximális kapacitás. |
| Ismeretlen ütem ne jelenjen meg nullaként, az apadó sor maradjon felismerhető. | Null/valódi nulla elkülönítése; előjeles sorváltozás; órák, számlálók és rések ellenőrzése. | Egység- és frontend-regressziók a kezdeti, hibás, visszaállított és apadó adatokra. |
| Feldolgozási lemaradás alatt is megfigyelhető legyen a beérkezés. | Közös monitor mintavétele kötegből és HTTP-állapotlekérésből; független callback-számláló. | A valódi MQTT/HTTP-kampány időben korlátozott megfigyelői visszatartása; a vizsgált eset önmagában nem tartós terhelésmérés. |
| Az eredmények verziója és hatóköre követhető legyen. | Aktuális PASS/verzió/forráslenyomat-kapuk; korábbi jelentések és exportok külön fájlnéven megőrizve. | A V13 rögzítő öt aktuális bizonyítékot igényel, a V12 profil és a V11 modellkampány saját dátummal marad meg. |

## 5. Aktuális bizonyítékok

A **2026. október 10-i V13** ellenőrzések mindegyike `PASS` eredménnyel zárult. Az alábbi eredmények végrehajtott, 0.13.0 verziójú, az aktuális forrásokkal egyező jelentésekből származnak. A rögzítő nem futtat tesztet, és hiányzó vagy korábbi bizonyítékból nem készít sikeres V13-jelentést.

A [teljes Python-egységtesztfuttatás](../results/ui/unit-summary-v13.json) **289 tesztet teljesített hiba, sikertelen eset és kihagyás nélkül**. A jelentés a teljes vizsgált Python-forrásállomány lenyomatait rögzíti; a források a futtatás alatt változatlanok maradtak. A V13 monitorregressziók a tényleges időablakot, a kezdő és ismeretlen adatokat, a számláló- és óravisszaállítást, az apadó sort, a mintaréseket és a megfigyelői feldolgozástól független HTTP-mintavételt vizsgálták.

A [V13 frontend-futtatás](../results/ui/perception-renderer-check-v13.json) **130 sikeres JavaScript-esetet** rögzített: 105 korábbi eset mellett 25 új V13 esetet. A forgalmi adatok, ismeretlen/nulla/negatív értékek, előzmények és időablakok mellett a kezdeti betöltés és a HTTP-kiesés kezelése is ellenőrzött. Ez böngészőtől független regressziós vizsgálat; a tényleges felületi megfigyelések külön jelentésben szerepelnek.

A [tényleges asztali böngészős ellenőrzés](../results/ui/perception-ui-check-v13.json) **hét megfigyelési pontot** teljesített. A mért kártyák és két, külön mértékegységgel feliratozott grafikon a jelenlegi élő futáshoz tartozott; a folytatás publikálási és szimulátori alkalmazási nyugtája külön jelent meg. A rögzített útösszegzés és az utolsó keretre ugrás a külön MQTT-érzékelési archívum pontos adatait mutatta. Új élő futás indításakor mindkét forgalmi grafikon azonosítója és előzménye lecserélődött, miközben a rögzített történeti keret 248,55 s-nál maradt. Az alapértelmezett asztali nézet nem okozott vízszintes dokumentumtúlcsordulást; konzolfigyelmeztetés és konzolhiba nem volt. A HTTP-kiesési ágat a regressziók vizsgálták; e böngészős bizonyíték nem állít mobilnézeti vagy natív letöltési ellenőrzést.

A [véges valódi MQTT/HTTP-kampány](../results/transport-capacity-check-v13.json) 2026. október 10-én `PASS` eredménnyel öt ellenőrzési pontot teljesített. Az `a1129b5da5e7` futás 12 aktív autóval, 2× sebességgel indult; a megfigyelőt a kísérlet korlátozott ideig visszatartotta, majd szünet/1× helyreállítást és külön új futást vizsgált. A rögzített megfigyelési időtartam **27,672 s gazdagépidő** volt, a kampány 60 HTTP-állapotmintát őrzött meg. A forgalmi ütemeket a program a mentett nyers számlálókból és tényleges időablakokból külön újraszámította.

| Megfigyelt szakasz | Tényleges ablak | Érkezés | Sorból kivétel | Diagnosztikai sor | Sorváltozás |
| --- | ---: | ---: | ---: | ---: | ---: |
| Természetes forgalom, visszatartás előtt | 4,0585 s | 410,49 üzenet/s | 410,49 üzenet/s | 0 | 0,00 üzenet/s |
| Visszatartott megfigyelő | 4,3064 s | 385,47 üzenet/s | 0,00 üzenet/s | 2970 | +385,47 üzenet/s |
| Kiürítés közbeni mentett pont | 4,0797 s | 22,80 üzenet/s | 690,00 üzenet/s | 248 | −667,20 üzenet/s |
| Helyreállított, szüneteltetett 1× futás | 4,0420 s | 15,59 üzenet/s | 15,59 üzenet/s | 0 | 0,00 üzenet/s |

A visszatartás végén a feldolgozott állapot kora 7,5800 s volt, miközben az állapotcallback kora 0,1306 s maradt. A HTTP-mintavétel ekkor is megfigyelte a beérkező üzeneteket és a növekvő sort. A felszabadítás után mindkét helyreállító parancshoz megérkezett az azonosított szimulátori alkalmazási visszajelzés. A kiürítés szüneteltetett, 1× futás mellett, csökkent bejövő forgalommal történt. A táblázat rövid időablakainak számai az adott helyi kampány megfigyelései; nem tartós kapacitás- vagy maximális feldolgozási sebességértékek.

Az új `ba261307724e` futás egyetlen induló mérési ponttal, `collecting` állapotban jelent meg: az ütemek és ablak ismeretlenek maradtak, a tényleges üres sor nulla értéke megmaradt. A kampány összesen 67 HTTP-kérést mért a vezérlésekkel és az újraindítással együtt; ezek helyi mediánja 16,00 ms, p95 értéke 47,00 ms volt. Ez ugyanazon véges kísérlet kérésmintája, nem garantált HTTP-válaszidő.

A külön [V13 MQTT/HTTP-érzékelési próba](../results/mqtt-proof/perception-v13-summary.json) nyolc ellenőrzési pontot teljesített a `ce6ad0a27bf0` futásban: 2486 fogadott útkeret, 2426 külön eredeti érzékelőminta és 60 elavult hibakori keret megőrzését, a rögzített mintahatárokat, a teljesút-összegzést és az exportokat ellenőrizte. Két autó beparkolt, referenciaütközés nélkül. A CAR001 utolsó fogadott rekordja a 4971-es azonosítójú, 248,55 s-os minta: 370 m és 18 m pozíció, P11 parkolóhely, 705 m jelentett modellodometria. Ez a fogadott archívum teljességét ellenőrzi; általános veszteségmentes MQTT-átvitelt nem állít.

Az eredeti V13 egységteszt-, érzékelési és összesített jelentés a V14 átállás előtt külön megmaradt: `results/ui/unit-summary-v13.json`, `results/mqtt-proof/perception-v13-summary.json`, `results/ui/verification-summary-v13.json`. A `results/ui/perception-renderer-check-v13.json`, `results/ui/perception-ui-check-v13.json` és `results/transport-capacity-check-v13.json` szintén az eredeti V13 bizonyíték. Az érzékelési összegzéshez tartozó exportok külön `-v13` fájlneveken találhatók a `results/mqtt-proof/` mappában. Az általános `results/unit-summary.json`, `results/PERCEPTION_MQTT_RENDSZERTESZT.json` és `results/ELLENORZES.json` fájlok későbbi, aktuális verziók eredményét tartalmazhatják; nem V13 bizonyítékhivatkozások.

A korábbi [V12 ellenőrzési összegzés](../results/ui/verification-summary-v12.json), [egységtesztek](../results/ui/unit-summary-v12.json) és [érzékelési exportbizonyíték](../results/mqtt-proof/perception-v12-summary.json) külön megmaradt. A [V12 profil](../results/profiling-v12/observer-profile.json) öt páros, memóriában végzett mérésből származó eredménye az eredeti V12 szerverkódra vonatkozik; a teljes szerverforrás V13-ban változott. A profil dátuma, verziója és időeredménye változatlan, és ebből új V13 gyorsulási állítás nem készül.

A [dátumozott V11 négyelrendezéses modellkampány](../results/network-load-check-v11.json) saját négy mozgási/koordinátori/geometriai/forgatókönyvi forrásának egyezése esetén továbbra is releváns. Ez determinisztikus modell- és protokollintegráció, nem valódi MQTT-terhelésmérés; a korábbi dátum és verzió nem íródik át V13-ra.

## 6. Megismétlés és következő mérési lépés

Az alábbi parancsok az eredeti V13 forrásállapothoz tartozó történeti futtatási receptet rögzítik. Az újabb V14 forrásból nem állítanak elő V13 bizonyítékot; az aktuális parancsok a [V14 leírásban](FORGALMI_ARCHIVUM_V14.md) és a README-ben találhatók. A teszteket a `projekt` mappából kell futtatni. A frontend-vizsgálat az előtte elkészített aktuális MQTT-érzékelési exportot is használja, ezért a sorrend számít:

```powershell
.\.venv\Scripts\python.exe verify_units.py
.\.venv\Scripts\python.exe -m tests.perception_mqtt_smoke
.\.venv\Scripts\python.exe -m experiments.verify_capacity_v13
.\.venv\Scripts\python.exe -m experiments.verify_frontend
```

A forgalmi kampány saját helyi közvetítővel és külön munkafolyamatokkal indul, és a `results/transport-capacity-check-v13.json` eredményt készíti el. Tizenkét autó természetes forgalmát, időben korlátozott megfigyelői visszatartást, szünet/1× helyreállítást és valódi új futásra váltást vizsgál; a forgalmi rátákat a megőrzött számlálók és monoton időablakok alapján is ellenőrzi. A tényleges asztali böngészős ellenőrzés külön bizonyítékot készít. Minden aktuális bizonyíték elkészülte után az összegzés és a hordozható csomag összeállítása:

```powershell
.\.venv\Scripts\python.exe record_verification_v13.py
.\.venv\Scripts\python.exe package_prototype.py
.\.venv\Scripts\python.exe -m experiments.verify_package_v13
```

Az utolsó parancs a csomag CRC-értékeit, valamennyi csomagtag pontos SHA-256 lenyomatát és a csomagolt források munkamappával való egyezését ellenőrzi. A jelentést a munkamappa fölötti `output/projekt/csomag_ellenorzes_v13.json` fájlba írja.

Tartós kapacitás értékeléséhez külön hosszabb, rögzített érkezési ütemű kampány szükséges több ismétléssel: aktív forgalom, időbeli sorfelhalmozódás, állapotkor, lemezírás, HTTP-kérések és gépterhelés megőrzésével. A V13 rövid, memóriabeli diagnosztikája ennek megfigyelési alapja, és nem helyettesíti az ilyen vizsgálatot.
