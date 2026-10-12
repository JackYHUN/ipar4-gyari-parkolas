# V03: szerkeszthető telephely és több konfliktuszónás közlekedés

## Cél és rendszerhatár

A rendszer a kész járművek gyártósor utáni parkolási folyamatát vizsgálja: kezdeti járművek és új termelési érkezések, beléptetési puffer, parkolóhely-kérés, központi helyfoglalás, engedélyköteles útvonalzónák, helyi gyalogos- és járműészlelés, majd beparkolás. A fizikai folyamatot a `World`, a központi döntéseket a `Coordinator` modellezi. A két komponens között valódi MQTT-adatút működik; a koordinátor a fogadott érzékelő- és telemetriaüzenetekre támaszkodik. A böngésző az MQTT-n fogadott állapotot jeleníti meg, a szerkesztő előnézete pedig egy még nem alkalmazott induló konfiguráció.

A rendszerhatáron kívül marad a csarnokon belüli gyártás, a valós kameraképek értelmezése, a földmérési lokalizáció és a közúti autonóm vezetés. A beparkolt jármű a megadott tartózkodási idő után absztrakt módon elhagyja a modellt; ennek tényleges közlekedési útvonala jelenleg nincs megvalósítva.

## Térképi alap és modellfeltevések

A győri sablon fő épülettömbjeinek relatív helye az Audi Hungaria hivatalos [2023-as nyílt napi programfüzetének](https://audi.hu/content/dam/audi-hu-site/hu/hu/news/2023/audi-hungaria-ny%C3%ADlt-nap/Programf%C3%BCzet.pdf) **2. oldalán** szereplő térképen alapul. Megmarad a bal oldali motorgyári térség, a középső szerszámgyár és energiaközpont, illetve a jobb oldali járműgyári tömb: karosszériaüzem, présüzem, lakkozó és az elnyújtott összeszerelési/végellenőrzési terület. A részletes forrásellenőrzés a [GYOR_FORRASOK.md](GYOR_FORRASOK.md) dokumentumban található.

A **600 × 400 m** terület a saját szimuláció mérete, és nem a valós telephely méretarányos leképezése. Az utak, azok egyirányúsága, a három zebra, a konfliktuszónák, a két parkolóterület és a két választható gyártási kijárat mérnöki feltevések. A `north` és `south` az oktatási tér felső és alsó parkolóterületének azonosítója; ezekből nem következik a valós gyár készautó-parkolóinak tájolása. Egy futásban egy kijárat és egy parkolóterület aktív. A másik térbeli alternatívaként látható, párhuzamos használatuk további fejlesztés.

## Beállítások és pozíciós korlátok

| Beállítás | Megengedett tartomány és jelentés |
| --- | --- |
| Aktív autók korlátja | 1-30; a modellben jelen levő mozgó és már beparkolt járművek együttes korlátja. |
| Kezdeti mozgó autók | 0-tól az aktív korlátig; a futás indulásakor ténylegesen az adott pozícióban jelennek meg. |
| Parkolóhelyek | 4-96 a kiválasztott parkolóterületen. |
| Kezdetben foglalt helyek | 0-tól a parkolóhelyek számáig; ezek külön helyállapotok, nem a mozgó járműlista elemei. |
| Termelési érkezési időköz | 1-120 szimulációs másodperc. A túlterhelés a puffer növekedésében is megjelenhet. |
| Parkolási tartózkodás | 5-600 szimulációs másodperc; leteltével absztrakt távozás történik. |
| Összes legyártandó autó | Üresen folyamatos érkezés; különben 1-1000, legalább a kezdeti autók száma. |
| Térképsablon és cél | Győri forrásalapú vagy egyszerű oktatási térkép; két kijárat és két parkolóterület közül választható. |

Az autók helye a kiválasztott kijárat bevezető sávján szerkeszthető: `x = 358-558 m`, a sávhoz tartozó `y` mellett, legfeljebb 0,05 m oldaleltéréssel. Két kezdeti autó között legalább **6,8 m** távolság szükséges. Az induló irány 180°. A kiosztott útvonalat a modell az autó aktuális helyére vetíti, ezért a jármű nem kerül vissza az útvonal kezdőpontjára.

A parkolóhely a kiválasztott terület belsejében, legalább 3 m széltávolsággal, a közlekedősáv középvonalától 9-20 m-re helyezhető el. A páronkénti ellenőrzés 3,4 m vízszintes és 6,8 m függőleges védőméretet használ: az egymáshoz mindkét irányban túl közeli helyeket elutasítja. Az áthelyezésből valódi új beparkolási útvonal készül, tehát nem csupán a rajz változik.

Az előnézet és az alkalmazás ugyanazt a `compile_scenario` validátort használja. Az előnézet nem állítja le az aktuális futást. Hibás alkalmazási kérés esetén a validálás a futó komponensek leállítása előtt történik; érvényes módosítás alkalmazása új futást indít, új futásazonosítóval.

## Útvonaltervezés és zónaengedélyezési érvelés

A `scenario.py` irányított gráfot állít össze. A Dijkstra-algoritmus az élek modellbeli euklideszi hosszának összege szerint választ elérhető útvonalat, determinisztikus csomópontazonosító szerinti döntetlennel. Ehhez kapcsolódik a parkoló közlekedősávja és a végső beállási szakasz. A `nearest` stratégia a szabad helyekhez tartozó hátralevő út hosszát, a `fixed` stratégia a helyazonosítók sorrendjét használja. Az útvonaltervezés jelenleg statikus; a rövidebb út nem feltétlenül jelent rövidebb várakozási időt, és nem történik torlódás miatti menet közbeni újratervezés.

| Követelmény | Megvalósítás | Vizsgálható bizonyíték |
| --- | --- | --- |
| A hely a kiválasztott kijáratból elérhető legyen. | Irányított gráf, Dijkstra, a célhelyig összeállított polyline; a szerkesztő a szerver validált eredményét kapja. | A futás konfigurációjában a gráf és minden hely útvonala; elérhetetlen irányt és egyedi helypozíciót vizsgáló `test_scenario.py` tesztesetek. |
| Egy konfliktuszónát egyszerre csak egy jogosult autó használhasson. | Tulajdonos és engedélyazonosító; friss telemetria, kiosztott útvonal, megközelítési irány és fizikailag előrébb álló jármű ellenőrzése. Más bevezető ágról az első jogosult várakozó kérés kap elsőbbséget. | `zone_granted` és `zone_wait` napló a döntés indokával, pozícióval és útvonal-előrehaladással; többirányú megközelítési és összeolvadási tesztesetek a `test_network_traffic.py` fájlban. |
| A következő zóna ne maradjon korábban lefoglalt erőforrás mögött. | Az autó csak a következő útvonalzónát kéri; új engedély előtt az előző tulajdonjog felszabadításának igazolása szükséges. | `other_zone_still_owned`, `earlier_route_zone_not_cleared` indokkódok, zónatulajdonosok és az ehhez tartozó célzott tesztek. |
| A zóna felszabadítása igazolt fizikai kilépéshez kapcsolódjon. | Egyező `zone_exit` esemény és engedélyazonosító, majd az eseményt követő telemetria; az útvonal mentén a teljes autó és a biztonsági ráhagyás is elhagyta a zónát. | `zone_release_pending` és `zone_released` rekord, kilépési esemény, telemetria-sorszám és pozíció. |

A geometria is számít: két egymást követő zóna között legyen teljes jármű számára elegendő várakozási hely. A protokollban elkerült többzónás foglalás önmagában nem bizonyítja tetszőleges térképek holtpontmentességét. A helyi fékezés, a szenzorfrissesség figyelése és az ütközésellenőrzés külön modellfunkciók maradnak.

## Reprodukálhatóság és eredmények

A kezelő a forgatókönyvet JSON-fájlba mentheti és visszatöltheti. A fájl tartalmazza a normalizált beállításokat, az egyedi kezdő autóhelyeket, parkolópozíciókat, a véletlenmagot és a stratégiát. Visszatöltéskor először előnézet készül; a futás indításához a beállítások alkalmazása szükséges.

Minden új futás külön `<run_id>-config.json` fájlt kap a `results/live-*` mappában. Ezt a tényleges konfigurációt olvassa a szimulátor és a koordinátor. A `<run_id>-metadata.json` rögzíti a konfigurációt, magot, stratégiát, sebességszorzót, mértékegységeket, modellverziót és a forrásfájlok SHA-256 ellenőrzőösszegeit. A SQLite-naplóban a forgatókönyv alkalmazása és a további kezelői beavatkozások is követhetők; a ZIP-naplóexport a metaadatokat, eseményeket és a megőrzött telemetriát tartalmazza.

Azonos modell, konfiguráció és mag a szimulációs kísérletek megismétlésének alapja. Valódi MQTT esetén az operációs rendszer ütemezése és az üzenetérkezés időzítése eltérhet; a kézi hibainjektálás is külön esemény. Ezért a hálózati késleltetésre és a teljesítményre vonatkozó eredményeket ismételt futásokkal kell értelmezni, nem teljesen azonos valós idejű üzenetsorrendet feltételezve.

**Ellenőrzési eredmények:** a `results/network-load-check.json` négy kijárat–parkoló kombinációt dokumentál, egyenként 30 kezdeti autóval, 64 parkolóval és automatikus gyalogosokkal. Mind a négy determinisztikus protokollszimulációban 30 autó beparkolt, modellezett ütközés nélkül. A `tests.scenario_mqtt_smoke` külön brokerrel és munkafolyamatokkal, valódi TCP/MQTT-adatúton 40 parkolót és 10 autót alkalmazott az alternatív kijárat és alsó parkoló esetén; mind a 10 autó beparkolt, ütközés nélkül. Az egyedi autó- és helypozíció, a hibás kérés elutasítása, a futásazonosító váltása és az exportból a konfiguráció reprodukálása is ellenőrzött. Ezek véges tesztesetek eredményei; nem bizonyítják tetszőleges térképek vagy egy valódi gyár biztonságát. A győri konfiguráción még nincs több véletlenmagra épülő teljesítmény-konfidenciaintervallum; a korábbi 30 magos összehasonlítás külön, a 140×90 m alapmodellre vonatkozik.

## Prioritásos folytatási pontok

1. **Tényleges kijutási út a parkolóból.** A mostani absztrakt távozás helyett kiszállítási cél, kihajtási szakasz és helyfelszabadítási bizonyíték készüljön. Elsőként a beálló és kihajtó járművek konfliktusait, a terület elhagyását és a helyfoglalás megszűnését kell verifikálni.
2. **Több parkolóterület párhuzamos kiosztása.** Egy futásban mindkét terület legyen használható, a célkijelölés pedig kapacitás és hátralevő út alapján működjön. Az összehasonlítás azonos véletlenmagokkal vizsgálja a puffer hosszát, a beparkolási időt, a megtett utat és a zónavárakozást.
3. **Hálózati terhelés és a broker külön gépre helyezése.** Külön kísérlet mérje a járműszám és publikálási ütem hatását késleltetésre, ingadozásra és adatfrissességre. Több számítógép esetén a jelenlegi, azonos gépen értelmezett `perf_counter_ns` időkülönbség helyett igazolt óraszinkronizáció vagy oda-vissza mérés szükséges; a broker elérését és a kapcsolati beállításokat is ehhez kell igazítani.
