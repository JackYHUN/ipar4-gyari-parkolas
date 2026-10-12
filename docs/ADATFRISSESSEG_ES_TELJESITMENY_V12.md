# Adatfrissesség, megfigyelői feldolgozás és igazolt naplóoptimalizálás · V12

A 0.12.0 változat a hálózati fogadást, a naplózási várakozást és a kijelzett állapot frissességét külön méri. A cél annak felismerése, hogy a közvetítőkapcsolat megszakadt-e, vagy az MQTT-üzenetek továbbra is megérkeznek, miközben a megfigyelő feldolgozása lemarad. Az utóbbi helyzetben korlátozott szüneteltetés és 1× sebességre lassítás kérhető, a kérelmek kiadásának és végrehajtásának bizonyítékai pedig elkülönülnek.

A rendszerhatár a laptopon futó két dimenziós udvarmodell, a külön szimulátor- és koordinátorfolyamat, a helyi MQTT-közvetítő, a megfigyelő és a HTTP-műszerfal. A járműmozgás, az útvonaltervezés és a szintetikus érzékelők modellje ebben a változatban nem módosult. A teljesút-archívum és a mentett érzékelőcsomagok megmaradnak; a szintetikus elölnézet továbbra is mentett modellmegfigyelések rekonstrukciója.

## 1. Négy különböző időadat

Egy üzenet a hálózati fogadási callback után a FIFO-sorba kerül, majd a megfigyelő feldolgozza és naplózza. A kijelzett állapot csak feldolgozott állapotcsomagból származhat. A következő adatok ezért külön műszaki jelentéssel rendelkeznek.

| Adat és API-mező | Mért mennyiség | Értelmezési korlát |
| --- | --- | --- |
| Bármely callback-fogadás kora: `mqtt.transport.last_message_age_s` | Az utolsó érvényes, az aktuális futáshoz tartozó MQTT-üzenet helyi fogadásától eltelt másodperc. | Telemetria vagy esemény is frissítheti; önmagában nem bizonyít friss állapotcsomagot. |
| Állapotcallback kora: `mqtt.transport.last_snapshot_age_s` | Az utolsó `snapshot` üzenet helyi callback-fogadásától eltelt másodperc. | Bizonyítja az állapotcsomag megérkezését, de nem annak megfigyelői feldolgozását. |
| Sorban várakozás: `mqtt.observer.last_queue_wait_ms` és `queue_wait_p95_ms` | A callback-fogadás és az adott teljes üzenet feldolgozásának kezdete közötti idő, ezredmásodpercben. | A feldolgozás és az adatbázisba írás ideje nincs benne. |
| Kijelzett állapot kora: `mqtt.last_message_age_s` | A legutóbb feldolgozott állapotcsomag eredeti callback-fogadásától eltelt másodperc. | A korábbi API-mező neve megmaradt; itt kifejezetten a kijelzett állapot korát jelenti. |

A `mqtt.transport.broker_connected` külön logikai adat a kliens közvetítőkapcsolatáról. A műszerfal felső `connected` állapota ennél szigorúbb: élő közvetítőkapcsolatot, 0,75 s-nál fiatalabb feldolgozott állapotot és kommunikációhibától mentes működést igényel. Így a közvetítőhöz kapcsolódó, de elavult állapotot mutató alkalmazás külön felismerhető.

A callback csak érvényes JSON-objektum és egyező futásazonosító esetén frissíti a fogadási diagnosztikát. Hibás JSON és más futás üzenete nem javíthatja a frissességi jelzést. Még nem látott állapotcsomag, hibás óra vagy negatív korkülönbség esetén az érték ismeretlen (`null`); a ténylegesen mért nulla megmarad nullától különböző, érvényes adatként.

A sorban várakozás p95 értéke legfeljebb a legutóbbi **1000 érvényes várakozási mintából** készül. A `queue_wait_sample_count` az aktuális mintaszám, a `queue_wait_invalid_clocks` a kihagyott hibás órák száma. A rangalapú p95 a rendezett minták `floor((n−1)×0,95)` indexű értéke; ez rövid helyi diagnosztikai ablak, és nem a teljes futás eloszlása. A sorhossz a legutóbbi köteg végén mért, közelítő `queue.qsize()` érték. Aktív callback mellett, illetve kísérletileg felfüggesztett megfigyelőnél nem tekinthető pillanatnyi, atomi sorszámlálónak.

A `stream_live` és `stream_stale` naplóesemény sorhossza külön mintavételt használ: a szerver közvetlenül az eseményhez lekéri az aktuális fogadási sor `qsize()` értékét, és az esemény `backlog_definition` mezőjében megnevezi ezt a közelítő mintavételt. A műszerfal kötegvégi sora és az állapotváltási esemény sora ezért eltérhet. Ez megakadályozza, hogy egy régi, üres kötegvégi sorérték új kapcsolatállapot-változás bizonyítékaként jelenjen meg.

## 2. Órák és feldolgozási kötegek

A callbackkor, a sorban várakozás és a kijelzett állapot kora a fogadó gép monoton `perf_counter_ns` óráját használja. Ezek valós futási idők; a 2× vagy 8× szimulációs sebesség nem szorozza meg őket. A `sim_time` ezzel szemben a modell ideje, amely a járműmozgás, az érzékelőadatok kora és a modellbeli hibák időzítése számára használható. Az UTC-időbélyegek a naplóbejegyzések emberi visszakeresését segítik. A három időtartomány közötti önkényes kivonás nem értelmezhető mérésként.

A fogadó nem írja át a küldő `sent_ns` mezőjét vagy eredeti UTC-adatát friss fogadási időre. A callback saját fogadási időt ad az üzenethez, a megfigyelő pedig az eredeti fogadási időt őrzi meg. Emiatt egy későn feldolgozott csomag nem válik mesterségesen frissé.

A V11-ben bevezetett korlátozott megfigyelői kötegek megmaradtak: egy köteg célértéke **128 teljes üzenet vagy 25 ms**. A vizsgálat teljes üzenetenként történik, ezért egyetlen drága feldolgozás vagy állapotellenőrzés túllépheti az időkeretet. A kötegek között az alkalmazás felszabadítja a közös zárat, hogy a HTTP-kérések hozzáférhessenek az állapothoz. A célérték puha feldolgozási keret; nem jelent 25 ms-os garantált válaszidőt vagy korlátos teljes sorhosszt.

## 3. Korlátozott helyreállító parancsok

Elavult kijelzett állapot mellett a szerver kizárólag akkor engedi a helyreállító parancsot, ha már volt feldolgozott állapotcsomag, a közvetítőkapcsolat él, az állapotcallback kora ismert és legfeljebb **0,75 s**, valamint nincs aktív injektált kommunikációhiba. Ebben az állapotban az engedélyezett művelet **kifejezett szüneteltetés (`pause`) vagy pontosan 1× sebesség (`speed`, `value=1`)**. A folytatás, a gyorsítás és a többi élő vezérlés elutasítást kap. Betöltéskor, megszakadt kapcsolatnál, ismeretlen vagy régi állapotcallback mellett a helyreállítás nem elérhető.

A műszerfal ugyanazokat a feltételeket követi. Elavult állapotnál a szünetgomb kifejezett `pause` kérelmet küld, ezért egy régi „szünetel” állapot nem fordíthatja át a kérelmet folytatásra. Friss állapot mellett a megszokott vezérlési viselkedés megmarad.

Az engedélyezett kérelem `recovery_request` mezőben megőrzi az engedélyezés okát, a feldolgozott és callback-állapot korát, az óra megnevezését és a megengedett műveleteket. Ez HTTP-oldali befogadási környezet; a szimulátor saját parancsellenőrzését nem helyettesíti. A szimulátor az alkalmazott és elutasított visszajelzésbe is átmásolja a környezetet. Egy végrehajtásig elavult parancs ezért továbbra is elutasítható.

A `recovery_request` fenntartott szervermező. A kliens által küldött azonos nevű mezőt a szerver minden vezérlésnél, friss állapot esetén is eltávolítja már a `control_requested` naplózása előtt. A tényleges helyreállítási feltételek teljesülésekor saját mért adataiból készíti el az új környezetet. Így a normál parancs sem hordozhat a kliens által kitalált helyreállítási eredetet; a bemeneti objektum változatlan marad.

A parancs azonosítója összeköti a `control_requested`, `control_published` és későbbi `control_applied` vagy `control_rejected` eseményeket. A QoS 1 közvetítői visszaigazolás után kiírt `control_published` a publikálás bizonyítéka. A végrehajtásról a szimulátor külön MQTT-visszajelzése ad bizonyítékot; a felület a publikálás pillanatában nem állítja át saját állapotát „végrehajtva” értékre. Lemaradt megfigyelő esetén az alkalmazási visszajelzés is várhat a sorban.

## 4. Naplóoptimalizálás az archívum változtatása nélkül

A gazdag telemetriabejegyzés JSON-hoz igazítása korábban a teljes bejegyzésen és annak érzékelő-részfáján is végigment. Az új út egyszer igazítja a teljes bejegyzést, majd ennek már megfelelő érzékelő-részét használja a tömör archívumhoz. A kompakt JSON-kódolás, a nem véges számok `null` értékre alakítása, a mezősorrend és a tömörített érzékelőadatok formátuma megmaradt.

A mintavételi és frissességi indexek továbbra is az eredeti fogadott objektum jelentését használják. Közvetlen Python-hívásból származó, karakterlánccá alakításkor összeütköző kulcs esetén külön kódolási út őrzi meg az eredeti érzékelőobjektumot. A közvetlen érzékelőarchiválás és a régi gazdag naplók migrációja is megtartotta saját érvényes útját.

A változást **9 célzott kódolási teszt** vizsgálja: a korábbi szerződéssel egyező JSON- és érzékelőbájtok, egyszeri normalizálás és bemeneti változatlanság, kulcsütközés, eredeti logikai jelzők, közvetlen archiválás, hiányzó vagy hibás érzékelőobjektum, ismételt/elavult/hibakori csomagok megőrzése, migráció és csak olvasható régi bizonyíték változatlansága. Ezek a teljes Python-tesztcsomag részei. Forrás: [kódolási regressziók](../tests/test_journal_encoding_v12.py).

## 5. Mért eredmények és hatókör

### 5.1. Valódi MQTT/HTTP-helyreállítás

A [V12 szállítási kísérlet](../results/transport-mqtt-check-v12.json) 2026. október 9-én `PASS` eredményt adott. A `ce0297311e46` futásban 12 kezdeti autó, 40 parkolóhely és 2× sebesség szerepelt, saját helyi TCP/MQTT 3.1.1 közvetítővel és külön munkafolyamatokkal. A kísérlet időben korlátozottan visszatartotta a megfigyelő FIFO-feldolgozását; üzenetet nem vont össze, nem dobott el és nem rendezett át.

A visszatartás alatt a kijelzett állapot kora **0,7636063 s** volt, miközben az állapotcallback kora **0,0531743 s**, bármely callback-üzeneté pedig **0,0056853 s** maradt. Ez közvetlenül megkülönbözteti a feldolgozási lemaradást a hálózati fogadás hiányától. A tényleges HTTP API elutasította a folytatást és a magasabb sebességet, az 1× lassítást és a szünetet viszont publikálta.

A visszatartott megfigyelő addig csak a publikálás visszaigazolásait mutatta. A feldolgozás felszabadítása után mindkét azonosítóhoz megérkezett a szimulátor alkalmazási visszajelzése, a befogadási környezettel együtt; a megfigyelt állapot **szünetelt, 1× sebességű** futás lett. Az utólagos megfigyelői állapot 404 érvényes várakozási mintából **664,3840 ms p95** értéket és 212 üzenetes korábbi sorcsúcsot jelzett, az akkori sorhossz pedig nulla volt. A visszatartás idején megmaradó korábbi kötegvégi sorhosszadat nem bizonyít üres sort.

Ez az eredmény a szakaszok elkülönítését és a korlátozott helyreállítást igazolja. A mesterséges visszatartásos, 12 autós próba nem mér tartós üzemű kapacitást. A V11-ben erős párhuzamos terhelés mellett megfigyelt sorfelhalmozódás továbbra is lényeges korlát; a friss négyautós demó és egy lezárt futás üres sora önmagában nem helyettesít hosszú terhelésmérést.

### 5.2. Páros, memóriában futó feldolgozási mérés

A [V12 profiljelentés](../results/profiling-v12/observer-profile.json) ugyanazon mentett fogadási sor első 400, illetve 1000 üzenetét játszotta vissza a korábbi és az új naplóimplementációnak. A szerver kódja mindkét ágon azonos aktuális kód volt; csak a naplóimplementáció különbözött. Öt mérési pár futott váltakozó előtte/utána sorrendben, Windows 11 és Python 3.12.14 környezetben. A táblázat a profilozó nélküli helyi futási idők mediánját mutatja.

| Fogadott üzenet | Korábbi napló mediánja | Új napló mediánja | Mediáncsökkenés | Archívbájt-egyezés |
| ---: | ---: | ---: | ---: | --- |
| 400 | 0,160114 s | 0,146120 s | 8,74% | `PASS` |
| 1000 | 0,732594 s | 0,686085 s | 6,35% | `PASS` |

A telemetria, az útpozíciók, a tömörített érzékelőcsomagok, a járműösszegző sorok, az események és a metaadatok tartalmi bájtlenyomata mindkét ágon egyezett. A nyers fogadási napló lenyomata is azonos maradt. A 400 üzenetes mintában 320, az 1000 üzenetes mintában 772 út- és érzékelősor keletkezett; a fogadott üzenetszám tehát nem azonos az autótelemetria-sorok számával.

A mérés memóriában működő adatbázissal készült. Nem tartalmaz élő MQTT-szállítást, callback-JSON-feldolgozást, lemezműveletet, WAL/fsync-költséget, böngészős lekérdezést vagy párhuzamos ütemezési terhelést. A mentett bemenet egy 12 autós futásból ered, de a visszajátszás nem reprodukálja annak érkezési ütemét. Az öt pár helyi javulást jelez; más gépre érvényes garantált százalék vagy tartós üzenetkapacitás nem következik belőle.

### 5.3. Regressziós bizonyíték

A [teljes Python-futtatás](../results/unit-summary.json) 2026. október 9-én **272 sikeres tesztet**, nulla hibát és nulla kihagyást rögzített a 0.12.0 változathoz. Ebben 23 új frissességi/helyreállítási eset és a 9 kódolási eset szerepel. Az új esetek között valódi helyi HTTP-kéréssel végzett kapuzási teszt is van; az egységtesztben helyettesített publikálást a fenti külön valódi MQTT-kísérlet egészíti ki.

A [frontend-futtatás](../results/ui/perception-renderer-check-v12.json) **105 sikeres JavaScript-esetet** adott, köztük 10 új esetet a külön órákra, az ismeretlen/nulla értékekre, a helyreállítás feltételeire és a publikálás-végrehajtás megkülönböztetésére. Ez böngészőtől független ellenőrzés. Az aktuális [MQTT/HTTP-érzékelési próba](../results/PERCEPTION_MQTT_RENDSZERTESZT.json) a `c6363d8094c2` futásban 2485 fogadott útkeret, 2425 külön eredeti érzékelőminta és 60 elavult hibakori keret megőrzését ellenőrizte; két autó beparkolt, referenciaütközés nélkül. A hivatkozott összegzés a hordozható bizonyíték; az eredeti futáskönyvtár a `results/perception-smoke-20261009-232528` volt.

A külön [asztali böngészős ellenőrzés](../results/ui/perception-ui-check-v12.json) hat tényleges felületi megfigyelést rögzített: az új diagnosztikai idők megjelenését, a szünet és az 1× sebesség külön alkalmazási visszajelzését, az aktuális teljesút-összegzés betöltését, a pontos utolsó keretet és a friss állapot melletti folytatást. A `d5ac37b78cce` élő demó mellett a `c6363d8094c2` archívum 2485. kerete 248,45 s modellidőnél, P11 helyen, `(370,00; 18,00)` pozícióban jelent meg, változatlan `#4969` minta- és `#820` eseményhatárral. A konzol nem tartalmazott figyelmeztetést vagy hibát. Az [élő diagnosztikai kép](../results/ui/mqtt_adatfrissesseg_v12.jpg) megmaradt. Az elavult kijelzés melletti korlátozott vezérlést a külön MQTT/HTTP-kísérlet és a frontend-regressziók vizsgálták; a hat asztali megfigyelésből nem következik új mobil- vagy natív letöltési igazolás.

A bizonyítékok a futás idejét, modellverzióját és vizsgált források SHA-256 lenyomatát tartalmazzák. A V11 térképes kampány eredeti dátummal és verzióval marad meg; az azonos mozgás- és vezérlésforrásokra vonatkozó hatóköre külön értelmezendő. A korábbi mobilnézeti és natív letöltési eredmények ettől nem válnak új V12-ellenőrzéssé.

## 6. Időzítési és bizonyítéki facet

| Követelmény | Megvalósítás | Bizonyíték és korlát |
| --- | --- | --- |
| A fogadott, de még nem feldolgozott csomag ne tegye frissé a kijelzett állapotot. | Külön callback- és feldolgozottállapot-óra; az eredeti fogadási idő megőrzése. | Célzott frissességi regressziók és a valódi visszatartásos MQTT/HTTP-próba; a kijelzett 0,764 s kor mellett 0,053 s állapotcallback-kor. |
| Elavult állapot mellett csak korlátozott helyreállító művelet legyen kiadható, megfelelő fogadási bizonyítékkal. | Korábbi feldolgozott állapot, élő közvetítő, legfeljebb 0,75 s állapotcallback, hibamentes kommunikáció; csak `pause` és `speed=1`. | HTTP-elutasítás folytatásra/gyorsításra; két azonosított alkalmazási visszajelzés a szünetre és lassításra. Ez nem biztonsági minősítés valós járműhöz. |
| A helyi feldolgozás gyorsítása ne gyengítse a visszakereshető archívumot. | Egyszeri JSON-hoz igazítás, változatlan kódolás, eredeti indexjelentés, teljes FIFO-sorrend. | 9 kódolási teszt és 400/1000 üzenetes bájtazonosság; 8,74%/6,35% helyi mediáncsökkenés, memóriában végzett mérésben. |
| A parancskiadás és a végrehajtás maradjon megkülönböztethető. | Külön kért, publikált és alkalmazott/elutasított esemény, közös parancsazonosítóval és befogadási környezettel. | Valódi QoS 1 publikálási nyugták és külön szimulátori alkalmazási események; a késői visszajelzés nem bizonyít késői végrehajtást. |
| A helyreállítás eredete és a kapcsolatállapot-változás diagnosztikája a szerver saját mérése legyen. | Kliensoldali `recovery_request` eltávolítása naplózás előtt; saját helyreállítási környezet és az egészségi eseményhez mért közelítő sorhossz. | Két célzott regresszió ellenőrzi a hamis kliensmező eltávolítását és a kötegvégi értéktől eltérő, eseménykori fogadási sor megőrzését. |

## 7. Megismétlés

A parancsok a `projekt` mappából, a telepített Python-környezetben futtathatók. A frontend-ellenőrzéshez Node.js is szükséges. A valódi szállítási próba saját helyi szabad portokat, külön futáskönyvtárat és saját munkafolyamatokat használ, majd leállítja azokat.

```powershell
.\.venv\Scripts\python.exe verify_units.py
.\.venv\Scripts\python.exe -m experiments.verify_transport_v12
.\.venv\Scripts\python.exe -m tests.perception_mqtt_smoke
.\.venv\Scripts\python.exe -m experiments.verify_frontend
```

A szállítási újrafuttatás eredménye a `results/transport-smoke-<időbélyeg>/summary.json` fájlba kerül; a rögzített V12-összegzés külön hordozható bizonyíték. A profil megismétléséhez a megőrzött `journal-before.py` és a tömörített fogadási fixture szükséges. Az alábbi parancs új eredményfájlt készít az eredeti mérés felülírása nélkül:

```powershell
.\.venv\Scripts\python.exe -m experiments.profile_observer_v12 --baseline results/profiling-v12/journal-before.py --fixture results/profiling-v12/received-fixture-c894ca257cbe.jsonl.gz --counts 400,1000 --repeats 5 --output results/profiling-v12/reproduction.json
```

Újrafuttatáskor a helyi idők eltérhetnek. Először a verziót, a forráslenyomatokat, a bemeneti lenyomatot és a bájtazonossági eredményt kell összevetni, majd a mediánokat az új gép terhelésével együtt értelmezni. Tartós kapacitás vizsgálatához külön, hosszabb élő MQTT-kampány szükséges: rögzített érkezési ütemmel, a sorhossz és állapotkor időbeli alakulásával, valamint a lemez és böngészős lekérdezések terhelésével.
