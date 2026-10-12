# Naplózás és adatexport · V14

A kijelző önálló MQTT-feliratkozó. A szemantikus napló és az autók időbeli mintasora a `yard/<run_id>/...` témákon ténylegesen fogadott üzenetekből készül. A helyi HTTP-kérések és a felügyelő által észlelt folyamatállapotok külön kezelői, illetve életciklus-bejegyzést kapnak. A V14 külön tartósan őrzi a fogadó gépen mintavételezett forgalmi diagnosztikát. Ez helyi megfigyelői mérés, nem egy új MQTT-telemetriatéma. A napló egy autó útjának, a rendelkezésre álló virtuális észleléseinek és a rendszer döntéseinek utólagos ellenőrzését teszi lehetővé. V06-tól a teljes fogadott `sensors` csomag és a tényleges helyi vezérlési döntés is megmarad. A részletes érzékelési séma a [V06 leírásban](ESZLELES_NAPLO_ES_RADAR_V06.md), az új forgalmi archívum a [V14 leírásban](FORGALMI_ARCHIVUM_V14.md) szerepel.

## Felületi használat

Az autó kiválasztása a térképen, a járműlistában vagy a részletpanelben történik. A panel az aktuális állapotot, az érzékelési adat korát és a rendelkezésre álló MQTT-telemetria sebességgörbéjét jeleníti meg. A grafikon szimulációs másodperceket és m/s értékeket használ. A lekérdezett mintákból készülő ablak korlátozott; a hiányzó minta nem helyettesíthető feltételezett mérési ponttal. A részletpanelből az adott autó naplójára lehet szűrni.

A napló szint, kategória, autóazonosító és szöveg szerint szűrhető. Az élő első oldal másodpercenként frissül. Egy felületi oldal legfeljebb 100 sort mutat; a régebbi oldalra lépéskor a nézet rögzített lapozási azonosítót használ, ezért az új sorok nem görgetik el az olvasott oldalt. A keresés az esemény szövegében, típusában és részletes adataiban is működik. A sor kinyitásával a teljes szemantikus bizonyíték olvasható.

A külön teljesút-előzménynézet futás és autó szerint keres. A mentett konfigurációhoz tartozó külön térképen fogadott mintapontok, időcsúszka, visszajátszás és események jelennek meg. A kiválasztott minta UTC ideje, szimulációs ideje, pozíciója, sebessége, állapota és várakozási oka együtt olvasható. A minták közötti adathiányt a nézet jelzi. V09-től legfeljebb 2000 keretes ablakokkal a teljes megőrzött út bejárható, az első 20 000 mintára korlátozás megszűnt. V10-től legfeljebb 4000 tényleges mentett pozíció külön teljesút-áttekintést ad, a visszajátszás pedig ablakhatáron át is folytatható. A teljes CSV/JSON útexport ettől a böngészőkorláttól független. Részletek: [V09 lapozás](ELTERESEK_ES_IGAZOLAS_V09.md), [V10 teljesút-áttekintés](ELTERESEK_ES_IGAZOLAS_V10.md).

Az élő érzékelőpanel és a mentett út idővonala ugyanabból a fogadott csomagból rajzol virtuális radarnézetet és szintetikus elölnézetet. A nézet mellett az eredeti mintavételi idő, az adatkor, a vezérlő célsebessége, az ideális fékút és reakcióráhagyás, valamint a döntés oka olvasható. Az elölnézet sematikus geometriai vetület, nem kamerafelvétel; a zebrajelzés térképi helyhez társított gyalogosállapot, nem gyalogospozíció. Az adott keret SVG állóképként és JSON-ként külön letölthető. Hiányzó régi csomagnál a nézet üres; adatrésben a korábbi észlelés marad külön jelzéssel, interpoláció nélkül.

A forgalmi archívum az élő üzenetforgalmi paneltől külön mutat egy kiválasztott futást, legfeljebb 120 pontos ablakban. Lapozáskor rögzített felső mentési azonosító tartja változatlanul a vizsgált prefixet. A pontokhoz tartozó modellidő és napló-/útazonosító-határok az összevetést segítik. Lemaradáskor a mentett modellidő korábbi, már feldolgozott állapothoz tartozhat: a forgalmi görbe és az autó mozgása közötti időbeli együttjárás önmagában nem oksági bizonyíték.

## Tárolás és megőrzés

A programindításhoz tartozó könyvtár `results/live-<időbélyeg>/`. Minden futás külön `<run_id>-journal.sqlite3` adatbázist és `<run_id>-metadata.json` fájlt kap. Az SQLite a Python szabványos könyvtárából származik, így a funkcióhoz nincs új telepítendő függőség. A napló az írásokat 100 mintánként, illetve 0,5 s után a következő MQTT-üzenet fogadásakor véglegesíti. A snapshot heartbeat szüneteltetéskor is elvégzi ezt. A lekérdezés, az export és a rendezett leállítás kiírja a függő módosításokat.

A megőrzési szabályok különböznek:

| Adatkör | Megőrzés | Teljesség jelzése |
|---|---|---|
| Szemantikus események | A futás összes esemény-, kérés-, parancs-, audit-, kezelői és életciklus-sora megmarad. | `records`, `last_id`; a mentett sorok nem a snapshotok ismétlődő beágyazott eseménylistái. |
| Részletes fogadott telemetria és hibakori diagnosztika | Futásonként globálisan a legújabb 200 000 minta, az összes autót együtt számolva. | `telemetry_records`, `telemetry_total_received`, `telemetry_truncated`, `telemetry_limit`; ez a részletes exportablak. A számlálás normál telemetriát és a külön témán kapott hibakori megfigyelői diagnosztikát is tartalmaz. |
| Tömör autóút-archívum | A futás minden feldolgozott, fogadott autótelemetriájának és hibakori diagnosztikájának fontos mezői, valamint a teljes `control` döntési mező a `trip_samples` táblában. | `trip_samples`, `trip_retention`, az út összegzésében teljesség- és hézagjelzés; régi adatbázisnál a már elveszett minták nem pótolhatók. |
| Teljes fogadott érzékelőcsomag | A mintaazonosítóhoz kötött teljes `sensors` JSON, zlib 3-as szinten tömörítve a `trip_perception` táblában. A részletes ablak rotációja nem törli. | `perception_samples`, tömörített/tömörítetlen payload-bájtok; az út összegzésében `perception_coverage`, `perception_distinct_samples`, `perception_stale_frames` és teljességjelzés. |
| V14 forgalmi megfigyelések | A monitor minden ténylegesen felvett diagnosztikai pontja futásonként tartósan megmarad a `transport_samples` táblában, az élő 120 pontos ablaktól függetlenül. | `run_id`, `id`, `segment`, rögzített `until_id`; régi, mintatábla nélküli futásnál `available=false`, `availability=not_recorded`. |
| Nyers MQTT JSONL | Futásonként alapértelmezetten 100 MiB körkörös keret, 25 MiB célméretű részekkel. | `raw_retained_bytes`, `raw_removed_segments`, `raw_removed_bytes`, `raw_oversize_dropped` és futásonkénti `RAW_LOG_RETENTION.json`. |

A nyers keret elérésekor a legrégebbi nyers fájlrészek törlődnek, az új forgalom mentése folytatódik. A normál rotáció információs esemény, és nem kapcsolati hibaként jelenik meg. A nyers részletnapló csak a megőrzött újabb időszak forgalma, nem a teljes futás másolata. A teljes tömör autóút, a külön érzékelőcsomag-archívum, a szemantikus napló és a V14 forgalmi archívum megőrzése ettől független. A futás ZIP-exportja V08-tól a részletes telemetriaablak mellett a teljes fogadott autóutat és észleléseket is tartalmazza, V14-től a forgalmi diagnosztikával kiegészítve. A külön CSV/JSON útexport továbbra is elérhető.

Az archivált csomagok számában ismételten közölt régi megfigyelés és még sikeres első mintavétel nélküli csomag is lehet. A `perception_distinct_samples` a sikeres minták különböző mintavételi időpontjait számolja, a `perception_stale_frames` az elavult vagy aktív hibás kereteket jelzi. A csomaglefedettség nem érzékelési pontosság és nem garantált MQTT-veszteségmentesség. Régebbi adatbázisból csak a megmaradt részletes telemetriához tartozó valódi csomagok olvashatók vagy migrálhatók; a törölt megfigyeléseket a program nem pótolja.

Reset esetén az előző futás naplója lezárul, fájljai megmaradnak, az új futás új azonosítót és új nyers keretet kap. A metaadat `previous_run_id` mezője az előző futásra mutat. Az élő vezérlés az aktuális futást kezeli; az előzménynézet és a külön történeti API a korábbi programindítások mentett futásait is olvassa. A futásazonosító és autóazonosító együtt határozza meg az autót. Az összes mentett adatbázis lemezmérete nincs a nyers gyűrű méretével korlátozva. A zlib szenzorpayload mérete mellett a sorok, indexek, vezérlési adatok és WAL is helyet igényelnek; folyamatos demonstráció után a program leállításával szüntethető meg az új adatok keletkezése.

## Idők és késleltetés

Az egyes órák jelentése eltér; összevonásuk hibás következtetéshez vezetne.

| Mező | Jelentés és mértékegység |
|---|---|
| `utc`, MQTT-sor esetén `received_utc` | A kijelző kliensének fogadási callbackjében rögzített gépi UTC idő. A későbbi megfigyelői feldolgozás vagy export nem tolja el ezt az értéket. HTTP- és életciklussornál a helyi bejegyzés létrehozásának UTC ideje. |
| `sim_time` | A modell ideje szimulációs másodpercben. Szünetnél áll, a sebességszorzóval gyorsítható. |
| `sensors.sample_sim_time_s`, `sensors.age_s` | Az eredeti érzékelőminta ideje és aktuális modellbeli kora, szimulációs másodpercben. A mintavétel a mozgáslépés előtt történik; hibakor a minta változatlan, csak a kor nő. |
| `control.decision_sim_time_s`, `control.motion_interval_s` | A végrehajtott helyi vezérlési döntés modellideje és a hozzá tartozó mozgáslépés hossza, szimulációs másodpercben. |
| `sent_ns`, `received_ns` | Ugyanazon laptop `time.perf_counter_ns()` számlálójának nanomásodpercértékei; különbségükből számítható a küldéstől a fogadási callbackig tartó alkalmazási kézbesítés. Nem abszolút UTC időpontok. |
| `details.mqtt.latency_ms` | A fogadás és küldés számlálókülönbsége milliszekundumban. A grafikon szimulációs idejétől független mennyiség. |
| Forgalmi pont UTC ideje és `elapsed_s` | A helyi diagnosztikai pont mentésének gépi UTC ideje, illetve a saját mérési szakaszában eltelt monoton gazdagépidő. Nem egy járműüzenet fogadási ideje. |
| Forgalmi pont `sim_time` | A mintavételkor legutóbb feldolgozott MQTT-állapot modellideje, amely lemaradáskor korábbi lehet a friss callback-forgalomnál. |

A futás metaadata tartalmazza a számláló megvalósítását, felbontását és azt, hogy a mérés azonos gépen történt. A késleltetés nem a teljes érzékelés–döntés–fékezés lánc reakcióideje. Az MQTT-n később továbbított, kiesés alatt keletkezett esemény eredeti `sim_time` értéke és tényleges fogadási UTC ideje eltérhet.

## Azonosítók és döntési bizonyíték

A `run_id` a teljes futást azonosítja. A `producer` a publikáló szerepét jelöli, a `producer_seq` a szerephez tartozó publikációs próbálkozás sorszáma, a `message_id` alakja `<run_id>:<producer>:<producer_seq>`. A sikertelen publikációs próbálkozás is saját sorszámot használ. Egy alkalmazási kérés ismételt küldése új üzenetazonosítót kaphat, miközben `request_id` értéke változatlan marad.

A `request_id` kérelmet, a `reservation_id` parkolófoglalást, a `grant_id` zónaengedélyt azonosít. A `slot_id`, `zone_id` és `car_id` a kapcsolódó erőforrást, illetve autót jelöli. A `control_id` a HTTP-kezelőműveletet kapcsolja össze a későbbi MQTT-feldolgozással. A részletek megőrzik a döntés okát, a vizsgált érzékelési sorszámot és frissességet, valamint ahol értelmezhető, a jelölt útvonalak hosszát vagy a felszabadítás fizikai bizonyítékát.

Egy parkolási út például az alábbi bizonyítékokból rekonstruálható:

1. A gyártósori érkezés és a parkolási kérelem azonosítja a feladatot és a pufferben töltött időt.
2. A koordinátori audit megadja a kiválasztott helyet, a választási szabályt, a jelölt útvonalakat és az érzékelési frissességet. A `parking_grant` parancs a foglalási azonosítót hordozza.
3. A jármű eseményei mutatják az elfogadott parkolóhelyet, az állapotváltást, a fékezés kezdetét és a tényleges megállást. Egy várakozási állapot önmagában nem állítja, hogy a mozgó jármű már megállt.
4. A zónakérelem, engedély, kilépési esemény és későbbi telemetria összeköti a közös terület használatát és bizonyított felszabadítását.
5. A `parked` esemény a parkolásig eltelt időt és a távolságot tartalmazza. A későbbi kiadás és az azt követő üres parkolóérzékelés igazolja a foglalás felszabadítását.

Az audit egyes azonos várakozási döntéseket öt szimulációs másodperces időablakban összesít. A `repeats` és `aggregation_interval_s` mező ennek értelmezését adja meg. A fogadott kérés- és parancssorok külön szerepelnek a szemantikus naplóban; az audit összesítése nem ezek törlése. Az ismételten érkező, változatlan alkalmazási engedély a jármű mozgását nem indíthatja újra.

A helyi járművezérlő `control` csomagja ettől külön diagnosztika. A ténylegesen végrehajtott döntés célsebességét, az előző és következő sebességet, az alkalmazott gyorsulást, a megállási cél útvonalbeli helyét és a döntés előtti hátralévő távolságot rögzíti. A `stop_reason`, `target_state`, `result_state`, `connected` és `zone_grants` a választott korlátozás értelmezését adja. A fékút a döntés előtti sebességből számított ideális `v²/(2b)`, a reakcióráhagyás `v·mintavételi_periódus`; ezek modellfeltevések, nem hitelesített járműbiztonsági értékek.

Érzékelőhiba alatt a friss normál telemetria és parkolóérzékelés szünetel. A `diagnostics/perception/<car_id>` külön, megfigyelői témán továbbítja az aktuális vezérlési döntést és a korábbi, elavuló érzékelőcsomagot. A diagnosztika `source=simulation_controller_diagnostics`, X/Y helyzete pedig `position_source=post_step_simulation_reference`. A koordinátor nem iratkozik fel erre a témára, a referenciahelyzet nem válik friss érzékelési bemenetté. MQTT-kiesés alatt a kijelzőbe ilyen diagnosztika sem jut el; ennek helyén időrés marad. A normál telemetria és ez a diagnosztika QoS 0 publikáció.

## Kezelőparancs: elküldés és alkalmazás

A HTTP-kérés `control_requested` sort kap. Hibás paraméter vagy offline állapot esetén `control_rejected` keletkezik. Elfogadott MQTT-küldésnél a `control_published` azt jelenti, hogy a broker visszaigazolta a parancsot; az HTTP 200 és a PUBACK önmagában nem bizonyítja a modellbeli végrehajtást.

A szimulátor saját aktuális állapotán ismét ellenőrzi a parancsot, majd ugyanazzal a `control_id` értékkel `control_applied` vagy `control_rejected` eseményt publikál. Ez kezeli például a HTTP-ellenőrzés után eltűnt autót, az elavult parancsot és a közben szüneteltetett modell időzített lekapcsolási kérelmét. Reset helyi felügyeleti művelet; új futás indításának alkalmazását az új futás naplója rögzíti. A reset miatti tervezett gyermekfolyamat-leállítás nem jelent váratlan folyamatleállási hibát.

## Helyi API

Az API a loopback HTTP-kiszolgálón érhető el. A paraméterek URL-kódolást igényelnek; a táblázat példái az alapportot használó felületből relatív útvonalként hívhatók.

| Végpont | Módszer | Paraméter és eredmény |
|---|---|---|
| `/api/state` | GET | Aktuális MQTT-állapot, parkoló- és zónakép, mutatók, figyelmeztetések, MQTT- és naplóstatisztika. Betöltéskor `loading=true`. |
| `/api/logs` | GET | `limit` alapértéke 100, backend felső korlátja 500; opcionális `level`, `category`, `car_id`, `search`, `before`. Újabb sorok előbb; a `before` kizáró ID-határ. Eredmény: `run_id`, `items`, `next_before`, a jelenlegi szűrő összes találatát jelölő `total`. |
| `/api/car?car_id=CAR001` | GET | Az adott autó aktuális adata, legfeljebb 200 legújabb telemetriamintája időrendi sorrendben, és legfeljebb 200 legújabb eseménysora. Az aktuális autóadat kiadás után `null` lehet, a történet megmaradhat. |
| `/api/runs` | GET | Mentett futások metaadatai, lapozható lista; a történeti térkép a kiválasztott futás konfigurációját használja. |
| `/api/cars` | GET | `run_id`, `search`, `limit`, `before`; a mentett autók kereshető és lapozható katalógusa. |
| `/api/car/history` | GET | `run_id`, `car_id`, `limit` (legfeljebb 2000), kizáró `after`, rögzített `until_id`, `events_before`. Időrendi fogadott mintalap `sensors` és `control` mezőkkel, lefedettségi összegzés, kapcsolódó események és mentett térkép. |
| `/api/car/history/export` | GET | `run_id`, `car_id`, `format=csv` vagy `json`; a teljes tárolt autóút, észlelések, vezérlési döntések és események az export kezdetén rögzített felső azonosítóig, kötegekben streamelve. |
| `/api/transport/history` | GET | `run_id`, kizáró `after`, `limit` (legfeljebb 500), befoglaló `until_id`; időrendi forgalmi diagnosztikai mintalap rögzített felső archívhatárral. |
| `/api/transport/export` | GET | `run_id`, `format=csv` vagy `json`, `until_id`; a kiválasztott futás mentett forgalmi megfigyeléseinek rögzített prefixe. |
| `/api/export?format=zip` | GET | Letölthető `csv`, `jsonl` vagy `zip`. A fájlnév az aktuális futásazonosítót tartalmazza. |
| `/api/control` | POST | JSON-objektum `action` és az adott művelet paramétereivel; a válasz `control_id`, `run_id` értéket ad. Műveletek: `pause`, `resume`, `reset`, `speed`, `pedestrian`, `block_slot`, `sensor_failure`, `disconnect`, `freeze_car`. |

Eseménysor mezői: `id`, `utc`, `sim_time`, `level`, `category`, `event_type`, `message`, `car_id`, `details`. A `details.mqtt` tartalmazza a relatív MQTT-témát, a fogadási számlálót, a fogadási UTC időt és a késleltetést. A helyi HTTP- és életciklussornak nem szükséges MQTT-fogadási bizonyítékot tartalmaznia.

## Jeladatok rövid sémája

| Téma vagy adat | Lényeges mezők | Eredet és értelmezés |
|---|---|---|
| `telemetry/<car_id>` | `car_id`, `seq`, `sim_time`, `x`, `y`, `speed`, `state`, `wait_reason`, `distance_m`, `period_s`, `slot_id`, `reservation_id`, `units`, `position_uncertainty_m`, `sensors`, `control` | Idealizált lépés utáni járműhelyzet; pozíció és távolság m, sebesség m/s. Alapmintavétel 0,1 szimulációs s. A zajos saját helybecslés és a mozgáslépés előtti érzékelési referencia külön a `sensors` csomagban van. |
| `diagnostics/perception/<car_id>` | `car_id`, `seq=null`, `sim_time`, lépés utáni helyzet és állapot, `source`, `position_source`, `sensors`, `control` | Kizárólag megfigyelői modelldiagnosztika érzékelőhiba alatt. Az eredeti szenzorminta változatlan marad; a referenciahelyzet nem jut a koordinátor érzékelési bemenetére. |
| `sensors/parking` | `seq`, `sim_time`, `period_s`, `slots[{id,occupied,blocked}]` | Virtuális parkolóérzékelés, alapmintavétel 0,1 szimulációs s. A központ foglaltsági döntésének bemenete; teljes kerete a nyers naplóban található. |
| `requests/parking`, `requests/zone` | `car_id`, `request_id`; zónánál `zone_id` | Erőforráskérés. Az alkalmazási kérés azonosítója különbözik a publikáció üzenetazonosítójától. |
| `commands/<car_id>` | `kind`, `car_id`, `request_id`, `reason`; engedélynél `reservation_id` vagy `grant_id` | Koordinátori válasz. A várakozás indoka és a foglalás azonossága ellenőrizhető. |
| `events`, `audit` | `type` vagy `event_type`, `message`, `category`, `level`, `sim_time`, korrelációs azonosítók | A modell állapotváltása és a koordinátor döntési bizonyítéka. |

Az összes publikáció futásazonosítót, küldési számlálót és publikálóazonosítást is tartalmaz. A virtuális érzékelő eredete nem jelent valós hardvermérést. Az autórészlet aktuális állapota a szimulátor MQTT-snapshotjából, sebességtörténete a külön telemetriaüzenetekből származik.

A `sensors` csomag 1-es sémájú virtuális fedélzeti megfigyelés. Az alapmodell 360°-os közelségi érzékelést használ takarásmodell nélkül. A helybecslés korlátos egyenletes zajt kap, az útvonalreferencia ismert térkép, az odometria ideális modelljel. A zebra helye térképi adat; a csomag hozzá társítja a mintavételezett gyalogosállapotot. A radar és a 90°-os szintetikus elölnézet a csomag geometriai ábrázolása; nyers kamera-, radar- vagy LiDAR-adat nem keletkezik.

## Export tartalma és ellenőrzése

A CSV és a JSONL a szemantikus eseménysorokat növekvő ID-sorrendben exportálja. A CSV UTF-8 BOM-ot használ a magyar szöveg Excelben történő felismeréséhez; a részletes adatok JSON-ként jelennek meg egy oszlopban. A ZIP az alábbi fájlokat tartalmazza:

- `events.jsonl` és `events.csv`: ugyanazon szemantikus eseménysorok két formátumban.
- `telemetry.csv`: a megőrzött, MQTT-n fogadott minták; az UTC és szimulációs idő mellett a sorszám, forrás, üzenetazonosítás, m/s és m értékek, valamint a teljes részletek is szerepelnek.
- `complete_trips.jsonl`: V08-tól a teljes fogadott útarchívum minden járműhöz, eredeti `sensors` és `control` csomagokkal, a gördülő részletes telemetriaablakon túl is.
- `transport.csv` és `transport.json`: V14-től a futás ténylegesen mentett forgalmi pontjai, az export rögzített határáig. A mintakör nem az élő 120 pontos memóriabeli ablak. A JSON a sémát, az elérhetőség jelzését és a teljes prefix összegzését is tartalmazza.
- `metadata.json`: konfiguráció, véletlenmag, kiosztási szabály, modellverzió, előző futásazonosító, óraadatok, megőrzési számlálók és a forrásfájlok SHA-256 ellenőrzőösszegei.
- `state.json`: az export időpontjához tartozó aktuális kijelzett állapot.
- `README.txt`: az órák, mértékegységek és az export hatókörének rövid leírása.

Az export a kijelzőben addig feldolgozott, konzisztens adatkészletet tartalmazza. A brokerben vagy a callback feldolgozási sorában még váró üzenet későbbi exportba kerülhet. Az értelmezéshez ellenőrizni kell a `run_id` egyezését, a telemetriacsonkolás számlálóját és a nyers keret jelzését. A `verify_units.py` a ténylegesen lefuttatott egységtesztek eredményét menti; a valódi brokeres működést ettől elkülönülten a `tests.mqtt_smoke` vizsgálja.

Az autónkénti teljes CSV/JSON útexport minden megőrzött mintát, teljes szenzorcsomagot és vezérlési döntést tartalmaz az export kezdetén rögzített határig. V08-tól az önálló JSON a kiválasztott futás saját `metadata`, `config`, `map` környezetét is megőrzi. A CSV `sensors`, `control` és `details` oszlopai JSON-szöveget tartalmaznak. A futás-ZIP `complete_trips.jsonl` tagja ugyanezt a teljes archivált mintakört adja át minden járműhöz; a `telemetry.csv` továbbra is a gördülő részletes ablak. Az egykeretes SVG/JSON letöltés csak az éppen kijelzett érzékelőmintára és nézetkontextusra vonatkozik, nem videó vagy teljes futásmentés.

A V08 ZIP-export önálló, rögzített SQLite-olvasási pillanatképből készül, 100 soros adagokban. A 8 MiB-nál nagyobb tömörített kimenet ideiglenes lemezfájlba kerül, a HTTP-válasz 256 KiB-os darabokat küld ismert fájlhosszal. A globális alkalmazászár nem marad fogva a tömörítés és letöltés alatt; a lassú kliens előtt az adatbázis-pillanatkép már felszabadul. A `metadata.export_snapshot` rögzíti a rekordhatárokat, a részletes és teljes mintaszámot. A tömörítés időt és lemezhelyet igényel, közben a WAL-fájl növekedhet. A bájtos `app.export("zip")` kompatibilitási segéd továbbra is memóriába olvas, ezért nagy futásnál a HTTP-export vagy az `export_archive()` kontextuskezelő szükséges.

A V14 forgalmi export legfeljebb 500 soros adagokkal olvas. A `metadata.export_snapshot` új `transport_until_id`, `transport_schema_version`, `transport_available` és `transport_members` mezői a forgalmi prefixet azonosítják. A teljes prefix összegzése minden megőrzött pontot végigolvas, ezért a lekérdezési idő az előzmény hosszával növekedhet; a korlátozott memóriahasználat nem állandó válaszidő-garancia. A forgalmi CSV és JSON önállóan is elérhető a `/api/transport/export` végponton.

V15-től a `/api/transport/context` egy pontos mentett forgalmi rekordból kérdez le járműállapotokat és szemantikus eseményeket. Az autók utolsó fogadott skalár rekordja és az eseménysor a mérésben megőrzött globális telemetria-/eseményhatárhoz tartozik. A két lista külön lapozható; a későbbi adatok és az új élő futás nem kerülnek bele. A böngészőből az adott autó pontos utolsó rekordjára, mentett érzékelőire lehet ugrani. A korábban mindig teljes archívumot átadó autóexportok e résznézetben nem aktívak; a külön határbővítés visszaállítja őket. Az adat nem egyidejű fizikai világállapot és nem oksági elemzés. Részletes adatmezők, mérési eredmény és reprodukálás: [V15 diagnosztikai kontextus](DIAGNOSZTIKAI_KONTEXTUS_V15.md).

A V06 ellenőrzésében **133 egységteszt sikeres volt**. A `tests.perception_mqtt_smoke` külön valódi MQTT/HTTP-próbája `69a5a1e0d503` azonosítóval két autó parkolását, nulla modellezett ütközést, szenzorhiba és helyreállás rögzítését, lapozást, CSV/JSON egyezést és reset utáni történeti olvasást igazolt. A CAR001 2485 fogadott keretéhez 2485 megőrzött szenzorcsomag tartozott; ezekben 2425 különböző sikeres mintavételi időpont és 60 hibakori diagnosztikai keret szerepelt. A nyers napló 215 részének rotációja nem törölte az észlelési archívumot. Bizonyíték: [a 2026. október 5-i rendszerteszt összegzése](../results/perception-smoke-20261005-225201/summary.json). Ezek véges tesztesetek eredményei; a csomaglefedettség nem általános hálózati vagy felismerési pontossági garancia.
# V16: tartós üzemeltetési incidensek

Az `operations_incidents` táblában az önálló problémaepizódok állapota mentődik. Az aktív és a nyugtázott állapot külön mező. A nyitás, megszűnés és nyugtázás az `operations` kategóriában külön szemantikus auditbejegyzést hoz létre; az ismételt HTTP-lekérés nem új esemény. A kezelői megjegyzés legfeljebb 500 karakter, és a nyugtázás nem küld mozgásvezérlő MQTT-parancsot.

Az epizód az első hibás megfigyelés forgalmi mintaazonosítóját és két fogadási rekordhatárát megőrzi. A megnyitás időpontja a megerősítő megfigyelés UTC-ideje; a két időpont eltérhet. Az ismeretlen adat nem igazolja a hiba megszűnését. Az incidenslista felső azonosítója a lapozásba bevont epizódokat rögzíti, az aktuális futásban az epizódok életciklusa közben tovább változhat. Az első hibás megfigyelésből nyitott diagnosztikai bizonyíték ezzel szemben rögzített előzmény.

A futás ZIP-exportja `incidents.json` fájlt is tartalmaz, a szemantikus audit az `events.jsonl` és `events.csv` része. A lista lapozható, az export korlátos memóriával készül. Korábbi, incidens-táblát még nem tartalmazó futásoknál az elérhetőség hiánya jelzett; ez nem nulla hibaszámot bizonyít. Részletek: [V16 üzemeltetési leírás](UZEMELTETES_V16.md).

