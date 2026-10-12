# V11 – teljesút-összegzés és telemetriaérvényesség

A változat a teljes rögzített járműút vizsgálatát egészíti ki állapot- és várakozásösszegzéssel. A részletes, legfeljebb 2000 mintás érzékelési ablak továbbra is külön hatókörű. Az összegzés a ténylegesen fogadott skaláris adatokból készül; hiányzó megfigyelésekből nem állít elő új mozgást vagy érzékelést.

## A teljes út és a részletes ablak

Az **Útösszegzés és várakozások** panel az autó minden megőrzött fogadott mintáját vizsgálja a kiválasztáskor rögzített felső azonosítóig. A járműminták `until_id`, az események `events_until_id` plafont kapnak ugyanabban az SQLite-olvasási pillanatképben. A panel a következő ablakra váltáskor változatlan marad; az **Új minták betöltése** új határokat kér. A felület a futást, járművet és mindkét plafont ellenőrzi, mielőtt újrahasználja az összegzést.

A kártyák a keretszámot, az állapotra besorolható becsült időt, a kihagyott pozitív időközök összegét és a jelentett odometriát mutatják. Külön szerepel az érzékelőcsomagok friss/elavult/minta nélküli/hiányzó darabszáma, az eltérő eredeti mintaórák száma és a legnagyobb értelmezhető adatkor. Az első és utolsó fogadott mintára a rögzített archívhatáron belüli kezdő-, illetve végablak lekérdezésével lehet ugrani. Az azonos mintaidők és a más autókkal összefűzött fogadási azonosítók sem változtatják meg a kiválasztott végpontot; az ugrás nem egy feltételezett előző azonosítóból számol.

A közelségcsatornák részletes minimumát az index nem őrzi külön mezőként. Ezért a teljesút-összegzés ilyen minimumot nem közöl. A részletesen betöltött ablak saját minimuma és érzékelési minősége változatlanul elérhető. Régi archívum esetén a hiányzó előzmény és az elérhetetlen érzékelési index kifejezetten jelzett.

## Az időbecslés szabálya

Két egymást követő fogadott rekord között a bal oldali rekord állapota és jelentett várakozási oka kapja a `t[i+1] - t[i]` időt, kizárólag akkor, ha mindkét óra érvényes és az időköz pozitív; mindkét sorozatszám érvényes és egymást követő; nincs mentett sorozatszám- vagy időrés; valamint a különbség nem haladja meg a következő rekord periódusának 1,5-szeresét, 1 µs ráhagyással. Régi, periódus nélküli soroknál a használt visszaesési érték 0,1 s. Ez mintatartással becsült állapotidő, nem pontos eseményidő.

Hiányzó, azonos vagy visszalépő óra, sorozatszámhiány, ismétlés, visszalépés vagy rés esetén az intervallum kizárt. Érvényes pozitív órakülönbsége ilyenkor az **adat nélküli pozitív idő** értékbe kerül. Ismeretlen vagy visszalépő órához nem rendelhető pozitív idő; kizárt intervallumként külön megszámolt. Az első–utolsó órakülönbség csak értelmezhető végpontoknál jelenik meg, és általános, hibás órasorozat esetén nem feltétlenül egyezik a pozitív részidők összegével.

A kieséskori megfigyelői diagnosztika külön MQTT-témán érkezik, és nem kap eredeti publikálói sorozatszámot. Ezek a keretek megőrzik a hibakori döntést és régi érzékelést, de nem szolgálnak bizonyítékként folytonos állapotidőre. A várakozási ok a vezérlő jelentett szövege; nem bizonyítja az állapotváltozás fizikai okát. Az utolsó mintától a még nem fogadott következőig idő nem számolódik.

Az állapot- és okdiagram legfeljebb húsz csoportot mutat, becsült idő, majd keretszám szerint rendezve. A többi csoport külön összegben szerepel. A százalékok nevezője a besorolható becsült idő, nem a teljes első–utolsó órakülönbség.

## Odometriából jelentett út és érzékelési adatkor

A jelentett út az első és utolsó mentett kumulatív `distance_m` számláló különbsége. Csak akkor elérhető, ha az összes számláló érvényes, nemnegatív és nem csökken. Számláló-visszaállítás vagy hiány esetén a panel nem helyettesíti a hiányt pozíciók közti egyenesek hosszával. Ez modellezett odometria, nem fizikai kerékjeladó mérése.

Az adatkor a keret szimulációs ideje és a benne mentett eredeti érzékelőminta idejének különbsége, érvényes, nemnegatív órák és helyes sorrend esetén. Hálózati késleltetést nem jelent. Az ismételten fogadott régi csomag egy eredeti mintaóraként számít; az összegzés az eltérő időbélyegeket számolja, nem állít önálló, fizikai mintaazonosítót.

## Erőforrás és kompatibilitás

A `/api/car/history/mission` önállóan, a `/api/car/history/page?include_mission=1` a részletes ablakkal azonos pillanatképen adja az összegzést. A `trip_samples` és `trip_perception` skaláris mezőit használja. Nem bontja ki a tömörített érzékelőcsomagokat, és nem módosítja a régi adatbázist.

A Python-sorbejárás állandó számú rekordot tart memóriában. A pontos csoportosítást és eltérő mintaórák számolását az SQLite végzi; nagy archívumnál időt és ideiglenes lemezterületet igényelhet. Az összegzés ezért nem állandó idejű. A részletes ablakváltás gyorsítótárazott, változatlan összegzést használ.

Az áttekintő útvonal az ismételt és visszalépő sorozatszámnál is megszakad. A régi események hibás vagy hiányzó ideje **Időadat nélkül** jelzést kap; nem lesz belőle hamis 0 s időpont. Egy másik jármű kiválasztása után későn érkező előzményhiba nem írhatja felül az új autó paneljét.

## Telemetria ellenőrzése a döntés előtt

A koordinátor csak kifejezett, véges, nemnegatív numerikus `sim_time` órát; nemnegatív egész `sent_ns` és `seq` értéket; véges numerikus X/Y koordinátát; valamint a `telemetry/{car_id}` témával pontosan egyező azonosítót fogad el. A logikai értékek és számmá alakítható karakterláncok elutasítottak. A helyi küldési óra továbbra is `perf_counter_ns()`, a meglévő frissességi ellenőrzéssel.

A teljes ellenőrzés és növekvő sorozatszám vizsgálata megelőzi a koordinátori óra, telemetria, frissesség és függő zónakilépés módosítását. Hibás keret csak indokolt elutasítási naplót hoz létre. A javított, ugyanolyan sorozatszámú érvényes keret később feldolgozható. Más témán megadott hibás óra sem kerülhet függő zónakilépési bizonyítékba.

## Műszerfal elérhetősége nagy MQTT-forgalomnál

A valódi, 12 aktív autós demó hosszabb futtatásánál a statikus HTTP-válasz még elérhető maradt, miközben az állapot- és archív lekérdezések nem jutottak az alkalmazás zárolásához. A korábbi megfigyelő a zárolás alatt teljesen kiürítette az MQTT-sort; folyamatosan érkező új üzenetek mellett a sor kiürülése nem volt garantált.

A megfigyelő most legfeljebb 128 teljes üzenetet dolgoz fel egy kötegben, és minden teljes üzenet után ellenőrzi a 25 ms-os feldolgozási célidőt. A következő köteg előtt elengedi a zárolást. Egyetlen hosszú naplóbeírás vagy állapotellenőrzés túllépheti a célidőt; ez nem kemény valós idejű határ. A feldolgozási sorrend és eredeti fogadási időbélyeg megmarad, az intézkedés nem dob el üzenetet.

A `mqtt.observer` mező valós futási számlálókat ad: feldolgozott kötegek és kivett üzenetek, utolsó/legnagyobb kötegidő, hozzávetőleges várakozó üzenetszám és addigi legnagyobb sorhossz. A még nem megfigyelt sorhossz `null`. A felület a sorhosszt és kötegidőt a hálózati késleltetéstől külön nevezi meg. A sorhossz a párhuzamos MQTT-visszahívás miatt pillanatnyi közelítés; a kötegelt feldolgozás nem ígér korlátos teljes backlogot, ha az írás tartósan lassabb a beérkezésnél.

A célzott próba folyamatosan újratöltődő sor mellett is valódi helyi `/api/state` választ kapott. Külön teszt igazolja az üzenetsorrend megtartását, a teljes üzenetek utáni időkeret-vizsgálatot és a vezérlő/HTTP-szál zároláshoz jutását.

A 12 autós, 2× sebességű futás erős párhuzamos tesztterhelés mellett továbbra is felhalmozott várakozó üzeneteket. Ezért a bemutatóhoz külön, négyautós véges forgatókönyv is készült: `config/gyor-monitor-demo.json`, illetve a szerkesztőben megnyitható `config/gyor-monitor-demo-scenario.json`. A kisebb aktív demóban a sor kiürült; a külön HTTP-bizonyíték már a befejezett futás három állapotválaszát rögzíti. E rövid megfigyelések nem tartós átviteli teljesítménymérések.

## Követelmény–megvalósítás–bizonyíték

| Szempont | Követelmény | Megvalósítás | Ellenőrzés |
|---|---|---|---|
| Követhetőség | A teljes út ne keveredjen a részletes ablakkal. | Külön panel, két rögzített plafon, ugyanaz az olvasási tranzakció. | Több mint 20 000 rekord, párhuzamos író, rögzített esemény- és mintahatár, felületi ablakváltás. |
| Időbeli érvelés | Adatrés ne legyen bizonyított várakozás. | Konzervatív mintatartás és külön kizárási számlálók. | Ismert állapotsor, rés, hiányzó/azonos/visszalépő óra és sorozatszám. |
| Erőforrás | Az összegzés ne dekódolja a gazdag észleléseket. | Skaláris sorbejárás és korlátozott csoportlista. | Tiltott gazdag dekódoló és tömörítésbontás melletti sikeres lekérdezés. |
| Adatérvényesség | Hibás telemetria ne szabadítson fel zónát. | Atomi típus-, téma-, óra- és sorozatszám-ellenőrzés. | Függő kilépés, rossz típusok, változatlan döntési állapot, azonos sorozatszámú helyreállítás. |
| Kezelhetőség | Késői kérés ne írja felül az új kiválasztást. | Kérésazonosság és mindkét plafon vizsgálata. | Késleltetett válasz/hiba, új autó, frissítés, pontos fogadási rekordra ugrás. |

## Ellenőrzés és reprodukálás

Az aktuális eredményeket a `results/unit-summary.json`, `results/ui/perception-renderer-check-v11.json`, `results/ui/perception-ui-check-v11.json`, `results/network-load-check-v11.json` és `results/PERCEPTION_MQTT_RENDSZERTESZT.json` rögzíti. A végső `results/ELLENORZES.json` csak sikeres, az ellenőrzött forrásokkal egyező új bizonyítékokból készül. A korábbi V10 dokumentum és kampány külön történeti eredmény.

```powershell
.\.venv\Scripts\python.exe verify_units.py
.\.venv\Scripts\python.exe -m tests.perception_mqtt_smoke
.\.venv\Scripts\python.exe -m experiments.verify_frontend
.\.venv\Scripts\python.exe -m experiments.verify_yard_layouts
.\.venv\Scripts\python.exe record_verification.py
.\.venv\Scripts\python.exe package_prototype.py
```

A négy térképes vizsgálat determinisztikus protokollteszt. A MQTT/HTTP-próba valódi helyi közvetítőt használ, külön hálózati bizonyítékként. A mozgásvezérlés és a fizikai szenzormodell e változatban nem bővült; az ismert modellhatárok megmaradnak. A böngészős terhelési próbán feltárt 5 s-os általános lekérdezési korlát az archív mintaablakokhoz külön, 30 s-os korlátot kapott. Az időtúllépés érthető hibát ad, és az utolsó sikeresen betöltött ablak megmarad. Ez nem teszi állandó idejűvé az archívum lekérdezését.

A publikált kódtár, működési videó és végleges, hivatkozott műszaki PDF továbbra is a teljes beadandó hátralévő eleme.
