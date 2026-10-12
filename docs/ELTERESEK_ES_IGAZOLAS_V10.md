# V10 – teljesút-áttekintés, folyamatos visszajátszás és adatérvényesség

A változat célja, hogy hosszú futásnál is áttekinthető maradjon a jármű mentett útja, miközben az érzékelési bizonyíték a ténylegesen fogadott csomaghoz kötődik. Az élő vezérlésben a hibás parkolóüzenet nem teheti frissnek a korábbi foglaltságot. A rendszer továbbra is laptopon futó oktatási modell, valódi helyi MQTT-adatúttal.

## Az archívum rögzített határai

Az előző változat a telemetria felső fogadási azonosítóját rögzítette. A külön szemantikus eseménytáblába közben érkező sorok azonban megváltoztathatták az eseménylistát. Most a válasz két önálló határt ad: `until_id` a járműmintákhoz, `events_until_id` az eseményekhez. Az ablakváltás és a régebbi események betöltése mindkettőt megőrzi. A frissítés új határokat vesz fel.

A csak olvasható SQLite-kapcsolat már a metaadatok beolvasása előtt rögzít olvasási tranzakciót. Így egy válasz metaadatai, számlálói, részletes sorai és opcionális áttekintése ugyanazt az adatbázis-pillanatképet látják. A két tábla azonosítói nem közös számlálók. A közös pillanatkép nem jelent azonos szimulációs időt vagy bizonyított oksági kapcsolatot egy esemény és egy pozíció között.

## A teljes út térképi áttekintése

A `/api/car/history/page?include_overview=1` a részletes, legfeljebb 2000 keretes ablak mellé a teljes rögzített út tömör pozícióit is átadja. A külön `/api/car/history/overview` végpont ugyanezt önállóan adja. Az áttekintés legfeljebb 4000 ténylegesen mentett pontot tartalmaz, az első és utolsó érvényes pozícióval együtt. Kizárólag a tömör mezőket olvassa: nem bontja ki a tömörített érzékelőcsomagokat.

A halvány szaggatott vonal a teljes áttekintést, a színes vonal a betöltött részletes ablakot mutatja. Az áttekintés kapcsolható. Az érzékelők és az adatminőségi kártyák továbbra is a betöltött keretekhez tartoznak; a térképi áttekintés nem bővíti ezek statisztikai hatókörét.

A pontok koordinátái pontos mentett értékek, de a kihagyott pontok közötti kanyarok egyenes szakasszal közelítettek lehetnek. A kiszolgáló az összes forrássort bejárja, ezért a kihagyott adatrés, hibás pozíció és visszalépő időbélyeg megszakítja az áttekintő vonalat. A ritkított pontok közötti hosszabb idő önmagában nem minősül új adatrésnek. A teljes bejárás időigénye az archívum méretével nő, még ha az átadott pontszám és a memóriaigény korlátozott is.

## Ablakokon át folytatódó visszajátszás

A **Folyamatos lejátszás** kapcsoló alapértelmezetten aktív. A betöltött ablak végén a felület ugyanazon futás, autó és két rögzített archívhatár következő ablakát kéri. A válasz érkezéséig az utolsó valóban mentett pozíciót és érzékelést tartja a képernyőn. Nem rajzol köztes járműhelyzetet vagy új érzékelőmintát.

A szünet, másik autó vagy futás kiválasztása, illetve új időkeresés érvényteleníti a függő folytatást. Egy későn érkező válasz nem indíthatja újra a lejátszást és nem írhatja felül az új kiválasztást. Azonos szimulációs időnél a fogadási sorrend marad érvényes; visszalépő vagy hiányzó időbélyegnél a visszajátszás megáll és jelzi a problémát. A rögzített út végén külön befejeződött állapot jelenik meg.

A runlista, a kiválasztott autó és az „út megtekintése” művelet aszinkron válaszai is ellenőrzik a kiválasztás azonosságát. A közelségérzékelő találat nélküli csatornája csak a mentett nyalábhoz igazíthatja a nézetet, ellentmondó találati koordinátához nem.

## Parkolóérzékelő-adatok ellenőrzése

A koordinátor egy teljes foglaltsági képet vár: minden konfigurált parkolóhely pontosan egyszer szerepeljen, idegen azonosító nélkül. Az `occupied` és az opcionális `blocked` érték logikai típus, a sorozatszám valódi egész, nem logikai érték. A teljes üzenet ellenőrzése megelőzi a frissesség, sorozatszám, szimulációs idő és foglalás módosítását.

A hiányos, ismételt helyet tartalmazó vagy hibás típusú üzenet indokolt elutasítást kap. Nem frissíti részlegesen az adatbázist és nem hosszabbítja meg a korábbi minta érvényességét. Ugyanazzal a sorozatszámmal később érkező, már teljesen érvényes kép feldolgozható, mert a hibás üzenet nem fogyasztotta el az azonosítót.

## Névleges és tényleges mintavételi időköz

A szimulátor az első olyan integrációs lépésben mintáz, amely eléri a névleges mintavételi határidőt. Például 0,1 s névleges periódus és 0,07 s állandó lépés mellett a tényleges időköz 0,14 s; 0,25 s lépés mellett 0,25 s. A korábbi napló és reakcióráhagyás ilyenkor tévesen a névleges 0,1 s értéket használta.

Most a `nominal_period_s` a beállított periódus, a `period_s` állandó lépés mellett a lépésre felfelé kerekített intervallum. A `sample_interval_s` az előző sikeres eredeti mintáig eltelt tényleges idő; az elsőnél `null`. Változó lépéseknél a periódus konzervatív felső korlátja a névleges érték és az addigi legnagyobb tényleges lépés összege. A `sampling.period_kind` megkülönbözteti e két értelmezést; a korlát a hibainjektálás miatti kiesést nem foglalja magában. A fékezési reakcióráhagyás az effektív értéket használja, amelyet a `control.reaction_period_s` rögzít.

A telemetria, a parkolóérzékelő-keret és a hibadiagnosztika ugyanazokat az időzítési mezőket közli. A tömör útarchívum is megőrzi ezeket. Kieséskor a régi eredeti minta órája és mintaidőköze marad meg; az ismételt diagnosztika nem új érzékelés. A 0,05 s lépés / 0,1 s periódus alapbeállítás mintavételi ideje és viselkedése változatlan. A felület a konkrét sor mentett periódusával vizsgálja az adatrést, így a nagyobb, de szabályos lépés nem jelent hamis útvonalszakadást.

## Követelmény–megvalósítás–bizonyíték

| Szempont | Követelmény | Megvalósítás | Célzott bizonyíték |
|---|---|---|---|
| Követhetőség | Egy bejárás alatt ne változzon az archívum határa. | Két fogadási plafon és korán indított olvasási tranzakció. | Párhuzamos író, új minta és új esemény, változatlan lapozott sorok. |
| Erőforrás | A teljesút-áttekintés ne bontsa ki a gazdag érzékelési archívumot. | Tömör mezők bejárása, legfeljebb 4000 pont. | 20 025 rekord, nulla `zlib`-bontás és gazdag JSON-feldolgozás. |
| Időbeli értelmezés | Az eltérő vagy hiányzó időbélyeg ne okozzon hamis mozgást. | Fogadási sorrend, törésjel és lejátszási megállás. | Fordított, hiányzó és azonos időbélyegek célzott felületi esetei. |
| Kezelhetőség | Egy megszakított folytatás ne írja felül az új kiválasztást. | Kérésgeneráció és futás–autó azonosság ellenőrzése. | Szünet, új keresés, új autó, késői válasz és HTTP-hiba tesztjei. |
| Adatérvényesség | Hibás foglaltsági kép ne váljon friss döntési alappá. | A teljes parkolóüzenet atomi ellenőrzése. | Hiányos/duplikált/idegen hely, hibás típus és azonos sorszámú helyreállás. |
| Mintavétel | A reakcióráhagyás ne legyen a lépés miatti időköznél rövidebb. | Effektív periódus, külön névleges és tényleges mintaidőköz. | 0,07/0,1 és 0,25/0,1 arány, futás közben változó lépés, alapbeállítás és hibakori mintaazonosság. |

Az ellenőrzések tényleges eredménye a `results/unit-summary.json`, `results/ui/perception-renderer-check-v10.json`, `results/ui/perception-ui-check-v10.json` és `results/network-load-check-v10.json` fájlokban szerepel. A valódi MQTT/HTTP-futás részlete a `results/PERCEPTION_MQTT_RENDSZERTESZT.json`; a négy, egyenként 30 autós térképes próba külön determinisztikus protokollteszt, nem MQTT-terhelésmérés. A korábbi V08/V09 eredmények eredeti dátummal és hatókörrel maradnak meg.

Az összeépített változat 203 Python-tesztje és 71 JavaScript-esete sikeres. A valódi MQTT/HTTP-próba 2486 mentett keretet, 2426 eredeti érzékelőmintát és 60 kiesési diagnosztikai keretet ellenőrzött; a két autó beparkolt ütközés nélkül. A négy térképes esetben egyenként 30/30 autó parkolt be, referenciaütközés nélkül.

## Reprodukálás

```powershell
.\.venv\Scripts\python.exe verify_units.py
.\.venv\Scripts\python.exe -m tests.perception_mqtt_smoke
.\.venv\Scripts\python.exe -m experiments.verify_frontend
.\.venv\Scripts\python.exe -m experiments.verify_yard_layouts
.\.venv\Scripts\python.exe record_verification.py
.\.venv\Scripts\python.exe package_prototype.py
```

A böngészős bizonyíték manuális, rögzített UI-próba. A korábbi natív kijelöltminta-letöltés korlátozása változatlan: a beépített böngésző útja nem igazolt, a szerveroldali HTTP-exportnak külön sikeres bizonyítéka van. A videó, a publikált kódtár és a végleges, megfelelően hivatkozott műszaki PDF továbbra is szükséges a teljes beadandóhoz.
